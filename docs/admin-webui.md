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

## Frontend Build

The Admin Web UI source lives in `webui/` and is built with npm, Vite, React, and TypeScript. The production build writes static assets into `src/scifinder_route_mcp/admin_webui/`, which the Python Admin server serves from `/` and `/assets/*`.

Development commands:

```text
cd webui
npm ci
npm run build
```

The Docker image uses a Node build stage for the Web UI and keeps the final runtime image Python-only. If built assets are missing in a local checkout, the Admin server falls back to the legacy inline dashboard so the service can still start.

## Pages and Panels

The task-oriented UI currently includes:

```text
- health metrics
- secure token entry
- integration config form
- runtime/queue/storage config form
- hot extraction and retention config form
- vector index rebuild/status
- endpoint health buttons
- Zotero MCP endpoint aliases/groups
- Zotero literature linking jobs and candidates
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
zotero_mcp
```

HTTP integrations use `GET <endpoint>/health` for lightweight checks. Postgres uses a direct `SELECT 1` when `psycopg` is installed and a URL is configured. Zotero MCP endpoints are tested through their configured Streamable HTTP MCP URL and the latest status is stored in SQLite for diagnostics.

## Config Editing Notes

The UI updates hot application config in `SCIFINDER_ROUTE_WEBUI_CONFIG`, which defaults to `/data/webui-config.yaml` in Docker-style deployments. This file is intentionally separate from `SCIFINDER_ROUTE_CONFIG` (`/data/config.yaml`) so a bad Web UI edit does not prevent the container from starting with the base configuration.

Startup order is:

```text
environment variables -> SCIFINDER_ROUTE_CONFIG -> SCIFINDER_ROUTE_WEBUI_CONFIG
```

If the Web UI config cannot be parsed, the service keeps the base config and reports warnings instead of failing startup.

The UI does not edit Docker-owned settings such as published ports, volume mounts, container networks, or restart policy.

Secret fields such as token, Redis URL, and PostgreSQL URL are not prefilled. Leaving them blank preserves the existing value; entering a value replaces it.

The supported editable hot config keys are validated server-side. Unknown sections or keys are rejected instead of being silently written.

## Zotero MCP Linking

The Zotero MCP panel edits `integrations.zotero_mcp_endpoints` in the separate Web UI config. Each endpoint has an `alias` and `group_name`.

Use the same `group_name` for multiple network routes to the same Zotero MCP server, such as a LAN address and a VPN address. The service picks the lowest-priority enabled endpoint in each group. Use different groups for different Zotero libraries; groups can be queried independently and merged as literature candidates.

The project does not create or secure the network path to Zotero MCP. Users may use LAN, VPN, reverse proxy, or other routing. This service only stores endpoint URLs/headers, tests reachability, and reports errors.

Literature links are stored at reaction-step level. DOI-exact matches with compatible metadata can be marked `auto_linked`; lower-confidence hits remain `candidate` until confirmed in the UI. Zotero full text is not cached. The database stores metadata, abstracts, short method/SI excerpts, extracted method fields, and field-level differences against the SciFinder reaction step.

Optional Zotero writeback is limited to creating a linked-route note when `write_note_enabled` is true for that endpoint. The service does not update Zotero item metadata or tags.

## Non-goals

The UI does not:

```text
- edit docker-compose.yml
- mount or access Docker socket
- start/stop host containers
- change host volume mounts or published ports
```

Change Docker-owned settings in `.env` and recreate the container.
