from __future__ import annotations

from pathlib import Path

import pytest

from scifinder_route_mcp.config import AppConfig
from scifinder_route_mcp.service import RouteService
from scifinder_route_mcp.storage import RouteStorage


CAS_RN = "31-123-CAS-1"


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


def seed_rdf_pdf_links(
    service: RouteService,
    *,
    rdf_yield: str = "95",
    pdf_yield: str = "95%",
    shared_batch: bool = True,
    cas_reaction_number: str = CAS_RN,
    rdf_file_path: str = "example.rdf",
    pdf_file_path: str = "example.pdf",
    rdf_hash: str = "rdf-hash",
    pdf_hash: str = "pdf-hash",
) -> tuple[str, str]:
    storage = service.storage
    rdf_doc = storage.upsert_document(
        file_path=rdf_file_path,
        file_hash=rdf_hash,
        file_type="rdf",
        title="Example RDF",
        doi=None,
        ingest_status="parsed",
    )
    pdf_doc = storage.upsert_document(
        file_path=pdf_file_path,
        file_hash=pdf_hash,
        file_type="pdf",
        title="Example PDF",
        doi=None,
        ingest_status="parsed",
    )
    storage.upsert_rdf_reaction_records(
        rdf_doc.id,
        [
            {
                "record_index": 1,
                "scheme_id": "SCHEME1",
                "step_id": "STEP1",
                "cas_reaction_number": cas_reaction_number,
                "yield_text": rdf_yield,
            }
        ],
    )
    rdf_reaction = storage.list_rdf_reactions(document_id=rdf_doc.id, limit=1)[0]
    rdf_link = storage.create_reaction_source_link(
        {
            "cas_reaction_number": cas_reaction_number,
            "source_mode": "rdf_only",
            "rdf_reaction_id": rdf_reaction["id"],
            "rdf_document_id": rdf_doc.id,
            "link_confidence": 1.0,
            "link_method": "rdf_import",
            "needs_review": 0,
        }
    )
    pdf_link = storage.create_reaction_source_link(
        {
            "cas_reaction_number": cas_reaction_number,
            "source_mode": "pdf_only",
            "pdf_document_id": pdf_doc.id,
            "primary_pdf_page": 2,
            "pdf_pages_json": [2],
            "link_confidence": 0.8,
            "link_method": "pdf_cas_only",
            "needs_review": 1,
        }
    )
    storage.create_pdf_reaction_evidence(
        {
            "source_document_id": pdf_doc.id,
            "reaction_source_link_id": pdf_link["id"],
            "cas_reaction_number": cas_reaction_number,
            "page_number": 2,
            "is_primary": 1,
            "page_text": f"CAS Reaction Number: {cas_reaction_number}. Yield: {pdf_yield}",
            "yield_text": pdf_yield,
            "procedure_text": "Typical procedure",
            "extraction_method": "cas_anchor",
        }
    )
    if shared_batch:
        batch_id = storage._create_export_batch(
            "Example batch",
            status="auto_merged",
            confidence=1.0,
            merge_method="test",
            explanation={"signals": [{"name": "test", "matched": True}]},
        )
        storage._link_document_to_batch(batch_id, rdf_doc.id, "rdf", 1.0, {})
        storage._link_document_to_batch(batch_id, pdf_doc.id, "pdf", 1.0, {})
    return rdf_link["id"], pdf_link["id"]


def test_pdf_only_cas_candidates_default_to_needs_review(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    evidence_rows = [
        {
            "source_document_id": "doc_pdf",
            "cas_reaction_number": CAS_RN,
            "page_number": 3,
            "page_text": f"CAS Reaction Number: {CAS_RN}. Procedure. Yield: 95%",
            "yield_text": "95%",
            "match_confidence": 0.8,
        }
    ]

    service._create_pdf_only_candidates("doc_pdf", evidence_rows)

    links = service.storage.list_reaction_source_links(document_id="doc_pdf", source_mode="pdf_only")
    assert len(links) == 1
    assert links[0]["needs_review"] == 1
    assert evidence_rows[0]["reaction_source_link_id"] == links[0]["id"]
    assert evidence_rows[0]["is_primary"] == 1


def test_rdf_pdf_same_batch_without_conflict_auto_confirms(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    rdf_link_id, pdf_link_id = seed_rdf_pdf_links(service)

    service._link_rdf_pdf_by_cas()

    linked = service.storage.get_reaction_source_link(rdf_link_id)
    assert linked is not None
    assert linked["source_mode"] == "rdf_pdf_linked"
    assert linked["needs_review"] == 0
    assert linked["link_method"] == "cas_reaction_number"
    assert service.storage.get_reaction_source_link(pdf_link_id) is None


def test_rdf_pdf_same_batch_with_yield_conflict_needs_review(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    rdf_link_id, _ = seed_rdf_pdf_links(service, rdf_yield="95", pdf_yield="80%")

    service._link_rdf_pdf_by_cas()

    linked = service.storage.get_reaction_source_link(rdf_link_id)
    assert linked is not None
    assert linked["source_mode"] == "rdf_pdf_linked"
    assert linked["needs_review"] == 1
    assert "yield" in linked["conflict_flags_json"]


def test_unlinking_rdf_pdf_returns_pdf_only_to_needs_review(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    rdf_link_id, _ = seed_rdf_pdf_links(service)
    service._link_rdf_pdf_by_cas()

    result = service.unlink_reaction_source_link(rdf_link_id)

    pdf_link = service.storage.get_reaction_source_link(result["pdf_link_id"])
    assert pdf_link is not None
    assert pdf_link["source_mode"] == "pdf_only"
    assert pdf_link["needs_review"] == 1


def test_same_cas_multiple_rdf_pdf_links_pair_without_overwriting(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    first_rdf, first_pdf = seed_rdf_pdf_links(
        service,
        rdf_file_path="route_a.rdf",
        pdf_file_path="route_a.pdf",
        rdf_hash="rdf-a",
        pdf_hash="pdf-a",
    )
    second_rdf, second_pdf = seed_rdf_pdf_links(
        service,
        rdf_file_path="route_b.rdf",
        pdf_file_path="route_b.pdf",
        rdf_hash="rdf-b",
        pdf_hash="pdf-b",
    )

    service._link_rdf_pdf_by_cas()

    linked = [service.storage.get_reaction_source_link(first_rdf), service.storage.get_reaction_source_link(second_rdf)]
    assert {item["source_mode"] for item in linked if item} == {"rdf_pdf_linked"}
    first_pdf_doc = service.storage.get_document(linked[0]["pdf_document_id"])
    second_pdf_doc = service.storage.get_document(linked[1]["pdf_document_id"])
    assert first_pdf_doc and first_pdf_doc.file_path == "route_a.pdf"
    assert second_pdf_doc and second_pdf_doc.file_path == "route_b.pdf"
    assert service.storage.get_reaction_source_link(first_pdf) is None
    assert service.storage.get_reaction_source_link(second_pdf) is None
    assert len(service.storage.list_reaction_source_links(source_mode="rdf_pdf_linked")) == 2


def test_ambiguous_same_cas_links_remain_for_review(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    first_rdf, first_pdf = seed_rdf_pdf_links(
        service,
        rdf_file_path="dir1/same.rdf",
        pdf_file_path="dir3/same.pdf",
        rdf_hash="rdf-amb-a",
        pdf_hash="pdf-amb-a",
        shared_batch=False,
    )
    second_rdf, second_pdf = seed_rdf_pdf_links(
        service,
        rdf_file_path="dir2/same.rdf",
        pdf_file_path="dir4/same.pdf",
        rdf_hash="rdf-amb-b",
        pdf_hash="pdf-amb-b",
        shared_batch=False,
    )

    service._link_rdf_pdf_by_cas()

    links = [service.storage.get_reaction_source_link(link_id) for link_id in (first_rdf, first_pdf, second_rdf, second_pdf)]
    assert all(link is not None for link in links)
    assert {link["source_mode"] for link in links if link} == {"rdf_only", "pdf_only"}
    assert all(link["needs_review"] == 1 for link in links if link)
    assert all("ambiguous_cas_link" in link["conflict_flags_json"] for link in links if link)


def test_ai_review_writes_advisory_conflict_and_keeps_manual_review(tmp_path: Path, monkeypatch) -> None:
    from scifinder_route_mcp.config import AiProvider

    service = make_service(tmp_path)
    service.storage.upsert_ai_provider(
        AiProvider(id="extract", name="Extraction", format="openai_compatible", endpoint="https://extract.example/v1", api_key="extract-secret")
    )
    service.storage.upsert_ai_provider(
        AiProvider(id="review", name="Evidence Review", format="openai_compatible", endpoint="https://review.example/v1", api_key="review-secret")
    )
    service.config = service.config.model_copy(
        update={
            "extraction_provider_id": "extract",
            "extraction_model": "gpt-extract",
            "ai_evidence_review_enabled": True,
            "ai_evidence_review_provider_id": "review",
            "ai_evidence_review_model": "gpt-review",
            "ai_evidence_review_schema_version": "reaction_evidence_review.v1",
            "ai_evidence_review_prompt_profile": "strict-evidence-review-json",
        }
    )
    rdf_link_id, _ = seed_rdf_pdf_links(service)
    service._link_rdf_pdf_by_cas()
    linked = service.storage.get_reaction_source_link(rdf_link_id)
    assert linked is not None
    assert linked["needs_review"] == 0

    seen: dict[str, object] = {}

    class FakeLLMStructuringAdapter:
        configured = True

        def __init__(self, endpoint, model, *, enabled, schema_version, prompt_profile, provider="openai_compatible", api_key=None):
            seen["init"] = {
                "endpoint": endpoint,
                "model": model,
                "enabled": enabled,
                "schema_version": schema_version,
                "prompt_profile": prompt_profile,
                "provider": provider,
                "api_key": api_key,
            }

        def review_reaction_evidence(self, payload):
            seen["payload"] = payload
            return {
                "recommendation": "needs_review",
                "confidence": 0.82,
                "extracted_fields": {"yield_text": "80%"},
                "agreement": {"yield": "conflict"},
                "conflict_flags": {"yield": True},
                "rationale": "PDF yield conflicts with RDF yield.",
                "cited_evidence": [{"source": "pdf", "page_number": 2, "quote": "Yield: 80%"}],
            }

    monkeypatch.setattr("scifinder_route_mcp.service.LLMStructuringAdapter", FakeLLMStructuringAdapter)

    result = service.analyze_reaction_link_with_ai(rdf_link_id)

    updated = service.storage.get_reaction_source_link(rdf_link_id)
    assert updated is not None
    assert updated["needs_review"] == 1
    assert updated["source_mode"] == "rdf_pdf_linked"
    assert result["ai_review"]["recommendation"] == "needs_review"
    assert "ai_review" in updated["conflict_flags_json"]
    assert seen["init"] == {
        "endpoint": "https://review.example/v1",
        "model": "gpt-review",
        "enabled": True,
        "schema_version": "reaction_evidence_review.v1",
        "prompt_profile": "strict-evidence-review-json",
        "provider": "openai_compatible",
        "api_key": "review-secret",
    }
    assert result["ai_review"]["route_kind"] == "ai_evidence_review"
    assert seen["payload"]["pdf_evidence"][0]["page_number"] == 2


def test_ai_review_falls_back_to_extraction_provider_when_review_route_is_unset(tmp_path: Path, monkeypatch) -> None:
    from scifinder_route_mcp.config import AiProvider

    service = make_service(tmp_path)
    service.storage.upsert_ai_provider(
        AiProvider(id="extract", name="Extraction", format="openai_compatible", endpoint="https://extract.example/v1", api_key="extract-secret")
    )
    service.config = service.config.model_copy(
        update={
            "extraction_provider_id": "extract",
            "extraction_model": "gpt-extract",
            "ai_evidence_review_enabled": True,
            "ai_evidence_review_provider_id": None,
            "ai_evidence_review_model": None,
        }
    )
    rdf_link_id, _ = seed_rdf_pdf_links(service)
    service._link_rdf_pdf_by_cas()

    seen: dict[str, object] = {}

    class FakeLLMStructuringAdapter:
        configured = True

        def __init__(self, endpoint, model, *, enabled, schema_version, prompt_profile, provider="openai_compatible", api_key=None):
            seen["init"] = {"endpoint": endpoint, "model": model, "enabled": enabled, "provider": provider, "api_key": api_key}

        def review_reaction_evidence(self, payload):
            return {"recommendation": "confirm", "confidence": 0.9, "conflict_flags": {}}

    monkeypatch.setattr("scifinder_route_mcp.service.LLMStructuringAdapter", FakeLLMStructuringAdapter)

    result = service.analyze_reaction_link_with_ai(rdf_link_id)

    assert seen["init"] == {
        "endpoint": "https://extract.example/v1",
        "model": "gpt-extract",
        "enabled": True,
        "provider": "openai_compatible",
        "api_key": "extract-secret",
    }
    assert result["ai_review"]["route_kind"] == "extraction"


def test_ai_review_disabled_rejects_without_calling_adapter(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path)
    service.config = service.config.model_copy(update={"ai_evidence_review_enabled": False})
    rdf_link_id, _ = seed_rdf_pdf_links(service)

    class UnexpectedLLMStructuringAdapter:
        def __init__(self, *args, **kwargs):
            raise AssertionError("AI adapter should not be constructed when evidence review is disabled")

    monkeypatch.setattr("scifinder_route_mcp.service.LLMStructuringAdapter", UnexpectedLLMStructuringAdapter)

    with pytest.raises(RuntimeError, match="AI evidence review is disabled"):
        service.analyze_reaction_link_with_ai(rdf_link_id)
