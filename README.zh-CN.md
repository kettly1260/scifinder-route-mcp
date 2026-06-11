# scifinder-route-mcp

面向 NAS / Docker 长期运行的 SciFinder 合成路线检索 MCP 服务。它可以从本地 SciFinder 导出文件中抽取“反应步骤级”的合成路线信息，并支持检索、证据链追踪、异步解析、Admin Web UI、外部 OCR / LLM / 向量 / 文档解析 / 结构识别服务接入，以及 SQLite 生产级降级运行。

> GHCR 可见性提示：如果匿名拉取镜像失败，请打开 GitHub → Packages → `scifinder-route-mcp` → Package settings → Change visibility → Public。当前 Compose 文件已经默认使用 `ghcr.io/kettly1260/scifinder-route-mcp:latest`。

## 使用预构建镜像快速部署

已发布的 Docker 镜像支持以下平台：

```text
linux/amd64
linux/arm64
```

这覆盖常见 x86 NAS / 服务器，以及 ARM64 NAS / 单板机。

部署步骤：

```bash
git clone https://github.com/kettly1260/scifinder-route-mcp.git
cd scifinder-route-mcp
cp .env.example .env
mkdir -p nas-data nas-inbox
docker compose -f docker-compose.image.yml up -d
```

启动后访问：

```text
Admin Web UI: http://<nas-host>:8001/
MCP SSE:      http://<nas-host>:8000/sse
```

把 SciFinder 导出的 PDF / HTML / MHTML / TXT 文件放入 `nas-inbox`，然后在 Admin Web UI 中点击 **Scan Inbox**，或通过 MCP 调用 `scan_inbox` 工具。`docker-compose.image.yml` 只依赖预构建 `image:`，不会在本地构建镜像。

## 本地构建部署

如果需要在本地构建镜像：

```bash
docker compose up -d --build
```

持久化路径：

```text
./nas-data  -> /data
./nas-inbox -> /inbox，容器内只读
./nas-data/uploads -> /data/uploads，用于 HTTP 上传和 sidecar 投递暂存
```

NAS 配置下解析任务默认异步执行。任务状态持久化在 SQLite 中；容器重启后，未完成的 `running` 任务会自动重新进入 `queued` 队列。可以轮询 `get_parse_job_status` 或 `list_parse_jobs` 查看进度。

## 环境变量与运行时配置

复制 `.env.example` 为 `.env` 后按需修改。Docker 层面的配置，例如发布端口、卷挂载、容器网络和重启策略，只应通过 `.env` / Compose 配置完成。Admin Web UI 不会修改 Docker 文件，也不会控制宿主机 Docker。

应用内热配置从 `/data/config.yaml` 读取。如果希望启动前就使用文件配置，可以复制：

```bash
cp config.example.yaml nas-data/config.yaml
```

支持热更新的配置包括：

```text
server.async_jobs, server.max_workers, server.storage_backend
queue.backend, queue.redis_url
security.allow_external_paths, security.token, security.users
ingest.scan_extensions
integrations.*
extraction.llm_schema_version, extraction.llm_prompt_profile, extraction.llm_cost_limit_usd
thresholds.verification_confidence_threshold
retention.evidence_retention_days, retention.cache_retention_days
```

可通过 MCP 工具管理配置：

```text
get_config
update_config
validate_config
reload_config
```

也可以直接使用 Admin Web UI 修改可热更新配置。

## Admin Web UI

Admin Web UI 是轻量级运维控制台，用于长期 NAS 部署中的配置、队列、索引和集成状态管理。当前包含：

```text
- 健康状态卡片与挂载路径诊断
- token 保护的配置修改
- 队列状态、最近任务、失败任务重试
- HTTP 上传入口，供 sidecar / 客户端投递文件
- LLM endpoint / model / enable toggle / schema version / prompt profile / cost limit
- embedding endpoint / model / vector rebuild / vector index status / last error
- OCR endpoint / model / OCR backlog
- document parser endpoint / model / parser fallback / endpoint health
- structure recognition endpoint / model / endpoint health
- PostgreSQL URL / backend status / SQLite fallback 状态
- DOI 低置信度队列数量
- 最近 evaluation metrics
- SQLite backup、retention dry-run cleanup、NAS 存储用量
- compound registry 数量；详细检索通过 MCP 工具完成
```

Admin Web UI 明确不做以下事情：

```text
- 不编辑 docker-compose.yml
- 不挂载 Docker socket
- 不启动或停止宿主机容器
- 不修改宿主机卷挂载或发布端口
```

这些 Docker 外部配置需要通过 `.env` 和 Compose 处理。

## MCP 工具

当前已实现工具：

```text
health_check
get_config
update_config
validate_config
reload_config
scan_inbox
register_document
upload_document
get_parse_job_status
list_parse_jobs
retry_parse_job
retry_failed_jobs
search_reaction_steps
get_reaction_step
get_reaction_provenance
record_doi_verification
reparse_document
export_evaluation_set
compute_evaluation_metrics
get_evaluation_status
rebuild_vector_index
get_vector_index_status
semantic_search_reaction_steps
search_compounds
get_compound
merge_compounds
search_by_smiles
recognize_structure_image
backup_database
get_storage_usage
cleanup_evidence_cache
test_integration_endpoint
```

## 功能矩阵

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| Docker / NAS SSE 服务 | 已实现 | 支持本地构建 Compose 和预构建镜像 Compose。 |
| GHCR 多架构镜像 workflow | 已实现 | 支持 `linux/amd64` 和 `linux/arm64`；GHCR 包可见性可能需要手动设为 Public。 |
| 只读 NAS inbox 扫描 | 已实现 | `/inbox` 在容器内只读挂载。 |
| HTTP 上传暂存 | 已实现 | `POST /api/upload` 写入 `/data/uploads`；支持 hash 去重。 |
| Sidecar watcher | 已实现 | `scifinder-route-sidecar` 轮询客户端目录，稳定后上传文件。 |
| Durable queue | 已实现 | 默认 SQLite 队列；支持重启恢复和失败任务重试。Redis 为可选/降级配置，不影响 SQLite 运行。 |
| SQLite 存储 | 已实现 | 包含文档、解析任务、反应步骤、证据链、DOI 验证、向量、化合物和评测指标。 |
| PostgreSQL backend | 可运行降级集成 | `SCIFINDER_ROUTE_BACKEND=postgres` 会测试连接并报告状态；Postgres 不可用时 SQLite 继续作为活动 fallback。 |
| pgvector | 可选/降级 | SQLite 以 JSON 存储 embedding 并在 Python 中做 cosine 检索；Postgres / pgvector 作为可扩展方向。 |
| PDF / HTML / MHTML / text 解析 | 已实现 | 内置解析器始终可作为 fallback。 |
| 外部文档解析 API | 已实现 | `/parse` JSON adapter；失败时默认回退到内置解析器，可关闭 fallback。 |
| OCR worker | 已实现 adapter | `/ocr` JSON adapter，用于 image-only / low-text PDF；失败会记录为任务错误，不导致服务崩溃。 |
| 规则抽取 | 已实现 | 反应候选段落检测和结构化字段抽取。 |
| LLM JSON structuring | 已实现 adapter | OpenAI-compatible `/chat/completions`；严格 JSON；非法输出自动回退规则抽取并记录 metadata。 |
| Embedding / vector index | 已实现 adapter | OpenAI-compatible `/embeddings`；支持 rebuild、status 和 semantic search。 |
| Compound registry | 已实现 | 支持 CAS / SMILES / InChIKey 文本抽取、alias registry、reaction roles；RDKit 可选。 |
| Image structure recognition | 已实现 adapter | `/recognize` adapter 生成低置信结构候选；不会覆盖文本证据。 |
| 多用户授权 | 已实现 | 支持 `viewer`、`operator`、`admin`；可通过 `SCIFINDER_ROUTE_USERS` 或 config users 配置；旧单 token 映射为 admin。 |
| Evaluation metrics | 已实现 | 支持 JSONL gold set 评测和最近指标状态。 |
| Backup / retention | 已实现 | SQLite backup、存储用量统计、evidence/cache cleanup dry-run。 |
| Endpoint health checks | 已实现 | 支持 LLM、embedding、OCR、parser、structure recognition、Postgres。 |

## 外部 API 约定

所有外部服务都是可选的。如果未配置或调用失败，服务会返回 `degraded` / `skipped` / `error` 状态，而不是让整个 MCP 服务崩溃。

### Embedding endpoint

请求：`POST <endpoint>/embeddings`

```json
{"model":"bge-m3","input":["text"]}
```

期望响应可以是 OpenAI-like：

```json
{"data":[{"embedding":[0.1,0.2]}]}
```

### LLM endpoint

请求：`POST <endpoint>/chat/completions`

要求 OpenAI-compatible。模型输出必须是严格 JSON，只处理候选反应文本块，不对整篇文档自由发挥。非法 JSON 会自动回退到规则抽取结果。

### OCR endpoint

请求：`POST <endpoint>/ocr`

```json
{"model":"mineru-layout","file_path":"/data/uploads/file.pdf"}
```

期望响应：

```json
{"text":"OCR text", "confidence":0.85}
```

### Document parser endpoint

请求：`POST <endpoint>/parse`

```json
{"model":"parser-name","file_path":"/data/uploads/file.pdf"}
```

期望响应：

```json
{"file_type":"pdf","title":"...","doi":"10....","chunks":[{"text":"...","page_number":1,"parser_name":"external","parser_version":"1"}]}
```

### Structure recognition endpoint

请求：`POST <endpoint>/recognize`

```json
{"model":"decimer","image_path":"/data/evidence/page1.png"}
```

期望响应：

```json
{"structures":[{"smiles":"CCO","confidence":0.7}]}
```

结构识别结果会进入 compound registry。低置信结果只作为候选证据，不覆盖文本证据。

## Sidecar Watcher

Sidecar 用于在客户端机器上监听本地 SciFinder 导出目录，并把稳定文件上传到 NAS 上的 Admin API。它使用轮询机制，不依赖 `watchdog`，因此适合 Windows / macOS / Linux 客户端。

创建 `sidecar.yaml`：

```yaml
watch_dir: /path/to/scifinder/exports
server_url: http://nas-host:8001
token: change-me
include_patterns:
  - "*.pdf"
  - "*.html"
settle_seconds: 3
upload_mode: http
poll_seconds: 2
```

运行：

```bash
scifinder-route-sidecar sidecar.yaml
```

单次扫描：

```bash
scifinder-route-sidecar sidecar.yaml --once
```

上传接口：

```text
POST /api/upload
Header: X-Scifinder-Route-Token: <token>
Form field: file
```

服务端会写入 `/data/uploads`，计算 SHA-256，按 hash 去重，注册文档并进入解析队列。

## 授权

### 单 token 模式

```env
SCIFINDER_ROUTE_TOKEN=change-me
```

旧版单 token 会被视为 admin。

### 多用户 token 模式

```env
SCIFINDER_ROUTE_USERS=alice:viewer-token:viewer,bob:operator-token:operator,root:admin-token:admin
```

角色权限：

```text
viewer   search / read / status
operator scan / reparse / retry / vector / evaluation / integration tests
admin    config / backup / cleanup / secret operations
```

## 常用部署命令

查看容器：

```bash
docker compose -f docker-compose.image.yml ps
```

查看日志：

```bash
docker compose -f docker-compose.image.yml logs -f
```

重启：

```bash
docker compose -f docker-compose.image.yml restart
```

更新到最新预构建镜像：

```bash
docker compose -f docker-compose.image.yml pull
docker compose -f docker-compose.image.yml up -d
```

## 开发

运行测试：

```bash
python -m pytest -q
```

可选 Docker 检查：

```bash
docker compose build
docker compose -f docker-compose.image.yml config
```

## 相关文档

```text
docs/deployment.md      部署说明
docs/admin-webui.md     Admin Web UI 说明
docs/architecture.md    架构说明
docs/sidecar.md         Sidecar watcher 说明
```
