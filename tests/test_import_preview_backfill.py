from __future__ import annotations

from pathlib import Path

import pytest

from scifinder_route_mcp.config import AppConfig
from scifinder_route_mcp.service import RouteService
from scifinder_route_mcp.storage import RouteStorage


CAS_RN = "31-456-CAS-7"


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


def write_pdf(path: Path, text: str) -> bytes:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()
    return path.read_bytes()


def test_import_preview_classifies_pdf_and_exact_rdf_pair_without_registering(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pdf_path = tmp_path / "example.pdf"
    rdf_path = tmp_path / "example.rdf"
    write_pdf(pdf_path, f"CAS Reaction Number: {CAS_RN}\nProducts\nReactants\nExperimental Protocols")
    rdf_path.write_text(f"$RDFILE\nRXN:VAR(1):CAS_Reaction_Number={CAS_RN}\n", encoding="utf-8")

    preview = service.preview_document_paths([str(pdf_path), str(rdf_path)])

    rows = {row["file_name"]: row for row in preview["items"]}
    assert preview["included_count"] == 2
    assert service.storage.count_documents() == 0
    assert rows["example.pdf"]["evidence_kind"] == "scifinder_pdf"
    assert rows["example.pdf"]["has_exact_rdf_pair"] is True
    assert rows["example.pdf"]["paired_rdf_name"] == "example.rdf"
    assert rows["example.pdf"]["cas_count"] == 1
    assert rows["example.rdf"]["has_exact_pdf_pair"] is True


def test_upload_preview_uses_classifier_but_does_not_register_document(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pdf_path = tmp_path / "upload.pdf"
    content = write_pdf(pdf_path, f"Supporting Information\nTypical Procedure\nCAS Reaction Number: {CAS_RN}")

    preview = service.preview_upload_document_bytes(content, "upload.pdf")

    row = preview["items"][0]
    assert row["preview_only"] is True
    assert row["include"] is True
    assert row["evidence_kind"] == "paper_si"
    assert service.storage.count_documents() == 0


def test_reaction_link_review_backfill_dry_run_and_apply(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pdf_doc = service.storage.upsert_document(
        file_path="legacy.pdf",
        file_hash="legacy-hash",
        file_type="pdf",
        title="Legacy PDF",
        doi=None,
        ingest_status="parsed",
    )
    link = service.storage.create_reaction_source_link(
        {
            "cas_reaction_number": CAS_RN,
            "source_mode": "pdf_only",
            "pdf_document_id": pdf_doc.id,
            "primary_pdf_page": 1,
            "pdf_pages_json": [1],
            "link_confidence": 0.8,
            "link_method": "legacy_pdf_cas_only",
            "needs_review": 0,
        }
    )

    dry_run = service.backfill_reaction_link_review(dry_run=True)
    assert dry_run["candidate_count"] == 1
    assert service.storage.get_reaction_source_link(link["id"])["needs_review"] == 0

    applied = service.backfill_reaction_link_review(dry_run=False)
    assert applied["updated_count"] == 1
    assert service.storage.get_reaction_source_link(link["id"])["needs_review"] == 1
