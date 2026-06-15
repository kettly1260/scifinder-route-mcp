FROM node:24-slim AS webui-build

WORKDIR /app/webui
COPY webui/package*.json ./
RUN npm ci
COPY webui ./
COPY src/scifinder_route_mcp/admin_webui ../src/scifinder_route_mcp/admin_webui
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SCIFINDER_ROUTE_TRANSPORT=auto \
    SCIFINDER_ROUTE_HOST=0.0.0.0 \
    SCIFINDER_ROUTE_PORT=8000 \
    SCIFINDER_ROUTE_ADMIN_ENABLED=true \
    SCIFINDER_ROUTE_ADMIN_HOST=0.0.0.0 \
    SCIFINDER_ROUTE_ADMIN_PORT=8001 \
    SCIFINDER_ROUTE_MCP_PATH=/mcp \
    SCIFINDER_ROUTE_SSE_PATH=/sse \
    SCIFINDER_ROUTE_DATA_DIR=/data \
    SCIFINDER_ROUTE_INBOX_DIR=/inbox \
    SCIFINDER_ROUTE_UPLOAD_DIR=/data/uploads \
    SCIFINDER_ROUTE_EVIDENCE_DIR=/data/evidence \
    SCIFINDER_ROUTE_DATABASE=/data/scifinder_routes.sqlite3 \
    SCIFINDER_ROUTE_CONFIG=/data/config.yaml \
    SCIFINDER_ROUTE_ASYNC_JOBS=true \
    SCIFINDER_ROUTE_ALLOW_EXTERNAL_PATHS=false

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=webui-build /app/src/scifinder_route_mcp/admin_webui ./src/scifinder_route_mcp/admin_webui

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends \
       libxrender1 libxext6 libexpat1 \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip \
    && python -m pip install ".[postgres]"

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data /data/uploads /data/evidence /inbox \
    && chown -R appuser:appuser /data /inbox /app

USER appuser

EXPOSE 8000 8001

CMD ["scifinder-route-mcp"]
