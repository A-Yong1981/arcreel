"""Small, project-scoped upload bridge for moving Muse VM media into ArcReel."""

from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


API_BASE = os.environ.get("ARCREEL_API_BASE", "http://host.docker.internal:1241/api/v1").rstrip("/")
ARCREEL_MCP_URL = os.environ.get(
    "ARCREEL_MCP_URL", f"{API_BASE.removesuffix('/api/v1')}/mcp"
).rstrip("/")
ARCREEL_API_KEY = os.environ.get("ARCREEL_API_KEY", "")
BRIDGE_TOKEN = os.environ.get("MUSE_BRIDGE_TOKEN", "")
ALLOWED_PROJECTS = frozenset(
    item.strip() for item in os.environ.get("ALLOWED_PROJECTS", "").split(",") if item.strip()
)
ALL_PROJECTS_ALLOWED = "*" in ALLOWED_PROJECTS
IMAGE_LIMIT = 30 * 1024 * 1024
# The current bearly.de5.net Cloudflare zone is Free (100 MB request body cap).
# Keep a margin for multipart framing so uploads fail in the connector, not at the edge.
VIDEO_LIMIT = 90 * 1024 * 1024
REQUEST_LIMIT = int(os.environ.get("MAX_REQUEST_BYTES", str(VIDEO_LIMIT + 2 * 1024 * 1024)))
PUBLIC_PATH_PREFIX = os.environ.get("PUBLIC_PATH_PREFIX", "/muse-connector").rstrip("/")

CONFIG_LOADED_AT = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
MCP_DISABLED_REASON = (
    "Muse performs all model generation. Use project-management tools to edit the project, "
    "then upload Muse-generated files through /v1/upload-shot."
)

app = FastAPI(
    title="ArcReel Muse Upload Connector",
    description=(
        "Muse authenticates with MUSE_BRIDGE_TOKEN. ARCREEL_API_KEY is used only by this "
        "connector for server-side ArcReel requests. The scripts REST endpoint reads formal "
        "episode JSON scripts; source text is available through MCP get_source_text. "
        "Use create_episode_script to create an empty formal script without model generation."
    ),
    version="1.1.0",
)
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="MuseBridgeBearer", description="Use MUSE_BRIDGE_TOKEN; ARCREEL_API_KEY is internal to the connector.")

# Muse owns all model work. Keep ArcReel's model-backed tools unavailable even though the
# underlying ArcReel MCP server also exposes them.
BLOCKED_MODEL_TOOLS = frozenset(
    {
        "generate_assets",
        "generate_storyboards",
        "edit_images",
        "generate_narration_audio",
        "generate_videos",
        "generate_grid",
        "plan_episodes",
        "generate_episode_script",
        "generate_script_plan",
    }
)


@app.middleware("http")
async def reject_oversized_uploads(request: Request, call_next):
    """Strip the Cloudflare path route and reject oversized bodies before parsing."""
    if PUBLIC_PATH_PREFIX and request.scope["path"].startswith(PUBLIC_PATH_PREFIX + "/"):
        request.scope["path"] = request.scope["path"][len(PUBLIC_PATH_PREFIX):]
        request.scope["raw_path"] = request.scope["path"].encode("utf-8")
    if request.url.path == "/v1/upload-shot":
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > REQUEST_LIMIT:
                    return JSONResponse(status_code=413, content={"detail": "Request is too large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid content-length"})
    return await call_next(request)


def require_config() -> None:
    if not ARCREEL_API_KEY or not BRIDGE_TOKEN or not ALLOWED_PROJECTS:
        raise HTTPException(status_code=503, detail="Connector is not configured")


async def authenticate(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    require_config()
    if credentials is None or not hmac.compare_digest(credentials.credentials, BRIDGE_TOKEN):
        raise HTTPException(
            status_code=401,
            detail=(
                "Bearer token rejected by Muse connector. Muse must use MUSE_BRIDGE_TOKEN; "
                "ARCREEL_API_KEY is internal-only. The uploader loads .env at container "
                "creation; check X-Bridge-Config-Loaded-At and recreate muse-uploader after "
                "changing configuration."
            ),
            headers={"X-Bridge-Config-Loaded-At": CONFIG_LOADED_AT},
        )


def check_project(project_name: str) -> None:
    if not ALL_PROJECTS_ALLOWED and project_name not in ALLOWED_PROJECTS:
        raise HTTPException(status_code=403, detail="Project is not allowlisted")


def arc_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ARCREEL_API_KEY}"}


async def arc_request(method: str, path: str, **kwargs: object) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0)) as client:
            response = await client.request(method, f"{API_BASE}{path}", headers=arc_headers(), **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="ArcReel is unreachable") from exc
    if response.is_error:
        # Do not return upstream bodies: they may contain server paths or deployment details.
        raise HTTPException(status_code=response.status_code, detail="ArcReel rejected the request")
    return response


@app.get("/health", summary="Health and non-secret configuration diagnostics")
async def health() -> dict[str, object]:
    return {
        "status": "ok" if ARCREEL_API_KEY and BRIDGE_TOKEN and ALLOWED_PROJECTS else "needs_configuration",
        "config_loaded_at": CONFIG_LOADED_AT,
        "muse_bearer_configured": bool(BRIDGE_TOKEN),
        "arcreel_upstream_credential_configured": bool(ARCREEL_API_KEY),
    }


@app.get("/v1/projects", dependencies=[Depends(authenticate)])
async def list_projects() -> dict[str, object]:
    response = await arc_request("GET", "/projects")
    data = response.json()
    projects = data.get("projects", []) if isinstance(data, dict) else []
    return {
        "projects": [
            p
            for p in projects
            if isinstance(p, dict) and (ALL_PROJECTS_ALLOWED or p.get("name") in ALLOWED_PROJECTS)
        ]
    }


@app.get(
    "/v1/projects/{project_name}/scripts/{script_file}",
    dependencies=[Depends(authenticate)],
    summary="Read a formal episode script",
    description="script_file is the formal episode JSON basename (e.g. episode_1.json), not a source filename, path, or episode ID. Read source/ files with MCP get_source_text.",
    responses={404: {"description": "Formal script not found; source text is not served here."}},
)
async def read_script(project_name: str, script_file: str) -> dict[str, object]:
    check_project(project_name)
    if PurePosixPath(script_file).name != script_file or script_file in {".", ".."} or not script_file.endswith(".json"):
        raise HTTPException(status_code=400, detail="script_file must be a formal JSON basename such as episode_1.json")
    try:
        response = await arc_request(
            "GET", f"/projects/{quote(project_name, safe='')}/scripts/{quote(script_file, safe='')}"
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail={"code": "formal_script_not_found", "message": f"Formal script {script_file!r} was not found. This endpoint accepts formal episode JSON scripts only; read source/ files with MCP get_source_text and create a missing script with MCP create_episode_script."}) from exc
        raise
    return response.json()


@app.post(
    "/v1/upload-shot",
    dependencies=[Depends(authenticate)],
    summary="Upload media to an existing formal-script scene",
    description=(
        "script_file is the formal JSON basename (e.g. episode_1.json). shot_id is the exact "
        "existing scene/item ID in that script; upload does not create it. Drama/storyboard scene "
        "IDs use ArcReel's E{episode}S{sequence} convention (e.g. E1S01); use the ID returned by "
        "get_episode_script/patch_episode_script. Create the formal script and insert scenes first. "
        "The response contains path, version, and artifact fingerprints."
    ),
    responses={404: {"description": "Formal script or scene ID not found; create them first."}},
)
async def upload_shot(
    request: Request,
    project_name: Annotated[str, Form(description="ArcReel project name")],
    script_file: Annotated[str, Form(description="Formal episode JSON basename, e.g. episode_1.json; not a source filename")],
    shot_id: Annotated[str, Form(description="Exact existing formal-script scene/item ID returned by ArcReel, e.g. E1S01")],
    kind: Annotated[Literal["storyboard", "video"], Form()],
    file: Annotated[UploadFile, File()],
) -> dict[str, object]:
    """Upload Muse media to an existing formal-script item; never creates a scene."""
    check_project(project_name)
    if PurePosixPath(script_file).name != script_file or script_file in {".", ".."} or not script_file.strip() or not script_file.endswith(".json"):
        raise HTTPException(status_code=400, detail="script_file must be a formal JSON basename such as episode_1.json")
    if not shot_id or len(shot_id) > 100 or any(char in shot_id for char in ("/", "\\", "\x00")):
        raise HTTPException(status_code=400, detail="Invalid shot id")
    max_size = IMAGE_LIMIT if kind == "storyboard" else VIDEO_LIMIT
    if file.size is not None and file.size > max_size:
        raise HTTPException(status_code=413, detail=f"File exceeds {max_size // (1024 * 1024)} MiB limit")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")
    filename = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or filename in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid filename")
    declared_length = request.headers.get("content-length")
    if declared_length and int(declared_length) > REQUEST_LIMIT:
        raise HTTPException(status_code=413, detail="Request is too large")

    path = (
        f"/projects/{quote(project_name, safe='')}/shots/{quote(shot_id, safe='')}"
        f"/upload/{kind}"
    )
    # ArcReel validates extension, content, file size, shot existence and project paths,
    # then records the upload in its own version/artifact metadata.
    try:
        response = await arc_request(
            "POST",
            path,
            params={"script_file": script_file},
            files={"file": (filename, file.file, file.content_type or "application/octet-stream")},
        )
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "script_or_scene_not_found",
                    "message": "Upload requires an existing formal script and exact existing scene/item ID. Create the script, insert the scene with patch_episode_script, then retry with the returned ID.",
                },
            ) from exc
        raise
    return response.json()


CREATE_EPISODE_SCRIPT_TOOL: dict[str, object] = {
    "name": "create_episode_script",
    "title": "Create empty formal episode script",
    "description": (
        "Create an empty formal JSON episode script without a model. Then call get_episode_script "
        "for its canonical revision and patch_episode_script to insert Muse-authored scenes. "
        "Returns script_file basename (e.g. episode_1.json) and script_path (e.g. scripts/episode_1.json)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Exact project name from list_projects."},
            "episode_id": {"type": "integer", "minimum": 1, "description": "Existing episode ID from project content/workflow plan."},
        },
        "required": ["project", "episode_id"],
        "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
}


def _decorate_tools_payload(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        return payload
    tools = result["tools"]
    names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
    for tool in tools:
        if isinstance(tool, dict) and tool.get("name") in BLOCKED_MODEL_TOOLS:
            tool["disabled"] = True
            tool["disabled_reason"] = MCP_DISABLED_REASON
            tool["description"] = f"DISABLED: {MCP_DISABLED_REASON}\n\n{tool.get('description', '')}"
    if CREATE_EPISODE_SCRIPT_TOOL["name"] not in names:
        tools.append(CREATE_EPISODE_SCRIPT_TOOL)
    return payload


def _decorate_tools_list_body(body: bytes, content_type: str) -> bytes:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    if "text/event-stream" in content_type:
        lines = []
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    payload = json.loads(line[5:].strip())
                    line = "data: " + json.dumps(_decorate_tools_payload(payload), ensure_ascii=False, separators=(",", ":"))
                except json.JSONDecodeError:
                    pass
            lines.append(line)
        return ("\n".join(lines) + ("\n" if text.endswith("\n") else "")).encode("utf-8")
    try:
        return json.dumps(_decorate_tools_payload(json.loads(text)), ensure_ascii=False).encode("utf-8")
    except json.JSONDecodeError:
        return body


def _mcp_tool_response(request_id: object, value: object, *, is_error: bool = False) -> JSONResponse:
    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                "structuredContent": value,
                "isError": is_error,
            },
        }
    )


@app.api_route("/mcp", methods=["GET", "POST", "DELETE"], dependencies=[Depends(authenticate)])
@app.api_route("/mcp/", methods=["GET", "POST", "DELETE"], dependencies=[Depends(authenticate)], include_in_schema=False)
async def proxy_arcreel_mcp(request: Request) -> Response:
    """Forward authenticated Muse MCP traffic to ArcReel using the private server-side API key."""
    body = await request.body() if request.method == "POST" else b""
    if len(body) > REQUEST_LIMIT:
        return JSONResponse(status_code=413, content={"detail": "MCP request is too large"})

    try:
        message = json.loads(body) if body else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        message = None
    mcp_method = message.get("method") if isinstance(message, dict) else None
    mcp_params = message.get("params") if isinstance(message, dict) else None
    tool_name = mcp_params.get("name") if isinstance(mcp_params, dict) else None

    if mcp_method == "tools/call" and tool_name == "create_episode_script":
        args = mcp_params.get("arguments") if isinstance(mcp_params, dict) else None
        args = args if isinstance(args, dict) else {}
        project_name = args.get("project")
        episode_id = args.get("episode_id")
        request_id = message.get("id")
        if not isinstance(project_name, str) or not isinstance(episode_id, int) or isinstance(episode_id, bool) or episode_id < 1:
            return _mcp_tool_response(request_id, {"code": "invalid_request", "message": "project and positive integer episode_id are required."}, is_error=True)
        try:
            check_project(project_name)
            upstream = await arc_request(
                "POST",
                f"/projects/{quote(project_name, safe='')}/episodes/{episode_id}/blank-script",
            )
            created = upstream.json()
            script_file = created.get("script_file") if isinstance(created, dict) else None
            if not isinstance(script_file, str) or not script_file:
                return _mcp_tool_response(request_id, {"code": "unexpected_upstream_response", "message": "ArcReel did not return a formal script filename."}, is_error=True)
            return _mcp_tool_response(
                request_id,
                {
                    "success": True,
                    "project": project_name,
                    "episode_id": episode_id,
                    "script_file": script_file,
                    "script_path": f"scripts/{script_file}",
                    "next_step": "Call get_episode_script, then patch_episode_script with that revision to insert Muse-authored scenes.",
                },
            )
        except HTTPException as exc:
            code = "episode_not_found" if exc.status_code == 404 else "script_creation_rejected"
            return _mcp_tool_response(request_id, {"code": code, "status": exc.status_code, "message": str(exc.detail)}, is_error=True)

    if mcp_method == "tools/call" and tool_name in BLOCKED_MODEL_TOOLS:
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Tool '{tool_name}' is disabled: {MCP_DISABLED_REASON}"}],
                },
            }
        )

    forwarded_headers = {
        key: request.headers[key]
        for key in ("accept", "content-type", "mcp-session-id", "mcp-protocol-version", "last-event-id")
        if key in request.headers
    }
    forwarded_headers["authorization"] = f"Bearer {ARCREEL_API_KEY}"
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=600.0))
    try:
        upstream_request = client.build_request(
            request.method,
            f"{ARCREEL_MCP_URL}{'/' if request.url.path.endswith('/') else ''}",
            headers=forwarded_headers,
            content=body,
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="ArcReel MCP is unreachable") from exc

    response_headers = {
        key: upstream.headers[key]
        for key in ("content-type", "mcp-session-id", "mcp-protocol-version", "cache-control")
        if key in upstream.headers
    }

    if mcp_method == "tools/list":
        upstream_body = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        decorated_body = _decorate_tools_list_body(upstream_body, upstream.headers.get("content-type", ""))
        return Response(content=decorated_body, status_code=upstream.status_code, headers=response_headers)

    async def close_upstream() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(close_upstream),
    )
