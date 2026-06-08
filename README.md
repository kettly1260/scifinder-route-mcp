# scifinder-route-mcp

NAS-hosted MCP server for indexing and searching reaction-step-level synthesis routes from local SciFinder exports.

## Quick Start With Prebuilt Image

The published Docker image supports:

```text
linux/amd64
linux/arm64
```

This covers common x86 NAS/server machines and ARM64 NAS/SBC machines.

Deploy with the prebuilt GHCR image:

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
MCP SSE:      http://<nas-host>:8000/sse
```

Put SciFinder exports into `nas-inbox`, then click `Scan Inbox` in the Admin Web UI or call the MCP `scan_inbox` tool.

## Local Build Deployment

Build and start on NAS:

```bash
docker compose up -d --build
```

Default SSE endpoint:

```text
http://<nas-host>:${SCIFINDER_ROUTE_PUBLISHED_PORT:-8000}/sse
```

Default Admin Web UI:

```text
http://<nas-host>:${SCIFINDER_ROUTE_ADMIN_PUBLISHED_PORT:-8001}/
```

Persistent paths:

```text
./nas-data  -> /data
./nas-inbox -> /inbox (read-only in the container)
./nas-data/uploads -> /data/uploads (sidecar/upload staging)
```

Place SciFinder export files in `./nas-inbox`, then call `scan_inbox`. You can also call `register_document` with the container-visible path, for example:

```text
/inbox/example.html
```

For clients that upload/copy files through the MCP tool, `upload_document` copies from a container-visible source path into `/data/uploads`, then registers the staged copy. The default Compose mount keeps `/inbox` read-only to protect source files.

In the NAS profile, parsing is asynchronous. `register_document`, `upload_document`, and `scan_inbox` return queued/running jobs; poll `get_parse_job_status` or `list_parse_jobs` until the job is complete.

If `SCIFINDER_ROUTE_TOKEN` is set, pass the same `token` argument to every MCP tool call.

## Environment

```text
SCIFINDER_ROUTE_TRANSPORT=sse
SCIFINDER_ROUTE_HOST=0.0.0.0
SCIFINDER_ROUTE_PORT=8000
SCIFINDER_ROUTE_PUBLISHED_PORT=8000
SCIFINDER_ROUTE_ADMIN_ENABLED=true
SCIFINDER_ROUTE_ADMIN_HOST=0.0.0.0
SCIFINDER_ROUTE_ADMIN_PORT=8001
SCIFINDER_ROUTE_ADMIN_PUBLISHED_PORT=8001
SCIFINDER_ROUTE_SSE_PATH=/sse
SCIFINDER_ROUTE_DATA_DIR=/data
SCIFINDER_ROUTE_INBOX_DIR=/inbox
SCIFINDER_ROUTE_UPLOAD_DIR=/data/uploads
SCIFINDER_ROUTE_EVIDENCE_DIR=/data/evidence
SCIFINDER_ROUTE_DATABASE=/data/scifinder_routes.sqlite3
SCIFINDER_ROUTE_CONFIG=/data/config.yaml
SCIFINDER_ROUTE_ASYNC_JOBS=true
SCIFINDER_ROUTE_MAX_WORKERS=1
SCIFINDER_ROUTE_ALLOW_EXTERNAL_PATHS=false
SCIFINDER_ROUTE_SCAN_EXTENSIONS=.pdf,.html,.htm,.mhtml,.mht,.txt
SCIFINDER_ROUTE_LLM_ENDPOINT=
SCIFINDER_ROUTE_LLM_MODEL=
SCIFINDER_ROUTE_EMBEDDING_ENDPOINT=
SCIFINDER_ROUTE_EMBEDDING_MODEL=
SCIFINDER_ROUTE_OCR_ENDPOINT=
SCIFINDER_ROUTE_OCR_MODEL=
SCIFINDER_ROUTE_DOCUMENT_PARSER_ENDPOINT=
SCIFINDER_ROUTE_DOCUMENT_PARSER_MODEL=
SCIFINDER_ROUTE_POSTGRES_URL=
SCIFINDER_ROUTE_VERIFICATION_CONFIDENCE_THRESHOLD=0.65
SCIFINDER_ROUTE_TOKEN=change-me
```

For normal deployments, copy `.env.example` to `.env` and edit `.env`. The `.env` file is ignored by git.

Use `SCIFINDER_ROUTE_TRANSPORT=stdio` for local CLI MCP clients.

## Runtime Config

The container reads hot application config from `/data/config.yaml`. This file is optional; environment variables provide defaults. Copy `config.example.yaml` to `./nas-data/config.yaml` before starting the NAS container if you want file-based config from day one.

Hot-reloadable sections:

```text
server.async_jobs
server.max_workers
security.allow_external_paths
security.token
ingest.scan_extensions
integrations.llm_endpoint
integrations.llm_model
integrations.embedding_endpoint
integrations.embedding_model
integrations.ocr_endpoint
integrations.ocr_model
integrations.document_parser_endpoint
integrations.document_parser_model
integrations.postgres_url
thresholds.verification_confidence_threshold
```

Use these MCP tools to manage hot config without editing Docker files:

```text
get_config
update_config
validate_config
reload_config
```

Example `update_config` payload:

```json
{
  "ingest": {
    "scan_extensions": [".pdf", ".html", ".mhtml"]
  },
  "integrations": {
    "llm_endpoint": "https://api.openai.com/v1",
    "llm_model": "gpt-4o-mini",
    "embedding_endpoint": "http://embedding:8000/v1",
    "embedding_model": "bge-m3",
    "ocr_endpoint": "http://mineru:9000"
  },
  "thresholds": {
    "verification_confidence_threshold": 0.75
  }
}
```

These settings are not hot-reloadable because Docker owns them outside the container:

```text
SCIFINDER_ROUTE_PUBLISHED_PORT
SCIFINDER_ROUTE_ADMIN_PUBLISHED_PORT
SCIFINDER_ROUTE_PORT
SCIFINDER_ROUTE_ADMIN_PORT
SCIFINDER_ROUTE_TRANSPORT
volume mounts
container network
restart policy
```

For port changes, edit `.env` only, not `docker-compose.yml`:

```env
SCIFINDER_ROUTE_PUBLISHED_PORT=8010
```

Then recreate the container:

```bash
docker compose up -d
```

## Admin Web UI

The Admin Web UI is a lightweight Material 3 Expressive-inspired glassmorphism control surface. It is intended for operational settings, not for editing Docker internals.

Currently included:

```text
- health/status cards
- token-protected config changes
- LLM endpoint and model settings
- embedding/vector API endpoint and model settings
- OCR endpoint and model settings
- document parser endpoint and model settings
- PostgreSQL URL placeholder setting
- scan extension policy
- async worker count and path safety toggle
- DOI verification confidence threshold
- scan_inbox trigger
- recent parse job table
- config warnings
```

Good future Web UI sections:

```text
- compound registry: CAS/SMILES/InChIKey aliases and merge review
- vector index: embedding model, rebuild button, recall diagnostics
- OCR pipeline: endpoint health, OCR confidence, image-only backlog
- document parser: parser selection, parser health, PDF/HTML/MHTML diagnostics
- LLM extraction: schema version, model cost limits, extraction prompt profile
- evaluation set: 40-file gold set export, field accuracy, regression metrics
- DOI verification: low-confidence queue and verified-field review
- backup/retention: SQLite/Postgres backup, evidence cache cleanup, NAS storage usage
```

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
get_parse_job_status
list_parse_jobs
search_reaction_steps
get_reaction_step
get_reaction_provenance
record_doi_verification
reparse_document
export_evaluation_set
```

## Current Feature Status

Implemented in this MVP:

```text
- Docker Compose NAS SSE service
- read-only NAS inbox scanning
- upload staging under /data/uploads
- async parse jobs for NAS deployment
- SQLite storage with source_document, parse_job, reaction_step, provenance, doi_verification
- PDF/HTML/MHTML/text parsing
- rule-based reaction candidate detection and field extraction
- FTS-backed text search plus reagent/solvent/document/confidence filters
- provenance and DOI verification recording
- evaluation JSONL export
- single-user token gate for MCP tools
- hot application config through /data/config.yaml and config MCP tools
- Docker published port controlled by .env
- Admin Web UI with Material 3 Expressive-inspired glassmorphism design
```

Not implemented yet:

```text
- PostgreSQL backend and pgvector indexes
- Redis or durable external task queue
- true sidecar binary/API for watching client folders
- MinerU/PaddleOCR OCR worker integration
- LLM JSON structuring adapter
- compound registry, CAS/SMILES/InChIKey normalization, RDKit fingerprints
- image structure recognition with MolScribe/DECIMER/OSRA
- multi-user authorization
```

Recommended next implementation order for a production NAS install:

```text
1. Add sidecar HTTP upload endpoint or separate CLI sidecar.
2. Add compound registry and text-level chemical identifier normalization.
3. Add LLM structuring adapter behind strict JSON schema.
4. Add OCR adapter interface and MinerU/PaddleOCR worker integration.
5. Add PostgreSQL backend once SQLite limits are reached.
```

## Development

```bash
python -m pytest -q
```
