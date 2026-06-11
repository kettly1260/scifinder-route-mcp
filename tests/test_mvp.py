from __future__ import annotations

from pathlib import Path
import base64
import json
import time

import pytest

from scifinder_route_mcp.admin import AdminRunConfig, admin_state, render_dashboard
from scifinder_route_mcp.config import AppConfig, merge_hot_config, read_config_yaml, write_config_yaml
from scifinder_route_mcp.server import ServerRunConfig, create_dual_transport_app, create_mcp, run_mcp_server
from scifinder_route_mcp.service import RouteService
from scifinder_route_mcp.parsers import parse_document
from scifinder_route_mcp.storage import RouteStorage
from scifinder_route_mcp.rdfile import parse_rdfile_reactions
from scifinder_route_mcp.literature import diff_reaction_fields, extract_method_fields


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


def test_mcp_content_upload_validates_and_parses_rtf(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.rtf"

    uploaded = service.upload_document_content("sample.rtf", base64.b64encode(fixture.read_bytes()).decode("ascii"))

    assert uploaded["document"]["file_type"] == "rtf"
    assert Path(uploaded["uploaded_path"]).parent == service.config.upload_dir
    hits = service.search_reaction_steps(query="hydrazine dimethylformamide", limit=5)
    assert hits


def test_upload_rejects_disguised_executable(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    with pytest.raises(ValueError, match="dangerous"):
        service.upload_document_content("not-a-pdf.pdf", base64.b64encode(b"MZ fake executable").decode("ascii"))


def test_parse_rdfile_extracts_structured_summary(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.rdf"

    parsed = parse_document(fixture)

    assert parsed.file_type == "rdf"
    assert "CAS Reaction Number: 31-614-CAS-40557461" in parsed.full_text
    assert "Experimental Procedure" in parsed.full_text


def test_rdfile_structure_parser_supports_v3000_and_v2000() -> None:
    v3000 = (Path(__file__).parent / "fixtures" / "sample_scifinder_export.rdf").read_text(encoding="utf-8")
    v2000 = (Path(__file__).parent / "fixtures" / "sample_scifinder_export_v2000.rdf").read_text(encoding="utf-8")

    first = parse_rdfile_reactions(v3000)[0]
    second = parse_rdfile_reactions(v2000)[0]

    assert first.scheme_id == "SCHEME1"
    assert first.molecules[0].molfile_version == "V3000"
    assert first.molecules[0].cas_rn == "19694-02-1"
    assert second.scheme_id == "SCHEME2"
    assert second.molecules[0].molfile_version == "V2000"
    assert second.molecules[1].role == "product"


def test_rdf_import_indexes_structures_and_trash(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.rdf"

    result = service.register_document(str(fixture))
    document_id = result["document"]["id"]
    reactions = service.list_rdf_reactions(document_id=document_id)
    structures = service.list_rdf_structures(document_id=document_id)

    assert reactions
    assert reactions[0]["scheme_id"] == "SCHEME1"
    assert len(structures) >= 2
    assert {item["molfile_version"] for item in structures if item["molfile"]} == {"V3000"}
    assert service.get_chem_status()["rdf_structure_index"]["total_structures"] >= 2

    trashed = service.trash_item("rdf_structure", structures[0]["id"])
    assert trashed["status"] == "trashed"
    assert all(item["id"] != structures[0]["id"] for item in service.list_rdf_structures(document_id=document_id))
    assert service.list_trash()
    service.restore_trash_item("rdf_structure", structures[0]["id"])
    assert any(item["id"] == structures[0]["id"] for item in service.list_rdf_structures(document_id=document_id))


def test_rdf_v2000_import_is_viewable_without_rdkit(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export_v2000.rdf"

    result = service.register_document(str(fixture))
    structures = service.list_rdf_structures(document_id=result["document"]["id"])

    assert [item["molfile_version"] for item in structures] == ["V2000", "V2000"]
    assert structures[0]["molfile"]


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
        assert {
            "register_document",
            "upload_document",
            "upload_document_content",
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
            "retry_parse_job",
            "retry_failed_jobs",
            "rebuild_vector_index",
            "get_vector_index_status",
            "semantic_search_reaction_steps",
            "search_compounds",
            "get_compound",
            "merge_compounds",
            "search_by_smiles",
            "recognize_structure_image",
            "get_chem_status",
            "list_rdf_reactions",
            "get_rdf_reaction",
            "search_rdf_structures",
            "similarity_search_structures",
            "substructure_search_structures",
            "trash_item",
            "restore_trash_item",
            "list_trash",
            "empty_trash",
            "compute_evaluation_metrics",
            "get_evaluation_status",
            "backup_database",
            "get_storage_usage",
            "cleanup_evidence_cache",
            "test_integration_endpoint",
            "list_zotero_mcp_endpoints",
            "upsert_zotero_mcp_endpoint",
            "delete_zotero_mcp_endpoint",
            "enqueue_literature_linking",
            "list_literature_links",
            "confirm_literature_link",
            "reject_literature_link",
            "get_reaction_literature_context",
            "write_zotero_link_note",
            "list_export_batches",
            "get_export_batch",
            "unlink_document_from_batch",
        }.issubset(set(mcp.tools))
        assert "docs://scifinder-import-guidance" in getattr(mcp, "resources", {})


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
        with pytest.raises(PermissionError):
            mcp.tools["update_config"]({}, token="bad")


def test_update_config_writes_and_reloads_hot_config(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    updated = service.update_config(
        {
            "server": {"storage_backend": "sqlite"},
            "queue": {"backend": "redis", "redis_url": "redis://queue:6379/0"},
            "ingest": {"scan_extensions": [".html"], "upload_extensions": [".html", ".rdf"], "upload_max_bytes": 4096},
            "integrations": {
                "embedding_endpoint": "http://embedding:8000/v1",
                "embedding_model": "bge-m3",
                "ocr_endpoint": "http://ocr-worker:9000",
                "document_parser_endpoint": "http://parser:9100",
                "document_parser_fallback": True,
                "structure_recognition_model": "decimer",
            },
            "extraction": {"llm_schema_version": "reaction_step.v2", "llm_prompt_profile": "strict", "llm_cost_limit_usd": 1.25},
            "thresholds": {"verification_confidence_threshold": 0.75},
            "retention": {"evidence_retention_days": 120, "cache_retention_days": 14},
        }
    )

    assert updated["server"]["storage_backend"] == "sqlite"
    assert updated["queue"]["backend"] == "redis"
    assert updated["queue"]["redis_url"] == "re***/0"
    assert updated["ingest"]["scan_extensions"] == [".html"]
    assert updated["ingest"]["upload_extensions"] == [".html", ".rdf"]
    assert updated["ingest"]["upload_max_bytes"] == 4096
    assert updated["integrations"]["embedding_model"] == "bge-m3"
    assert updated["integrations"]["ocr_endpoint"] == "http://ocr-worker:9000"
    assert updated["integrations"]["document_parser_endpoint"] == "http://parser:9100"
    assert updated["integrations"]["document_parser_fallback"] is True
    assert updated["integrations"]["structure_recognition_model"] == "decimer"
    assert updated["extraction"]["llm_schema_version"] == "reaction_step.v2"
    assert updated["extraction"]["llm_cost_limit_usd"] == 1.25
    assert updated["thresholds"]["verification_confidence_threshold"] == 0.75
    assert updated["retention"]["cache_retention_days"] == 14
    assert service.config.config_path.exists() is False
    webui_config = read_config_yaml(service.config.data_dir / "webui-config.yaml")
    assert webui_config["queue"]["backend"] == "redis"
    assert webui_config["queue"]["redis_url"] == "redis://queue:6379/0"

    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"
    html_target = service.config.inbox_dir / "sample.html"
    txt_target = service.config.inbox_dir / "sample.txt"
    html_target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    txt_target.write_text("Experimental procedure stirred for 2 h in THF, yield 50%.", encoding="utf-8")

    result = service.scan_inbox()

    assert result["registered_count"] == 1
    assert result["registered"][0]["document"]["file_path"].endswith("sample.html")


def test_auto_batch_links_similar_scifinder_exports(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    rdf = Path(__file__).parent / "fixtures" / "sample_scifinder_export.rdf"
    rtf = Path(__file__).parent / "fixtures" / "sample_scifinder_export.rtf"

    first = service.upload_document(str(rdf), filename="Reaction_20260612_0018.rdf")
    second = service.upload_document(str(rtf), filename="Reaction_20260612_0018.rtf")

    first_links = service.storage.list_batches_for_document(first["document"]["id"])
    second_links = service.storage.list_batches_for_document(second["document"]["id"])
    assert first_links
    assert second_links
    assert first_links[0]["id"] == second_links[0]["id"]
    batch = service.storage.get_export_batch(first_links[0]["id"])
    assert batch is not None
    assert len(batch["documents"]) == 2


def test_update_config_preserves_secret_when_section_omits_it(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    service.update_config({"security": {"token": "secret-token"}, "queue": {"redis_url": "redis://queue:6379/0"}})
    updated = service.update_config({"security": {"allow_external_paths": False}, "queue": {"backend": "redis"}})
    webui_config = read_config_yaml(service.config.data_dir / "webui-config.yaml")

    assert updated["security"]["token"] == "se***en"
    assert webui_config["security"]["token"] == "secret-token"
    assert webui_config["queue"]["redis_url"] == "redis://queue:6379/0"


def test_merge_hot_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError, match="Unsupported config keys"):
        merge_hot_config({"server": {}}, {"server": {"published_port": 8001}})


def test_validate_config_reports_invalid_threshold(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    result = service.update_config({"thresholds": {"verification_confidence_threshold": 1.5}})
    validation = service.validate_config()

    assert result["thresholds"]["verification_confidence_threshold"] == 1.5
    assert validation["valid"] is False
    assert validation["warnings"]


def test_webui_config_manages_zotero_endpoints_separately(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    endpoint = service.upsert_zotero_mcp_endpoint(
        {
            "alias": "lab-zotero",
            "group_name": "lab",
            "url": "http://zotero-host:23120/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "write_note_enabled": True,
        }
    )
    webui_config = read_config_yaml(service.config.data_dir / "webui-config.yaml")

    assert endpoint["alias"] == "lab-zotero"
    assert service.config.config_path.exists() is False
    assert webui_config["integrations"]["zotero_mcp_endpoints"][0]["alias"] == "lab-zotero"
    listed = service.list_zotero_mcp_endpoints()
    assert listed[0]["headers"]["Authorization"] == "****"
    assert service.get_config()["paths"]["webui_config_path"].endswith("webui-config.yaml")


def test_nested_webui_yaml_round_trips_zotero_endpoints(tmp_path: Path) -> None:
    path = tmp_path / "webui-config.yaml"
    payload = {
        "integrations": {
            "zotero_linking_enabled": True,
            "zotero_mcp_endpoints": [
                {
                    "id": "lan",
                    "alias": "lan",
                    "group_name": "main",
                    "url": "http://zotero-lan:23120/mcp",
                    "enabled": True,
                    "priority": 10,
                    "timeout_seconds": 5,
                    "write_note_enabled": False,
                    "headers": {"Authorization": "Bearer secret"},
                }
            ],
        }
    }

    write_config_yaml(path, payload)
    parsed = read_config_yaml(path)

    assert parsed["integrations"]["zotero_linking_enabled"] is True
    assert parsed["integrations"]["zotero_mcp_endpoints"][0]["group_name"] == "main"
    assert parsed["integrations"]["zotero_mcp_endpoints"][0]["headers"]["Authorization"] == "Bearer secret"


def test_literature_link_storage_and_field_diff(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"
    result = service.register_document(str(fixture))
    step = service.search_reaction_steps(query="triethylamine", limit=1)[0]

    fields = extract_method_fields("The product was purified by column chromatography and afforded in 82% yield in dichloromethane for 2 h.")
    diff = diff_reaction_fields(step, fields)
    link = service.storage.upsert_literature_link(
        {
            "reaction_step_id": step["id"],
            "source_document_id": result["document"]["id"],
            "endpoint_id": "zotero-main",
            "endpoint_alias": "main",
            "endpoint_group": "main",
            "zotero_item_key": "ITEM1",
            "doi": "10.1021/acs.joc.0c00001",
            "title": "Sample synthesis",
            "status": "auto_linked",
            "confidence": 0.95,
            "match_signals": {"doi_exact": True},
            "method_excerpt": "afforded in 82% yield",
            "extracted_fields": fields,
            "field_diff": diff,
        }
    )

    assert link["status"] == "auto_linked"
    assert link["match_signals"]["doi_exact"] is True
    context = service.get_reaction_literature_context(step["id"])
    assert context["links"][0]["zotero_item_key"] == "ITEM1"
    confirmed = service.confirm_literature_link(link["id"])
    assert confirmed["status"] == "confirmed"


def test_admin_dashboard_contains_modern_config_controls(tmp_path: Path) -> None:
    service = make_service(tmp_path)

    html = render_dashboard(service)
    state = admin_state(service)

    assert "Embedding endpoint" in html
    assert "OCR endpoint" in html
    assert "Document parser endpoint" in html
    assert "Queue backend" in html
    assert "data-type=\"enum\"" in html
    assert "LLM cost limit USD" in html
    assert "Parser fallback" in html
    assert "Zotero MCP" in html
    assert "webui-config.yaml" in html
    assert "Start Zotero Linking" in html
    assert "zotero_linking_enabled" in html
    assert "Unchanged when blank" in html
    assert "backdrop-filter" in html
    assert "@media (min-width: 1440px)" in html
    assert "@media (min-width: 700px) and (max-width: 1023px)" in html
    assert "@media (max-width: 699px)" in html
    assert "@media (hover: none) and (pointer: coarse)" in html
    assert state["production"]


def test_admin_run_config_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCIFINDER_ROUTE_ADMIN_HOST", raising=False)

    assert AdminRunConfig.from_env().host == "127.0.0.1"


def test_durable_queue_recovers_running_and_retries_failed(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"
    result = service.register_document(str(fixture))
    document_id = result["document"]["id"]
    running = service.storage.create_job(document_id, status="running", stage="document_parse")
    failed = service.storage.create_job(document_id, status="queued", stage="queued")
    service.storage.update_job(failed.id, status="failed", stage="failed", error="boom")

    recovered = service.storage.recover_interrupted_jobs()
    retried = service.retry_parse_job(failed.id)

    assert recovered == 1
    assert service.get_parse_job_status(running.id)["status"] == "queued"
    assert retried["status"] == "queued"


def test_upload_bytes_hash_dedupes(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    content = (Path(__file__).parent / "fixtures" / "sample_scifinder_export.html").read_bytes()

    first = service.upload_document_bytes(content, "sample.html")
    second = service.upload_document_bytes(content, "copy.html")

    assert Path(first["uploaded_path"]).exists()
    assert second["deduplicated"] is True


def test_upload_document_rejects_external_source_when_disabled(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        inbox_dir=tmp_path / "data" / "inbox",
        upload_dir=tmp_path / "data" / "uploads",
        evidence_dir=tmp_path / "data" / "evidence",
        database_path=tmp_path / "data" / "routes.sqlite3",
        config_path=tmp_path / "data" / "config.yaml",
        sample_dir=None,
        allow_external_paths=False,
    )
    service = RouteService(config=config, storage=RouteStorage(config.database_path))
    external = tmp_path / "external.html"
    external.write_text("Experimental procedure stirred for 2 h in THF, yield 50%.", encoding="utf-8")

    with pytest.raises(ValueError, match="outside allowed NAS roots"):
        service.upload_document(str(external))


def test_vector_index_without_endpoint_degrades(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"
    service.register_document(str(fixture))

    result = service.rebuild_vector_index()

    assert result["configured"] is False
    assert result["status"] == "skipped"


def test_compound_registry_extracts_cas_and_smiles(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    text_file = tmp_path / "data" / "inbox" / "compound.txt"
    text_file.parent.mkdir(parents=True, exist_ok=True)
    text_file.write_text("Experimental procedure: compound 64-17-5 and CCO were stirred in ethanol for 2 h to give 50% yield.", encoding="utf-8")

    service.register_document(str(text_file))
    compounds = service.search_compounds("64-17-5")

    assert compounds
    assert compounds[0]["cas"] == "64-17-5"


def test_backup_usage_cleanup_and_evaluation_metrics(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "sample_scifinder_export.html"
    service.register_document(str(fixture))
    backup = service.backup_database()
    cache_file = service.config.evidence_dir / "old.txt"
    cache_file.write_text("cache", encoding="utf-8")
    old_time = time.time() - 60 * 86400
    import os

    os.utime(cache_file, (old_time, old_time))
    cleanup = service.cleanup_evidence_cache(dry_run=True, max_age_days=1)
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps({"query": "triethylamine dichloromethane", "fields": {"yield_text": "82%"}}) + "\n", encoding="utf-8")
    metrics = service.compute_evaluation_metrics(str(gold))

    assert Path(backup["output_path"]).exists()
    assert service.get_storage_usage()["data_dir"]["exists"] is True
    assert cleanup["files"] >= 1
    assert metrics["metrics"]["records"] == 1


def test_multi_user_roles(tmp_path: Path) -> None:
    from scifinder_route_mcp.auth import UserCredential

    config = AppConfig(
        data_dir=tmp_path / "data",
        inbox_dir=tmp_path / "data" / "inbox",
        upload_dir=tmp_path / "data" / "uploads",
        evidence_dir=tmp_path / "data" / "evidence",
        database_path=tmp_path / "data" / "routes.sqlite3",
        config_path=tmp_path / "data" / "config.yaml",
        users=(UserCredential("view", "viewer-token", "viewer"), UserCredential("ops", "operator-token", "operator")),
    )
    service = RouteService(config=config, storage=RouteStorage(config.database_path))
    mcp = create_mcp(service)

    if hasattr(mcp, "tools"):
        assert mcp.tools["health_check"]("viewer-token")["status"] == "ok"
        with pytest.raises(PermissionError):
            mcp.tools["scan_inbox"](token="viewer-token")
        assert mcp.tools["scan_inbox"](token="operator-token")["registered_count"] == 0


def test_sidecar_reads_flat_yaml_and_detects_stable_file(tmp_path: Path) -> None:
    from scifinder_route_mcp.sidecar import PollingSidecar, SidecarConfig

    watch = tmp_path / "watch"
    watch.mkdir()
    config_file = tmp_path / "sidecar.yaml"
    config_file.write_text(
        "watch_dir: " + str(watch).replace("\\", "/") + "\nserver_url: http://localhost:8001\ntoken: token\ninclude_patterns:\n  - '*.html'\nsettle_seconds: 0\n",
        encoding="utf-8",
    )
    config = SidecarConfig.from_file(config_file)
    sidecar = PollingSidecar(config)
    sample = watch / "sample.html"
    sample.write_text("Experimental procedure stirred for 2 h in THF, yield 50%.", encoding="utf-8")

    assert config.watch_dir == watch.resolve()
    assert config.include_patterns == ("*.html",)
    assert sidecar._candidate_files() == [sample]


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


class FakeHttpAppServer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def http_app(self, **kwargs: object) -> object:
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        self.calls.append(kwargs)

        async def endpoint(_request: object) -> PlainTextResponse:
            return PlainTextResponse("ok")

        return Starlette(routes=[Route(str(kwargs["path"]), endpoint)])


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


def test_run_mcp_server_defaults_http_to_mcp_path() -> None:
    server = FakeRunnableServer()

    run_mcp_server(
        server,
        ServerRunConfig(transport="http", host="0.0.0.0", port=8123, log_level="DEBUG"),
    )

    assert server.calls == [
        {
            "transport": "http",
            "host": "0.0.0.0",
            "port": 8123,
            "path": "/mcp",
            "log_level": "DEBUG",
        }
    ]


def test_run_mcp_server_maps_streamable_http_alias_to_fastmcp_http() -> None:
    server = FakeRunnableServer()

    run_mcp_server(
        server,
        ServerRunConfig(transport="streamable-http", host="0.0.0.0", port=8123, log_level="DEBUG"),
    )

    assert server.calls == [
        {
            "transport": "http",
            "host": "0.0.0.0",
            "port": 8123,
            "path": "/mcp",
            "log_level": "DEBUG",
        }
    ]


def test_server_run_config_uses_mcp_path_for_http(monkeypatch: object) -> None:
    monkeypatch.setenv("SCIFINDER_ROUTE_TRANSPORT", "http")
    monkeypatch.setenv("SCIFINDER_ROUTE_MCP_PATH", "/mcp")
    monkeypatch.setenv("SCIFINDER_ROUTE_SSE_PATH", "/sse")

    config = ServerRunConfig.from_env()

    assert config.transport == "http"
    assert config.path == "/mcp"


def test_server_run_config_keeps_sse_path_for_legacy_sse(monkeypatch: object) -> None:
    monkeypatch.setenv("SCIFINDER_ROUTE_TRANSPORT", "sse")
    monkeypatch.delenv("SCIFINDER_ROUTE_MCP_PATH", raising=False)
    monkeypatch.setenv("SCIFINDER_ROUTE_SSE_PATH", "/sse")

    config = ServerRunConfig.from_env()

    assert config.transport == "sse"
    assert config.path == "/sse"


def test_create_dual_transport_app_mounts_mcp_and_sse() -> None:
    server = FakeHttpAppServer()

    app = create_dual_transport_app(
        server,
        ServerRunConfig(transport="auto", host="0.0.0.0", port=8123, mcp_path="/mcp", sse_path="/sse"),
    )

    assert server.calls == [
        {"path": "/mcp", "transport": "http"},
        {"path": "/sse", "transport": "sse"},
    ]
    assert sorted(route.path for route in app.routes) == ["/mcp", "/sse"]


def test_run_mcp_server_stdio_falls_back_to_legacy_run() -> None:
    server = LegacyRunnableServer()

    run_mcp_server(server, ServerRunConfig(transport="stdio"))

    assert server.calls == 1
