# Sidecar Watcher

`scifinder-route-sidecar` watches a client-local folder and uploads stable files to the NAS Admin API. It is useful when SciFinder exports are created on a workstation that cannot mount the NAS inbox directly.

The sidecar uses polling and does not require `watchdog`, so it works on Windows, macOS, Linux, and lightweight NAS client environments.

## Install

From the project checkout:

```bash
python -m pip install -e .
```

## Config

Create `sidecar.yaml`:

```yaml
watch_dir: G:/SciFinderExports
server_url: http://nas-host:8001
token: change-me
include_patterns:
  - "*.pdf"
  - "*.html"
  - "*.mhtml"
settle_seconds: 3
upload_mode: http
poll_seconds: 2
```

JSON config is also supported.

## Run

```bash
scifinder-route-sidecar sidecar.yaml
```

One-shot scan:

```bash
scifinder-route-sidecar sidecar.yaml --once
```

## Upload Endpoint

The sidecar sends multipart uploads to:

```text
POST /api/upload
Header: X-Scifinder-Route-Token: <token>
Field: file
```

The server writes files to `/data/uploads`, computes SHA-256, deduplicates by hash, registers the document, and queues parsing.

## Behavior

A file is uploaded only after its size and modification timestamp remain stable for `settle_seconds`. This prevents partial upload of files still being written by a browser or SciFinder export workflow.
