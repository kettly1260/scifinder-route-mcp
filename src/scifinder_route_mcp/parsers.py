from __future__ import annotations

import email
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

from .rdfile import split_rdfile_records


@dataclass(frozen=True)
class TextChunk:
    text: str
    page_number: int | None
    parser_name: str
    parser_version: str = "0.1.0"


@dataclass(frozen=True)
class ParsedDocument:
    file_type: str
    title: str | None
    doi: str | None
    chunks: list[TextChunk]

    @property
    def full_text(self) -> str:
        return "\n\n".join(chunk.text for chunk in self.chunks if chunk.text.strip())


class _TextOnlyHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"p", "br", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        return normalize_text(unescape(" ".join(self.parts)))


def parse_document(path: Path | str) -> ParsedDocument:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        return _parse_pdf(file_path)
    if suffix == ".rtf":
        return _parse_rtf(file_path)
    if suffix == ".rdf":
        return _parse_rdfile(file_path)
    if suffix in {".html", ".htm"}:
        return _parse_html(file_path.read_text(encoding="utf-8", errors="ignore"), "html")
    if suffix in {".mhtml", ".mht"}:
        return _parse_mhtml(file_path)
    if suffix in {".md", ".markdown"}:
        return _parse_markdown(file_path)
    return _parse_text(file_path)


def detect_file_type(path: Path | str) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    return suffix or "unknown"


def sniff_document_type(content: bytes) -> str:
    head = content[:4096].lstrip()
    lowered = head[:2048].lower()
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"$RDFILE") and (b"$RXN" in content[:65536] or b"$MOL" in content[:65536]):
        return "rdf"
    if lowered.startswith((b"mime-version:", b"content-type: multipart/related", b"content-type: multipart/mixed")):
        return "mhtml"
    if any(marker in lowered for marker in (b"<!doctype html", b"<html", b"<head", b"<body")):
        return "html"
    if is_text_like(content):
        return "text"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    return "binary"


def is_text_like(content: bytes) -> bool:
    sample = content[:8192]
    if b"\x00" in sample:
        return False
    if not sample:
        return True
    control = sum(1 for byte in sample if byte < 9 or (13 < byte < 32))
    return control / max(len(sample), 1) < 0.05


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_title(text: str) -> str | None:
    for line in text.splitlines():
        candidate = line.strip()
        if 8 <= len(candidate) <= 220 and not candidate.lower().startswith(("doi:", "abstract", "experimental")):
            return candidate
    return None


def extract_doi(text: str) -> str | None:
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;)") if match else None


def _parse_text(file_path: Path) -> ParsedDocument:
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    text = normalize_text(text)
    return ParsedDocument(
        file_type=detect_file_type(file_path),
        title=extract_title(text),
        doi=extract_doi(text),
        chunks=[TextChunk(text=text, page_number=None, parser_name="text")],
    )


def _parse_markdown(file_path: Path) -> ParsedDocument:
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#>*_`|~-]+", " ", text)
    text = normalize_text(text)
    return ParsedDocument(
        file_type=detect_file_type(file_path),
        title=extract_title(text),
        doi=extract_doi(text),
        chunks=[TextChunk(text=text, page_number=None, parser_name="markdown")],
    )


def _parse_rtf(file_path: Path) -> ParsedDocument:
    raw = file_path.read_bytes()
    if not raw.lstrip().startswith(b"{\\rtf"):
        raise ValueError("RTF file does not start with an RTF header")
    text = raw.decode("latin-1", errors="ignore")
    lowered = text.lower()
    if "\\object" in lowered or "\\objdata" in lowered:
        raise ValueError("RTF embedded objects are not allowed")
    plain = rtf_to_text(text)
    return ParsedDocument(
        file_type="rtf",
        title=extract_title(plain),
        doi=extract_doi(plain),
        chunks=[TextChunk(text=plain, page_number=None, parser_name="rtf")],
    )


def rtf_to_text(text: str) -> str:
    text = re.sub(r"\{\\fonttbl.*?\}\s*", " ", text, flags=re.DOTALL)
    text = re.sub(r"\{\\colortbl.*?\}\s*", " ", text, flags=re.DOTALL)
    text = re.sub(r"\{\\\*[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", " ", text, flags=re.DOTALL)

    def hex_repl(match: re.Match[str]) -> str:
        return bytes.fromhex(match.group(1)).decode("cp1252", errors="ignore")

    def unicode_repl(match: re.Match[str]) -> str:
        value = int(match.group(1))
        if value < 0:
            value += 65536
        return chr(value)

    text = re.sub(r"\\u(-?\d+)\??", unicode_repl, text)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", hex_repl, text)
    text = re.sub(r"\\(?:par|line|tab)\b", "\n", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", text)
    text = re.sub(r"\\[^a-zA-Z]", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    return normalize_text(text)


def _parse_rdfile(file_path: Path) -> ParsedDocument:
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    if not text.lstrip().startswith("$RDFILE"):
        raise ValueError("RDfile does not start with $RDFILE")
    records = parse_rdfile_records(text)
    chunks: list[TextChunk] = []
    for index, record in enumerate(records, start=1):
        summary = rdfile_record_summary(record, index)
        if summary.strip():
            chunks.append(TextChunk(text=summary, page_number=None, parser_name="rdfile"))
    full_text = "\n\n".join(chunk.text for chunk in chunks)
    return ParsedDocument(
        file_type="rdf",
        title=extract_title(full_text),
        doi=extract_doi(full_text),
        chunks=chunks or [TextChunk(text=normalize_text(text[:4000]), page_number=None, parser_name="rdfile")],
    )


def parse_rdfile_records(text: str) -> list[str]:
    return split_rdfile_records(text)


def rdfile_record_summary(record: str, index: int) -> str:
    fields = rdfile_fields(record)
    molecule_lines = rdfile_molecule_headers(record)
    parts = [f"SciFinder RDF reaction record {index}"]
    for key, label in [
        ("RXN:VAR(1):CAS_Reaction_Number", "CAS Reaction Number"),
        ("RXN:VAR(1):STEPS", "Steps"),
        ("RXN:VAR(1):STAGES", "Stages"),
        ("RXN:VAR(1):PRO(1):YIELD", "Yield"),
        ("RXN:VAR(1):NOTES", "Notes"),
        ("RXN:VAR(1):EXP_PROC", "Experimental Procedure"),
        ("RXN:VAR(1):REFERENCE(1):TITLE", "Reference Title"),
        ("RXN:VAR(1):REFERENCE(1):AUTHOR", "Reference Author"),
        ("RXN:VAR(1):REFERENCE(1):CITATION", "Reference Citation"),
    ]:
        if key in fields:
            parts.append(f"{label}: {fields[key]}")
    for prefix, label in [
        ("RXN:RCT", "Reactant CAS"),
        ("RXN:PRO", "Product CAS"),
        ("RXN:VAR(1):RGT", "Reagent CAS"),
        ("RXN:VAR(1):CAT", "Catalyst CAS"),
        ("RXN:VAR(1):SOL", "Solvent CAS"),
    ]:
        values = [value for key, value in fields.items() if key.startswith(prefix) and key.endswith(":CAS_RN")]
        if values:
            parts.append(f"{label}: {', '.join(values)}")
    if molecule_lines:
        parts.append("Molecule blocks: " + "; ".join(molecule_lines[:8]))
    return normalize_text("\n".join(parts))


def rdfile_fields(record: str) -> dict[str, str]:
    from .rdfile import rdfile_fields as parse_fields

    return parse_fields(record)


def rdfile_molecule_headers(record: str) -> list[str]:
    headers: list[str] = []
    for block in record.split("$MOL")[1:]:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        readable = []
        for line in lines[:5]:
            if line.startswith(("M  V30", "M  END")) or re.match(r"^\d+\s+\d+", line):
                continue
            readable.append(line)
        if readable:
            headers.append(" | ".join(readable[:3]))
    return headers


def _parse_html(html: str, file_type: str) -> ParsedDocument:
    text = _html_to_text(html)
    return ParsedDocument(
        file_type=file_type,
        title=_extract_html_title(html) or extract_title(text),
        doi=extract_doi(text),
        chunks=[TextChunk(text=text, page_number=None, parser_name="html")],
    )


def _parse_mhtml(file_path: Path) -> ParsedDocument:
    message = email.message_from_bytes(file_path.read_bytes())
    html_parts: list[str] = []
    text_parts: list[str] = []
    for part in message.walk():
        content_type = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="ignore")
        if content_type == "text/html":
            html_parts.append(decoded)
        elif content_type == "text/plain":
            text_parts.append(decoded)
    if html_parts:
        parsed = _parse_html("\n".join(html_parts), "mhtml")
        return parsed
    text = normalize_text("\n".join(text_parts))
    return ParsedDocument(
        file_type="mhtml",
        title=extract_title(text),
        doi=extract_doi(text),
        chunks=[TextChunk(text=text, page_number=None, parser_name="mhtml")],
    )


def _parse_pdf(file_path: Path) -> ParsedDocument:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on optional runtime dependency
        raise RuntimeError("PDF parsing requires PyMuPDF. Install the project dependencies first.") from exc

    chunks: list[TextChunk] = []
    with fitz.open(file_path) as document:
        for index, page in enumerate(document, start=1):
            text = normalize_text(page.get_text("text"))
            if text:
                chunks.append(TextChunk(text=text, page_number=index, parser_name="pymupdf"))
    full_text = "\n\n".join(chunk.text for chunk in chunks)
    return ParsedDocument(
        file_type="pdf",
        title=extract_title(full_text),
        doi=extract_doi(full_text),
        chunks=chunks,
    )


def _html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore[import-not-found]
    except ImportError:
        parser = _TextOnlyHTMLParser()
        parser.feed(html)
        return parser.text()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    return normalize_text(text)


def _extract_html_title(html: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return normalize_text(re.sub(r"<[^>]+>", "", unescape(match.group(1))))
