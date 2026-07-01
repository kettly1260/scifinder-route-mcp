from __future__ import annotations

from pathlib import Path

import pytest

from scifinder_route_mcp.config import AppConfig
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


def write_pdf(path: Path, text: str, metadata: dict[str, str] | None = None) -> None:
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    if metadata:
        document.set_metadata(metadata)
    document.save(path)
    document.close()


def test_register_excludes_obsidian_user_note_pdf(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    path = tmp_path / "C12.pdf"
    write_pdf(
        path,
        "This is a derived mechanistic note, not a primary source.",
        {"title": "C12 - Obsidian - Obsidian v1.8.9", "creator": "Obsidian"},
    )

    with pytest.raises(ValueError, match="Obsidian"):
        service.register_document(str(path))


def test_register_tags_paper_si_as_high_priority_evidence(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    path = tmp_path / "paper_si.pdf"
    write_pdf(
        path,
        "Supporting Information for Example Paper. Typical Procedure for the reaction: add reagent and stir.",
    )

    result = service.register_document(str(path))
    document = result["document"]

    assert document["scifinder_metadata"]["evidence_kind"] == "paper_si"
    assert document["scifinder_metadata"]["evidence_priority"] == 90


def test_register_tags_patent_pdf_as_patent_evidence(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    path = tmp_path / "patent.pdf"
    write_pdf(path, "CN 109293561 A patent application. Example 1 describes a reaction process.")

    result = service.register_document(str(path))
    document = result["document"]

    assert document["scifinder_metadata"]["evidence_kind"] == "patent"
    assert document["scifinder_metadata"]["evidence_priority"] == 55


def test_register_excludes_invalid_pdf(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    path = tmp_path / "rsc_si.pdf"
    path.write_bytes(b"%PDF-1.4\nnot a complete pdf")

    with pytest.raises(ValueError, match="PDF could not be opened"):
        service.register_document(str(path))
