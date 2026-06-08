from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SourceDocument:
    id: str
    file_path: str
    file_hash: str
    file_type: str
    title: str | None
    doi: str | None
    ingest_status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParseJob:
    id: str
    document_id: str
    status: str
    stage: str
    error: str | None
    started_at: str | None
    finished_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReactionStep:
    id: str
    source_document_id: str
    step_index: int
    reaction_name: str | None
    substrate_text: str | None
    product_text: str | None
    reagent_text: str | None
    catalyst_text: str | None
    solvent_text: str | None
    temperature: str | None
    time: str | None
    atmosphere: str | None
    yield_text: str | None
    scale: str | None
    workup: str | None
    purification: str | None
    original_text: str
    confidence: float
    verification_status: str
    needs_ocr: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Provenance:
    id: str
    reaction_step_id: str
    source_document_id: str
    page_number: int | None
    text_span: str
    image_region_path: str | None
    ocr_output: str | None
    parser_name: str
    parser_version: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DoiVerification:
    id: str
    reaction_step_id: str
    doi: str
    paper_title: str | None
    verified_fields: dict[str, Any]
    original_paper_excerpt: str | None
    verification_confidence: float
    verifier_agent: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
