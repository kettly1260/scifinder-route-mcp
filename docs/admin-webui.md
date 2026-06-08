# Admin Web UI

The Admin Web UI is served on `SCIFINDER_ROUTE_ADMIN_PORT` inside the container and published through `SCIFINDER_ROUTE_ADMIN_PUBLISHED_PORT`.

Default URL:

```text
http://<nas-host>:8001/
```

## Security

If `SCIFINDER_ROUTE_TOKEN` or `SCIFINDER_ROUTE_USERS` is configured, mutating UI actions require the token in the password field. The UI sends it as `X-Scifinder-Route-Token`.

Roles:

```text
viewer   read/search/status
operator scan/retry/reparse/vector/evaluation/integration checks
admin    config/backup/cleanup/secrets
```

## Pages and Panels

The single-page UI currently includes:

```text
- health metrics
- secure token entry
- integration config form
- runtime/queue/storage config form
- vector index rebuild/status
- endpoint health buttons
- OCR backlog and DOI low-confidence queue
- evaluation latest metrics
- backup and retention controls
- storage usage table
- compound registry count
- production diagnostics snapshot
- recent parse jobs
- config warnings
```

## Endpoint Health

The UI can test:

```text
llm
embedding
ocr
document_parser
structure_recognition
postgres
```

HTTP integrations use `GET <endpoint>/health` for lightweight checks. Postgres uses a direct `SELECT 1` when `psycopg` is installed and a URL is configured.

## Non-goals

The UI does not:

```text
- edit docker-compose.yml
- mount or access Docker socket
- start/stop host containers
- change host volume mounts or published ports
```

Change Docker-owned settings in `.env` and recreate the container.
