from __future__ import annotations

import email
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path


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
    if suffix in {".html", ".htm"}:
        return _parse_html(file_path.read_text(encoding="utf-8", errors="ignore"), "html")
    if suffix in {".mhtml", ".mht"}:
        return _parse_mhtml(file_path)
    return _parse_text(file_path)


def detect_file_type(path: Path | str) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    return suffix or "unknown"


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
