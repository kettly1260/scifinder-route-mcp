from pathlib import Path
import sys
from types import SimpleNamespace

from scifinder_route_mcp.parsers import _extract_pdf_page_blocks, _extract_pdf_reaction_evidence, _select_primary_pdf_page

def test_extract_pdf_page_blocks():
    text = """
    Products: Compound A
    Reactants: Compound B
    Stage Reagents Catalysts Solvents Conditions: Room temp, 2 hours
    Procedure
    Stir everything together.
    Yield: 95%
    """
    blocks = _extract_pdf_page_blocks(text)
    assert blocks["products"] == "Compound A"
    assert blocks["reactants"] == "Compound B"
    assert blocks["conditions"] == "Room temp, 2 hours"
    assert "Stir everything together" in blocks["procedure"]
    assert blocks["yield"] == "95%"

def test_select_primary_pdf_page():
    rows = [
        {"page_number": 1, "page_text": "Just some intro text."},
        {"page_number": 2, "page_text": "Procedure: do something", "yield_text": "90%"},
        {"page_number": 3, "page_text": "Procedure: do something else, experimental protocol"},
    ]
    primary = _select_primary_pdf_page(rows)
    assert primary == 3  # "procedure" (100) + "experimental protocol" (80) = 180. Page 2 is 100 + 20 = 120.


def test_extract_pdf_reaction_evidence_respects_max_pages(monkeypatch):
    class FakePage:
        def __init__(self, text):
            self.text = text

        def get_text(self, _mode):
            return self.text

    class FakeDocument(list):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    fake_document = FakeDocument(
        [
            FakePage("CAS Reaction Number: 31-123-CAS-1. Yield: 80%"),
            FakePage("CAS Reaction Number: 31-456-CAS-2. Yield: 81%"),
            FakePage("CAS Reaction Number: 31-789-CAS-3. Yield: 82%"),
        ]
    )
    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda _path: fake_document))

    rows = _extract_pdf_reaction_evidence(Path("fake.pdf"), "doc-1", max_pages=2)
    empty_rows = _extract_pdf_reaction_evidence(Path("fake.pdf"), "doc-1", max_pages=0)

    assert [row["page_number"] for row in rows] == [1, 2]
    assert [row["cas_reaction_number"] for row in rows] == ["31-123-CAS-1", "31-456-CAS-2"]
    assert empty_rows == []
