from __future__ import annotations

from pathlib import Path
import time

import pytest

from scifinder_route_mcp.admin import admin_state, render_dashboard
from scifinder_route_mcp.config import AppConfig
from scifinder_route_mcp.server import ServerRunConfig, create_mcp, run_mcp_server
from scifinder_route_mcp.service import RouteService
from scifinder_route_mcp.storage import RouteStorage


def make_service(tmp_path: Path) -> RouteService:
    config = AppConfig(
        data_dir=tmp_path / "data",
        inbox_dir=tmp_path / "data" / "inbox",
        upload_dir=tmp_path / "data" / "uploads",
        evidence_dir=tmp_path / "data" / "evidence",
        database_path=tmp_path / "data" / "routes.sqlite3",
        config_path=tmp_path / "data" / "config.yaml",
        sample_dir=None,
    )
    config.ensure_directories()
    return RouteService(config=config, storage=RouteStorage(config.database_path))


def test_register_search_provenance_and_export(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"

    result = service.register_document(str(fixture))
    assert result["document"]["doi"] == "10.1021/acs.joc.0c00001"
    assert result["job"]["status"] == "completed"

    hits = service.search_reaction_steps(query="triethylamine dichloromethane", limit=5)
    assert len(hits) >= 1
    first = hits[0]
    assert first["yield_text"] == "82%"
    assert "dichloromethane" in first["solvent_text"]
    assert first["needs_ocr"] is False

    detail = service.get_reaction_step(first["id"])
    assert detail["id"] == first["id"]
    provenance = service.get_reaction_provenance(first["id"])
    assert provenance[0]["text_span"]
    assert provenance[0]["parser_name"] == "html"

    verification = service.record_doi_verification(
        reaction_step_id=first["id"],
        doi="10.1021/acs.joc.0c00001",
        verified_fields={"yield_text": "82%"},
        verification_confidence=0.9,
        verifier_agent="test",
    )
    assert verification["verified_fields"]["yield_text"] == "82%"

    export = service.export_evaluation_set(limit=10)
    assert export["rows"] >= 1
    assert Path(export["output_path"]).exists()


def test_upload_and_reparse(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"

    uploaded = service.upload_document(str(fixture))
    assert Path(uploaded["uploaded_path"]).exists()
    assert Path(uploaded["uploaded_path"]).parent == service.config.upload_dir
    document_id = uploaded["document"]["id"]

    reparsed = service.reparse_document(document_id)
    assert reparsed["job"]["status"] == "completed"
    hits = service.search_reaction_steps(document_id=document_id, limit=10)
    assert len(hits) >= 1


def test_scan_inbox_deduplicates_registered_files(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"
    target = service.config.inbox_dir / "sample.html"
    target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    first = service.scan_inbox()
    second = service.scan_inbox()

    assert first["registered_count"] == 1
    assert second["registered_count"] == 0
    assert second["skipped"][0]["reason"] == "already_registered"


def test_health_check_reports_counts(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"

    service.register_document(str(fixture))
    health = service.health_check()

    assert health["status"] == "ok"
    assert health["documents"] == 1
    assert health["reaction_steps"] >= 1


def test_async_register_returns_queued_job_then_completes(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        inbox_dir=tmp_path / "data" / "inbox",
        upload_dir=tmp_path / "data" / "uploads",
        evidence_dir=tmp_path / "data" / "evidence",
        database_path=tmp_path / "data" / "routes.sqlite3",
        config_path=tmp_path / "data" / "config.yaml",
        sample_dir=None,
        async_jobs=True,
    )
    service = RouteService(config=config, storage=RouteStorage(config.database_path))
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"

    result = service.register_document(str(fixture))
    job_id = result["job"]["id"]
    deadline = time.time() + 5
    status = result["job"]
    while time.time() < deadline:
        status = service.get_parse_job_status(job_id)
        if status["status"] == "completed":
            break
        time.sleep(0.05)
    service.shutdown()

    assert status["status"] == "completed"
    assert service.search_reaction_steps(query="triethylamine", limit=5)


def test_mcp_fallback_registers_core_tools(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    mcp = create_mcp(service)
    if hasattr(mcp, "tools"):
        assert set(mcp.tools) == {
            "register_document",
            "upload_document",
            "scan_inbox",
            "get_parse_job_status",
            "list_parse_jobs",
            "health_check",
            "get_config",
            "update_config",
            "validate_config",
            "reload_config",
            "search_reaction_steps",
            "get_reaction_step",
            "get_reaction_provenance",
            "reparse_document",
            "record_doi_verification",
            "export_evaluation_set",
        }


def test_mcp_token_guard_blocks_calls(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        inbox_dir=tmp_path / "data" / "inbox",
        upload_dir=tmp_path / "data" / "uploads",
        evidence_dir=tmp_path / "data" / "evidence",
        database_path=tmp_path / "data" / "routes.sqlite3",
        config_path=tmp_path / "data" / "config.yaml",
        sample_dir=None,
        auth_token="secret",
    )
    service = RouteService(config=config, storage=RouteStorage(config.database_path))
    mcp = create_mcp(service)

    if hasattr(mcp, "tools"):
        with pytest.raises(PermissionError):
            mcp.tools["health_check"]()
        assert mcp.tools["health_check"]("secret")["status"] == "ok"


def test_update_config_writes_and_reloads_hot_config(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    updated = service.update_config(
        {
            "ingest": {"scan_extensions": [".html"]},
            "integrations": {
                "embedding_endpoint": "http://embedding:8000/v1",
                "embedding_model": "bge-m3",
                "ocr_endpoint": "http://ocr-worker:9000",
                "document_parser_endpoint": "http://parser:9100",
            },
            "thresholds": {"verification_confidence_threshold": 0.75},
        }
    )

    assert updated["ingest"]["scan_extensions"] == [".html"]
    assert updated["integrations"]["embedding_model"] == "bge-m3"
    assert updated["integrations"]["ocr_endpoint"] == "http://ocr-worker:9000"
    assert updated["integrations"]["document_parser_endpoint"] == "http://parser:9100"
    assert updated["thresholds"]["verification_confidence_threshold"] == 0.75
    assert service.config.config_path.exists()

    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"
    html_target = service.config.inbox_dir / "sample.html"
    txt_target = service.config.inbox_dir / "sample.txt"
    html_target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    txt_target.write_text("Experimental procedure stirred for 2 h in THF, yield 50%.", encoding="utf-8")

    result = service.scan_inbox()

    assert result["registered_count"] == 1
    assert result["registered"][0]["document"]["file_path"].endswith("sample.html")


def test_validate_config_reports_invalid_threshold(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    result = service.update_config({"thresholds": {"verification_confidence_threshold": 1.5}})
    validation = service.validate_config()

    assert result["thresholds"]["verification_confidence_threshold"] == 1.5
    assert validation["valid"] is False
    assert validation["warnings"]


def test_admin_dashboard_contains_modern_config_controls(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    html = render_dashboard(service)
    state = admin_state(service)

    assert "Embedding endpoint" in html
    assert "OCR endpoint" in html
    assert "Document parser endpoint" in html
    assert "backdrop-filter" in html
    assert "@media (min-width: 1440px)" in html
    assert "@media (min-width: 700px) and (max-width: 1023px)" in html
    assert "@media (max-width: 699px)" in html
    assert "@media (hover: none) and (pointer: coarse)" in html
    assert state["future_webui_sections"]


class FakeRunnableServer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class LegacyRunnableServer:
    def __init__(self) -> None:
        self.calls = 0

    def run(self) -> None:
        self.calls += 1


def test_run_mcp_server_passes_sse_configuration() -> None:
    server = FakeRunnableServer()

    run_mcp_server(
        server,
        ServerRunConfig(transport="sse", host="0.0.0.0", port=8123, path="/sse", log_level="DEBUG"),
    )

    assert server.calls == [
        {
            "transport": "sse",
            "host": "0.0.0.0",
            "port": 8123,
            "path": "/sse",
            "log_level": "DEBUG",
        }
    ]


def test_run_mcp_server_stdio_falls_back_to_legacy_run() -> None:
    server = LegacyRunnableServer()

    run_mcp_server(server, ServerRunConfig(transport="stdio"))

    assert server.calls == 1
