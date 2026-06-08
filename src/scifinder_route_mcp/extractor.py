from __future__ import annotations

import re
from dataclasses import dataclass

from .parsers import ParsedDocument, TextChunk, normalize_text


SOLVENTS = [
    "dichloromethane",
    "DCM",
    "THF",
    "tetrahydrofuran",
    "methanol",
    "MeOH",
    "ethanol",
    "EtOH",
    "toluene",
    "DMF",
    "DMSO",
    "acetonitrile",
    "MeCN",
    "water",
    "EtOAc",
    "ethyl acetate",
    "hexane",
    "diethyl ether",
    "1,4-dioxane",
]

REACTION_HINTS = [
    "experimental",
    "procedure",
    "preparation",
    "synthesis",
    "step",
    "yield",
    "added",
    "stirred",
    "heated",
    "cooled",
    "reflux",
    "purified",
    "washed",
]


@dataclass(frozen=True)
class CandidateBlock:
    text: str
    page_number: int | None
    parser_name: str
    parser_version: str


def extract_reaction_steps(parsed: ParsedDocument, document_id: str) -> list[tuple[dict[str, object], dict[str, object]]]:
    candidates = detect_candidate_blocks(parsed.chunks)
    results: list[tuple[dict[str, object], dict[str, object]]] = []
    for index, candidate in enumerate(candidates, start=1):
        fields = extract_fields(candidate.text)
        confidence = score_candidate(candidate.text, fields)
        step = {
            "source_document_id": document_id,
            "step_index": index,
            "reaction_name": fields.get("reaction_name"),
            "substrate_text": fields.get("substrate_text"),
            "product_text": fields.get("product_text"),
            "reagent_text": fields.get("reagent_text"),
            "catalyst_text": fields.get("catalyst_text"),
            "solvent_text": fields.get("solvent_text"),
            "temperature": fields.get("temperature"),
            "time": fields.get("time"),
            "atmosphere": fields.get("atmosphere"),
            "yield_text": fields.get("yield_text"),
            "scale": fields.get("scale"),
            "workup": fields.get("workup"),
            "purification": fields.get("purification"),
            "original_text": candidate.text,
            "confidence": confidence,
            "verification_status": "unverified",
            "needs_ocr": False,
        }
        provenance = {
            "page_number": candidate.page_number,
            "text_span": candidate.text[:1000],
            "image_region_path": None,
            "ocr_output": None,
            "parser_name": candidate.parser_name,
            "parser_version": candidate.parser_version,
            "confidence": confidence,
        }
        results.append((step, provenance))
    if not results and looks_like_image_only(parsed):
        text = parsed.full_text or "No extractable text was found; OCR is required."
        step = {
            "source_document_id": document_id,
            "step_index": 1,
            "original_text": text[:1000],
            "confidence": 0.1,
            "verification_status": "unverified",
            "needs_ocr": True,
        }
        provenance = {
            "page_number": None,
            "text_span": text[:1000],
            "image_region_path": None,
            "ocr_output": None,
            "parser_name": "ocr-placeholder",
            "parser_version": "0.1.0",
            "confidence": 0.1,
        }
        results.append((step, provenance))
    return results


def detect_candidate_blocks(chunks: list[TextChunk]) -> list[CandidateBlock]:
    blocks: list[CandidateBlock] = []
    for chunk in chunks:
        paragraphs = [normalize_text(part) for part in re.split(r"\n\s*\n+", chunk.text) if normalize_text(part)]
        buffer: list[str] = []
        for paragraph in paragraphs:
            if is_reaction_like(paragraph):
                buffer.append(paragraph)
                if len("\n\n".join(buffer)) > 350:
                    blocks.append(_candidate_from_buffer(buffer, chunk))
                    buffer = []
            elif buffer and len(paragraph) < 180:
                buffer.append(paragraph)
            elif buffer:
                blocks.append(_candidate_from_buffer(buffer, chunk))
                buffer = []
        if buffer:
            blocks.append(_candidate_from_buffer(buffer, chunk))
    return dedupe_blocks(blocks)


def is_reaction_like(text: str) -> bool:
    lowered = text.lower()
    if len(text) < 80:
        return False
    hint_count = sum(1 for hint in REACTION_HINTS if hint in lowered)
    has_condition = bool(
        re.search(r"\b\d{1,3}\s*%", text)
        or re.search(r"-?\d{1,3}\s*(?:°\s*)?C\b", text, flags=re.IGNORECASE)
        or re.search(r"\b\d+(?:\.\d+)?\s*(?:h|hr|hours|min|minutes)\b", text, flags=re.IGNORECASE)
        or any(solvent.lower() in lowered for solvent in SOLVENTS)
    )
    return hint_count >= 2 or (hint_count >= 1 and has_condition)


def extract_fields(text: str) -> dict[str, str | None]:
    return {
        "reaction_name": extract_reaction_name(text),
        "substrate_text": extract_after_label(text, ["substrate", "starting material"]),
        "product_text": extract_after_label(text, ["product", "compound"]),
        "reagent_text": extract_reagents(text),
        "catalyst_text": extract_after_label(text, ["catalyst"]),
        "solvent_text": extract_solvents(text),
        "temperature": extract_temperature(text),
        "time": extract_time(text),
        "atmosphere": extract_atmosphere(text),
        "yield_text": extract_yield(text),
        "scale": extract_scale(text),
        "workup": extract_sentence_containing(text, ["washed", "quenched", "extracted", "dried"]),
        "purification": extract_sentence_containing(text, ["purified", "chromatography", "recrystallized", "distilled"]),
    }


def score_candidate(text: str, fields: dict[str, str | None]) -> float:
    populated = sum(1 for value in fields.values() if value)
    base = 0.25 + min(populated * 0.08, 0.48)
    if len(text) > 250:
        base += 0.1
    if fields.get("yield_text"):
        base += 0.08
    if fields.get("temperature") or fields.get("time"):
        base += 0.06
    return round(min(base, 0.95), 2)


def dedupe_blocks(blocks: list[CandidateBlock]) -> list[CandidateBlock]:
    seen: set[str] = set()
    unique: list[CandidateBlock] = []
    for block in blocks:
        key = normalize_text(block.text[:300]).lower()
        if key not in seen:
            unique.append(block)
            seen.add(key)
    return unique


def looks_like_image_only(parsed: ParsedDocument) -> bool:
    text = parsed.full_text.strip()
    return parsed.file_type == "pdf" and len(text) < 80


def _candidate_from_buffer(buffer: list[str], chunk: TextChunk) -> CandidateBlock:
    return CandidateBlock(
        text=normalize_text("\n\n".join(buffer)),
        page_number=chunk.page_number,
        parser_name=chunk.parser_name,
        parser_version=chunk.parser_version,
    )


def extract_reaction_name(text: str) -> str | None:
    match = re.search(r"(?:general\s+)?(?:procedure|synthesis|preparation)\s+(?:for|of)?\s*([^\.\n:]{3,120})", text, re.IGNORECASE)
    return clean_value(match.group(1)) if match else None


def extract_after_label(text: str, labels: list[str]) -> str | None:
    joined = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{joined})\s*[:=]\s*([^\.;\n]{{2,160}})", text, re.IGNORECASE)
    return clean_value(match.group(1)) if match else None


def extract_reagents(text: str) -> str | None:
    label_value = extract_after_label(text, ["reagent", "reagents"])
    if label_value:
        return label_value
    snippets = []
    for pattern in [r"(?:was|were)\s+added\s+([^\.]{3,160})", r"with\s+([^\.]{3,120})"]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            snippets.append(clean_value(match.group(1)))
    return "; ".join(snippets) if snippets else None


def extract_solvents(text: str) -> str | None:
    found: list[str] = []
    lowered = text.lower()
    for solvent in SOLVENTS:
        if solvent.lower() in lowered and solvent not in found:
            found.append(solvent)
    return "; ".join(found) if found else extract_after_label(text, ["solvent", "solvents"])


def extract_temperature(text: str) -> str | None:
    match = re.search(r"(?:at|to|room temperature|rt|reflux)[^\.\n]{0,30}?(-?\d{1,3}\s*(?:°\s*)?C)\b", text, re.IGNORECASE)
    if match:
        return clean_value(match.group(1))
    match = re.search(r"\b(room temperature|rt|reflux)\b", text, re.IGNORECASE)
    return clean_value(match.group(1)) if match else None


def extract_time(text: str) -> str | None:
    match = re.search(r"\b\d+(?:\.\d+)?\s*(?:h|hr|hrs|hour|hours|min|minute|minutes)\b", text, re.IGNORECASE)
    return clean_value(match.group(0)) if match else None


def extract_atmosphere(text: str) -> str | None:
    match = re.search(r"\b(?:under|in)\s+(nitrogen|argon|air|N2|Ar)\b", text, re.IGNORECASE)
    return clean_value(match.group(0)) if match else None


def extract_yield(text: str) -> str | None:
    match = re.search(r"(?:yield(?:ed)?\s*)?\(?\s*(\d{1,3}(?:\.\d+)?)\s*%\s*\)?", text, re.IGNORECASE)
    return f"{match.group(1)}%" if match else None


def extract_scale(text: str) -> str | None:
    match = re.search(r"\b\d+(?:\.\d+)?\s*(?:mmol|mol|g|mg|kg)\b", text, re.IGNORECASE)
    return clean_value(match.group(0)) if match else None


def extract_sentence_containing(text: str, needles: list[str]) -> str | None:
    sentences = re.split(r"(?<=[\.])\s+", text)
    for sentence in sentences:
        lowered = sentence.lower()
        if any(needle in lowered for needle in needles):
            return clean_value(sentence[:240])
    return None


def clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ;,.")
