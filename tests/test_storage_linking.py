from __future__ import annotations
import pytest
from pathlib import Path
from scifinder_route_mcp.storage import RouteStorage

@pytest.fixture
def storage(tmp_path: Path) -> RouteStorage:
    db_path = tmp_path / "test.sqlite3"
    storage = RouteStorage(db_path)
    storage.init_schema()
    return storage

def test_reaction_source_link_crud(storage: RouteStorage):
    # Create
    data = {
        "cas_reaction_number": "31-123-CAS-1",
        "source_mode": "rdf_only",
        "rdf_reaction_id": "rxn_1",
        "pdf_pages_json": [1, 2],
        "link_confidence": 1.0,
        "link_method": "cas_reaction_number",
        "needs_review": 1,
        "conflict_flags_json": {"yield": True}
    }
    created = storage.create_reaction_source_link(data)
    assert created["id"] is not None
    assert created["cas_reaction_number"] == "31-123-CAS-1"
    assert created["source_mode"] == "rdf_only"
    assert created["pdf_pages_json"] == "[1, 2]"
    assert created["needs_review"] == 1
    assert created["conflict_flags_json"] == '{"yield": true}'

    # Update
    updated = storage.update_reaction_source_link(created["id"], {
        "source_mode": "rdf_pdf_linked",
        "needs_review": 0,
        "pdf_pages_json": [1, 2, 3]
    })
    assert updated is not None
    assert updated["source_mode"] == "rdf_pdf_linked"
    assert updated["needs_review"] == 0
    assert updated["pdf_pages_json"] == "[1, 2, 3]"

    # Get
    fetched = storage.get_reaction_source_link(created["id"])
    assert fetched is not None
    assert fetched["source_mode"] == "rdf_pdf_linked"

    # List
    links = storage.list_reaction_source_links()
    assert len(links) == 1
    
    # List filtered
    links = storage.list_reaction_source_links(source_mode="rdf_pdf_linked")
    assert len(links) == 1
    links = storage.list_reaction_source_links(source_mode="pdf_only")
    assert len(links) == 0

def test_pdf_reaction_evidence_crud(storage: RouteStorage):
    data = {
        "source_document_id": "doc_1",
        "cas_reaction_number": "31-123-CAS-2",
        "page_number": 5,
        "is_primary": 1,
        "page_text": "Experimental Procedure...",
        "procedure_text": "Mix things...",
        "yield_text": "95%",
        "extraction_method": "cas_anchor"
    }
    created = storage.create_pdf_reaction_evidence(data)
    assert created["id"] is not None
    assert created["source_document_id"] == "doc_1"
    assert created["page_number"] == 5
    assert created["is_primary"] == 1
    assert created["yield_text"] == "95%"

    # List
    evidences = storage.list_pdf_reaction_evidence(document_id="doc_1")
    assert len(evidences) == 1
    evidences = storage.list_pdf_reaction_evidence(cas_reaction_number="31-123-CAS-2")
    assert len(evidences) == 1

def test_enriched_reaction_source_links_are_paged_and_batched(storage: RouteStorage):
    pdf_doc = storage.upsert_document(
        file_path="exports/example.pdf",
        file_hash="pdf-hash",
        file_type="pdf",
        title="Example PDF",
        doi=None,
        ingest_status="parsed",
    )
    rdf_doc = storage.upsert_document(
        file_path="exports/example.rdf",
        file_hash="rdf-hash",
        file_type="rdf",
        title="Example RDF",
        doi=None,
        ingest_status="parsed",
    )
    storage.update_document_scifinder_metadata(
        pdf_doc.id,
        {
            "evidence_kind": "readable_pdf",
            "evidence_priority": 70,
            "label": "Readable PDF",
            "provenance_warning": "Use page evidence.",
        },
    )
    first = storage.create_reaction_source_link(
        {
            "cas_reaction_number": "31-123-CAS-1",
            "source_mode": "rdf_pdf_linked",
            "rdf_document_id": rdf_doc.id,
            "pdf_document_id": pdf_doc.id,
            "link_confidence": 0.9,
            "link_method": "cas_reaction_number",
            "needs_review": 1,
            "conflict_flags_json": {"yield": True},
        }
    )
    storage.create_pdf_reaction_evidence(
        {
            "source_document_id": pdf_doc.id,
            "reaction_source_link_id": first["id"],
            "cas_reaction_number": "31-123-CAS-1",
            "page_number": 2,
            "page_text": "CAS Reaction Number: 31-123-CAS-1.",
            "extraction_method": "cas_anchor",
        }
    )
    storage.create_pdf_reaction_evidence(
        {
            "source_document_id": pdf_doc.id,
            "reaction_source_link_id": first["id"],
            "cas_reaction_number": "31-123-CAS-1",
            "page_number": 4,
            "page_text": "CAS Reaction Number: 31-123-CAS-1 continued.",
            "extraction_method": "cas_anchor",
        }
    )
    storage.create_reaction_source_link(
        {
            "cas_reaction_number": "31-123-CAS-2",
            "source_mode": "rdf_only",
            "rdf_document_id": rdf_doc.id,
            "link_confidence": 1.0,
            "link_method": "rdf_import",
            "needs_review": 0,
        }
    )

    page = storage.list_enriched_reaction_source_links(limit=1)
    assert page["total"] == 2
    assert len(page["items"]) == 1

    enriched = storage.get_enriched_reaction_source_link(first["id"])
    assert enriched is not None
    assert enriched["pdf_file_name"] == "example.pdf"
    assert enriched["rdf_file_name"] == "example.rdf"
    assert enriched["evidence_kind"] == "readable_pdf"
    assert enriched["pdf_evidence_count"] == 2
    assert enriched["pdf_evidence_pages"] == [2, 4]
    assert enriched["has_conflicts"] is True
    assert enriched["conflict_flags"] == {"yield": True}

    filtered = storage.list_enriched_reaction_source_links(evidence_kind="readable_pdf")
    assert filtered["total"] == 1
    assert filtered["items"][0]["id"] == first["id"]

def test_structure_evidence_candidate_crud(storage: RouteStorage):
    data = {
        "pdf_evidence_id": "ev_1",
        "source_document_id": "doc_1",
        "page_number": 5,
        "candidate_smiles": "C1=CC=CC=C1",
        "confidence": 0.8,
        "validation_status": "candidate"
    }
    created = storage.create_structure_evidence_candidate(data)
    assert created["id"] is not None
    assert created["candidate_smiles"] == "C1=CC=CC=C1"
    assert created["validation_status"] == "candidate"

    # Update
    updated = storage.update_structure_evidence_candidate(created["id"], {
        "validation_status": "manual_verified",
        "validation_signals_json": {"verified_by": "user1"}
    })
    assert updated is not None
    assert updated["validation_status"] == "manual_verified"
    assert updated["validation_signals_json"] == '{"verified_by": "user1"}'
