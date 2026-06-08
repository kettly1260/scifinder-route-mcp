# Deployment

## Prebuilt Image

Use the GHCR image for NAS and server deployments:

```text
ghcr.io/kettly1260/scifinder-route-mcp:latest
```

Supported platforms:

```text
linux/amd64
linux/arm64
```

If anonymous pull fails, the GitHub package is probably private. Fix manually:

```text
GitHub repository → Packages → scifinder-route-mcp → Package settings → Change visibility → Public
```

The workflow already publishes to GHCR using `packages: write` and multi-platform Buildx.

## Quick NAS Compose

```bash
git clone https://github.com/kettly1260/scifinder-route-mcp.git
cd scifinder-route-mcp
cp .env.example .env
mkdir -p nas-data nas-inbox
docker compose -f docker-compose.image.yml up -d
```

`docker-compose.image.yml` uses `image:` and does not contain `build:`.

## Published Ports

Change ports in `.env`, not in compose files:

```env
SCIFINDER_ROUTE_PUBLISHED_PORT=8000
SCIFINDER_ROUTE_ADMIN_PUBLISHED_PORT=8001
```

Recreate after port changes:

```bash
docker compose -f docker-compose.image.yml up -d
```

## Volumes

```text
./nas-data  -> /data
./nas-inbox -> /inbox:ro
```

The inbox is read-only inside the container. Client uploads and sidecar uploads land in `/data/uploads`.

## Queue and Restart Recovery

Default queue backend:

```env
SCIFINDER_ROUTE_QUEUE_BACKEND=sqlite
```

SQLite queue is production-capable for single-container NAS deployments. `parse_job` rows move through `queued`, `running`, `completed`, and `failed`. On startup, interrupted `running` jobs are requeued.

Redis can be declared for future external queue deployments:

```env
SCIFINDER_ROUTE_QUEUE_BACKEND=redis
SCIFINDER_ROUTE_REDIS_URL=redis://redis:6379/0
```

If Redis is unavailable or not configured, the app reports degraded status while SQLite remains the safe fallback.

## Storage Backend

Default:

```env
SCIFINDER_ROUTE_BACKEND=sqlite
SCIFINDER_ROUTE_DATABASE=/data/scifinder_routes.sqlite3
```

PostgreSQL can be configured for connectivity checks and future migration work:

```env
SCIFINDER_ROUTE_BACKEND=postgres
SCIFINDER_ROUTE_POSTGRES_URL=postgresql://user:pass@postgres:5432/scifinder
```

The current cross-platform image keeps SQLite active as a fallback if PostgreSQL or pgvector is unavailable. This avoids breaking NAS deployments when optional database services are offline.

## Backups and Retention

Use the Admin Web UI or MCP tools:

```text
backup_database
get_storage_usage
cleanup_evidence_cache
```

SQLite backup copies the database into `/data/backups`. Cleanup only removes generated evidence/cache files and never deletes source documents.

## Release Checklist

1. `python -m pytest -q`
2. `docker compose -f docker-compose.image.yml config`
3. If Docker is available: `docker compose build`
4. Push to `main`.
5. Confirm GitHub Actions `Build and publish Docker image` succeeds.
6. Confirm anonymous pull:
   ```bash
   docker pull ghcr.io/kettly1260/scifinder-route-mcp:latest
   ```
7. If pull fails with authorization, set package visibility to Public in GitHub package settings.
