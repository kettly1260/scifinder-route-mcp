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
from typing import Any, Callable
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
        if parsed.path == "/api/status":
            try:
                self._require_role("viewer")
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return
            try:
                self._send_json(admin_status(self.server.service))
            except Exception as exc:
                if not self._send_lock_error(exc):
                    raise
            return
        if parsed.path == "/api/inbox/preview":
            try:
                self._require_role("operator")
                query = parse_qs(parsed.query)
                self._send_json(
                    self.server.service.preview_inbox(
                        reparse=first_bool(query, "reparse", False),
                        limit=first_int(query, "limit", 500, max_value=1000),
                    )
                )
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/reaction_links"):
            self._send_guarded_json("viewer", lambda: self._document_reaction_links_payload(parsed))
            return
        if parsed.path == "/api/documents":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(
                    self.server.service.list_documents(
                        query=first_query(query, "q"),
                        file_type=first_query(query, "file_type"),
                        limit=first_int(query, "limit", 100, max_value=500),
                        offset=first_int(query, "offset", 0, max_value=100000),
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
                            limit=first_int(query, "limit", 50, max_value=500),
                            offset=first_int(query, "offset", 0, max_value=100000),
                        )
                    )
                else:
                    document_id = parsed.path.rsplit("/", 1)[-1]
                    self._send_json(
                        self.server.service.get_document_parse_result(
                            document_id,
                            chunk_limit=first_int(query, "chunk_limit", 50, max_value=500),
                            chunk_offset=first_int(query, "chunk_offset", 0, max_value=100000),
                            reaction_limit=first_int(query, "reaction_limit", 100, max_value=500),
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
                self._send_json(self.server.service.list_rdf_reactions(document_id=first_query(query, "document_id"), query=first_query(query, "q"), limit=first_int(query, "limit", 50, max_value=500), offset=first_int(query, "offset", 0, max_value=100000)))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/rdf/structures":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(self.server.service.list_rdf_structures(document_id=first_query(query, "document_id"), query=first_query(query, "q"), limit=first_int(query, "limit", 50, max_value=500), offset=first_int(query, "offset", 0, max_value=100000)))
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
                self._send_json(self.server.service.list_trash(limit=first_int(query, "limit", 100, max_value=500)))
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
                self._send_json(self.server.service.list_literature_link_jobs(status=first_query(query, "status"), limit=first_int(query, "limit", 50, max_value=500)))
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
                        limit=first_int(query, "limit", 50, max_value=500),
                    )
                )
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/reaction_links":
            self._send_guarded_json("viewer", lambda: self._reaction_links_payload(parsed))
            return
        if parsed.path.startswith("/api/reaction_links/") and not parsed.path.endswith("/confirm") and not parsed.path.endswith("/unlink") and not parsed.path.endswith("/relink") and not parsed.path.endswith("/set_primary_page") and not parsed.path.endswith("/ai_review"):
            # This is a specific reaction link detail GET
            try:
                self._require_role("viewer")
                link_id = parsed.path.rsplit("/", 1)[-1]
                self._send_json(self.server.service.storage.get_reaction_source_link(link_id))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_error_json(exc)
            return
        if parsed.path == "/api/pdf_evidence":
            self._send_guarded_json("viewer", lambda: self._pdf_evidence_payload(parsed))
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
            if parsed.path == "/api/import/preview":
                self._require_role("operator")
                payload = self._read_json()
                if payload.get("inbox"):
                    self._send_json(
                        self.server.service.preview_inbox(
                            reparse=bool(payload.get("reparse", False)),
                            limit=payload_int(payload, "limit", 500, max_value=1000),
                        )
                    )
                else:
                    paths = payload.get("paths") if isinstance(payload.get("paths"), list) else []
                    self._send_json(
                        self.server.service.preview_document_paths(
                            [str(path) for path in paths],
                            reparse=bool(payload.get("reparse", False)),
                            limit=payload_int(payload, "limit", 500, max_value=1000),
                        )
                    )
                return
            if parsed.path == "/api/reload":
                self._require_role("admin")
                self._send_json(self.server.service.reload_config())
                return
            if parsed.path == "/api/upload/preview":
                self._require_role("operator")
                filename, content = self._read_upload()
                self._send_json(self.server.service.preview_upload_document_bytes(content, filename))
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
            if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/recognize_structure"):
                self._require_role("operator")
                document_id = parsed.path.removeprefix("/api/documents/").removesuffix("/recognize_structure")
                self._send_json(self.server.service.run_manual_structure_recognition(document_id))
                return
            if parsed.path == "/api/reaction_links/bulk":
                self._require_role("operator")
                payload = self._read_json()
                link_ids = payload.get("link_ids") if isinstance(payload.get("link_ids"), list) else []
                self._send_json(self.server.service.bulk_update_reaction_links([str(item) for item in link_ids], str(payload.get("action") or "")))
                return
            if parsed.path == "/api/reaction_links/backfill_review":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(
                    self.server.service.backfill_reaction_link_review(
                        dry_run=bool(payload.get("dry_run", True)),
                        limit=payload_int(payload, "limit", 10000, max_value=100000),
                    )
                )
                return
            if parsed.path.startswith("/api/reaction_links/") and parsed.path.endswith("/ai_review"):
                self._require_role("operator")
                link_id = parsed.path.removeprefix("/api/reaction_links/").removesuffix("/ai_review")
                self._send_json(self.server.service.analyze_reaction_link_with_ai(link_id))
                return
            if parsed.path.startswith("/api/reaction_links/") and parsed.path.endswith("/confirm"):
                self._require_role("operator")
                link_id = parsed.path.removeprefix("/api/reaction_links/").removesuffix("/confirm")
                self.server.service.storage.update_reaction_source_link(link_id, {"needs_review": 0})
                self._send_json({"status": "success", "id": link_id})
                return
            if parsed.path.startswith("/api/reaction_links/") and parsed.path.endswith("/unlink"):
                self._require_role("operator")
                link_id = parsed.path.removeprefix("/api/reaction_links/").removesuffix("/unlink")
                res = self.server.service.unlink_reaction_source_link(link_id)
                self._send_json(res)
                return
            if parsed.path.startswith("/api/reaction_links/") and parsed.path.endswith("/set_primary_page"):
                self._require_role("operator")
                link_id = parsed.path.removeprefix("/api/reaction_links/").removesuffix("/set_primary_page")
                payload = self._read_json()
                self.server.service.set_primary_page(link_id, payload.get("pdf_page"))
                self._send_json({"status": "success", "id": link_id})
                return
            if parsed.path.startswith("/api/documents/") and parsed.path.endswith("/force_link"):
                self._require_role("operator")
                document_id = parsed.path.removeprefix("/api/documents/").removesuffix("/force_link")
                payload = self._read_json()
                res = self.server.service.force_link_reaction(document_id, payload.get("rdf_reaction_id"), payload.get("pdf_page"))
                self._send_json(res)
                return
            if parsed.path == "/api/structure_evidence/verify":
                self._require_role("operator")
                payload = self._read_json()
                res = self.server.service.storage.update_structure_evidence_candidate(str(payload.get("candidate_id")), {"validation_status": "verified"})
                self._send_json(res)
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
                self._send_json(self.server.service.similarity_search_structures(str(payload.get("query") or ""), query_type=str(payload.get("query_type") or "smiles"), min_similarity=payload_float(payload, "min_similarity", 0.2, min_value=0.0, max_value=1.0), limit=payload_int(payload, "limit", 20, max_value=200)))
                return
            if parsed.path == "/api/chem/substructure-search":
                self._require_role("viewer")
                payload = self._read_json()
                self._send_json(self.server.service.substructure_search_structures(str(payload.get("query") or ""), query_type=str(payload.get("query_type") or "smarts"), limit=payload_int(payload, "limit", 20, max_value=200)))
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
            if parsed.path == "/api/provider/test":
                self._require_role("admin")
                payload = self._read_json()
                self._send_json(self.server.service.test_provider_endpoint(str(payload.get("id") or "")))
                return
            if parsed.path == "/api/provider/models":
                self._require_role("admin")
                payload = self._read_json()
                self._send_json(self.server.service.list_provider_models(str(payload.get("id") or "")))
                return
            if parsed.path == "/api/provider/models/update":
                self._require_role("admin")
                payload = self._read_json()
                self._send_json(self.server.service.update_provider_models(
                    str(payload.get("id") or ""),
                    payload.get("available_models") or [],
                    payload.get("enabled_models") or []
                ))
                return
            if parsed.path == "/api/provider/models/enabled":
                self._require_role("viewer")
                payload = self._read_json()
                self._send_json(self.server.service.get_provider_enabled_models(str(payload.get("id") or "")))
                return
        except PermissionError as exc:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 0:
                    self.rfile.read(length)
            except Exception:
                pass
            self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            return
        except Exception as exc:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 0:
                    self.rfile.read(length)
            except Exception:
                pass
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

    def _send_guarded_json(self, role: str, producer: Callable[[], Any]) -> None:
        try:
            self._require_role(role)
            self._send_json(producer())
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
        except Exception as exc:
            self._send_error_json(exc)

    def _document_reaction_links_payload(self, parsed: Any) -> list[dict[str, Any]]:
        query = parse_qs(parsed.query)
        document_id = parsed.path.removeprefix("/api/documents/").removesuffix("/reaction_links")
        return self.server.service.list_reaction_links(
            document_id=document_id,
            needs_review=first_bool(query, "needs_review", None),
            limit=first_int(query, "limit", 100, max_value=500),
        )["items"]

    def _reaction_links_payload(self, parsed: Any) -> dict[str, Any]:
        query = parse_qs(parsed.query)
        return self.server.service.list_reaction_links(
            document_id=first_query(query, "document_id"),
            source_mode=first_query(query, "source_mode"),
            needs_review=first_bool(query, "needs_review", None),
            evidence_kind=first_query(query, "evidence_kind"),
            has_conflicts=first_bool(query, "has_conflicts", None),
            cas_reaction_number=first_query(query, "cas_reaction_number"),
            limit=first_int(query, "limit", 100, max_value=500),
            offset=first_int(query, "offset", 0, max_value=100000),
        )

    def _pdf_evidence_payload(self, parsed: Any) -> list[dict[str, Any]]:
        query = parse_qs(parsed.query)
        return self.server.service.storage.list_pdf_reaction_evidence(
            document_id=first_query(query, "document_id"),
            cas_reaction_number=first_query(query, "cas_reaction_number"),
            reaction_source_link_id=first_query(query, "reaction_source_link_id"),
            limit=first_int(query, "limit", 100, max_value=500),
            offset=first_int(query, "offset", 0, max_value=100000),
        )

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
    return {
        **admin_status(service),
        "config": service.get_config(),
    }


def admin_status(service: RouteService) -> dict[str, Any]:
    try:
        jobs = service.list_parse_jobs(limit=20)
    except Exception as exc:
        if not is_sqlite_locked_error(exc):
            raise
        jobs = []
    return {
        "auth_required": bool(service.config.auth_token or service.config.users),
        "health": service.health_check(),
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


def parse_optional_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None or value == "":
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def first_bool(query: dict[str, list[str]], key: str, default: bool | None = None) -> bool | None:
    return parse_optional_bool(first_query(query, key, ""), default)


def bounded_int(value: Any, default: int, *, min_value: int = 0, max_value: int = 500) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def first_int(query: dict[str, list[str]], key: str, default: int, *, min_value: int = 0, max_value: int = 500) -> int:
    return bounded_int(first_query(query, key, str(default)), default, min_value=min_value, max_value=max_value)


def payload_int(payload: dict[str, Any], key: str, default: int, *, min_value: int = 0, max_value: int = 500) -> int:
    return bounded_int(payload.get(key, default), default, min_value=min_value, max_value=max_value)


def payload_float(payload: dict[str, Any], key: str, default: float, *, min_value: float = 0.0, max_value: float = 1.0) -> float:
    try:
        parsed = float(str(payload.get(key, default)).strip())
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


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

