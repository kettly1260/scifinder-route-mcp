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
