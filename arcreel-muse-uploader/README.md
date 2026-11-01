# ArcReel Muse Upload Connector

This connector exposes an authenticated Streamable HTTP MCP proxy and a media upload API for Muse. Muse runs all model-backed generation; ArcReel stores project data and uploaded results.

## Authentication roles

- `MUSE_BRIDGE_TOKEN` is the Bearer credential Muse uses to authenticate to this connector.
- `ARCREEL_API_KEY` is used only inside the connector for its server-side ArcReel requests. Never give it to Muse or use it as the Muse Bearer token.
- `ALLOWED_PROJECTS` controls project scope. The deployed scope is preserved by this change.

The connector loads `env_file` values when its container is created. After changing `.env`, recreate the service so the running process receives the new environment:

```sh
cd /vol1/1000/docker/arcreel
docker compose -f compose.yml up -d --force-recreate muse-uploader
docker compose -f compose.yml ps muse-uploader
```

Health returns `config_loaded_at` and credential-presence booleans without returning secret values. A 401 response includes `X-Bridge-Config-Loaded-At`; compare it with the most recent config change to determine whether the running container has been recreated.

## Script and scene workflow

`GET /v1/projects/{project_name}/scripts/{script_file}` reads a **formal episode JSON script basename**, such as `episode_1.json`. It does not read files in `source/`. Read source text using MCP `list_source_files` and `get_source_text`.

The connector adds a non-generative MCP project tool, `create_episode_script(project, episode_id)`. It calls ArcReel's existing blank-script project operation and returns `script_file` (basename) and `script_path` (project-relative path). Then use `get_episode_script` for the canonical revision and `patch_episode_script` to insert Muse-authored content. Set the episode title with `patch_episode_meta(project, script, field="title", value="...")`.

For a drama/storyboard project, the formal items live in the `scenes` array. ArcReel assigns each inserted item's actual `scene_id` (normally `E{episode}S{sequence}`, e.g. `E1S01`). Re-read the formal script and use that exact returned ID. The upload field remains named `shot_id`; it is an alias for the existing scene/item ID and does not create a scene. `script_file` in upload is the formal JSON basename, not `scripts/...` and not a source file.

## Endpoints

- MCP: `https://arcreel.bearly.de5.net/muse-connector/mcp`
- Upload: `https://arcreel.bearly.de5.net/muse-connector/v1/upload-shot`
- Health: `https://arcreel.bearly.de5.net/muse-connector/health`

Upload uses multipart fields `project_name`, `script_file`, `shot_id`, `kind`, and `file`. `kind` is `storyboard` or `video`. Images are limited to 30 MiB; videos to 90 MiB. ArcReel validates that the formal script and exact item already exist and then returns the media path, version, and artifact fingerprints.

## Disabled model tools

The connector marks ArcReel model-generation tools as `disabled=true`, includes `disabled_reason`, and prefixes their descriptions. Calls are also rejected server-side. Muse must perform all model generation and use project tools only for non-generative ArcReel data operations.

## Unified Compose

All services remain managed by `/vol1/1000/docker/arcreel/compose.yml`. Rebuild only the connector after code changes:

```sh
cd /vol1/1000/docker/arcreel
docker compose -f compose.yml up -d --build --force-recreate muse-uploader
docker compose -f compose.yml ps muse-uploader
```

See [MUSE_BOT_SETUP.md](MUSE_BOT_SETUP.md) for Muse's workflow prompt and first-episode call sequence. Never write real credential values into documentation, prompts, shell transcripts, or logs.
