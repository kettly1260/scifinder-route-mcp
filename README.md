# scifinder-route-mcp

NAS-hosted MCP server for indexing and searching reaction-step-level synthesis routes from local SciFinder exports. It is designed to run long-term on Docker/NAS with a read-only inbox, durable SQLite queue fallback, optional external OCR/LLM/vector/parser/structure-recognition APIs, and an operational Admin Web UI for trusted LAN/VPN deployments.

> GHCR visibility note: if anonymous pull fails, open GitHub → Packages → `scifinder-route-mcp` → Package settings → Change visibility → Public. The compose file is already configured for `ghcr.io/kettly1260/scifinder-route-mcp:latest`.

## Quick Start With Prebuilt Image

The published Docker image targets both `linux/amd64` and `linux/arm64`.

```bash
git clone https://github.com/kettly1260/scifinder-route-mcp.git
cd scifinder-route-mcp
cp .env.example .env
mkdir -p nas-data nas-inbox
docker compose -f docker-compose.image.yml up -d
```

Then open:

```text
Admin Web UI: http://<nas-host>:8001/
MCP HTTP:     http://<nas-host>:8000/mcp
Legacy SSE:   http://<nas-host>:8000/sse
```

Put SciFinder exports into `nas-inbox`, then click **Scan Inbox** in the Admin Web UI or call the MCP `scan_inbox` tool. Supported import formats are `.pdf`, `.rtf`, `.rdf`, `.html`, `.htm`, `.mhtml`, `.mht`, `.md`, `.markdown`, and `.txt`. The image compose file uses `image:` only and does not build locally.

Do not expose the Admin Web UI directly to the public internet. Use a trusted LAN/VPN or a reverse proxy with TLS and authentication. The Python default Admin bind address is `127.0.0.1`; the Docker compose profiles explicitly bind `0.0.0.0` for NAS access.

## Local Build Deployment

```bash
docker compose up -d --build
```

Persistent paths:

```text
./nas-data  -> /data
./nas-inbox -> /inbox (read-only in the container)
./nas-data/uploads -> /data/uploads (HTTP upload and sidecar staging)
```

Parsing is asynchronous in the NAS profile. Jobs are stored durably in SQLite; after a container restart, interrupted `running` jobs are re-queued. Poll `get_parse_job_status` or `list_parse_jobs` until completion.

## Environment and Runtime Config

Copy `.env.example` to `.env`. Docker-level settings such as published ports, volumes, container network, and restart policy belong in `.env`/Compose only. The Admin Web UI never edits Docker files and never controls host Docker.

Hot application config is read from `/data/config.yaml`; copy `config.example.yaml` to `./nas-data/config.yaml` if desired. Hot-reloadable sections include:

```text
server.async_jobs, server.max_workers, server.storage_backend
queue.backend, queue.redis_url
security.allow_external_paths, security.token, security.users
ingest.scan_extensions, ingest.upload_extensions, ingest.upload_max_bytes,
ingest.reject_file_type_mismatch, ingest.extract_visual_evidence
integrations.*
extraction.llm_schema_version, extraction.llm_prompt_profile, extraction.llm_cost_limit_usd
thresholds.verification_confidence_threshold
retention.evidence_retention_days, retention.cache_retention_days
security.upload_av_scan_enabled, security.upload_av_engine,
security.upload_av_endpoint, security.upload_av_fail_closed
```

Use MCP tools `get_config`, `update_config`, `validate_config`, and `reload_config`, or use the Admin Web UI.

## MCP Transport

Docker deployments default to adaptive MCP transport mode:

```env
SCIFINDER_ROUTE_TRANSPORT=auto
SCIFINDER_ROUTE_MCP_PATH=/mcp
SCIFINDER_ROUTE_SSE_PATH=/sse
```

In `auto` mode, the same container and port expose both MCP endpoints:

```text
http://<nas-host>:8000/mcp  Streamable HTTP for modern MCP clients
http://<nas-host>:8000/sse  Legacy SSE for older MCP clients
```

`/mcp` handles MCP JSON-RPC requests such as `initialize`, `tools/list`, and `tools/call`; `GET /mcp` behavior is provided by FastMCP according to the MCP Streamable HTTP transport. `/sse` is retained for older clients that have not moved to Streamable HTTP.

For debugging or strict compatibility, force a single transport explicitly:

```env
SCIFINDER_ROUTE_TRANSPORT=http
SCIFINDER_ROUTE_MCP_PATH=/mcp
```

or:

```env
SCIFINDER_ROUTE_TRANSPORT=sse
SCIFINDER_ROUTE_SSE_PATH=/sse
```

## Admin Web UI

The Admin Web UI provides operational controls for:

```text
- health/status cards and mounted storage diagnostics
- token-protected config changes
- queue status, recent jobs, failed-job retry
- HTTP upload endpoint for sidecar/client upload
- LLM endpoint/model/enable toggle, schema version, prompt profile, cost limit
- embedding endpoint/model, vector rebuild, vector index status and errors
- OCR endpoint/model, OCR backlog status
- document parser endpoint/model, parser fallback and endpoint health
- structure recognition endpoint/model health
- PostgreSQL URL/backend status with SQLite fallback
- DOI low-confidence queue count
- evaluation latest metrics
- SQLite backup, retention dry-run cleanup, NAS storage usage
- compound registry count and search via MCP
```

Secret fields in the UI are not prefilled. Leaving token, Redis URL, or PostgreSQL URL blank preserves the current value; entering a value replaces it. Docker-owned settings such as published ports, volume mounts, and container networks remain in `.env`/Compose.

## MCP Tools

Implemented tools:

```text
health_check
get_config
update_config
validate_config
reload_config
scan_inbox
register_document
upload_document
upload_document_content
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
list_export_batches
get_export_batch
unlink_document_from_batch
```

## Feature Matrix

| Area | Status | Notes |
| --- | --- | --- |
| Docker/NAS adaptive MCP service | Implemented | Default `auto` mode exposes `/mcp` Streamable HTTP and `/sse` legacy SSE on the same port. |
| Single-transport override | Implemented | Set `SCIFINDER_ROUTE_TRANSPORT=http` or `sse` to expose only one transport. |
| GHCR multi-arch image workflow | Implemented | `linux/amd64`, `linux/arm64`. GHCR package visibility may need manual public setting. |
| Read-only inbox scanning | Implemented | `/inbox` mounted read-only. |
| HTTP upload staging | Implemented | `POST /api/upload` writes to `/data/uploads`; hash dedupe supported. |
| Sidecar watcher | Implemented | `scifinder-route-sidecar` polling CLI uploads stable files. |
| Durable queue | Implemented | SQLite queue is default; restart recovery and retry tools. Redis is optional/degraded via config status, not required. |
| SQLite storage | Implemented | Source documents, jobs, reaction steps, provenance, DOI verification, vector rows, compounds, metrics. |
| PostgreSQL backend | Runnable degraded integration | `SCIFINDER_ROUTE_BACKEND=postgres` tests connectivity and reports status; SQLite remains active fallback unless a Postgres adapter is added for a deployment. |
| pgvector | Optional/degraded | SQLite stores embeddings as JSON and cosine-searches them; Postgres/pgvector reports endpoint/backend status. |
| PDF/HTML/MHTML/text parsing | Implemented | Built-in parser remains fallback. |
| External document parser | Implemented | `/parse` JSON adapter; failure falls back unless disabled. |
| OCR worker | Implemented adapter | `/ocr` JSON adapter for image-only PDFs/low-text docs; errors are job errors, not service crashes. |
| Rule extraction | Implemented | Candidate blocks and structured fields. |
| LLM JSON structuring | Implemented adapter | OpenAI-compatible `/chat/completions`; strict JSON; invalid responses fall back to rule fields with metadata error. |
| Embedding/vector index | Implemented adapter | OpenAI-compatible `/embeddings`; rebuild/status/semantic search tools. |
| Compound registry | Implemented | CAS/SMILES/InChIKey text extraction, alias registry, reaction roles; RDKit optional. |
| Image structure recognition | Implemented adapter | `/recognize` adapter creates low-confidence image candidates; does not overwrite text evidence. |
| Multi-user authorization | Implemented | `viewer`, `operator`, `admin` roles via `SCIFINDER_ROUTE_USERS` or config users. Legacy single token maps to admin. |
| Evaluation metrics | Implemented | JSONL gold-set metrics and latest metric status. |
| Backup/retention | Implemented | SQLite backup, storage usage, evidence/cache cleanup dry-run. |
| Endpoint health checks | Implemented | LLM, embedding, OCR, parser, structure recognition, Postgres. |

## External API Schemas

All external services are optional. If a service is not configured or fails, the server returns a degraded/skipped/error status instead of crashing the process.

Embedding endpoint: `POST <endpoint>/embeddings`

```json
{"model":"bge-m3","input":["text"]}
```

Expected response can be OpenAI-like:

```json
{"data":[{"embedding":[0.1,0.2]}]}
```

LLM endpoint: `POST <endpoint>/chat/completions`, OpenAI-compatible. The assistant content must be strict JSON with reaction-step fields.

OCR endpoint: `POST <endpoint>/ocr`

```json
{"model":"mineru-layout","file_path":"/data/uploads/file.pdf"}
```

Expected response:

```json
{"text":"OCR text", "confidence":0.85}
```

Document parser endpoint: `POST <endpoint>/parse`

```json
{"model":"parser-name","file_path":"/data/uploads/file.pdf"}
```

Expected response:

```json
{"file_type":"pdf","title":"...","doi":"10....","chunks":[{"text":"...","page_number":1,"parser_name":"external","parser_version":"1"}]}
```

Structure recognition endpoint: `POST <endpoint>/recognize`

```json
{"model":"decimer","image_path":"/data/evidence/page1.png"}
```

Expected response:

```json
{"structures":[{"smiles":"CCO","confidence":0.7}]}
```

## Sidecar Watcher

Create `sidecar.yaml` on a client machine:

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

Run:

```bash
scifinder-route-sidecar sidecar.yaml
```

The sidecar polls by default and does not require `watchdog`, making it suitable for Windows/macOS/Linux clients.

## Authorization

Legacy single-token mode:

```env
SCIFINDER_ROUTE_TOKEN=change-me
```

Multi-user token mode:

```env
SCIFINDER_ROUTE_USERS=alice:viewer-token:viewer,bob:operator-token:operator,root:admin-token:admin
```

Roles:

```text
viewer   search/read/status
operator scan/reparse/retry/vector/evaluation/integration tests
admin    config/backup/cleanup/secret operations
```

## Development

```bash
python -m pytest -q
```

Optional Docker check:

```bash
docker compose build
docker compose -f docker-compose.image.yml config
```
