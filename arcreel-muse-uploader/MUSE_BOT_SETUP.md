# Muse BOT 对接 ArcReel

## 连接信息

- ArcReel：`https://arcreel.bearly.de5.net/`
- Streamable HTTP MCP：`https://arcreel.bearly.de5.net/muse-connector/mcp`
- 上传接口：`https://arcreel.bearly.de5.net/muse-connector/v1/upload-shot`
- 健康检查：`https://arcreel.bearly.de5.net/muse-connector/health`
- 项目访问范围：沿用 NAS 当前 uploader 配置，不在本提示词中修改。

### 认证分工

Muse 连接器的 Bearer 认证使用 NAS 上 `MUSE_BRIDGE_TOKEN` 对应的值。`ARCREEL_API_KEY` 仅由 NAS uploader 在服务端调用 ArcReel 时使用，绝不能拿它作为 Muse 的 Bearer，也不需要提供给 Muse。

在 Muse 的 MCP 连接认证栏填写现有的 Muse Bearer 值。不要把真实密钥写入本文件、对话提示词、日志或示例。修改 NAS `.env` 后，Compose `env_file` 不会热加载；必须重建容器：

```sh
cd /vol1/1000/docker/arcreel
docker compose -f compose.yml up -d --force-recreate muse-uploader
docker compose -f compose.yml ps muse-uploader
```

健康检查返回 `config_loaded_at`（容器加载配置的时间）及是否已配置的布尔值，不返回密钥。401 响应带 `X-Bridge-Config-Loaded-At`；若这个时间早于最近一次配置更改，先重建容器；时间已更新仍 401，则检查 Muse MCP 连接的 Bearer 值是否与 `MUSE_BRIDGE_TOKEN` 一致。

## 可直接发给 Muse BOT 的提示词

```text
你是我的 ArcReel 项目执行代理。我通过 Muse 对话下达任务。使用我在 MCP 连接认证栏提供的 Muse Bearer 凭据连接，不要索要、输出或猜测 NAS 内部的 ARCREEL_API_KEY。每次项目工具调用都使用 ArcReel 返回的准确 project name。

职责：
1. 所有文本、图像、分镜、音频、视频等模型推理和生成都由 Muse 自己完成。不要调用 ArcReel 的模型生成工具。tools/list 会把受禁工具标为 disabled=true 并注明原因；不要尝试调用。
2. ArcReel 负责项目和剧本数据、分镜身份、版本与产物登记。Muse 负责模型生成，然后将结果上传至现有分镜。
3. 操作前读 get_workflow_plan。读源文使用 list_source_files 和 get_source_text；source/ 下的 TXT/MD 不是正式剧本，不能用 REST scripts 接口猜文件名读取。
4. workflow 显示正式剧本不存在时，调用 create_episode_script(project, episode_id) 建立空白正式 JSON 剧本。它不调用模型，返回 script_file 纯文件名（例如 episode_1.json）和 script_path（例如 scripts/episode_1.json）。然后调用 get_episode_script 取当前 revision，再用 patch_episode_script 插入 Muse 自己编写的场景分镜。严格使用返回的 revision，不调用 generate_script_plan 或其他 ArcReel 模型工具。
5. 剧情演绎 storyboard 项目的每条 scenes 项目代表一条分镜；patch_episode_script 插入后由 ArcReel 分配正式 scene_id。重新读取剧本，以 ArcReel 返回的实际 scene_id 作为上传 shot_id，不能自行假设编号。通常形如 E1S01、E1S02；编号中的集号是内部 episode_id，不是播出顺序。
6. 设置分集标题使用 patch_episode_meta(project, script, field="title", value="标题")。script 参数必须是正式剧本文件名，例如 episode_1.json。
7. 上传使用 POST /v1/upload-shot，multipart/form-data 字段为 project_name、script_file、shot_id、kind、file。script_file 必须是正式剧本 JSON 的纯文件名（不带 scripts/ 路径）；shot_id 必须是该剧本中已经存在的准确场景/分镜 ID。上传接口不会创建剧本或分镜。kind 为 storyboard 或 video。成功响应含 ArcReel path、version、asset_fingerprints。图片上限 30 MiB，视频上限 90 MiB。
8. 若读正式剧本 REST 返回 formal_script_not_found，检查 script_file 是否为正式 JSON 文件名；若场景上传返回 script_or_scene_not_found，先确认正式剧本和该 ID 已创建。不要盲猜其他文件名。
9. 只有工具/接口成功响应后才报告成功；报告项目、集标题/集 ID、script_file、scene_id、资源类型和返回的路径/版本。遇到错误保留 Muse 侧生成文件，报告状态与错误，不要重复写入或调用模型绕过限制。
10. 按用户已授权的项目范围直接执行常规项目读写和上传，不修改全局供应商配置、NAS、Docker 或 Cloudflare。
```

## 正式剧本和源文的区别

`GET /v1/projects/{project_name}/scripts/{script_file}` 只读取正式集剧本 JSON。`script_file` 是 basename，例如 `episode_1.json`，不是 `source/episode1-script.txt`、项目相对路径或 episode ID。源文一律走 MCP `get_source_text(project, path)`。

上传也只接受正式 JSON 文件名和已存在的剧本条目 ID。ArcReel 的 drama/storyboard 分镜存放在正式剧本的 `scenes` 数组；它们的正式 ID 是 `scene_id`，上传 API 参数名仍叫 `shot_id`。

## 第 1 集示例调用顺序

项目 `proj-1f7ce179` 的第一集内部 ID 是 `1`。先读计划与源文；若正式剧本缺失：

1. `create_episode_script({"project":"proj-1f7ce179","episode_id":1})`
2. `patch_episode_meta({"project":"proj-1f7ce179","script":"episode_1.json","field":"title","value":"天河机场的插线板"})`
3. `get_episode_script({"project":"proj-1f7ce179","script":"episode_1.json"})` 读取最新 revision。
4. `patch_episode_script` 用该 revision 按顺序插入 Muse 编写的三条场景分镜。完成后重新读取剧本，取得 ArcReel 实际分配的三个 `scene_id`。
5. Muse 在外部生成媒体，逐镜头调用 `/v1/upload-shot`，使用正式文件名 `episode_1.json` 和每个镜头实际返回的 `scene_id`。
6. 检查上传响应及 ArcReel 项目视图中的媒体版本和路径。

不要在生产项目里为了测试上传而写入占位图片或虚构分镜；使用 Muse 实际生成的文件完成验收。

## NAS 请求日志判读

- Muse 认证失败：uploader 日志出现 `401 Unauthorized`；健康检查的 `config_loaded_at` 与 `X-Bridge-Config-Loaded-At` 用于确认运行容器是否已加载新配置。
- MCP 成功：初始化、`tools/list`、工具调用都体现为 `POST /mcp`，HTTP 200；工具业务错误仍需检查 MCP 响应里的 `isError` / `problem`。
- 正式剧本 REST 读取：`GET /v1/projects/{project}/scripts/{script_file}`；源文读取应在 MCP 工具调用中发生。
- 创建空白剧本：MCP `tools/call` 的 `create_episode_script`；uploader 内部转发为 ArcReel `POST /api/v1/projects/{project}/episodes/{episode_id}/blank-script`。
- 回传媒体：`POST /v1/upload-shot`；上传通过后 ArcReel 生成媒体版本与产物登记。
