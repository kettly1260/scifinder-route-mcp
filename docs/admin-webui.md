# Admin Web UI

The Admin Web UI is served on `SCIFINDER_ROUTE_ADMIN_PORT` inside the container and published through `SCIFINDER_ROUTE_ADMIN_PUBLISHED_PORT` when Docker binds `SCIFINDER_ROUTE_ADMIN_HOST=0.0.0.0`.

Default URL:

```text
http://<nas-host>:8001/
```

The Python default bind address is `127.0.0.1`. The Docker compose profiles set `SCIFINDER_ROUTE_ADMIN_HOST=0.0.0.0` so the UI can be reached from the NAS network.

## Security

If `SCIFINDER_ROUTE_TOKEN` or `SCIFINDER_ROUTE_USERS` is configured, mutating UI actions require the token in the password field. The UI sends it as `X-Scifinder-Route-Token`.

For NAS deployments, keep the UI on a trusted LAN/VPN or behind a reverse proxy that provides TLS and authentication. Do not expose it directly to the public internet. If no token or users are configured, the server treats the deployment as local/trusted and does not require a token.

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
- hot extraction and retention config form
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

## Config Editing Notes

The UI updates only hot application config in `config.yaml`. It does not edit Docker-owned settings such as published ports, volume mounts, container networks, or restart policy.

Secret fields such as token, Redis URL, and PostgreSQL URL are not prefilled. Leaving them blank preserves the existing value; entering a value replaces it.

The supported editable hot config keys are validated server-side. Unknown sections or keys are rejected instead of being silently written.

## Non-goals

The UI does not:

```text
- edit docker-compose.yml
- mount or access Docker socket
- start/stop host containers
- change host volume mounts or published ports
```

Change Docker-owned settings in `.env` and recreate the container.
