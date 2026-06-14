from __future__ import annotations

import html
import json
import mimetypes
import os
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlparse

from .auth import authenticate_token, role_allows
from .service import RouteService
from .storage import is_sqlite_locked_error


@dataclass(frozen=True)
class AdminRunConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8001

    @classmethod
    def from_env(cls) -> "AdminRunConfig":
        return cls(
            enabled=os.getenv("SCIFINDER_ROUTE_ADMIN_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
            host=os.getenv("SCIFINDER_ROUTE_ADMIN_HOST", "127.0.0.1"),
            port=int(os.getenv("SCIFINDER_ROUTE_ADMIN_PORT", "8001")),
        )


class AdminServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], service: RouteService):
        super().__init__(server_address, AdminHandler)
        self.service = service


class AdminHandler(BaseHTTPRequestHandler):
    server: AdminServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/assets/"):
            if self._send_static_asset(parsed.path.lstrip("/")):
                return
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/state":
            try:
                self._require_role("viewer")
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return
            try:
                self._send_json(admin_state(self.server.service))
            except Exception as exc:
                if not self._send_lock_error(exc):
                    raise
            return
        if parsed.path == "/api/documents":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(
                    self.server.service.list_documents(
                        query=first_query(query, "q"),
                        file_type=first_query(query, "file_type"),
                        limit=int(first_query(query, "limit", "100")),
                        offset=int(first_query(query, "offset", "0")),
                    )
                )
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path.startswith("/api/documents/"):
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                suffix = "/chunks"
                if parsed.path.endswith(suffix):
                    document_id = parsed.path.removeprefix("/api/documents/").removesuffix(suffix)
                    self._send_json(
                        self.server.service.list_document_parsed_chunks(
                            document_id,
                            limit=int(first_query(query, "limit", "50")),
                            offset=int(first_query(query, "offset", "0")),
                        )
                    )
                else:
                    document_id = parsed.path.rsplit("/", 1)[-1]
                    self._send_json(
                        self.server.service.get_document_parse_result(
                            document_id,
                            chunk_limit=int(first_query(query, "chunk_limit", "50")),
                            chunk_offset=int(first_query(query, "chunk_offset", "0")),
                            reaction_limit=int(first_query(query, "reaction_limit", "100")),
                        )
                    )
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/rdf/reactions":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(self.server.service.list_rdf_reactions(document_id=first_query(query, "document_id"), query=first_query(query, "q"), limit=int(first_query(query, "limit", "50")), offset=int(first_query(query, "offset", "0"))))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/rdf/structures":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(self.server.service.list_rdf_structures(document_id=first_query(query, "document_id"), query=first_query(query, "q"), limit=int(first_query(query, "limit", "50")), offset=int(first_query(query, "offset", "0"))))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path.startswith("/api/rdf/structures/") and parsed.path.endswith("/image.svg"):
            try:
                self._require_role("viewer")
                structure_id = parsed.path.removeprefix("/api/rdf/structures/").removesuffix("/image.svg")
                self._send_svg(self.server.service.render_rdf_structure_svg(structure_id))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except KeyError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path.startswith("/api/rdf/reactions/"):
            try:
                self._require_role("viewer")
                reaction_id = parsed.path.rsplit("/", 1)[-1]
                self._send_json(self.server.service.get_rdf_reaction(reaction_id))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/chem/status":
            try:
                self._require_role("viewer")
                self._send_json(self.server.service.get_chem_status())
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/trash":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(self.server.service.list_trash(limit=int(first_query(query, "limit", "100"))))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/zotero/endpoints":
            try:
                self._require_role("viewer")
                self._send_json(self.server.service.list_zotero_mcp_endpoints())
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/literature/jobs":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(self.server.service.list_literature_link_jobs(status=first_query(query, "status"), limit=int(first_query(query, "limit", "50"))))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/literature/links":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(
                    self.server.service.list_literature_links(
                        status=first_query(query, "status"),
                        reaction_step_id=first_query(query, "reaction_step_id"),
                        document_id=first_query(query, "document_id"),
                        limit=int(first_query(query, "limit", "50")),
                    )
                )
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if not parsed.path.startswith("/api/"):
            if self._send_static_asset("index.html"):
                return
            try:
                self._send_html(render_dashboard(self.server.service))
            except Exception as exc:
                if not self._send_lock_error(exc):
                    raise
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/config":
                self._require_role("admin")
                payload = self._read_json()
                self._send_json(self.server.service.update_config(payload))
                return
            if parsed.path == "/api/scan":
                self._require_role("operator")
                self._send_json(self.server.service.scan_inbox())
                return
            if parsed.path == "/api/reload":
                self._require_role("admin")
                self._send_json(self.server.service.reload_config())
                return
            if parsed.path == "/api/upload":
                self._require_role("operator")
                filename, content = self._read_upload()
                self._send_json(self.server.service.upload_document_bytes(content, filename))
                return
            if parsed.path == "/api/retry-failed":
                self._require_role("operator")
                self._send_json(self.server.service.retry_failed_jobs())
                return
            if parsed.path == "/api/documents/reparse":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.reparse_document(str(payload.get("document_id") or "")))
                return
            if parsed.path == "/api/vector/rebuild":
                self._require_role("operator")
                self._send_json(self.server.service.rebuild_vector_index())
                return
            if parsed.path == "/api/backup":
                self._require_role("admin")
                self._send_json(self.server.service.backup_database())
                return
            if parsed.path == "/api/cleanup":
                self._require_role("admin")
                payload = self._read_json()
                self._send_json(self.server.service.cleanup_evidence_cache(dry_run=bool(payload.get("dry_run", True))))
                return
            if parsed.path == "/api/integration/test":
                self._require_role("operator")
                payload = self._read_json()
                overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else None
                self._send_json(self.server.service.test_integration_endpoint(str(payload.get("kind") or ""), overrides=overrides))
                return
            if parsed.path == "/api/integration/models":
                self._require_role("viewer")
                payload = self._read_json()
                overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else None
                self._send_json(self.server.service.list_integration_models(str(payload.get("kind") or ""), overrides=overrides))
                return
            if parsed.path == "/api/zotero/endpoints":
                self._require_role("admin")
                payload = self._read_json()
                self._send_json(self.server.service.upsert_zotero_mcp_endpoint(payload))
                return
            if parsed.path == "/api/zotero/endpoints/test":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.test_zotero_mcp_endpoint(str(payload.get("id") or "")))
                return
            if parsed.path == "/api/zotero/endpoints/delete":
                self._require_role("admin")
                payload = self._read_json()
                self._send_json(self.server.service.delete_zotero_mcp_endpoint(str(payload.get("id") or "")))
                return
            if parsed.path == "/api/literature/jobs/start":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.enqueue_literature_linking(str(payload.get("document_id") or "") or None))
                return
            if parsed.path == "/api/literature/links/confirm":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.confirm_literature_link(str(payload.get("id") or "")))
                return
            if parsed.path == "/api/literature/links/reject":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.reject_literature_link(str(payload.get("id") or ""), str(payload.get("reason") or "")))
                return
            if parsed.path == "/api/literature/links/write-note":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.write_zotero_link_note(str(payload.get("id") or "")))
                return
            if parsed.path == "/api/chem/install-rdkit":
                self._require_role("admin")
                self._send_json(self.server.service.install_rdkit_async())
                return
            if parsed.path == "/api/chem/similarity-search":
                self._require_role("viewer")
                payload = self._read_json()
                self._send_json(self.server.service.similarity_search_structures(str(payload.get("query") or ""), query_type=str(payload.get("query_type") or "smiles"), min_similarity=float(payload.get("min_similarity") or 0.2), limit=int(payload.get("limit") or 20)))
                return
            if parsed.path == "/api/chem/substructure-search":
                self._require_role("viewer")
                payload = self._read_json()
                self._send_json(self.server.service.substructure_search_structures(str(payload.get("query") or ""), query_type=str(payload.get("query_type") or "smarts"), limit=int(payload.get("limit") or 20)))
                return
            if parsed.path == "/api/trash/delete":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.trash_item(str(payload.get("entity_type") or ""), str(payload.get("entity_id") or "")))
                return
            if parsed.path == "/api/trash/restore":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.restore_trash_item(str(payload.get("entity_type") or ""), str(payload.get("entity_id") or "")))
                return
            if parsed.path == "/api/trash/empty":
                self._require_role("admin")
                self._send_json(self.server.service.empty_trash())
                return
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            return
        except Exception as exc:
            self._send_error_json(exc)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _require_role(self, role: str) -> None:
        config = self.server.service.config
        if not config.auth_token and not config.users:
            return
        token = self.headers.get("X-Scifinder-Route-Token") or ""
        user = authenticate_token(config.users, config.auth_token, token)
        if not user:
            raise PermissionError("Invalid or missing admin token")
        if not role_allows(user.role, role):
            raise PermissionError(f"Token role '{user.role}' cannot perform '{role}' operations")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _read_upload(self) -> tuple[str, bytes]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if "multipart/form-data" in content_type:
            return parse_multipart_upload(content_type, body)
        filename = safe_upload_name(self.headers.get("X-Filename") or "upload.bin")
        return filename, body

    def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, exc: Exception) -> None:
        if self._send_lock_error(exc):
            return
        self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _send_lock_error(self, exc: Exception) -> bool:
        if not is_sqlite_locked_error(exc):
            return False
        self._send_json({"error": "database is locked", "status": "degraded"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
        return True

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_svg(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_static_asset(self, relative_path: str) -> bool:
        if ".." in relative_path.replace("\\", "/").split("/"):
            return False
        try:
            root = files("scifinder_route_mcp.admin_webui")
            asset = root.joinpath(relative_path)
            if not asset.is_file():
                return False
            body = asset.read_bytes()
        except (FileNotFoundError, ModuleNotFoundError):
            return False
        content_type = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        if relative_path.endswith(".html"):
            content_type = "text/html; charset=utf-8"
        elif relative_path.endswith(".js"):
            content_type = "text/javascript; charset=utf-8"
        elif relative_path.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store" if relative_path.endswith(".html") else "public, max-age=31536000, immutable")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True


def start_admin_server(service: RouteService, config: AdminRunConfig | None = None) -> ThreadingHTTPServer | None:
    run_config = config or AdminRunConfig.from_env()
    if not run_config.enabled:
        return None
    server = AdminServer((run_config.host, run_config.port), service)
    thread = threading.Thread(target=server.serve_forever, name="route-admin-ui", daemon=True)
    thread.start()
    return server


def admin_state(service: RouteService) -> dict[str, Any]:
    try:
        jobs = service.list_parse_jobs(limit=20)
    except Exception as exc:
        if not is_sqlite_locked_error(exc):
            raise
        jobs = []
    return {
        "auth_required": bool(service.config.auth_token or service.config.users),
        "health": service.health_check(),
        "config": service.get_config(),
        "validation": service.validate_config(),
        "jobs": jobs,
        "production": service.get_production_status(),
    }


def render_dashboard(service: RouteService) -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>SciFinder Route MCP</title>
  <style>
    body { font-family: system-ui, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; background: #0f172a; color: #e2e8f0; margin: 0; }
    .card { background: #1e293b; padding: 2rem; border-radius: 12px; text-align: center; border: 1px solid #334155; max-width: 480px; }
    h1 { color: #f87171; margin-top: 0; }
    code { background: #0f172a; padding: 0.2rem 0.4rem; border-radius: 4px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>前端资源未构建</h1>
    <p>请先在 <code>webui</code> 目录下运行 <code>npm run build</code> 来构建静态资源，或者运行开发服务器。</p>
  </div>
</body>
</html>"""



def safe_upload_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in os.path.basename(value)) or "upload.bin"


def first_query(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return values[0] if values else default


def parse_multipart_upload(content_type: str, body: bytes) -> tuple[str, bytes]:
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("multipart upload is missing boundary")
    boundary = content_type.split(marker, 1)[1].split(";", 1)[0].strip().strip('"')
    delimiter = ("--" + boundary).encode("utf-8")
    for part in body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--" or b"\r\n\r\n" not in part:
            continue
        raw_headers, content = part.split(b"\r\n\r\n", 1)
        headers = raw_headers.decode("utf-8", errors="ignore")
        if 'name="file"' not in headers:
            continue
        filename = "upload.bin"
        if "filename=" in headers:
            filename = headers.split("filename=", 1)[1].split(";", 1)[0].split("\r\n", 1)[0].strip().strip('"')
        if content.endswith(b"\r\n--"):
            content = content[:-4]
        return safe_upload_name(filename), content.rstrip(b"\r\n")
    raise ValueError("multipart upload requires a file field")


# Legacy inline CSS and JS variables removed as modern React Web UI is used instead.

