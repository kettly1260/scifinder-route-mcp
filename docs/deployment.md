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

## Compose

Copy the example environment file and start the image compose file:

```bash
cp .env.example .env
mkdir -p nas-data nas-inbox
docker compose -f docker-compose.image.yml up -d
```

## Published Ports

Change ports in `.env`, not in `docker-compose.image.yml`:

```env
SCIFINDER_ROUTE_PUBLISHED_PORT=8000
SCIFINDER_ROUTE_ADMIN_PUBLISHED_PORT=8001
```

## Image Tags

The GitHub workflow publishes:

```text
latest              on main branch
sha-<commit>        on each pushed commit
<semver>            on vX.Y.Z tags
<major>.<minor>     on vX.Y.Z tags
```

## Build Locally

For local development builds:

```bash
docker compose up -d --build
```
