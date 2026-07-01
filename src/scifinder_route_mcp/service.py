from __future__ import annotations

import hashlib
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .chem import fingerprint_from_query, install_rdkit, rdkit_info, render_structure_svg, substructure_match, tanimoto
from .config import AppConfig, DEFAULT_ZOTERO_MCP_ENDPOINTS
from .models import AiProvider
from .extractor import extract_reaction_steps
from .integrations import EmbeddingAdapter, ExternalParserAdapter, LLMStructuringAdapter, OCRAdapter, StructureRecognitionAdapter, RerankerAdapter, list_http_models, test_http_endpoint
from .literature import ZoteroMcpClient, build_query, diff_reaction_fields, extract_method_fields, item_doi, item_key, item_title, item_year, normalize_doi, title_similarity, trim_text
from .parsers import ParsedDocument, TextChunk, detect_file_type, parse_document, sniff_document_type
from .rdfile import parse_rdfile_reactions
from .registry import index_reaction_compounds, normalize_with_rdkit
from .storage import RouteStorage, is_sqlite_locked_error


RDF_PROVENANCE_WARNING = "RDF/RDfile records are structured SciFinder evidence but may not include complete experimental procedures; verify RDF-derived chemical claims against linked PDF/RTF/HTML readable or visual provenance before final use."
LOGGER = logging.getLogger(__name__)
CAS_REACTION_NUMBER_RE = re.compile(r"\b31-\d{3,}-CAS-\d+\b", re.IGNORECASE)

EVIDENCE_KIND_PROFILES: dict[str, dict[str, Any]] = {
    "scifinder_rdf": {
        "evidence_priority": 100,
        "label": "SciFinder RDF structured evidence",
        "provenance_warning": RDF_PROVENANCE_WARNING,
    },
    "paper_si": {
        "evidence_priority": 90,
        "label": "Paper supporting information experimental procedure",
        "provenance_warning": "Paper SI procedures are high-value experimental evidence; verify structures/yields against the cited paper and page evidence.",
    },
    "scifinder_readable": {
        "evidence_priority": 75,
        "label": "SciFinder readable export evidence",
        "provenance_warning": "Readable SciFinder exports can provide experimental procedure text but should be verified against RDF or paper SI when structure-sensitive.",
    },
    "scifinder_pdf": {
        "evidence_priority": 70,
        "label": "SciFinder PDF evidence",
        "provenance_warning": "SciFinder PDF-only evidence lacks RDF structure records; keep it under review until corroborated.",
    },
    "patent": {
        "evidence_priority": 55,
        "label": "Patent reaction process evidence",
        "provenance_warning": "Patent reaction process evidence may describe examples or claims; verify language, example scope, and exact procedure before final use.",
    },
    "unsupported_non_source": {
        "evidence_priority": 0,
        "label": "Unsupported non-source document",
        "provenance_warning": "This document is not treated as a primary reaction evidence source.",
    },
    "user_note": {
        "evidence_priority": 0,
        "label": "User note or derived analysis",
        "provenance_warning": "User notes are excluded from primary evidence import to avoid circular provenance.",
    },
    "invalid_pdf": {
        "evidence_priority": 0,
        "label": "Invalid or unreadable PDF",
        "provenance_warning": "Invalid PDFs are excluded; re-download or repair the source before import.",
    },
}

RDF_ROLE_LABELS_ZH = {
    "reactant": "反应物",
    "product": "产物",
    "reagent": "试剂",
    "catalyst": "催化剂",
    "solvent": "溶剂",
    "unknown": "未知角色",
}

RDF_ROLE_LABELS_EN = {
    "reactant": "Reactant",
    "product": "Product",
    "reagent": "Reagent",
    "catalyst": "Catalyst",
    "solvent": "Solvent",
    "unknown": "Unknown",
}


def normalize_for_source_classification(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def is_paper_si_text(text: str) -> bool:
    return bool(
        "supporting information" in text
        or "supplementary material" in text
        or "electronic supplementary material" in text
        or ("experimental section" in text and "typical procedure" in text)
    )


def is_patent_text(text: str) -> bool:
    patent_markers = (
        "patent",
        "发明专利",
        "专利申请",
        "中华人民共和国国家知识产权局",
        "российская федерация",
        "описание изобретения",
        "【特許請求の範囲】",
        "特許",
    )
    if any(marker in text for marker in patent_markers):
        return True
    return bool(re.search(r"\b(?:cn|jp|ru|us|wo|ep)\s*\d{4,}[a-z0-9 -]*(?:a|b|c1|a1)\b", text, re.IGNORECASE))


def is_scifinder_pdf_text(text: str) -> bool:
    return bool(
        re.search(r"31-\d{3}-cas-\d+", text, re.IGNORECASE)
        or "cas reaction number" in text
        or ("task history" in text and ("products" in text or "reactants" in text))
        or ("experimental protocols" in text and ("products" in text or "reactants" in text))
    )


class RouteService:
    def __init__(self, config: AppConfig | None = None, storage: RouteStorage | None = None):
        self.config = config or AppConfig.from_env()
        self.config.ensure_directories()
        self.storage_backend_status = self._resolve_storage_backend_status()
        if storage:
            self.storage = storage
        elif self.config.storage_backend == "postgres":
            if not self.config.postgres_url:
                raise ValueError("Integrations.postgres_url is required when server.storage_backend is postgres")
            self.storage = RouteStorage(self.config.postgres_url)
        else:
            self.storage = RouteStorage(self.config.database_path)
        self._migrate_legacy_providers()
        self._stop_event = threading.Event()
        self._workers: list[threading.Thread] = []
        self._active_jobs: set[str] = set()
        self._active_jobs_lock = threading.Lock()
        self._rdkit_install_job: dict[str, Any] | None = None
        self._rdkit_install_lock = threading.Lock()
        if self.config.async_jobs:
            self._start_workers()

    def _evidence_profile(self, evidence_kind: str, **extra: Any) -> dict[str, Any]:
        profile = dict(EVIDENCE_KIND_PROFILES.get(evidence_kind, EVIDENCE_KIND_PROFILES["unsupported_non_source"]))
        profile["evidence_kind"] = evidence_kind
        profile["import_action"] = "exclude" if profile["evidence_priority"] <= 0 else "include"
        profile.update(extra)
        return profile

    def _classify_document_evidence(self, path: Path) -> dict[str, Any]:
        suffix = path.suffix.lower()
        if suffix == ".rdf":
            return self._evidence_profile("scifinder_rdf", classifier="extension")
        if suffix in {".rtf", ".html", ".htm", ".mhtml", ".mht", ".md", ".markdown", ".txt"}:
            return self._evidence_profile("scifinder_readable", classifier="extension")
        if suffix == ".pdf":
            return self._classify_pdf_evidence(path)
        return self._evidence_profile(
            "unsupported_non_source",
            classifier="extension",
            exclude_reason=f"Unsupported evidence extension: {suffix or '<none>'}",
        )

    def _classify_pdf_evidence(self, path: Path) -> dict[str, Any]:
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError:
            return self._evidence_profile("scifinder_pdf", classifier="pdf_no_fitz")

        try:
            with fitz.open(path) as document:
                page_count = len(document)
                metadata = document.metadata or {}
                if page_count <= 0:
                    return self._evidence_profile(
                        "invalid_pdf",
                        classifier="pdf_metadata",
                        page_count=page_count,
                        exclude_reason="PDF has no readable pages",
                    )
                sample_pages = []
                for index in range(min(page_count, 3)):
                    sample_pages.append(document[index].get_text("text") or "")
        except Exception as exc:
            return self._evidence_profile(
                "invalid_pdf",
                classifier="pdf_open",
                exclude_reason=f"PDF could not be opened: {exc}",
            )

        sample_text = normalize_for_source_classification("\n".join(sample_pages))
        metadata_text = normalize_for_source_classification(
            " ".join(str(metadata.get(key) or "") for key in ("title", "author", "creator", "producer"))
        )
        combined = f"{metadata_text}\n{sample_text}"

        if "obsidian" in metadata_text:
            return self._evidence_profile(
                "user_note",
                classifier="pdf_metadata",
                page_count=page_count,
                exclude_reason="PDF appears to be an Obsidian/user note export",
            )

        if is_paper_si_text(combined):
            return self._evidence_profile("paper_si", classifier="pdf_text", page_count=page_count)

        if is_patent_text(combined):
            return self._evidence_profile("patent", classifier="pdf_text", page_count=page_count)

        if is_scifinder_pdf_text(combined):
            return self._evidence_profile("scifinder_pdf", classifier="pdf_text", page_count=page_count)

        return self._evidence_profile(
            "unsupported_non_source",
            classifier="pdf_text",
            page_count=page_count,
            exclude_reason="PDF does not look like SciFinder, paper SI, or patent reaction evidence",
        )

    def _document_evidence_profile(self, document_id: str) -> dict[str, Any]:
        document = self.storage.get_document(document_id)
        metadata = document.scifinder_metadata if document else {}
        return metadata if isinstance(metadata, dict) else {}

    def _pdf_only_link_confidence(self, document_id: str, fallback: float = 0.8) -> float:
        kind = self._document_evidence_profile(document_id).get("evidence_kind")
        if kind == "paper_si":
            return 0.9
        if kind == "patent":
            return 0.65
        if kind == "scifinder_pdf":
            return fallback
        return min(fallback, 0.5)

    def _pdf_only_provenance_warning(self, profile: dict[str, Any]) -> str:
        warning = str(profile.get("provenance_warning") or "")
        return warning or "PDF-only evidence needs review before final chemical use."

    def _document_manifest_text_summary(self, path: Path) -> dict[str, Any]:
        suffix = path.suffix.lower()
        text = ""
        summary: dict[str, Any] = {
            "page_count": None,
            "text_status": "not_sampled",
            "cas_reaction_numbers": [],
            "cas_count": 0,
        }
        if suffix == ".pdf":
            try:
                import fitz  # type: ignore[import-not-found]

                with fitz.open(path) as document:
                    summary["page_count"] = len(document)
                    sample_pages = []
                    for index in range(min(len(document), 8)):
                        sample_pages.append(document[index].get_text("text") or "")
                    text = "\n".join(sample_pages)
                    summary["text_status"] = "text_sampled" if text.strip() else "no_text_in_sample"
            except Exception as exc:
                summary["text_status"] = f"pdf_unreadable: {exc}"
        else:
            try:
                text = path.read_bytes()[: 512 * 1024].decode("utf-8", errors="replace")
                summary["text_status"] = "text_sampled" if text.strip() else "no_text"
            except Exception as exc:
                summary["text_status"] = f"text_unreadable: {exc}"

        cas_numbers = sorted({match.group(0).upper() for match in CAS_REACTION_NUMBER_RE.finditer(text)})
        summary["cas_reaction_numbers"] = cas_numbers[:50]
        summary["cas_count"] = len(cas_numbers)
        return summary

    def _preview_document_path(self, path: Path, *, reparse: bool = False) -> dict[str, Any]:
        resolved = path.resolve()
        base_row: dict[str, Any] = {
            "file_name": resolved.name,
            "file_path": str(resolved),
            "extension": resolved.suffix.lower(),
            "include": False,
            "import_action": "exclude",
            "source_path_exists": resolved.exists(),
            "has_exact_rdf_pair": False,
            "paired_rdf_name": "",
            "paired_pdf_name": "",
        }
        if not resolved.exists():
            return {
                **base_row,
                "reason": "file_not_found",
                "exclude_reason": "File does not exist",
                "text_status": "missing",
                "cas_count": 0,
                "cas_reaction_numbers": [],
            }
        if not resolved.is_file():
            return {
                **base_row,
                "reason": "not_a_file",
                "exclude_reason": "Path is not a file",
                "text_status": "not_a_file",
                "cas_count": 0,
                "cas_reaction_numbers": [],
            }

        stat = resolved.stat()
        try:
            self._assert_allowed_path(resolved)
            file_hash = hash_file(resolved)
            existing_by_path = self.storage.get_document_by_hash_path(file_hash=file_hash, file_path=str(resolved))
            existing_by_hash = self.storage.get_document_by_hash(file_hash)
            existing = existing_by_path or existing_by_hash
            profile = self._classify_document_evidence(resolved)
            text_summary = self._document_manifest_text_summary(resolved)
        except Exception as exc:
            return {
                **base_row,
                "size_bytes": stat.st_size,
                "reason": str(exc),
                "exclude_reason": str(exc),
                "text_status": "classification_failed",
                "cas_count": 0,
                "cas_reaction_numbers": [],
            }

        include = profile.get("import_action") == "include"
        if existing and not reparse:
            reason = "already_registered"
        elif include:
            reason = str(profile.get("label") or profile.get("evidence_kind") or "included")
        else:
            reason = str(profile.get("exclude_reason") or profile.get("label") or "excluded")
        return {
            **base_row,
            "size_bytes": stat.st_size,
            "sha256": file_hash,
            "include": include,
            "import_action": profile.get("import_action"),
            "evidence_kind": profile.get("evidence_kind"),
            "evidence_priority": profile.get("evidence_priority", 0),
            "label": profile.get("label"),
            "provenance_warning": profile.get("provenance_warning"),
            "exclude_reason": profile.get("exclude_reason", ""),
            "classifier": profile.get("classifier"),
            "page_count": profile.get("page_count", text_summary.get("page_count")),
            "cas_count": text_summary.get("cas_count", 0),
            "cas_reaction_numbers": text_summary.get("cas_reaction_numbers", []),
            "text_status": text_summary.get("text_status"),
            "already_registered": bool(existing),
            "existing_document_id": existing.id if existing else "",
            "duplicate_scope": "same_path" if existing_by_path else "same_hash" if existing_by_hash else "",
            "reason": reason,
        }

    def _add_exact_pair_signals(self, rows: list[dict[str, Any]]) -> None:
        selected_paths = {
            str(Path(str(row.get("file_path") or "")).resolve()).lower()
            for row in rows
            if row.get("source_path_exists") and row.get("file_path")
        }
        for row in rows:
            if not row.get("source_path_exists") or not row.get("file_path"):
                continue
            path = Path(str(row["file_path"])).resolve()
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                pair = path.with_suffix(".rdf")
                has_pair = str(pair.resolve()).lower() in selected_paths or pair.exists()
                row["has_exact_rdf_pair"] = has_pair
                row["paired_rdf_name"] = pair.name if has_pair else ""
            elif suffix == ".rdf":
                pair = path.with_suffix(".pdf")
                has_pair = str(pair.resolve()).lower() in selected_paths or pair.exists()
                row["has_exact_pdf_pair"] = has_pair
                row["paired_pdf_name"] = pair.name if has_pair else ""

    def preview_document_paths(self, paths: list[str], *, reparse: bool = False, limit: int = 500) -> dict[str, Any]:
        rows = [self._preview_document_path(Path(raw), reparse=reparse) for raw in paths[:limit]]
        self._add_exact_pair_signals(rows)
        included = sum(1 for row in rows if row.get("include"))
        excluded = len(rows) - included
        return {
            "items": rows,
            "total": len(rows),
            "included_count": included,
            "excluded_count": excluded,
            "truncated": len(paths) > limit,
        }

    def preview_inbox(self, *, reparse: bool = False, limit: int = 500) -> dict[str, Any]:
        supported = set(self.config.scan_extensions)
        paths = [
            str(path)
            for path in sorted(self.config.inbox_dir.rglob("*"))
            if path.is_file() and path.suffix.lower() in supported
        ]
        return self.preview_document_paths(paths, reparse=reparse, limit=limit)

    def preview_upload_document_bytes(self, content: bytes, filename: str, *, reparse: bool = False) -> dict[str, Any]:
        self._validate_upload_content(content, filename)
        safe_name = safe_filename(filename)
        preflight_dir = self.config.upload_dir / ".preflight"
        preflight_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(safe_name).suffix.lower()
        with tempfile.NamedTemporaryFile(prefix="preview-", suffix=suffix, dir=preflight_dir, delete=False) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        try:
            row = self._preview_document_path(temp_path, reparse=reparse)
            row.update(
                {
                    "file_name": safe_name,
                    "original_file_name": filename,
                    "file_path": safe_name,
                    "preview_only": True,
                }
            )
            return {"items": [row], "total": 1, "included_count": 1 if row.get("include") else 0, "excluded_count": 0 if row.get("include") else 1}
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Failed to remove upload preview temp file: %s", temp_path)

    def _json_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _reaction_link_has_conflicts(self, link: dict[str, Any]) -> bool:
        return bool(self._json_dict(link.get("conflict_flags_json")))

    def _document_link_summary(self, document_id: str | None) -> dict[str, Any]:
        if not document_id:
            return {}
        document = self.storage.get_document(document_id)
        if not document:
            return {"id": document_id}
        data = document.to_dict()
        metadata = data.get("scifinder_metadata") if isinstance(data.get("scifinder_metadata"), dict) else {}
        return {
            "id": data.get("id"),
            "file_name": Path(str(data.get("file_path") or "")).name,
            "file_path": data.get("file_path"),
            "file_type": data.get("file_type"),
            "title": data.get("title"),
            "ingest_status": data.get("ingest_status"),
            "evidence_kind": metadata.get("evidence_kind"),
            "evidence_priority": metadata.get("evidence_priority"),
            "evidence_label": metadata.get("label"),
            "provenance_warning": metadata.get("provenance_warning"),
        }

    def _enrich_reaction_link(self, link: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(link)
        pdf_document = self._document_link_summary(link.get("pdf_document_id"))
        rdf_document = self._document_link_summary(link.get("rdf_document_id"))
        evidence = self.storage.list_pdf_reaction_evidence(reaction_source_link_id=str(link.get("id") or ""), limit=1000)
        enriched["pdf_document"] = pdf_document
        enriched["rdf_document"] = rdf_document
        enriched["pdf_file_name"] = pdf_document.get("file_name", "")
        enriched["rdf_file_name"] = rdf_document.get("file_name", "")
        enriched["evidence_kind"] = pdf_document.get("evidence_kind") or rdf_document.get("evidence_kind")
        enriched["evidence_priority"] = pdf_document.get("evidence_priority") or rdf_document.get("evidence_priority")
        enriched["evidence_label"] = pdf_document.get("evidence_label") or rdf_document.get("evidence_label")
        enriched["provenance_warning"] = pdf_document.get("provenance_warning") or rdf_document.get("provenance_warning")
        enriched["pdf_evidence_count"] = len(evidence)
        enriched["pdf_evidence_pages"] = sorted({item.get("page_number") for item in evidence if item.get("page_number") is not None})
        enriched["has_conflicts"] = self._reaction_link_has_conflicts(link)
        enriched["conflict_flags"] = self._json_dict(link.get("conflict_flags_json"))
        return enriched

    def list_reaction_links(
        self,
        *,
        document_id: str = "",
        source_mode: str = "",
        needs_review: bool | None = None,
        evidence_kind: str = "",
        has_conflicts: bool | None = None,
        cas_reaction_number: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        raw_links = self.storage.list_reaction_source_links(
            document_id=document_id,
            source_mode=source_mode,
            needs_review=needs_review,
            cas_reaction_number=cas_reaction_number,
            limit=10000,
            offset=0,
        )
        enriched = [self._enrich_reaction_link(link) for link in raw_links]
        if evidence_kind:
            enriched = [link for link in enriched if str(link.get("evidence_kind") or "") == evidence_kind]
        if has_conflicts is not None:
            enriched = [link for link in enriched if bool(link.get("has_conflicts")) == has_conflicts]
        total = len(enriched)
        return {"items": enriched[offset : offset + limit], "total": total, "limit": limit, "offset": offset}

    def _trim_ai_evidence_text(self, value: Any, limit: int = 1800) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."

    def _compact_pdf_evidence_for_ai(self, evidence: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": evidence.get("id"),
            "page_number": evidence.get("page_number"),
            "is_primary": bool(evidence.get("is_primary")),
            "cas_reaction_number": evidence.get("cas_reaction_number"),
            "products_text": self._trim_ai_evidence_text(evidence.get("products_text"), 900),
            "reactants_text": self._trim_ai_evidence_text(evidence.get("reactants_text"), 900),
            "conditions_text": self._trim_ai_evidence_text(evidence.get("conditions_text"), 900),
            "procedure_text": self._trim_ai_evidence_text(evidence.get("procedure_text"), 1800),
            "yield_text": evidence.get("yield_text"),
            "reference_text": self._trim_ai_evidence_text(evidence.get("reference_text"), 700),
            "page_text_excerpt": self._trim_ai_evidence_text(evidence.get("page_text"), 1800),
            "extraction_method": evidence.get("extraction_method"),
            "match_confidence": evidence.get("match_confidence"),
        }

    def _compact_rdf_reaction_for_ai(self, rdf_reaction: dict[str, Any] | None) -> dict[str, Any]:
        if not rdf_reaction:
            return {}
        keys = [
            "id",
            "cas_reaction_number",
            "record_index",
            "scheme_id",
            "step_id",
            "yield_text",
            "reference_text",
            "doi",
            "raw_fields",
        ]
        compact = {key: rdf_reaction.get(key) for key in keys if key in rdf_reaction}
        if isinstance(compact.get("raw_fields"), dict):
            raw_fields = compact["raw_fields"]
            compact["raw_fields"] = {
                key: self._trim_ai_evidence_text(value, 700)
                for key, value in raw_fields.items()
                if any(marker in key for marker in ("CAS_Reaction_Number", "YIELD", "TXT", "REF", "SOL", "RGT", "CAT", "RCT", "PRO"))
            }
        return compact

    def _reaction_link_ai_review_payload(self, link: dict[str, Any]) -> dict[str, Any]:
        pdf_evidence = self.storage.list_pdf_reaction_evidence(reaction_source_link_id=str(link.get("id") or ""), limit=50)
        if not pdf_evidence and link.get("pdf_document_id") and link.get("cas_reaction_number"):
            pdf_evidence = self.storage.list_pdf_reaction_evidence(
                document_id=str(link["pdf_document_id"]),
                cas_reaction_number=str(link["cas_reaction_number"]),
                limit=50,
            )
        rdf_reaction = self.storage.get_rdf_reaction(str(link.get("rdf_reaction_id") or "")) if link.get("rdf_reaction_id") else None
        enriched = self._enrich_reaction_link(link)
        return {
            "link": {
                "id": link.get("id"),
                "cas_reaction_number": link.get("cas_reaction_number"),
                "source_mode": link.get("source_mode"),
                "primary_pdf_page": link.get("primary_pdf_page"),
                "link_confidence": link.get("link_confidence"),
                "link_method": link.get("link_method"),
                "needs_review": bool(link.get("needs_review")),
                "existing_conflict_flags": self._json_dict(link.get("conflict_flags_json")),
            },
            "evidence_policy": {
                "rdf_priority": "RDF is preferred for structured CAS, roles, yield, molecules, and references.",
                "paper_si_priority": "Paper SI procedures should outrank SciFinder PDF text for experimental procedure details.",
                "patent_scope": "Patent evidence must be labeled as patent reaction process evidence.",
                "ai_boundary": "AI review is advisory and cannot confirm or overwrite records automatically.",
            },
            "pdf_document": enriched.get("pdf_document", {}),
            "rdf_document": enriched.get("rdf_document", {}),
            "rdf_reaction": self._compact_rdf_reaction_for_ai(rdf_reaction),
            "pdf_evidence": [self._compact_pdf_evidence_for_ai(item) for item in pdf_evidence],
        }

    def analyze_reaction_link_with_ai(self, link_id: str) -> dict[str, Any]:
        if not getattr(self.config, "ai_evidence_review_enabled", False):
            raise RuntimeError("AI evidence review is disabled")
        link = self.storage.get_reaction_source_link(link_id)
        if not link:
            raise KeyError(f"Reaction source link not found: {link_id}")
        endpoint, model, provider, api_key, route_kind = self._ai_evidence_review_endpoint_settings()
        schema_version = getattr(self.config, "ai_evidence_review_schema_version", "reaction_evidence_review.v1")
        adapter = LLMStructuringAdapter(
            endpoint,
            model,
            enabled=bool(endpoint),
            schema_version=schema_version,
            prompt_profile=getattr(self.config, "ai_evidence_review_prompt_profile", "strict-evidence-review-json"),
            provider=provider,
            api_key=api_key,
        )
        if not adapter.configured:
            raise RuntimeError("AI evidence review provider is not configured")

        payload = self._reaction_link_ai_review_payload(link)
        review = adapter.review_reaction_evidence(payload)
        if not isinstance(review, dict):
            raise RuntimeError("AI endpoint returned no review JSON")

        recommendation = str(review.get("recommendation") or "needs_review").strip().lower()
        if recommendation not in {"confirm", "needs_review", "reject"}:
            recommendation = "needs_review"
        conflict_flags = review.get("conflict_flags") if isinstance(review.get("conflict_flags"), dict) else {}
        ai_review = {
            "schema_version": schema_version,
            "provider": provider,
            "model": model,
            "route_kind": route_kind,
            "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "recommendation": recommendation,
            "confidence": review.get("confidence"),
            "extracted_fields": review.get("extracted_fields") if isinstance(review.get("extracted_fields"), dict) else {},
            "agreement": review.get("agreement") if isinstance(review.get("agreement"), dict) else {},
            "conflict_flags": conflict_flags,
            "rationale": self._trim_ai_evidence_text(review.get("rationale"), 1200),
            "cited_evidence": review.get("cited_evidence") if isinstance(review.get("cited_evidence"), list) else [],
        }
        flags = self._json_dict(link.get("conflict_flags_json"))
        flags["ai_review"] = ai_review
        if conflict_flags:
            flags["ai_conflict_flags"] = conflict_flags

        update: dict[str, Any] = {"conflict_flags_json": flags}
        if recommendation != "confirm" or conflict_flags:
            update["needs_review"] = 1
        updated = self.storage.update_reaction_source_link(link_id, update)
        return {"ai_review": ai_review, "link": self._enrich_reaction_link(updated or link), "payload_summary": {"pdf_evidence_count": len(payload.get("pdf_evidence", [])), "has_rdf": bool(payload.get("rdf_reaction"))}}

    def bulk_update_reaction_links(self, link_ids: list[str], action: str) -> dict[str, Any]:
        normalized = action.strip().lower()
        results: list[dict[str, Any]] = []
        for link_id in [str(item) for item in link_ids if str(item)]:
            try:
                if normalized == "confirm":
                    updated = self.storage.update_reaction_source_link(link_id, {"needs_review": 0})
                    results.append({"id": link_id, "status": "confirmed" if updated else "missing"})
                elif normalized in {"unlink", "reject"}:
                    results.append({"id": link_id, "status": "unlinked", "result": self.unlink_reaction_source_link(link_id)})
                else:
                    raise ValueError(f"Unsupported bulk reaction-link action: {action}")
            except Exception as exc:
                results.append({"id": link_id, "status": "error", "error": str(exc)})
        return {
            "action": normalized,
            "items": results,
            "count": len(results),
            "error_count": sum(1 for item in results if item.get("status") == "error"),
        }

    def backfill_reaction_link_review(self, *, dry_run: bool = True, limit: int = 10000) -> dict[str, Any]:
        links = self.storage.list_reaction_source_links(limit=limit, offset=0)
        candidates: list[dict[str, Any]] = []
        for link in links:
            if int(link.get("needs_review") or 0):
                continue
            reasons = []
            if link.get("source_mode") in {"pdf_only", "pdf_only_low_confidence"}:
                reasons.append("pdf_only_evidence_requires_review")
            if link.get("source_mode") == "rdf_pdf_linked" and self._reaction_link_has_conflicts(link):
                reasons.append("linked_evidence_has_conflicts")
            if reasons:
                candidates.append({"link": self._enrich_reaction_link(link), "reasons": reasons, "target_update": {"needs_review": 1}})

        if not dry_run:
            for item in candidates:
                self.storage.update_reaction_source_link(str(item["link"]["id"]), {"needs_review": 1})

        return {
            "dry_run": dry_run,
            "candidate_count": len(candidates),
            "updated_count": 0 if dry_run else len(candidates),
            "items": candidates,
        }

    def _migrate_legacy_providers(self) -> None:
        from .config import read_config_yaml, write_config_yaml, AiProvider
        target = self.config.webui_config_path or self.config.data_dir / "webui-config.yaml"
        target = Path(target)
        if not target.exists():
            return
            
        data = read_config_yaml(target)
        integrations = data.get("integrations", {})
        if not isinstance(integrations, dict):
            return
        dirty = False
        
        # Legacy flat key migration
        legacy_keys = [
            ("llm_endpoint", "llm_api_key", "llm_model", "llm_provider", "legacy-extraction", ["gpt-4o-mini", "claude-3-5-sonnet-20240620"], "extraction_provider_id", "extraction_model"),
            ("embedding_endpoint", "embedding_api_key", "embedding_model", "openai_compatible", "legacy-embedding", ["text-embedding-3-small"], "embedding_provider_id", "embedding_model"),
            ("ocr_endpoint", "ocr_api_key", "ocr_model", "ocr_provider", "legacy-ocr", ["paddleocr_vl", "got_ocr2_vl"], "ocr_provider_id", "ocr_model"),
            ("document_parser_endpoint", "document_parser_api_key", "document_parser_model", "document_parser_provider", "legacy-document_parser", ["doc2x", "marker"], "document_parser_provider_id", "document_parser_model"),
            ("structure_recognition_endpoint", "structure_recognition_api_key", "structure_recognition_model", "structure_recognition_provider", "legacy-structure_recognition", ["molar"], "structure_recognition_provider_id", "structure_recognition_model"),
        ]
        
        for ep_key, apik_key, mod_key, prov_key, provider_id, avail_models, pid_key, mod_config_key in legacy_keys:
            if ep_key in integrations:
                model_val = integrations.get(mod_key)
                if not self.storage.get_ai_provider(provider_id):
                    p = AiProvider(
                        id=provider_id,
                        name=provider_id.replace("legacy-", "").title() + " Provider",
                        format=integrations.get(prov_key, "openai_compatible"),
                        endpoint=integrations[ep_key],
                        api_key=integrations.get(apik_key) or "",
                        available_models=tuple(avail_models),
                        enabled_models=tuple([model_val] if model_val else [])
                    )
                    self.storage.upsert_ai_provider(p)
                del integrations[ep_key]
                integrations.pop(apik_key, None)
                integrations.pop(mod_key, None)
                integrations.pop(prov_key, None)
                if pid_key not in integrations:
                    integrations[pid_key] = provider_id
                if model_val and mod_config_key not in integrations:
                    integrations[mod_config_key] = model_val
                dirty = True
        
        if "ai_providers" in integrations:
            existing_providers = self.storage.list_ai_providers()
            if not existing_providers:
                for p_dict in integrations["ai_providers"]:
                    try:
                        self.storage.upsert_ai_provider(AiProvider(**p_dict))
                    except Exception:
                        pass
            del integrations["ai_providers"]
            dirty = True
            
        if "zotero_mcp_endpoints" in integrations:
            existing_endpoints = self.storage.list_zotero_endpoints()
            if not existing_endpoints:
                for z_dict in integrations["zotero_mcp_endpoints"]:
                    try:
                        self.storage.upsert_zotero_endpoint(z_dict)
                    except Exception:
                        pass
            del integrations["zotero_mcp_endpoints"]
            dirty = True
            
        if dirty:
            write_config_yaml(target, data)

    def get_config(self, include_secrets: bool = False) -> dict[str, Any]:
        cfg = self.config.effective_config(include_secrets=include_secrets)
        if "integrations" not in cfg:
            cfg["integrations"] = {}
        
        cfg["integrations"]["ai_providers"] = [p.to_dict() for p in self.storage.list_ai_providers()]
        if not include_secrets:
            for p in cfg["integrations"]["ai_providers"]:
                if p.get("api_key"):
                    p["api_key"] = "****"

        cfg["integrations"]["zotero_mcp_endpoints"] = self.storage.list_zotero_endpoints(include_headers=include_secrets)
        
        return cfg

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        if "integrations" in updates:
            from .config import AiProvider
            integrations = updates["integrations"]
            
            if "ai_providers" in integrations:
                incoming_ids = {p.get("id") for p in integrations["ai_providers"] if p.get("id")}
                existing_ids = {p.id for p in self.storage.list_ai_providers()}
                
                for p_dict in integrations["ai_providers"]:
                    try:
                        if p_dict.get("api_key") == "****":
                            existing = self.storage.get_ai_provider(p_dict["id"])
                            if existing:
                                p_dict["api_key"] = existing.api_key
                        self.storage.upsert_ai_provider(AiProvider(**p_dict))
                    except Exception as e:
                        LOGGER.warning("Failed to upsert ai_provider from config UI: %s", e)
                        
                for to_delete in existing_ids - incoming_ids:
                    self.storage.delete_ai_provider(to_delete)
                del integrations["ai_providers"]
            
            if "zotero_mcp_endpoints" in integrations:
                incoming_ids = {z.get("id") for z in integrations["zotero_mcp_endpoints"] if z.get("id")}
                existing_ids = {z["id"] for z in self.storage.list_zotero_endpoints()}
                
                for z_dict in integrations["zotero_mcp_endpoints"]:
                    try:
                        if any(v == "****" for v in z_dict.get("headers", {}).values()):
                            existing = next((item for item in self.storage.list_zotero_endpoints(include_headers=True) if item["id"] == z_dict["id"]), None)
                            if existing:
                                z_dict["headers"] = existing.get("headers", {})
                        self.storage.upsert_zotero_endpoint(z_dict)
                    except Exception as e:
                        LOGGER.warning("Failed to upsert zotero endpoint from config UI: %s", e)
                        
                for to_delete in existing_ids - incoming_ids:
                    self.storage.delete_zotero_endpoint(to_delete)
                del integrations["zotero_mcp_endpoints"]
                
        self.config.write_hot_config(updates)
        return self.reload_config()

    def reload_config(self) -> dict[str, Any]:
        old_async = self.config.async_jobs
        old_workers = self.config.max_workers
        self.config = self.config.apply_file_overrides()
        self.config.ensure_directories()
        self.storage_backend_status = self._resolve_storage_backend_status()
        if old_async != self.config.async_jobs or old_workers != self.config.max_workers:
            self._restart_workers()
        return self.get_config()

    def validate_config(self) -> dict[str, Any]:
        warnings = self.config.validate()
        if getattr(self.config, "ai_evidence_review_enabled", False):
            endpoint, _model, _provider, _api_key, route_kind = self._ai_evidence_review_endpoint_settings()
            if not endpoint:
                warnings.append("integrations.ai_evidence_review_enabled=true requires integrations.ai_evidence_review_provider_id or integrations.extraction_provider_id")
            elif route_kind == "extraction" and not getattr(self.config, "ai_evidence_review_provider_id", None):
                warnings.append("integrations.ai_evidence_review_provider_id is not set; AI evidence review will use the extraction provider fallback")
        return {
            "valid": not warnings,
            "warnings": warnings,
            "hot_reloadable_sections": ["server", "security", "ingest", "integrations", "thresholds", "queue", "extraction", "retention"],
            "restart_required_for": [
                "SCIFINDER_ROUTE_PUBLISHED_PORT",
                "SCIFINDER_ROUTE_PORT",
                "SCIFINDER_ROUTE_TRANSPORT",
                "SCIFINDER_ROUTE_MCP_PATH",
                "SCIFINDER_ROUTE_SSE_PATH",
                "volume mounts",
                "container network",
                "runtime pip-installed packages such as RDKit",
            ],
            "runtime_install_note": "Packages installed from the Web UI are applied to the running container only. Re-pulling or recreating the container can remove them unless the image itself includes the package or the Python package directory is persistently mounted.",
        }

    def register_document(self, file_path: str, reparse: bool = False) -> dict[str, Any]:
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Document does not exist: {path}")
        self._assert_allowed_path(path)
        evidence_profile = self._classify_document_evidence(path)
        if evidence_profile.get("import_action") == "exclude":
            reason = evidence_profile.get("exclude_reason") or evidence_profile.get("label") or evidence_profile.get("evidence_kind")
            raise ValueError(f"Document excluded by evidence classifier: {reason}")
        file_hash = hash_file(path)
        existing = self.storage.get_document_by_hash_path(file_hash=file_hash, file_path=str(path))
        if existing and not reparse:
            return {"document": existing.to_dict(), "job": None, "deduplicated": True}
        parsed = None if self.config.async_jobs else self._parse_with_optional_external(path)
        document, job = self.storage.create_queued_document_job(
            file_path=str(path),
            file_hash=file_hash,
            file_type=parsed.file_type if parsed else detect_file_type(path),
            title=parsed.title if parsed else None,
            doi=parsed.doi if parsed else None,
        )
        self.storage.update_document_scifinder_metadata(document.id, evidence_profile)
        self._start_or_run_job(document.id, job.id, parsed=parsed, reparse=reparse)
        completed_job = self.storage.get_job(job.id)
        return {"document": self.storage.get_document(document.id).to_dict(), "job": completed_job.to_dict() if completed_job else job.to_dict()}

    def upload_document(self, source_path: str, filename: str | None = None, reparse: bool = False) -> dict[str, Any]:
        source = Path(source_path).resolve()
        if not source.exists():
            raise FileNotFoundError(f"Upload source does not exist: {source}")
        self._assert_allowed_path(source)
        safe_name = safe_filename(filename or source.name)
        source_hash = hash_file(source)
        existing = self.storage.get_document_by_hash(source_hash)
        if existing and not reparse:
            return {"document": existing.to_dict(), "job": None, "deduplicated": True, "uploaded_path": existing.file_path}
        target = unique_target(self.config.upload_dir / safe_name)
        shutil.copy2(source, target)
        result = self.register_document(str(target), reparse=reparse)
        result["uploaded_path"] = str(target)
        return result

    def upload_document_bytes(self, content: bytes, filename: str, reparse: bool = False) -> dict[str, Any]:
        self._validate_upload_content(content, filename)
        digest = hashlib.sha256(content).hexdigest()
        existing = self.storage.get_document_by_hash(digest)
        if existing and not reparse:
            return {"document": existing.to_dict(), "job": None, "deduplicated": True, "uploaded_path": existing.file_path}
        target = unique_target(self.config.upload_dir / safe_filename(filename))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        result = self.register_document(str(target), reparse=reparse)
        result["uploaded_path"] = str(target)
        result["sha256"] = digest
        return result

    def upload_document_content(self, filename: str, content_base64: str, reparse: bool = False) -> dict[str, Any]:
        estimated = (len(content_base64.strip()) * 3) // 4
        if estimated > self.config.upload_max_bytes + 3:
            raise ValueError(f"Upload exceeds configured limit of {self.config.upload_max_bytes} bytes")
        try:
            content = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise ValueError("content_base64 is not valid base64") from exc
        return self.upload_document_bytes(content, filename, reparse=reparse)

    def scan_inbox(self, reparse: bool = False, limit: int = 500) -> dict[str, Any]:
        supported = set(self.config.scan_extensions)
        registered: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []
        for path in sorted(self.config.inbox_dir.rglob("*")):
            if len(registered) >= limit:
                break
            if not path.is_file() or path.suffix.lower() not in supported:
                continue
            resolved = path.resolve()
            try:
                file_hash = hash_file(resolved)
                existing = self.storage.get_document_by_hash_path(file_hash=file_hash, file_path=str(resolved))
                if existing and not reparse:
                    skipped.append({"file_path": str(resolved), "reason": "already_registered", "document_id": existing.id})
                    continue
                registered.append(self.register_document(str(resolved), reparse=reparse))
            except Exception as exc:
                skipped.append({"file_path": str(resolved), "reason": str(exc)})
        return {"registered": registered, "skipped": skipped, "registered_count": len(registered), "skipped_count": len(skipped)}

    def get_parse_job_status(self, job_id: str) -> dict[str, Any]:
        job = self.storage.get_job(job_id)
        if not job:
            raise KeyError(f"Parse job not found: {job_id}")
        return job.to_dict()

    def list_parse_jobs(self, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return [job.to_dict() for job in self.storage.list_jobs(status=status, limit=limit)]

    def list_documents(self, query: str = "", file_type: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.storage.list_documents(query=query, file_type=file_type, limit=limit, offset=offset)

    def get_document_parse_result(self, document_id: str, *, chunk_limit: int = 50, chunk_offset: int = 0, reaction_limit: int = 100) -> dict[str, Any]:
        document = self.storage.get_document(document_id)
        if not document:
            raise KeyError(f"Document not found: {document_id}")
        chunks = self.storage.list_parsed_chunks(document_id, limit=chunk_limit, offset=chunk_offset)
        reactions = [step.to_dict() for step in self.storage.list_reaction_steps_for_document(document_id, limit=reaction_limit)]
        latest_job = self.storage.get_latest_job_for_document(document_id)
        return {"document": document.to_dict(), "latest_job": latest_job.to_dict() if latest_job else None, "chunks": chunks, "reaction_steps": reactions}

    def list_document_parsed_chunks(self, document_id: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        if not self.storage.get_document(document_id):
            raise KeyError(f"Document not found: {document_id}")
        return self.storage.list_parsed_chunks(document_id, limit=limit, offset=offset)

    def retry_parse_job(self, job_id: str) -> dict[str, Any]:
        job = self.storage.retry_job(job_id)
        return job.to_dict()

    def retry_failed_jobs(self, limit: int = 100) -> dict[str, Any]:
        jobs = self.storage.retry_failed_jobs(limit=limit)
        return {"retried": [job.to_dict() for job in jobs], "count": len(jobs)}

    def health_check(self) -> dict[str, Any]:
        webui_config_path = self.config.webui_config_path or self.config.data_dir / "webui-config.yaml"
        health: dict[str, Any] = {
            "status": "ok",
            "database": str(self.config.database_path),
            "data_dir": str(self.config.data_dir),
            "inbox_dir": str(self.config.inbox_dir),
            "upload_dir": str(self.config.upload_dir),
            "config_path": str(self.config.config_path),
            "webui_config_path": str(webui_config_path),
            "active_config_path": str(webui_config_path if webui_config_path.exists() else self.config.config_path),
            "async_jobs": self.config.async_jobs,
            "queue_backend": self.config.queue_backend,
            "storage_backend": self.config.storage_backend,
            "storage_backend_status": self.storage_backend_status,
            "scan_extensions": list(self.config.scan_extensions),
            "config_warnings": self.config.validate(),
        }
        try:
            health.update(
                {
                    "documents": self.storage.count_documents(),
                    "reaction_steps": self.storage.count_reaction_steps(),
                    "vector_index": self.storage.vector_index_status(),
                    "ocr_backlog": self.storage.count_ocr_backlog(),
                    "integrations": self.storage.list_integration_statuses(),
                    "zotero_endpoints": self.list_zotero_mcp_endpoints(),
                }
            )
        except Exception as exc:
            if not is_sqlite_locked_error(exc):
                raise
            health.update(
                {
                    "status": "degraded",
                    "database_error": "database is locked",
                    "documents": None,
                    "reaction_steps": None,
                    "vector_index": {"indexed": None, "errors": None},
                    "ocr_backlog": None,
                    "integrations": [],
                    "zotero_endpoints": [],
                }
            )
        return health

    def shutdown(self) -> None:
        self._stop_event.set()
        for worker in self._workers:
            worker.join(timeout=5)
        self._workers = []

    def search_reaction_steps(self, query: str = "", reagent: str = "", solvent: str = "", document_id: str = "", min_confidence: float = 0.0, limit: int = 10) -> list[dict[str, Any]]:
        steps = self.storage.search_reaction_steps(query=query, reagent=reagent, solvent=solvent, document_id=document_id, min_confidence=min_confidence, limit=limit)
        results = []
        for step in steps:
            data = step.to_dict()
            batch_links = self.storage.list_batches_for_document(step.source_document_id)
            data["batch_links"] = batch_links
            metadata = step.metadata or {}
            
            # Retrieve reaction link information
            if metadata.get("structured_source") == "rdf" and metadata.get("rdf_reaction_id"):
                link = self.storage.get_reaction_source_link_by_rdf(metadata.get("rdf_reaction_id"))
                if link:
                    data["reaction_source_link_id"] = link["id"]
                    data["source_mode"] = link["source_mode"]
                    data["needs_review"] = link["needs_review"]
                    data["link_confidence"] = link["link_confidence"]

            if metadata.get("structured_source") == "rdf" and not batch_links:
                data["provenance_warning"] = "RDF structured reaction has no linked readable/visual export batch; verify against PDF/RTF/HTML before final chemical judgment."
            elif metadata.get("has_visual_evidence"):
                data["provenance_warning"] = "This result has linked visual chemical evidence; inspect provenance images when making structure-sensitive judgments."
            results.append(data)

        # Append PDF-only items at the end up to the total limit
        if len(results) < limit:
            pdf_links_data = self.storage.search_pdf_only_reaction_links(document_id=document_id, limit=10000)
            for link, evidences in pdf_links_data:
                if not evidences:
                    continue
                evidence_profile = self._document_evidence_profile(str(link.get("pdf_document_id") or ""))
                evidence_kind = str(evidence_profile.get("evidence_kind") or "pdf_only")
                # Filter by min_confidence
                if min_confidence and link.get("link_confidence", 0.0) < min_confidence:
                    continue
                primary_evidence = evidences[0]
                # Filter by query/reagent/solvent against evidence text
                searchable_text = " ".join(filter(None, [
                    primary_evidence.get("page_text", ""),
                    primary_evidence.get("reactants_text", ""),
                    primary_evidence.get("products_text", ""),
                    primary_evidence.get("conditions_text", ""),
                    primary_evidence.get("procedure_text", ""),
                    link.get("cas_reaction_number", ""),
                ])).lower()
                if query and query.lower() not in searchable_text:
                    continue
                if reagent and reagent.lower() not in searchable_text:
                    continue
                if solvent and solvent.lower() not in searchable_text:
                    continue
                pseudo_step = {
                    "id": f"pdf_pseudo_{link['id']}",
                    "record_type": "pdf_only",
                    "source_mode": link["source_mode"],
                    "reaction_source_link_id": link["id"],
                    "pdf_evidence_ids": [e["id"] for e in evidences],
                    "source_document_id": link["pdf_document_id"],
                    "primary_pdf_page": link["primary_pdf_page"],
                    "evidence_kind": evidence_kind,
                    "evidence_priority": evidence_profile.get("evidence_priority"),
                    "provenance_warning": self._pdf_only_provenance_warning(evidence_profile),
                    "verification_status": "needs_review" if link.get("needs_review") else "pdf_only_unverified",
                    "reaction_name": "Patent Reaction Process" if evidence_kind == "patent" else "PDF Evidence (Unstructured)",
                    "reagent_text": primary_evidence.get("reactants_text"),
                    "product_text": primary_evidence.get("products_text"),
                    "solvent_text": primary_evidence.get("conditions_text"),
                    "original_text": primary_evidence.get("page_text"),
                    "confidence": link.get("link_confidence", 0.0),
                    "step_index": 0,
                    "metadata": {"evidence_profile": evidence_profile}
                }
                results.append(pseudo_step)
                if len(results) >= limit:
                    break

        return results

    def get_reaction_step(self, reaction_step_id: str) -> dict[str, Any]:
        step = self.storage.get_reaction_step(reaction_step_id)
        if not step:
            raise KeyError(f"Reaction step not found: {reaction_step_id}")
        return step.to_dict()

    def get_reaction_provenance(self, reaction_step_id: str) -> list[dict[str, Any]]:
        if not self.storage.get_reaction_step(reaction_step_id):
            raise KeyError(f"Reaction step not found: {reaction_step_id}")
        return [item.to_dict() for item in self.storage.get_provenance(reaction_step_id)]

    def reparse_document(self, document_id: str) -> dict[str, Any]:
        document = self.storage.get_document(document_id)
        if not document:
            raise KeyError(f"Document not found: {document_id}")
        job = self.storage.create_job(document.id)
        self._start_or_run_job(document.id, job.id, reparse=True)
        completed_job = self.storage.get_job(job.id)
        return {"document": self.storage.get_document(document.id).to_dict(), "job": completed_job.to_dict() if completed_job else job.to_dict()}

    def run_manual_structure_recognition(self, document_id: str) -> dict[str, Any]:
        if not getattr(self.config, 'structure_recognition_manual_enabled', False):
            raise ValueError("Manual structure recognition is disabled in config.")
            
        document = self.storage.get_document(document_id)
        if not document:
            raise KeyError(f"Document not found: {document_id}")
        job = self.storage.create_job(document.id)
        
        def _task():
            try:
                self.storage.update_job(job.id, status="running", stage="vision_structure_extraction")
                visual_metadata = self._extract_visual_evidence(Path(document.file_path), document_id)
                struct_endpoint, _, _, _ = self._integration_endpoint_settings("structure_recognition")
                if not struct_endpoint:
                    self.storage.update_job(job.id, status="failed", error="structure_recognition integration not configured")
                    return
                if not visual_metadata or not visual_metadata.get("rendered_paths"):
                    self.storage.update_job(job.id, status="failed", error="No rendered images found for document")
                    return
                vision_smiles = []
                for img_path in visual_metadata["rendered_paths"]:
                    try:
                        structs = self.recognize_structure_image(img_path)
                        if structs.get("status") == "success":
                            for c in structs.get("compounds", []):
                                if c.get("smiles"):
                                    vision_smiles.append(c["smiles"])
                    except Exception as e:
                        print(f"Error in recognize_structure_image: {e}")
                if vision_smiles:
                    vision_text = "Extracted molecular structures from document images (manual):\n" + "\n".join(vision_smiles)
                    import dataclasses
                    from .parsers import TextChunk
                    new_chunk = TextChunk(text=vision_text, page_number=None, parser_name="vision_llm")
                    
                    existing = self.storage.list_parsed_chunks(document_id, limit=10000).get("chunks", [])
                    class DummyChunk:
                        def __init__(self, d):
                            self.page_number = d.get("page_number")
                            self.text = d.get("text") or ""
                            self.parser_name = d.get("parser_name") or ""
                            self.parser_version = d.get("parser_version") or "unknown"
                    merged_chunks = [DummyChunk(c) for c in existing] + [new_chunk]
                    self.storage.replace_parsed_chunks(document_id, merged_chunks)
                self.storage.update_job(job.id, status="completed", stage="done")
            except Exception as e:
                self.storage.update_job(job.id, status="failed", error=str(e))
                
        if getattr(self.config, "async_jobs", True):
            import threading
            threading.Thread(target=_task, daemon=True).start()
        else:
            _task()
            
        completed_job = self.storage.get_job(job.id)
        return completed_job.to_dict() if completed_job else {}

    def record_doi_verification(self, reaction_step_id: str, doi: str, verified_fields: dict[str, Any], paper_title: str | None = None, original_paper_excerpt: str | None = None, verification_confidence: float = 0.0, verifier_agent: str | None = None) -> dict[str, Any]:
        if not self.storage.get_reaction_step(reaction_step_id):
            raise KeyError(f"Reaction step not found: {reaction_step_id}")
        return self.storage.record_doi_verification(
            reaction_step_id=reaction_step_id,
            doi=doi,
            verified_fields=verified_fields,
            paper_title=paper_title,
            original_paper_excerpt=original_paper_excerpt,
            verification_confidence=verification_confidence,
            verifier_agent=verifier_agent,
        )

    def export_evaluation_set(self, output_path: str | None = None, limit: int = 500) -> dict[str, Any]:
        target = Path(output_path).resolve() if output_path else self.config.data_dir / "evaluation_set.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with target.open("w", encoding="utf-8") as handle:
            for row in self.storage.export_evaluation_rows(limit=limit):
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                count += 1
        return {"output_path": str(target), "rows": count}

    def compute_evaluation_metrics(self, gold_set_path: str) -> dict[str, Any]:
        path = Path(gold_set_path).resolve()
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not records:
            metrics = {"step_recall": 0, "field_accuracy": 0, "doi_match_rate": 0, "provenance_completeness": 0, "confidence_calibration": 0, "records": 0}
            return self.storage.record_evaluation_metrics(str(path), metrics)
        found = 0
        field_total = 0
        field_correct = 0
        doi_total = 0
        doi_correct = 0
        provenance_ok = 0
        calibration_error = 0.0
        for record in records:
            query = record.get("query") or record.get("original_text") or record.get("yield_text") or ""
            hits = self.search_reaction_steps(query=str(query)[:120], limit=1) if query else []
            expected_fields = record.get("fields") if isinstance(record.get("fields"), dict) else record
            if hits:
                found += 1
                hit = hits[0]
                provenance_ok += 1 if self.get_reaction_provenance(hit["id"]) else 0
                calibration_error += abs(float(hit.get("confidence") or 0) - float(record.get("confidence", hit.get("confidence") or 0)))
                for key, expected in expected_fields.items():
                    if key not in hit or key in {"id", "source_document_id", "original_text"}:
                        continue
                    field_total += 1
                    if expected is None or str(expected).strip().lower() in str(hit.get(key) or "").strip().lower():
                        field_correct += 1
                if record.get("doi"):
                    doi_total += 1
                    doc_id = hit.get("source_document_id")
                    # DOI lives on source document; exported rows include doi for direct comparison.
                    doi_correct += 1 if str(record.get("doi")).lower() in json.dumps(hit).lower() or doc_id else 0
        metrics = {
            "step_recall": found / len(records),
            "field_accuracy": field_correct / field_total if field_total else 0,
            "doi_match_rate": doi_correct / doi_total if doi_total else 0,
            "provenance_completeness": provenance_ok / len(records),
            "confidence_calibration": 1 - (calibration_error / found) if found else 0,
            "records": len(records),
        }
        return self.storage.record_evaluation_metrics(str(path), metrics)

    def get_evaluation_status(self) -> dict[str, Any]:
        return {"latest": self.storage.latest_evaluation_metrics()}

    def rebuild_vector_index(self, limit: int = 10000) -> dict[str, Any]:
        endpoint, model, _fmt, api_key = self._integration_endpoint_settings("embedding")
        adapter = EmbeddingAdapter(endpoint, model, api_key)
        if not adapter.configured:
            return {"configured": False, "status": "skipped", "reason": "embedding endpoint is not configured", **self.storage.vector_index_status()}
        indexed = 0
        errors: list[str] = []
        steps = self.storage.list_reaction_steps_for_index(limit=limit)
        for step in steps:
            text = "\n".join(str(value or "") for value in [step.reaction_name, step.substrate_text, step.product_text, step.reagent_text, step.solvent_text, step.original_text])
            try:
                vector = adapter.embed([text])[0]
                self.storage.upsert_embedding(step.id, model=adapter.model, embedding=vector)
                indexed += 1
            except Exception as exc:
                errors.append(f"{step.id}: {exc}")
                self.storage.upsert_embedding(step.id, model=adapter.model, embedding=[], error=str(exc))
        return {"configured": True, "status": "completed" if not errors else "partial_failed", "indexed": indexed, "errors": errors[:20], **self.storage.vector_index_status()}

    def get_vector_index_status(self) -> dict[str, Any]:
        endpoint, _, _, _ = self._integration_endpoint_settings("embedding")
        return {"configured": bool(endpoint), **self.storage.vector_index_status()}

    def semantic_search_reaction_steps(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        endpoint, model, _fmt, api_key = self._integration_endpoint_settings("embedding")
        adapter = EmbeddingAdapter(endpoint, model, api_key)
        if not adapter.configured:
            return []
        vector = adapter.embed([query])[0]
        return [{**step.to_dict(), "semantic_score": score} for step, score in self.storage.semantic_search(vector, limit=limit)]

    def test_integration_endpoint(self, kind: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        if kind == "postgres":
            result = self._test_postgres(self._override_value(overrides, "integrations", "postgres_url", self.config.postgres_url))
            return self.storage.record_integration_status("postgres", **result)
        if kind == "zotero_mcp":
            endpoints = self.storage.list_zotero_endpoints()
            if not endpoints:
                return self.storage.record_integration_status("zotero_mcp", configured=False, status="unknown", detail="No Zotero MCP endpoints configured")
            results = [self.test_zotero_mcp_endpoint(endpoint["id"]) for endpoint in endpoints]
            ok = [item for item in results if item.get("status") == "ok"]
            detail = json.dumps(results, ensure_ascii=False)
            return self.storage.record_integration_status("zotero_mcp", configured=True, status="ok" if ok else "error", detail=detail[:1000])
        test_kind = kind
        if kind == "ai_evidence_review":
            enabled = self._override_bool(
                overrides,
                "integrations",
                "ai_evidence_review_enabled",
                getattr(self.config, "ai_evidence_review_enabled", False),
            )
            if not enabled:
                return self.storage.record_integration_status(kind, configured=False, status="disabled", detail="AI evidence review is disabled")
            endpoint, model, provider, api_key, route_kind = self._ai_evidence_review_endpoint_settings(overrides)
            test_kind = route_kind
        else:
            endpoint, model, provider, api_key = self._integration_endpoint_settings(kind, overrides)
        result = test_http_endpoint(endpoint, model=model, provider=provider, api_key=api_key, kind=test_kind)
        detail = result.detail
        if kind == "ai_evidence_review" and test_kind == "extraction" and result.configured:
            detail = f"{detail} (using extraction provider fallback)"
        if result.status != "ok":
            LOGGER.warning("Integration endpoint test failed kind=%s provider=%s configured=%s detail=%s", kind, provider, result.configured, result.detail)
        else:
            LOGGER.info("Integration endpoint test succeeded kind=%s provider=%s", kind, provider)
        return self.storage.record_integration_status(kind, configured=result.configured, status=result.status, detail=detail)

    def list_integration_models(self, kind: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        endpoint, model, provider_format, api_key = self._integration_endpoint_settings(kind, overrides)
        provider_id = self._override_value(overrides, "integrations", f"{kind}_provider_id", getattr(self.config, f"{kind}_provider_id", None))
        if kind == "extraction":
            provider_id = self._override_value(overrides, "integrations", "extraction_provider_id", getattr(self.config, "extraction_provider_id", None))
        provider = self._resolve_provider(provider_id, overrides)
        
        models_endpoint = provider.models_endpoint if provider else None
        
        result = list_http_models(endpoint, provider=provider_format, api_key=api_key, kind=kind, model=model, models_endpoint=models_endpoint)
        if result.status != "ok":
            LOGGER.warning("Integration model listing failed kind=%s provider=%s configured=%s detail=%s", kind, provider_format, result.configured, result.detail)
        payload = result.payload if isinstance(result.payload, dict) else {}
        return {"kind": kind, "configured": result.configured, "status": result.status, "detail": result.detail, "models": payload.get("models", [])}

    def test_provider_endpoint(self, provider_id: str) -> dict[str, Any]:
        provider = self._resolve_provider(provider_id)
        if not provider:
            return {"status": "error", "detail": "Provider not found"}
        # Use model listing as connectivity test – the same path that
        # "fetch models" uses.  Most OpenAI-compatible / Gemini / Claude
        # providers expose /models but not /health, so a generic health
        # check almost always fails while model listing succeeds.
        result = list_http_models(
            provider.endpoint,
            provider=provider.format,
            api_key=provider.api_key,
            kind="llm",
            models_endpoint=provider.models_endpoint,
        )
        if result.status == "ok":
            model_count = len((result.payload or {}).get("models", []))
            detail = f"Endpoint reachable – {model_count} model(s) available"
        else:
            detail = result.detail
        return {"configured": result.configured, "status": result.status, "detail": detail}

    def list_provider_models(self, provider_id: str) -> dict[str, Any]:
        provider = self._resolve_provider(provider_id)
        if not provider:
            return {"status": "error", "detail": "Provider not found", "models": []}
        result = list_http_models(provider.endpoint, provider=provider.format, api_key=provider.api_key, kind="generic", models_endpoint=provider.models_endpoint)
        payload = result.payload if isinstance(result.payload, dict) else {}
        return {"status": result.status, "detail": result.detail, "models": payload.get("models", [])}

    def update_provider_models(self, provider_id: str, available_models: list[str], enabled_models: list[str]) -> dict[str, Any]:
        provider = self.storage.get_ai_provider(provider_id)
        if provider:
            from .config import AiProvider
            updated = AiProvider(
                id=provider.id,
                name=provider.name,
                format=provider.format,
                endpoint=provider.endpoint,
                api_key=provider.api_key,
                models_endpoint=provider.models_endpoint,
                available_models=tuple(available_models),
                enabled_models=tuple(enabled_models)
            )
            self.storage.upsert_ai_provider(updated)
            return {"status": "ok"}
        return {"status": "error", "detail": "Provider not found"}

    def get_provider_enabled_models(self, provider_id: str) -> list[str]:
        provider = self._resolve_provider(provider_id)
        if provider:
            return list(provider.enabled_models)
        return []

    def _resolve_provider(self, provider_id: str | None, overrides: dict[str, Any] | None = None) -> Any | None:
        if not provider_id:
            return None
        if isinstance(overrides, dict) and isinstance(overrides.get("integrations"), dict):
            ai_providers_list = overrides["integrations"].get("ai_providers")
            if ai_providers_list:
                from .config import parse_ai_providers, _unmask_value
                import dataclasses
                for p_dict in ai_providers_list:
                    if isinstance(p_dict, dict) and p_dict.get("id") == provider_id:
                        parsed = parse_ai_providers([p_dict])[0]
                        stored = self.storage.get_ai_provider(provider_id)
                        if stored and parsed.api_key and stored.api_key:
                            unmasked_key = _unmask_value(parsed.api_key, stored.api_key)
                            if unmasked_key != parsed.api_key:
                                parsed = dataclasses.replace(parsed, api_key=unmasked_key)
                        return parsed
        return self.storage.get_ai_provider(provider_id)

    def _integration_endpoint_settings(self, kind: str, overrides: dict[str, Any] | None = None) -> tuple[str | None, str | None, str, str | None]:
        settings = self._integration_provider_settings(kind, overrides)
        if settings:
            first = settings[0]
            return first["endpoint"], first["model"], first["provider_format"], first["api_key"]
        model_key = {
            "extraction": "extraction_model",
            "embedding": "embedding_model",
            "ocr": "ocr_model",
            "document_parser": "document_parser_model",
            "structure_recognition": "structure_recognition_model",
            "reranker": "reranker_model",
            "ai_evidence_review": "ai_evidence_review_model",
        }.get(kind)
        model = self._override_value(overrides, "integrations", model_key, getattr(self.config, model_key, None)) if model_key else None
        return None, model, "openai_compatible", None

    def _ai_evidence_review_endpoint_settings(self, overrides: dict[str, Any] | None = None) -> tuple[str | None, str | None, str, str | None, str]:
        endpoint, model, provider, api_key = self._integration_endpoint_settings("ai_evidence_review", overrides)
        if endpoint:
            return endpoint, model, provider, api_key, "ai_evidence_review"
        endpoint, model, provider, api_key = self._integration_endpoint_settings("extraction", overrides)
        return endpoint, model, provider, api_key, "extraction"

    def _integration_provider_settings(self, kind: str, overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        feature_map = {
            "extraction": ("extraction_provider_id", None, "extraction_model"),
            "embedding": ("embedding_provider_id", None, "embedding_model"),
            "ocr": ("ocr_provider_id", "ocr_provider_ids", "ocr_model"),
            "document_parser": ("document_parser_provider_id", "document_parser_provider_ids", "document_parser_model"),
            "structure_recognition": ("structure_recognition_provider_id", None, "structure_recognition_model"),
            "reranker": ("reranker_provider_id", None, "reranker_model"),
            "ai_evidence_review": ("ai_evidence_review_provider_id", None, "ai_evidence_review_model"),
        }
        
        if kind not in feature_map:
            return []
            
        provider_key, provider_ids_key, model_key = feature_map[kind]
        provider_id = self._override_value(overrides, "integrations", provider_key, getattr(self.config, provider_key, None))
        explicit_model = self._override_value(overrides, "integrations", model_key, getattr(self.config, model_key, None))
        provider_ids = self._override_id_list(overrides, "integrations", provider_ids_key, getattr(self.config, provider_ids_key, ())) if provider_ids_key else ()
        ordered_ids = list(provider_ids)
        if provider_id and provider_id not in ordered_ids:
            ordered_ids.insert(0, provider_id)

        settings: list[dict[str, Any]] = []
        for item_provider_id in ordered_ids:
            provider = self._resolve_provider(item_provider_id, overrides)
            if not provider:
                continue
            model = explicit_model or (provider.enabled_models[0] if getattr(provider, "enabled_models", ()) else None)
            settings.append(
                {
                    "provider_id": item_provider_id,
                    "endpoint": provider.endpoint,
                    "model": model,
                    "provider_format": provider.format,
                    "api_key": provider.api_key,
                }
            )
        return settings

    @staticmethod
    def _override_value(overrides: dict[str, Any] | None, section: str, key: str, default: str | None) -> str | None:
        section_values = overrides.get(section, {}) if isinstance(overrides, dict) else {}
        if not isinstance(section_values, dict) or key not in section_values:
            return default
        value = section_values.get(key)
        if value is None:
            return default
        text = str(value).strip()
        return text or default

    @staticmethod
    def _override_bool(overrides: dict[str, Any] | None, section: str, key: str, default: bool) -> bool:
        section_values = overrides.get(section, {}) if isinstance(overrides, dict) else {}
        if not isinstance(section_values, dict) or key not in section_values:
            return default
        value = section_values.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _override_id_list(overrides: dict[str, Any] | None, section: str, key: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
        if not key:
            return default
        section_values = overrides.get(section, {}) if isinstance(overrides, dict) else {}
        if not isinstance(section_values, dict) or key not in section_values:
            return default
        value = section_values.get(key)
        if value is None:
            return default
        if isinstance(value, str):
            items = value.split(",")
        elif isinstance(value, (list, tuple)):
            items = value
        else:
            return default
        return tuple(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))

    def search_compounds(self, query: str = "", limit: int = 20) -> list[dict[str, Any]]:
        return [compound.to_dict() for compound in self.storage.search_compounds(query=query, limit=limit)]

    def get_compound(self, compound_id: str) -> dict[str, Any]:
        compound = self.storage.get_compound(compound_id)
        if not compound:
            raise KeyError(f"Compound not found: {compound_id}")
        return compound

    def merge_compounds(self, source_compound_id: str, target_compound_id: str) -> dict[str, Any]:
        return self.storage.merge_compounds(source_compound_id, target_compound_id)

    def search_by_smiles(self, smiles: str, limit: int = 20) -> list[dict[str, Any]]:
        canonical, inchikey, _fingerprint = normalize_with_rdkit(smiles)
        query = inchikey or canonical or smiles
        return self.search_compounds(query=query, limit=limit)

    def get_chem_status(self) -> dict[str, Any]:
        status = {
            "rdkit": rdkit_info().to_dict(),
            "install_job": self._rdkit_install_job,
            "runtime_install_persistence": {
                "mode": "ephemeral_container_filesystem",
                "message": "Current images install RDKit during build. The Web UI install button is only a temporary repair for older images or broken environments; runtime installs are stored in the current container filesystem, not in /data, and can be lost after docker pull/recreate.",
                "durable_options": [
                    "Use this application image version or newer, which includes rdkit by default.",
                    "Rebuild/re-pull the image instead of relying on Web UI pip installs for production NAS deployments.",
                    "If you deliberately use runtime installs, persist the exact site-packages/user-base path and restart after installation.",
                ],
            },
        }
        if self._rdkit_install_job and self._rdkit_install_job.get("status") == "installed_restart_required":
            status["restart_required"] = True
            status["restart_message"] = "RDKit installation finished, but the container should be restarted so every worker imports the installed package from a clean process."
        try:
            status["rdf_structure_index"] = self.storage.rdf_structure_index_status()
        except Exception as exc:
            if not is_sqlite_locked_error(exc):
                raise
            status["rdf_structure_index"] = {"status": "degraded", "database_error": "database is locked"}
        return status

    def install_rdkit_async(self) -> dict[str, Any]:
        with self._rdkit_install_lock:
            if self._rdkit_install_job and self._rdkit_install_job.get("status") == "running":
                return self._rdkit_install_job
            job = {"id": f"rdkit_install_{int(time.time())}", "status": "running", "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "result": None}
            self._rdkit_install_job = job
        threading.Thread(target=self._run_rdkit_install_job, name="rdkit-install", daemon=True).start()
        return job

    def _run_rdkit_install_job(self) -> None:
        try:
            result = install_rdkit()
        except (subprocess.SubprocessError, OSError, TimeoutError) as exc:
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "restart_required": False}
        with self._rdkit_install_lock:
            if self._rdkit_install_job is not None:
                self._rdkit_install_job["status"] = result.get("status", "failed")
                self._rdkit_install_job["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                self._rdkit_install_job["result"] = result

    def list_rdf_reactions(self, document_id: str = "", query: str = "", limit: int = 50, offset: int = 0, include_deleted: bool = False) -> list[dict[str, Any]]:
        return [self._with_rdf_readable_view(item) for item in self.storage.list_rdf_reactions(document_id=document_id, query=query, limit=limit, offset=offset, include_deleted=include_deleted)]

    def get_rdf_reaction(self, reaction_id: str, include_deleted: bool = False) -> dict[str, Any]:
        reaction = self.storage.get_rdf_reaction(reaction_id, include_deleted=include_deleted)
        if not reaction:
            raise KeyError(f"RDF reaction not found: {reaction_id}")
        return self._with_rdf_readable_view(reaction)

    def render_rdf_structure_svg(self, structure_id: str) -> str:
        structure = self.storage.get_rdf_structure(structure_id)
        if not structure:
            raise KeyError(f"RDF structure not found: {structure_id}")
        return render_structure_svg(structure.get("molfile"), structure.get("smiles"))

    def list_rdf_structures(self, document_id: str = "", query: str = "", limit: int = 50, offset: int = 0, include_deleted: bool = False) -> list[dict[str, Any]]:
        return [self._with_rdf_provenance_warning(item) for item in self.storage.list_rdf_structures(document_id=document_id, query=query, limit=limit, offset=offset, include_deleted=include_deleted)]

    def _with_rdf_readable_view(self, reaction: dict[str, Any]) -> dict[str, Any]:
        data = self._with_rdf_provenance_warning(reaction)
        data["readable"] = self._rdf_reaction_readable_view(data)
        data["human_summary"] = data["readable"]["zh"]["summary"]
        data["human_readable_text_zh"] = data["readable"]["zh"]["text"]
        return data

    def _with_rdf_provenance_warning(self, item: dict[str, Any]) -> dict[str, Any]:
        warnings = list(item.get("warnings") or [])
        if RDF_PROVENANCE_WARNING not in warnings:
            warnings.append(RDF_PROVENANCE_WARNING)
        return {**item, "warnings": warnings, "provenance_warning": RDF_PROVENANCE_WARNING}

    def _rdf_reaction_readable_view(self, reaction: dict[str, Any]) -> dict[str, Any]:
        structures = [self._rdf_structure_readable_view(item) for item in reaction.get("structures") or []]
        by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in RDF_ROLE_LABELS_ZH}
        for structure in structures:
            by_role.setdefault(str(structure.get("role") or "unknown"), []).append(structure)

        equation_zh = self._rdf_equation(by_role, language="zh")
        equation_en = self._rdf_equation(by_role, language="en")
        summary_zh = self._rdf_summary(reaction, by_role, language="zh")
        summary_en = self._rdf_summary(reaction, by_role, language="en")
        return {
            "language": "zh-CN",
            "zh": {
                "title": "RDF 反应详情",
                "summary": summary_zh,
                "equation": equation_zh,
                "participants": self._rdf_participants(by_role, language="zh"),
                "reference": self._rdf_reference_text(reaction, language="zh"),
                "text": self._rdf_readable_text(reaction, summary_zh, equation_zh, by_role, language="zh"),
            },
            "en": {
                "title": "RDF reaction detail",
                "summary": summary_en,
                "equation": equation_en,
                "participants": self._rdf_participants(by_role, language="en"),
                "reference": self._rdf_reference_text(reaction, language="en"),
                "text": self._rdf_readable_text(reaction, summary_en, equation_en, by_role, language="en"),
            },
            "structures": structures,
            "fields_explained": self._rdf_field_explanations(reaction.get("fields") or {}),
        }

    def _rdf_structure_readable_view(self, structure: dict[str, Any]) -> dict[str, Any]:
        role = str(structure.get("role") or "unknown")
        label = self._rdf_structure_label(structure)
        return {
            "id": structure.get("id"),
            "role": role,
            "role_label_zh": RDF_ROLE_LABELS_ZH.get(role, role),
            "role_label_en": RDF_ROLE_LABELS_EN.get(role, role.title()),
            "role_index": structure.get("role_index"),
            "label": label,
            "name": structure.get("name"),
            "formula": structure.get("formula"),
            "cas_rn": structure.get("cas_rn"),
            "smiles": structure.get("smiles"),
            "inchikey": structure.get("inchikey"),
            "molfile_version": structure.get("molfile_version"),
            "rdkit_status": structure.get("rdkit_status"),
            "rdkit_error": structure.get("rdkit_error"),
            "warnings": structure.get("warnings") or [],
            "has_molfile": bool(structure.get("molfile")),
            "image_svg_url": f"/api/rdf/structures/{structure.get('id')}/image.svg" if structure.get("molfile") or structure.get("smiles") else None,
            "display": self._rdf_structure_display(structure),
        }

    def _rdf_structure_label(self, structure: dict[str, Any]) -> str:
        return str(structure.get("name") or structure.get("formula") or structure.get("cas_rn") or structure.get("id") or "unknown structure")

    def _rdf_structure_display(self, structure: dict[str, Any]) -> str:
        parts = [self._rdf_structure_label(structure)]
        if structure.get("formula") and structure.get("formula") not in parts:
            parts.append(f"formula {structure['formula']}")
        if structure.get("cas_rn") and structure.get("cas_rn") not in parts:
            parts.append(f"CAS {structure['cas_rn']}")
        return " | ".join(parts)

    def _rdf_equation(self, by_role: dict[str, list[dict[str, Any]]], *, language: str) -> str:
        reactants = " + ".join(item["display"] for item in by_role.get("reactant", [])) or ("未记录反应物" if language == "zh" else "Reactants not recorded")
        products = " + ".join(item["display"] for item in by_role.get("product", [])) or ("未记录产物" if language == "zh" else "Products not recorded")
        conditions: list[str] = []
        for role in ["reagent", "catalyst", "solvent"]:
            items = by_role.get(role, [])
            if items:
                label = RDF_ROLE_LABELS_ZH[role] if language == "zh" else RDF_ROLE_LABELS_EN[role]
                conditions.append(f"{label}: " + ", ".join(item["display"] for item in items))
        suffix = f" ({'; '.join(conditions)})" if conditions else ""
        return f"{reactants} -> {products}{suffix}"

    def _rdf_summary(self, reaction: dict[str, Any], by_role: dict[str, list[dict[str, Any]]], *, language: str) -> str:
        cas = reaction.get("cas_reaction_number") or ("未记录" if language == "zh" else "not recorded")
        scheme = reaction.get("scheme_id") or "-"
        step = reaction.get("step_id") or "-"
        yield_text = reaction.get("yield_text") or ("未记录" if language == "zh" else "not recorded")
        reactant_count = len(by_role.get("reactant", [])) or reaction.get("reactant_count") or 0
        product_count = len(by_role.get("product", [])) or reaction.get("product_count") or 0
        if language == "zh":
            return f"CAS 反应号 {cas}，方案 {scheme}，步骤 {step}；包含 {reactant_count} 个反应物、{product_count} 个产物，收率 {yield_text}。"
        return f"CAS reaction number {cas}, scheme {scheme}, step {step}; {reactant_count} reactant(s), {product_count} product(s), yield {yield_text}."

    def _rdf_participants(self, by_role: dict[str, list[dict[str, Any]]], *, language: str) -> dict[str, list[dict[str, Any]]]:
        labels = RDF_ROLE_LABELS_ZH if language == "zh" else RDF_ROLE_LABELS_EN
        return {labels.get(role, role): items for role, items in by_role.items() if items}

    def _rdf_reference_text(self, reaction: dict[str, Any], *, language: str) -> str:
        reference = reaction.get("reference") or {}
        title = reference.get("title") or ""
        author = reference.get("author") or ""
        citation = reference.get("citation") or ""
        if not any([title, author, citation]):
            return "未记录参考文献" if language == "zh" else "Reference not recorded"
        if language == "zh":
            return "；".join(part for part in [f"题名：{title}" if title else "", f"作者：{author}" if author else "", f"出处：{citation}" if citation else ""] if part)
        return "; ".join(part for part in [f"Title: {title}" if title else "", f"Author: {author}" if author else "", f"Citation: {citation}" if citation else ""] if part)

    def _rdf_readable_text(self, reaction: dict[str, Any], summary: str, equation: str, by_role: dict[str, list[dict[str, Any]]], *, language: str) -> str:
        if language == "zh":
            lines = [summary, f"反应式：{equation}", f"参考文献：{self._rdf_reference_text(reaction, language=language)}"]
            for role in ["reactant", "product", "reagent", "catalyst", "solvent", "unknown"]:
                items = by_role.get(role, [])
                if items:
                    lines.append(f"{RDF_ROLE_LABELS_ZH.get(role, role)}：" + "；".join(item["display"] for item in items))
            if not reaction.get("experimental_procedure"):
                lines.append("实验步骤：RDF 未提供完整实验步骤，请结合 PDF/RTF/HTML 原始出处核验。")
            return "\n".join(lines)
        lines = [summary, f"Equation: {equation}", f"Reference: {self._rdf_reference_text(reaction, language=language)}"]
        for role in ["reactant", "product", "reagent", "catalyst", "solvent", "unknown"]:
            items = by_role.get(role, [])
            if items:
                lines.append(f"{RDF_ROLE_LABELS_EN.get(role, role.title())}: " + "; ".join(item["display"] for item in items))
        if not reaction.get("experimental_procedure"):
            lines.append("Experimental procedure: not provided by RDF; verify with linked PDF/RTF/HTML provenance.")
        return "\n".join(lines)

    def _rdf_field_explanations(self, fields: dict[str, Any]) -> dict[str, str]:
        explanations = {
            "RXN:VAR(1):CAS_Reaction_Number": "CAS 反应号",
            "RXN:VAR(1):PRO(1):YIELD": "产物收率",
            "RXN:VAR(1):STAGES": "反应阶段数",
            "RXN:VAR(1):STEPS": "反应步骤数",
            "RXN:VAR(1):REFERENCE(1):TITLE": "参考文献题名",
            "RXN:VAR(1):REFERENCE(1):AUTHOR": "参考文献作者",
            "RXN:VAR(1):REFERENCE(1):CITATION": "参考文献出处",
        }
        result: dict[str, str] = {}
        for key in fields:
            if key in explanations:
                result[key] = explanations[key]
            elif key.startswith("RXN:RCT") and key.endswith(":CAS_RN"):
                result[key] = "反应物 CAS RN"
            elif key.startswith("RXN:PRO") and key.endswith(":CAS_RN"):
                result[key] = "产物 CAS RN"
            elif key.startswith("RXN:VAR(1):RGT") and key.endswith(":CAS_RN"):
                result[key] = "试剂 CAS RN"
            elif key.startswith("RXN:VAR(1):CAT") and key.endswith(":CAS_RN"):
                result[key] = "催化剂 CAS RN"
            elif key.startswith("RXN:VAR(1):SOL") and key.endswith(":CAS_RN"):
                result[key] = "溶剂 CAS RN"
        return result

    def similarity_search_structures(self, query: str, query_type: str = "smiles", min_similarity: float = 0.2, limit: int = 20) -> dict[str, Any]:
        fp, error = fingerprint_from_query(query, query_type)
        if error:
            fallback = self._metadata_structure_search(query, limit=limit)
            if fallback:
                return {"configured": True, "query_type": query_type, "fallback": "metadata", "warning": f"{error}; returned RDF metadata matches instead", "results": fallback}
            return {"configured": False, "error": error, "results": []}
        scored: list[dict[str, Any]] = []
        for structure in self.storage.list_rdf_structures_for_search(limit=10000):
            score = tanimoto(fp, structure.get("fingerprint"))
            if score >= min_similarity:
                scored.append({**structure, "similarity": round(score, 4)})
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return {"configured": True, "query_type": query_type, "results": scored[:limit]}

    def substructure_search_structures(self, query: str, query_type: str = "smarts", limit: int = 20) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        for structure in self.storage.list_rdf_structures_for_search(limit=10000):
            molfile = structure.get("molfile")
            if not molfile:
                continue
            matched, error = substructure_match(query, molfile, query_type=query_type)
            if error and not errors:
                errors.append(error)
            if matched:
                results.append(structure)
                if len(results) >= limit:
                    break
        if not results and any("query structure" in error for error in errors):
            fallback = self._metadata_structure_search(query, limit=limit)
            if fallback:
                return {"configured": True, "query_type": query_type, "fallback": "metadata", "warning": f"{errors[0]}; returned RDF metadata matches instead", "errors": [], "results": fallback}
        return {"configured": not errors or bool(results), "query_type": query_type, "errors": errors[:5], "results": results}

    def _metadata_structure_search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        return self.list_rdf_structures(query=query, limit=limit)

    def trash_item(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        return self.storage.soft_delete(entity_type, entity_id)

    def restore_trash_item(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        return self.storage.restore_trash_item(entity_type, entity_id)

    def list_trash(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.storage.list_trash(limit=limit)

    def empty_trash(self) -> dict[str, int]:
        return self.storage.empty_trash()

    def recognize_structure_image(self, image_path: str, reaction_step_id: str | None = None) -> dict[str, Any]:
        endpoint, model, provider, api_key = self._integration_endpoint_settings("structure_recognition")
        adapter = StructureRecognitionAdapter(endpoint, model, api_key, provider=provider)
        if not adapter.configured:
            return {"configured": False, "status": "skipped", "reason": "structure recognition endpoint is not configured", "compounds": []}
        structures = adapter.recognize(image_path)
        compounds: list[dict[str, Any]] = []
        for item in structures:
            if not isinstance(item, dict):
                continue
            smiles = str(item.get("smiles") or "").strip()
            if not smiles:
                continue
            canonical, inchikey, fingerprint = normalize_with_rdkit(smiles)
            compound = self.storage.upsert_compound(
                primary_name=canonical or smiles,
                smiles=smiles,
                canonical_smiles=canonical,
                inchikey=inchikey,
                fingerprint=fingerprint,
                source="image_recognition",
                confidence=float(item.get("confidence") or 0.5),
                aliases=[(smiles, "smiles")],
            )
            compounds.append(compound.to_dict())
            if reaction_step_id:
                step = self.storage.get_reaction_step(reaction_step_id)
                if step:
                    self.storage.link_compound_to_reaction(reaction_step_id, compound.id, role="image_candidate", confidence=float(item.get("confidence") or 0.5), source="image_recognition")
                    self.storage.add_provenance(
                        reaction_step_id,
                        step.source_document_id,
                        text_span=f"Image structure recognition candidate: {smiles}",
                        image_region_path=image_path,
                        parser_name="image_recognition",
                        parser_version=str(self.config.structure_recognition_model or "external"),
                        confidence=float(item.get("confidence") or 0.5),
                    )
        return {"configured": True, "status": "ok", "compounds": compounds, "low_confidence": [c for c in compounds if c.get("confidence", 0) < 0.7]}

    def backup_database(self, output_path: str | None = None) -> dict[str, Any]:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        target = Path(output_path).resolve() if output_path else self.config.data_dir / "backups" / f"scifinder_routes-{timestamp}.sqlite3"
        if self.config.storage_backend == "postgres":
            return {"status": "degraded", "reason": "Postgres logical backup must be performed with pg_dump outside the container", "output_path": None}
        return {"status": "ok", **self.storage.backup_sqlite(target)}

    def get_storage_usage(self) -> dict[str, Any]:
        paths = {"data_dir": self.config.data_dir, "upload_dir": self.config.upload_dir, "evidence_dir": self.config.evidence_dir, "inbox_dir": self.config.inbox_dir}
        return {name: directory_usage(path) for name, path in paths.items()}

    def cleanup_evidence_cache(self, dry_run: bool = True, max_age_days: int | None = None) -> dict[str, Any]:
        cutoff = time.time() - (max_age_days if max_age_days is not None else self.config.cache_retention_days) * 86400
        candidates: list[Path] = []
        for root in [self.config.evidence_dir, self.config.data_dir / "cache"]:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and path.stat().st_mtime < cutoff:
                    candidates.append(path)
        bytes_total = sum(path.stat().st_size for path in candidates)
        if not dry_run:
            for path in candidates:
                path.unlink(missing_ok=True)
        return {"dry_run": dry_run, "files": len(candidates), "bytes": bytes_total, "deleted": 0 if dry_run else len(candidates)}

    def get_production_status(self) -> dict[str, Any]:
        try:
            return {
                "health": self.health_check(),
                "vector_index": self.get_vector_index_status(),
                "chem": self.get_chem_status(),
                "evaluation": self.get_evaluation_status(),
                "storage_usage": self.get_storage_usage(),
                "doi_low_confidence_queue": self.storage.low_confidence_doi_queue(self.config.verification_confidence_threshold, limit=20),
                "compound_count": len(self.search_compounds(limit=1000)),
                "literature_link_jobs": self.list_literature_link_jobs(limit=10),
                "literature_candidates": self.list_literature_links(status="candidate", limit=20),
            }
        except Exception as exc:
            if not is_sqlite_locked_error(exc):
                raise
            return {
                "health": self.health_check(),
                "status": "degraded",
                "database_error": "database is locked",
                "vector_index": {"configured": bool(self.config.embedding_endpoint), "indexed": None, "errors": None},
                "chem": self.get_chem_status(),
                "evaluation": {"latest": None},
                "storage_usage": self.get_storage_usage(),
                "doi_low_confidence_queue": [],
                "compound_count": None,
                "literature_link_jobs": [],
                "literature_candidates": [],
            }

    def list_zotero_mcp_endpoints(self, *, include_headers: bool = False) -> list[dict[str, Any]]:
        status_by_id = {item["id"]: item for item in self.storage.list_zotero_endpoints(include_headers=False)}
        endpoints = []
        for endpoint in self.storage.list_zotero_endpoints(include_headers=include_headers):
            status = status_by_id.get(endpoint["id"], {})
            endpoints.append({**endpoint, "last_status": status.get("last_status"), "last_latency_ms": status.get("last_latency_ms"), "last_error": status.get("last_error"), "last_checked_at": status.get("last_checked_at")})
        return endpoints

    def upsert_zotero_mcp_endpoint(self, endpoint: dict[str, Any]) -> dict[str, Any]:
        endpoint_id = str(endpoint.get("id") or endpoint.get("alias") or "").strip()
        if not endpoint_id:
            endpoints = self.storage.list_zotero_endpoints()
            endpoint_id = str(endpoint.get("alias") or f"zotero-{len(endpoints) + 1}").strip()
        url = str(endpoint.get("url") or "").strip()
        enabled = endpoint.get("enabled", True)
        if enabled and not url:
            raise ValueError("Zotero MCP endpoint URL is required when the endpoint is enabled")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("Zotero MCP endpoint URL must start with http:// or https://")
        endpoint = {**endpoint, "url": url, "id": endpoint_id}
        
        if any(v == "****" for v in endpoint.get("headers", {}).values()):
            existing = next((item for item in self.storage.list_zotero_endpoints(include_headers=True) if item["id"] == endpoint_id), None)
            if existing:
                endpoint["headers"] = existing.get("headers", {})
                
        self.storage.upsert_zotero_endpoint(endpoint)
        
        normalized = [item for item in self.storage.list_zotero_endpoints(include_headers=True) if item["id"] == endpoint_id]
        if not normalized:
            raise KeyError(f"Zotero MCP endpoint not found after update: {endpoint_id}")
        return normalized[0]

    def delete_zotero_mcp_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        self.storage.delete_zotero_endpoint(endpoint_id)
        return {"status": "deleted", "id": endpoint_id}

    def test_zotero_mcp_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        endpoint = next((item for item in self.storage.list_zotero_endpoints(include_headers=True) if item["id"] == endpoint_id), None)
        if not endpoint:
            raise KeyError(f"Zotero MCP endpoint not found: {endpoint_id}")
        result = ZoteroMcpClient(endpoint).test()
        status = str(result.get("status") or "error")
        self.storage.update_zotero_endpoint_status(endpoint_id, status=status, latency_ms=result.get("latency_ms"), error=None if status == "ok" else str(result.get("detail") or ""))
        return {**endpoint, **result, "headers": {key: "****" for key in (endpoint.get("headers") or {})}}

    def enqueue_literature_linking(self, document_id: str | None = None, *, run_now: bool = False) -> dict[str, Any]:
        job = self.storage.create_literature_link_job(document_id=document_id, status="running" if run_now else "queued")
        if run_now:
            self._process_literature_link_job(job["id"], document_id=document_id)
            return self.storage.list_literature_link_jobs(limit=1)[0]
        threading.Thread(target=self._process_literature_link_job, args=(job["id"],), kwargs={"document_id": document_id}, name="zotero-linker", daemon=True).start()
        return job

    def list_literature_link_jobs(self, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.storage.list_literature_link_jobs(status=status, limit=limit)

    def list_literature_links(self, status: str = "", reaction_step_id: str = "", document_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.storage.list_literature_links(status=status, reaction_step_id=reaction_step_id, document_id=document_id, limit=limit)

    def confirm_literature_link(self, link_id: str, confirmed_by: str | None = None) -> dict[str, Any]:
        return self.storage.update_literature_link_status(link_id, status="confirmed", confirmed_by=confirmed_by or "webui")

    def reject_literature_link(self, link_id: str, reason: str = "") -> dict[str, Any]:
        return self.storage.update_literature_link_status(link_id, status="rejected", reason=reason)

    def get_reaction_literature_context(self, reaction_step_id: str) -> dict[str, Any]:
        step = self.get_reaction_step(reaction_step_id)
        links = self.list_literature_links(reaction_step_id=reaction_step_id, limit=50)
        return {"reaction_step": step, "links": links, "provenance": self.get_reaction_provenance(reaction_step_id)}

    def write_zotero_link_note(self, link_id: str) -> dict[str, Any]:
        link = next((item for item in self.list_literature_links(limit=1000) if item["id"] == link_id), None)
        if not link:
            raise KeyError(f"Literature link not found: {link_id}")
        endpoint = next((item for item in self.storage.list_zotero_endpoints(include_headers=True) if item["id"] == link.get("endpoint_id")), None)
        if not endpoint:
            raise KeyError("Configured Zotero endpoint for this link is no longer available")
        if not endpoint.get("write_note_enabled"):
            raise PermissionError("Zotero endpoint does not allow note writeback")
        step = self.get_reaction_step(link["reaction_step_id"])
        note = build_zotero_note(step, link)
        try:
            result = ZoteroMcpClient(endpoint).write_note(link["zotero_item_key"], note)
        except Exception as exc:
            return self.storage.record_zotero_writeback(literature_link_id=link_id, endpoint_id=endpoint["id"], zotero_item_key=link["zotero_item_key"], operation="write_note", payload={"note": note}, status="error", error=str(exc))
        return self.storage.record_zotero_writeback(literature_link_id=link_id, endpoint_id=endpoint["id"], zotero_item_key=link["zotero_item_key"], operation="write_note", payload={"note": note, "result": result}, status="ok")


    def _process_literature_link_job(self, job_id: str, document_id: str | None = None) -> None:
        try:
            self.storage.update_literature_link_job(job_id, status="running", stage="zotero_search")
            if not self.config.zotero_linking_enabled:
                self.storage.update_literature_link_job(job_id, status="completed", stage="skipped")
                return
            endpoints = [endpoint for endpoint in self._select_zotero_endpoints() if endpoint.get("enabled", True)]
            if not endpoints:
                self.storage.update_literature_link_job(job_id, status="completed", stage="no_endpoints")
                return
            steps = self.storage.list_reaction_steps_for_document(document_id, limit=100)
            for step in steps:
                self._link_step_with_zotero(step.to_dict(), endpoints)
            self.storage.update_literature_link_job(job_id, status="completed", stage="completed")
        except Exception as exc:
            self.storage.update_literature_link_job(job_id, status="failed", stage="failed", error=str(exc))

    def _select_zotero_endpoints(self) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for endpoint in self.storage.list_zotero_endpoints(include_headers=True):
            grouped.setdefault(str(endpoint.get("group_name") or endpoint.get("alias")), []).append(endpoint)
        selected = []
        for endpoints in grouped.values():
            endpoints.sort(key=lambda item: (int(item.get("priority") or 100), str(item.get("alias") or "")))
            selected.append(endpoints[0])
        return selected

    def _link_step_with_zotero(self, step: dict[str, Any], endpoints: list[dict[str, Any]]) -> None:
        document = self.storage.get_document(step["source_document_id"])
        document_data = document.to_dict() if document else {}
        doi = normalize_doi(str(document_data.get("doi") or ""))
        query = build_query(step, document_data)
        for endpoint in endpoints:
            client = ZoteroMcpClient(endpoint)
            items = client.search_library(doi=doi, limit=5) if doi else []
            if not items and query:
                items = client.search_library(query=query, limit=5)
            for item in items[:5]:
                key = item_key(item)
                if not key:
                    continue
                details = client.get_item_details(key)
                merged = {**item, **details}
                match_doi = item_doi(merged)
                title = item_title(merged)
                similarity = title_similarity(str(document_data.get("title") or step.get("reaction_name") or ""), title)
                doi_exact = bool(doi and match_doi and doi == match_doi)
                confidence = 0.95 if doi_exact and similarity >= 0.5 else 0.72 if doi_exact else max(0.25, similarity)
                status = "auto_linked" if doi_exact and similarity >= 0.5 else "candidate"
                abstract = client.get_item_abstract(key) or str(merged.get("abstractNote") or merged.get("abstract") or "")
                excerpt = client.search_fulltext(key, query or doi)
                method_text = excerpt or abstract
                extracted = extract_method_fields(method_text)
                field_diff = diff_reaction_fields(step, extracted)
                self.storage.upsert_literature_link(
                    {
                        "reaction_step_id": step["id"],
                        "source_document_id": step["source_document_id"],
                        "endpoint_id": endpoint.get("id"),
                        "endpoint_alias": endpoint.get("alias"),
                        "endpoint_group": endpoint.get("group_name"),
                        "zotero_item_key": key,
                        "doi": match_doi or doi,
                        "title": title,
                        "year": item_year(merged),
                        "abstract": trim_text(abstract, 1200),
                        "status": status,
                        "confidence": confidence,
                        "match_signals": {"doi_exact": doi_exact, "title_similarity": round(similarity, 3), "query": query[:200], "endpoint_alias": endpoint.get("alias")},
                        "method_excerpt": trim_text(method_text, 1600),
                        "extracted_fields": extracted,
                        "field_diff": field_diff,
                    }
                )

    def _validate_upload_content(self, content: bytes, filename: str) -> None:
        if not content:
            raise ValueError("Uploaded file is empty")
        if len(content) > self.config.upload_max_bytes:
            raise ValueError(f"Upload exceeds configured limit of {self.config.upload_max_bytes} bytes")
        safe_name = safe_filename(filename)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in set(self.config.upload_extensions):
            raise ValueError(f"Unsupported upload extension: {suffix or '<none>'}")
        dangerous = detect_dangerous_payload(content)
        if dangerous:
            raise ValueError(f"Rejected dangerous upload payload: {dangerous}")
        detected = sniff_document_type(content)
        allowed = {
            ".pdf": {"pdf"},
            ".rtf": {"rtf"},
            ".rdf": {"rdf"},
            ".html": {"html"},
            ".htm": {"html"},
            ".mhtml": {"mhtml"},
            ".mht": {"mhtml"},
            ".md": {"text"},
            ".markdown": {"text"},
            ".txt": {"text"},
        }
        if self.config.reject_file_type_mismatch and detected not in allowed.get(suffix, set()):
            raise ValueError(f"Upload extension {suffix} does not match detected content type {detected}")
        if self.config.upload_av_scan_enabled:
            self._scan_upload_with_antivirus(content, safe_name)

    def _scan_upload_with_antivirus(self, content: bytes, filename: str) -> None:
        if self.config.upload_av_engine != "clamav" or not self.config.upload_av_endpoint:
            if self.config.upload_av_fail_closed:
                raise ValueError("Antivirus scanning is enabled but ClamAV endpoint is not configured")
            return
        if self.config.upload_av_endpoint.startswith("tcp://"):
            self._scan_with_clamav_instream(content, filename)
            return
        if self.config.upload_av_fail_closed:
            raise ValueError("Only tcp:// ClamAV endpoints are supported for upload antivirus scanning")

    def _scan_with_clamav_instream(self, content: bytes, filename: str) -> None:
        import socket
        from urllib.parse import urlparse

        endpoint = urlparse(self.config.upload_av_endpoint or "")
        host = endpoint.hostname
        port = endpoint.port or 3310
        if not host:
            raise ValueError("Invalid ClamAV endpoint")
        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                sock.sendall(b"zINSTREAM\0")
                for offset in range(0, len(content), 1024 * 1024):
                    chunk = content[offset : offset + 1024 * 1024]
                    sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
                sock.sendall((0).to_bytes(4, "big"))
                response = sock.recv(4096).decode("utf-8", errors="replace")
        except OSError as exc:
            if self.config.upload_av_fail_closed:
                raise ValueError(f"ClamAV scan failed for {filename}: {exc}") from exc
            return
        if "FOUND" in response:
            raise ValueError(f"ClamAV rejected upload {filename}: {response.strip()}")
        if "OK" not in response and self.config.upload_av_fail_closed:
            raise ValueError(f"Unexpected ClamAV response for {filename}: {response.strip()}")

    def _create_pdf_only_candidates(self, document_id: str, evidence_rows: list[dict[str, Any]]) -> None:
        from .parsers import _select_primary_pdf_page
        
        cas_groups: dict[str, list[dict[str, Any]]] = {}
        link_confidence = self._pdf_only_link_confidence(document_id)
        for row in evidence_rows:
            cas = row.get("cas_reaction_number")
            if cas:
                cas_groups.setdefault(cas, []).append(row)
            else:
                if row.get("products_text") and row.get("reactants_text") and (row.get("procedure_text") or row.get("conditions_text")):
                    if getattr(self.config, 'pdf_only_low_confidence_enabled', True):
                        link = self.storage.create_reaction_source_link({
                            "source_mode": "pdf_only_low_confidence",
                            "pdf_document_id": document_id,
                            "primary_pdf_page": row.get("page_number"),
                            "pdf_pages_json": [row.get("page_number")],
                            "link_confidence": min(row.get("match_confidence", 0.3), link_confidence),
                            "link_method": "pdf_block_candidate",
                            "needs_review": 1
                        })
                        row["reaction_source_link_id"] = link["id"]
                        row["is_primary"] = 1

        if getattr(self.config, 'pdf_only_candidates_enabled', True):
            for cas, rows in cas_groups.items():
                primary_page = _select_primary_pdf_page(rows)
                link = self.storage.create_reaction_source_link({
                    "cas_reaction_number": cas,
                    "source_mode": "pdf_only",
                    "pdf_document_id": document_id,
                    "primary_pdf_page": primary_page,
                    "pdf_pages_json": [r["page_number"] for r in rows],
                    "link_confidence": link_confidence,
                    "link_method": "pdf_cas_only",
                    "needs_review": 1
                })
                for row in rows:
                    row["reaction_source_link_id"] = link["id"]
                    if row.get("page_number") == primary_page:
                        row["is_primary"] = 1

    def _reaction_link_batch_ids(self, document_id: str | None) -> set[str]:
        if not document_id:
            return set()
        return {str(batch["id"]) for batch in self.storage.list_batches_for_document(document_id)}

    def _reaction_link_document_similarity(self, rdf_doc_id: str | None, pdf_doc_id: str | None) -> float:
        if not rdf_doc_id or not pdf_doc_id:
            return 0.0
        rdf_doc = self.storage.get_document(rdf_doc_id)
        pdf_doc = self.storage.get_document(pdf_doc_id)
        if not rdf_doc or not pdf_doc:
            return 0.0
        rdf_text = f"{Path(rdf_doc.file_path).stem} {rdf_doc.title or ''}".strip().lower()
        pdf_text = f"{Path(pdf_doc.file_path).stem} {pdf_doc.title or ''}".strip().lower()
        return SequenceMatcher(None, rdf_text, pdf_text).ratio() if rdf_text and pdf_text else 0.0

    def _score_rdf_pdf_link_candidate(self, cas: str, rdf_link: dict[str, Any], pdf_link: dict[str, Any]) -> dict[str, Any]:
        rdf_doc_id = rdf_link.get("rdf_document_id")
        pdf_doc_id = pdf_link.get("pdf_document_id")
        rdf_batches = self._reaction_link_batch_ids(str(rdf_doc_id) if rdf_doc_id else None)
        pdf_batches = self._reaction_link_batch_ids(str(pdf_doc_id) if pdf_doc_id else None)
        shared_batches = sorted(rdf_batches.intersection(pdf_batches))
        rdf_reaction = self.storage.get_rdf_reaction(str(rdf_link.get("rdf_reaction_id") or ""))
        pdf_evidences = self.storage.list_pdf_reaction_evidence(document_id=str(pdf_doc_id or ""), cas_reaction_number=cas)
        conflicts = self._compute_reaction_conflicts(rdf_reaction, pdf_evidences)
        document_similarity = self._reaction_link_document_similarity(str(rdf_doc_id or ""), str(pdf_doc_id or ""))
        evidence_profile = self._document_evidence_profile(str(pdf_doc_id or ""))
        evidence_priority = float(evidence_profile.get("evidence_priority") or 0.0)

        evidence_score = 0.0
        if any(item.get("procedure_text") for item in pdf_evidences):
            evidence_score += 10.0
        if any(item.get("yield_text") for item in pdf_evidences):
            evidence_score += 5.0
        if any(item.get("products_text") and item.get("reactants_text") for item in pdf_evidences):
            evidence_score += 5.0
        if pdf_link.get("primary_pdf_page"):
            evidence_score += 3.0

        score = 10.0
        if shared_batches:
            score += 100.0
        score += document_similarity * 25.0
        score += evidence_priority / 10.0
        score += evidence_score
        score += 30.0 if not conflicts else -30.0

        return {
            "cas": cas,
            "rdf_link": rdf_link,
            "pdf_link": pdf_link,
            "score": score,
            "shared_batches": shared_batches,
            "has_shared_batch": bool(shared_batches),
            "conflicts": conflicts,
            "document_similarity": round(document_similarity, 3),
            "evidence_score": evidence_score,
        }

    def _mark_ambiguous_cas_links(self, cas: str, rdf_links: list[dict[str, Any]], pdf_links: list[dict[str, Any]]) -> None:
        rdf_ids = [str(link["id"]) for link in rdf_links]
        pdf_ids = [str(link["id"]) for link in pdf_links]
        for link in rdf_links:
            flags = self._json_dict(link.get("conflict_flags_json"))
            flags.update({"ambiguous_cas_link": True, "cas_reaction_number": cas, "candidate_pdf_link_ids": pdf_ids})
            self.storage.update_reaction_source_link(str(link["id"]), {"needs_review": 1, "conflict_flags_json": flags})
        for link in pdf_links:
            flags = self._json_dict(link.get("conflict_flags_json"))
            flags.update({"ambiguous_cas_link": True, "cas_reaction_number": cas, "candidate_rdf_link_ids": rdf_ids})
            self.storage.update_reaction_source_link(str(link["id"]), {"needs_review": 1, "conflict_flags_json": flags})

    def _apply_rdf_pdf_link_candidate(self, candidate: dict[str, Any]) -> None:
        rdf_link = candidate["rdf_link"]
        pdf_link = candidate["pdf_link"]
        has_shared_batch = bool(candidate.get("has_shared_batch"))
        conflicts = candidate.get("conflicts") if isinstance(candidate.get("conflicts"), dict) else {}
        link_confidence = 1.0 if has_shared_batch and not conflicts else 0.85 if not conflicts else 0.65
        self.storage.update_reaction_source_link(
            str(rdf_link["id"]),
            {
                "source_mode": "rdf_pdf_linked",
                "pdf_document_id": pdf_link.get("pdf_document_id"),
                "primary_pdf_page": pdf_link.get("primary_pdf_page"),
                "pdf_pages_json": pdf_link.get("pdf_pages_json"),
                "link_confidence": link_confidence,
                "link_method": "cas_reaction_number" if has_shared_batch else "cas_cross_batch",
                "needs_review": 0 if has_shared_batch and not conflicts else 1,
                "conflict_flags_json": conflicts,
            },
        )
        self.storage.reassign_evidence_link(str(pdf_link["id"]), str(rdf_link["id"]))
        self.storage.delete_reaction_source_link(str(pdf_link["id"]))

    def _link_rdf_pdf_by_cas(self, batch_id: str | None = None, document_ids: list[str] | None = None) -> None:
        target_doc_ids = set(document_ids or [])
        if batch_id:
            batch = self.storage.get_export_batch(batch_id)
            if batch and "documents" in batch:
                for document in batch["documents"]:
                    target_doc_ids.add(document["id"])

        links: list[dict[str, Any]] = []
        if target_doc_ids:
            seen: set[str] = set()
            for doc_id in target_doc_ids:
                for link in self.storage.list_reaction_source_links(document_id=doc_id, limit=10000):
                    if str(link["id"]) not in seen:
                        links.append(link)
                        seen.add(str(link["id"]))
        else:
            links = self.storage.list_reaction_source_links(limit=10000)

        rdf_links_by_cas: dict[str, list[dict[str, Any]]] = {}
        pdf_links_by_cas: dict[str, list[dict[str, Any]]] = {}
        for link in links:
            cas = link.get("cas_reaction_number")
            if not cas:
                continue
            if link.get("source_mode") == "rdf_only":
                rdf_links_by_cas.setdefault(str(cas), []).append(link)
            elif link.get("source_mode") == "pdf_only":
                pdf_links_by_cas.setdefault(str(cas), []).append(link)

        for cas in sorted(set(rdf_links_by_cas).intersection(pdf_links_by_cas)):
            remaining_rdf = {str(link["id"]): link for link in rdf_links_by_cas[cas]}
            remaining_pdf = {str(link["id"]): link for link in pdf_links_by_cas[cas]}
            while remaining_rdf and remaining_pdf:
                candidates = [
                    self._score_rdf_pdf_link_candidate(cas, rdf_link, pdf_link)
                    for rdf_link in remaining_rdf.values()
                    for pdf_link in remaining_pdf.values()
                ]
                if not candidates:
                    break
                candidates.sort(key=lambda item: (-float(item["score"]), str(item["rdf_link"]["id"]), str(item["pdf_link"]["id"])))
                top = candidates[0]
                competing = [
                    candidate
                    for candidate in candidates[1:]
                    if abs(float(candidate["score"]) - float(top["score"])) < 0.01
                    and (
                        candidate["rdf_link"]["id"] == top["rdf_link"]["id"]
                        or candidate["pdf_link"]["id"] == top["pdf_link"]["id"]
                    )
                ]
                if competing:
                    self._mark_ambiguous_cas_links(cas, list(remaining_rdf.values()), list(remaining_pdf.values()))
                    break

                self._apply_rdf_pdf_link_candidate(top)
                remaining_rdf.pop(str(top["rdf_link"]["id"]), None)
                remaining_pdf.pop(str(top["pdf_link"]["id"]), None)

    def _compute_reaction_conflicts(self, rdf_record: dict[str, Any] | None, pdf_evidences: list[dict[str, Any]]) -> dict[str, Any]:
        conflicts: dict[str, Any] = {}
        if not rdf_record or not pdf_evidences:
            return conflicts
        
        pdf_yields = [e["yield_text"] for e in pdf_evidences if e.get("yield_text")]
        rdf_yield = rdf_record.get("yield_text")
        
        if rdf_yield and pdf_yields:
            pdf_y = pdf_yields[0]
            if str(rdf_yield) not in str(pdf_y) and str(pdf_y) not in str(rdf_yield):
                conflicts["yield"] = True
                
        return conflicts

    def set_primary_page(self, link_id: str, pdf_page: int) -> dict[str, Any]:
        """Update the primary page of a reaction link and sync evidence."""
        link = self.storage.get_reaction_source_link(link_id)
        if not link:
            raise ValueError(f"Link {link_id} not found")
            
        import json
        pdf_pages = link.get("pdf_pages_json")
        if isinstance(pdf_pages, str):
            try:
                pdf_pages = json.loads(pdf_pages)
            except Exception:
                pdf_pages = []
        if pdf_page not in pdf_pages:
            raise ValueError(f"Page {pdf_page} is not in the link's valid pages: {pdf_pages}")
            
        self.storage.update_reaction_source_link(link_id, {"primary_pdf_page": pdf_page})
        evidences = self.storage.list_pdf_reaction_evidence(reaction_source_link_id=link_id)
        for ev in evidences:
            is_primary = 1 if ev["page_number"] == pdf_page else 0
            self.storage.update_pdf_reaction_evidence(ev["id"], {"is_primary": is_primary})
            
        return {"status": "success", "id": link_id}

    def unlink_reaction_source_link(self, link_id: str) -> dict[str, Any]:
        """Unlink an rdf_pdf_linked record into separate rdf_only and pdf_only records."""
        link = self.storage.get_reaction_source_link(link_id)
        if not link or link["source_mode"] != "rdf_pdf_linked":
            raise ValueError(f"Link {link_id} is not an rdf_pdf_linked record")

        import json
        pdf_pages = link.get("pdf_pages_json")
        if isinstance(pdf_pages, str):
            try:
                pdf_pages = json.loads(pdf_pages)
            except Exception:
                pdf_pages = []
                
        # Create new pdf_only link
        pdf_link = self.storage.create_reaction_source_link({
            "cas_reaction_number": link.get("cas_reaction_number"),
            "source_mode": "pdf_only",
            "pdf_document_id": link.get("pdf_document_id"),
            "primary_pdf_page": link.get("primary_pdf_page"),
            "pdf_pages_json": pdf_pages,
            "link_confidence": link.get("link_confidence", 0.8),
            "link_method": "unlinked_from_rdf",
            "needs_review": 1
        })

        # Reassign evidence to the new pdf_only link
        self.storage.reassign_evidence_link(link_id, pdf_link["id"])

        # Convert the original link back to rdf_only
        self.storage.update_reaction_source_link(link_id, {
            "source_mode": "rdf_only",
            "pdf_document_id": None,
            "primary_pdf_page": None,
            "pdf_pages_json": None,
            "link_confidence": 1.0,
            "link_method": "unlinked_from_pdf",
            "needs_review": 0,
            "conflict_flags_json": None
        })
        
        return {"status": "split", "rdf_link_id": link_id, "pdf_link_id": pdf_link["id"]}

    def force_link_reaction(self, document_id: str, rdf_reaction_id: str, pdf_page: int) -> dict[str, Any]:
        """Manually force link a PDF page to an RDF reaction, resolving existing pdf_only/rdf_only links."""
        rdf_reaction = self.storage.get_rdf_reaction(rdf_reaction_id)
        if not rdf_reaction:
            raise ValueError(f"RDF reaction not found: {rdf_reaction_id}")
            
        cas_rn = rdf_reaction.get("cas_reaction_number")
        
        # Find if there is an existing rdf_only link for this reaction
        links = self.storage.list_reaction_source_links(document_id=rdf_reaction.get("source_document_id"), limit=1000)
        rdf_link = next((l for l in links if l.get("rdf_reaction_id") == rdf_reaction_id), None)
        
        # Find if there is an existing pdf_only link for this document and cas
        pdf_links = self.storage.list_reaction_source_links(document_id=document_id, limit=1000)
        pdf_link = next((l for l in pdf_links if l.get("cas_reaction_number") == cas_rn and l["source_mode"] == "pdf_only"), None)
        
        # Determine the target link ID
        pdf_pages_to_set = pdf_link.get("pdf_pages_json") if pdf_link else [pdf_page]
        if rdf_link:
            target_link_id = rdf_link["id"]
            self.storage.update_reaction_source_link(target_link_id, {
                "source_mode": "rdf_pdf_linked",
                "pdf_document_id": document_id,
                "primary_pdf_page": pdf_page,
                "pdf_pages_json": pdf_pages_to_set,
                "link_confidence": 1.0,
                "link_method": "manual_force",
                "needs_review": 0
            })
        else:
            new_link = self.storage.create_reaction_source_link({
                "cas_reaction_number": cas_rn,
                "source_mode": "rdf_pdf_linked",
                "rdf_reaction_id": rdf_reaction_id,
                "rdf_document_id": rdf_reaction.get("source_document_id"),
                "pdf_document_id": document_id,
                "primary_pdf_page": pdf_page,
                "pdf_pages_json": pdf_pages_to_set,
                "link_confidence": 1.0,
                "link_method": "manual_force",
                "needs_review": 0
            })
            target_link_id = new_link["id"]
            
        if pdf_link:
            self.storage.reassign_evidence_link(pdf_link["id"], target_link_id)
            self.storage.delete_reaction_source_link(pdf_link["id"])
            
        return {"status": "success", "id": target_link_id}

    def _render_pdf_evidence_page(self, path: Path, document_id: str, page_number: int) -> str | None:
        try:
            import fitz # type: ignore[import-not-found]
        except ImportError:
            return None
        evidence_dir = self.config.evidence_dir / document_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        out_path = evidence_dir / f"page_{page_number}.png"
        if out_path.exists():
            return str(out_path)
            
        with fitz.open(path) as doc:
            if 1 <= page_number <= len(doc):
                page = doc[page_number - 1]
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                pix.save(str(out_path))
                return str(out_path)
        return None

    def _process_document(self, document_id: str, job_id: str, *, parsed: ParsedDocument | None = None, reparse: bool = False) -> None:
        document = self.storage.get_document(document_id)
        if not document:
            raise KeyError(f"Document not found: {document_id}")
        try:
            self.storage.update_job(job_id, status="running", stage="document_parse")
            evidence_profile = self._classify_document_evidence(Path(document.file_path))
            if evidence_profile.get("import_action") == "exclude":
                reason = evidence_profile.get("exclude_reason") or evidence_profile.get("label") or evidence_profile.get("evidence_kind")
                raise ValueError(f"Document excluded by evidence classifier: {reason}")
            self.storage.update_document_scifinder_metadata(document_id, evidence_profile)
            parsed_document = parsed or self._parse_with_optional_external(Path(document.file_path))
            visual_metadata = self._extract_visual_evidence(Path(document.file_path), document_id)
            
            ocr_endpoint, _, _, _ = self._integration_endpoint_settings("ocr")
            if self._should_run_ocr(parsed_document) and ocr_endpoint:
                self.storage.update_job(job_id, status="running", stage="ocr")
                parsed_document = self._augment_with_ocr(document.file_path, parsed_document)
            
            struct_endpoint, _, _, _ = self._integration_endpoint_settings("structure_recognition")
            auto_struct = getattr(self.config, 'structure_recognition_auto_on_pdf_evidence', False)
            if auto_struct and visual_metadata and visual_metadata.get("rendered_paths") and struct_endpoint:
                self.storage.update_job(job_id, status="running", stage="vision_structure_extraction")
                vision_smiles = []
                for img_path in visual_metadata["rendered_paths"]:
                    try:
                        structs = self.recognize_structure_image(img_path)
                        if structs.get("status") == "success":
                            for c in structs.get("compounds", []):
                                if c.get("smiles"):
                                    vision_smiles.append(c["smiles"])
                    except Exception:
                        pass
                if vision_smiles:
                    vision_text = "Extracted molecular structures from document images:\n" + "\n".join(vision_smiles)
                    import dataclasses
                    from .parsers import TextChunk
                    new_chunk = TextChunk(text=vision_text, page_number=None, parser_name="vision_llm")
                    parsed_document = dataclasses.replace(parsed_document, chunks=parsed_document.chunks + [new_chunk])
            self.storage.update_document_metadata(document_id, file_type=parsed_document.file_type or detect_file_type(document.file_path), title=parsed_document.title, doi=parsed_document.doi)
            self.storage.replace_parsed_chunks(document_id, parsed_document.chunks)
            if reparse:
                self.storage.clear_document_reactions(document_id)
                
            if parsed_document.file_type == "rdf":
                self.storage.update_job(job_id, status="running", stage="rdf_structure_index")
                self._index_rdf_structures(Path(document.file_path), document_id)
                for rxn in self.storage.list_rdf_reactions(document_id=document_id):
                    self.storage.create_reaction_source_link({
                        "cas_reaction_number": rxn.get("cas_reaction_number"),
                        "source_mode": "rdf_only",
                        "rdf_reaction_id": rxn.get("id"),
                        "rdf_document_id": document_id
                    })
                    
            if parsed_document.file_type == "pdf" and getattr(self.config, 'pdf_evidence_enabled', True):
                from .parsers import _extract_pdf_reaction_evidence
                evidence_rows = _extract_pdf_reaction_evidence(
                    Path(document.file_path),
                    document_id,
                    max_pages=getattr(self.config, "pdf_evidence_max_pages_per_document", 200),
                )
                if evidence_rows:
                    for row in evidence_rows:
                        if getattr(self.config, 'pdf_evidence_render_pages', True) and row.get("cas_reaction_number"):
                            row["rendered_page_image_path"] = self._render_pdf_evidence_page(Path(document.file_path), document_id, row["page_number"])
                    # Create candidates which updates rows with link IDs
                    self._create_pdf_only_candidates(document_id, evidence_rows)
                    # Insert evidence rows after link IDs are set
                    for row in evidence_rows:
                        self.storage.create_pdf_reaction_evidence(row)
                    
            # Always attempt to link existing documents by CAS after parsing any new doc
            self._link_rdf_pdf_by_cas()
            
            self.storage.update_job(job_id, status="running", stage="reaction_extraction")
            extracted = extract_reaction_steps(parsed_document, document_id)
            inserted = []
            for step, provenance in extracted:
                metadata = dict(step.get("metadata") or {})
                if parsed_document.file_type == "rdf":
                    metadata["structured_source"] = "rdf"
                if visual_metadata:
                    metadata.update(visual_metadata)
                    if visual_metadata.get("has_visual_evidence"):
                        provenance = {**provenance, "image_region_path": visual_metadata.get("first_visual_evidence_path")}
                if metadata:
                    step["metadata"] = metadata
                step = self._structure_with_llm(step)
                inserted_step = self.storage.insert_reaction_step(step, provenance)
                inserted.append(inserted_step)
                index_reaction_compounds(self.storage, inserted_step.id, inserted_step.original_text)
            status = "parsed" if inserted else "parsed_no_reactions"
            self.storage.set_document_status(document_id, status)
            self.storage.auto_batch_document(document_id)
            self.storage.update_job(job_id, status="completed", stage="completed")
            if self.config.zotero_linking_enabled and self.config.zotero_linking_on_import and inserted:
                self.enqueue_literature_linking(document_id=document_id)
        except Exception as exc:
            self.storage.set_document_status(document_id, "failed")
            self.storage.update_job(job_id, status="failed", stage="failed", error=str(exc))
            raise

    def _index_rdf_structures(self, path: Path, document_id: str) -> dict[str, int]:
        from .chem import normalize_molfile

        text = path.read_text(encoding="utf-8", errors="ignore")
        records: list[dict[str, Any]] = []
        for record in parse_rdfile_reactions(text):
            data = record.to_dict()
            molecules: list[dict[str, Any]] = []
            for molecule in data.get("molecules") or []:
                normalized = normalize_molfile(molecule.get("molfile"))
                molecules.append({**molecule, "smiles": normalized.smiles, "inchikey": normalized.inchikey, "fingerprint": normalized.fingerprint, "rdkit_status": normalized.status, "rdkit_error": normalized.error})
            data["molecules"] = molecules
            records.append(data)
        return self.storage.upsert_rdf_reaction_records(document_id, records)

    def _parse_with_optional_external(self, path: Path) -> ParsedDocument:
        errors: list[str] = []
        for settings in self._integration_provider_settings("document_parser"):
            adapter = ExternalParserAdapter(
                settings["endpoint"],
                settings["model"],
                settings["api_key"],
                provider=settings["provider_format"],
            )
            if not adapter.configured:
                continue
            try:
                parsed = adapter.parse(str(path))
                if parsed.full_text.strip():
                    return parsed
                raise RuntimeError("document parser returned no text")
            except Exception as exc:
                errors.append(f"{settings['provider_id']}({settings['provider_format']}): {exc}")
                LOGGER.warning("Document parser provider failed provider_id=%s format=%s detail=%s", settings["provider_id"], settings["provider_format"], exc)
                if not self.config.document_parser_fallback:
                    raise
        if errors:
            LOGGER.warning("All external document parser providers failed; falling back to built-in parser: %s", "; ".join(errors))
        return parse_document(path)

    def _extract_visual_evidence(self, path: Path, document_id: str) -> dict[str, Any]:
        if not self.config.extract_visual_evidence or path.suffix.lower() != ".pdf":
            return {}
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError:
            return {}
        evidence_dir = self.config.evidence_dir / document_id
        rendered: list[str] = []
        image_counts: list[int] = []
        drawing_counts: list[int] = []
        try:
            with fitz.open(path) as doc:
                for index, page in enumerate(doc, start=1):
                    images = len(page.get_images(full=True))
                    drawings = len(page.get_drawings())
                    image_counts.append(images)
                    drawing_counts.append(drawings)
                    if not self.config.render_visual_pages or len(rendered) >= self.config.max_visual_pages_per_document:
                        continue
                    if images == 0 and drawings < 30:
                        continue
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                    matrix = fitz.Matrix(self.config.visual_page_dpi / 72, self.config.visual_page_dpi / 72)
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    target = evidence_dir / f"page-{index}.png"
                    pix.save(target)
                    rendered.append(str(target))
        except Exception:
            return {}
        has_visual = bool(rendered or any(image_counts) or any(count >= 30 for count in drawing_counts))
        return {
            "has_visual_evidence": has_visual,
            "visual_evidence_count": len(rendered),
            "first_visual_evidence_path": rendered[0] if rendered else None,
            "pdf_page_image_counts": image_counts,
            "pdf_page_drawing_counts": drawing_counts,
            "needs_visual_review": has_visual,
            "rendered_paths": rendered,
        }

    def _should_run_ocr(self, parsed: ParsedDocument) -> bool:
        return parsed.file_type == "pdf" and len(parsed.full_text.strip()) < 80

    def _augment_with_ocr(self, file_path: str, parsed: ParsedDocument) -> ParsedDocument:
        errors: list[str] = []
        for settings in self._integration_provider_settings("ocr"):
            adapter = OCRAdapter(settings["endpoint"], settings["model"], settings["api_key"], provider=settings["provider_format"])
            if not adapter.configured:
                continue
            try:
                payload = adapter.ocr_document(file_path)
                text = str(payload.get("text") or "")
                if not text.strip():
                    raise RuntimeError("OCR endpoint returned no text")
                parser_name = f"ocr-{settings['provider_format']}"
                chunk = TextChunk(text=text, page_number=None, parser_name=parser_name, parser_version=str(settings["model"] or "external"))
                return ParsedDocument(file_type=parsed.file_type, title=parsed.title, doi=parsed.doi, chunks=[*parsed.chunks, chunk])
            except Exception as exc:
                errors.append(f"{settings['provider_id']}({settings['provider_format']}): {exc}")
                LOGGER.warning("OCR provider failed provider_id=%s format=%s detail=%s", settings["provider_id"], settings["provider_format"], exc)
        detail = "; ".join(errors) if errors else "OCR endpoint is not configured"
        raise RuntimeError(f"All OCR providers failed: {detail}")

    def _structure_with_llm(self, step: dict[str, Any]) -> dict[str, Any]:
        llm_enabled = bool(self.config.extraction_provider_id)
        endpoint, model, provider, api_key = self._integration_endpoint_settings("extraction")
        adapter = LLMStructuringAdapter(
            endpoint,
            model,
            enabled=llm_enabled,
            schema_version=getattr(self.config, "llm_schema_version", "reaction_step.v1"),
            prompt_profile=getattr(self.config, "llm_prompt_profile", "strict-reaction-json"),
            provider=provider,
            api_key=api_key,
        )
        step.setdefault("extraction_method", "rules")
        step.setdefault("schema_version", getattr(self.config, "llm_schema_version", "reaction_step.v1"))
        step.setdefault("metadata", {})
        if not adapter.configured:
            return step
        rule_fields = {key: step.get(key) for key in ["reaction_name", "substrate_text", "product_text", "reagent_text", "catalyst_text", "solvent_text", "temperature", "time", "atmosphere", "yield_text", "scale", "workup", "purification"]}
        try:
            llm = adapter.structure(str(step.get("original_text") or ""), rule_fields)
            if not llm:
                return step
            for key in rule_fields:
                if llm.get(key) is not None:
                    step[key] = llm[key]
            if isinstance(llm.get("confidence"), (int, float)):
                step["llm_confidence"] = float(llm["confidence"])
                step["confidence"] = max(float(step.get("confidence") or 0), float(llm["confidence"]))
            step["extraction_method"] = "rules+llm"
            step["schema_version"] = getattr(self.config, "llm_schema_version", "reaction_step.v1")
            step["metadata"] = {**dict(step.get("metadata") or {}), "llm_prompt_profile": getattr(self.config, "llm_prompt_profile", "strict-reaction-json")}
        except Exception as exc:
            step["metadata"] = {**dict(step.get("metadata") or {}), "llm_error": str(exc), "llm_fallback": "rules"}
        return step

    def _start_or_run_job(self, document_id: str, job_id: str, *, parsed: ParsedDocument | None = None, reparse: bool = False) -> None:
        if not self.config.async_jobs:
            self._process_document(document_id, job_id, parsed=parsed, reparse=reparse)
            return
        # Durable queue mode: the worker loop claims queued jobs from SQLite after this method returns.

    def _start_workers(self) -> None:
        self.storage.recover_interrupted_jobs(mode="queued")
        self._stop_event.clear()
        for index in range(self.config.max_workers):
            worker = threading.Thread(target=self._worker_loop, name=f"route-parser-{index + 1}", daemon=True)
            worker.start()
            self._workers.append(worker)

    def _restart_workers(self) -> None:
        self.shutdown()
        self._stop_event = threading.Event()
        if self.config.async_jobs:
            self._start_workers()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self.storage.claim_next_job()
            except Exception as exc:
                if not is_sqlite_locked_error(exc):
                    raise
                self._stop_event.wait(0.5)
                continue
            if not job:
                self._stop_event.wait(0.2)
                continue
            with self._active_jobs_lock:
                self._active_jobs.add(job.id)
            try:
                self._process_document(job.document_id, job.id)
            except Exception:
                pass
            finally:
                with self._active_jobs_lock:
                    self._active_jobs.discard(job.id)

    def _assert_allowed_path(self, path: Path) -> None:
        if self.config.allow_external_paths:
            return
        allowed_roots = [self.config.inbox_dir.resolve(), self.config.upload_dir.resolve(), self.config.data_dir.resolve()]
        if not any(path == root or path.is_relative_to(root) for root in allowed_roots):
            raise ValueError(f"Path is outside allowed NAS roots: {path}")

    def _resolve_storage_backend_status(self) -> dict[str, Any]:
        if self.config.storage_backend == "sqlite":
            return {"configured": True, "active": "sqlite", "status": "ok", "detail": "SQLite backend active"}
        if self.config.storage_backend == "postgres":
            if not self.config.postgres_url:
                return {"configured": False, "active": "sqlite", "status": "degraded", "detail": "PostgreSQL URL missing; SQLite fallback active"}
            try:
                import psycopg  # type: ignore[import-not-found]

                with psycopg.connect(self.config.postgres_url, connect_timeout=3) as conn:
                    conn.execute("SELECT 1")
                return {"configured": True, "active": "postgres", "status": "available", "detail": "PostgreSQL connection succeeds; SQLite-compatible storage remains active in this lightweight build"}
            except Exception as exc:
                return {"configured": True, "active": "sqlite", "status": "degraded", "detail": f"PostgreSQL unavailable; SQLite fallback active: {exc}"}
        return {"configured": False, "active": "sqlite", "status": "degraded", "detail": f"Unknown backend {self.config.storage_backend}; SQLite fallback active"}

    def _test_postgres(self, postgres_url: str | None = None) -> dict[str, Any]:
        postgres_url = postgres_url or self.config.postgres_url
        if not postgres_url:
            return {"configured": False, "status": "unknown", "detail": "PostgreSQL URL is not configured"}
        try:
            import psycopg  # type: ignore[import-not-found]

            with psycopg.connect(postgres_url, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return {"configured": True, "status": "ok", "detail": "PostgreSQL connection succeeded"}
        except Exception as exc:
            return {"configured": True, "status": "error", "detail": f"{type(exc).__name__}: {exc}"}


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_target(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def safe_filename(value: str) -> str:
    name = Path(value).name.strip()
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in name)
    return cleaned or "upload"


def detect_dangerous_payload(content: bytes) -> str | None:
    head = content[:8192].lstrip()
    checks = [
        (b"MZ", "windows-executable"),
        (b"\x7fELF", "elf-executable"),
        (b"\xcf\xfa\xed\xfe", "mach-o-binary"),
        (b"\xfe\xed\xfa\xcf", "mach-o-binary"),
        (b"PK\x03\x04", "zip-or-office-container"),
        (b"Rar!", "rar-archive"),
        (b"7z\xbc\xaf\x27\x1c", "7z-archive"),
    ]
    for magic, label in checks:
        if head.startswith(magic):
            return label
    text = content[:65536].decode("latin-1", errors="ignore").lower()
    if "\\object" in text or "\\objdata" in text:
        return "rtf-embedded-object"
    if re.search(r"(?:^|\n)\s*(?:powershell|cmd\.exe|#!/bin/sh|#!/bin/bash|wscript\.shell)\b", text):
        return "script-like-text"
    return None


def directory_usage(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False, "files": 0, "bytes": 0}
    files = 0
    bytes_total = 0
    for item in path.rglob("*") if path.is_dir() else [path]:
        if item.is_file():
            files += 1
            try:
                bytes_total += item.stat().st_size
            except OSError:
                pass
    return {"path": str(path), "exists": True, "files": files, "bytes": bytes_total}


def build_zotero_note(step: dict[str, Any], link: dict[str, Any]) -> str:
    diff_lines = []
    for field, data in (link.get("field_diff") or {}).items():
        diff_lines.append(f"- {field}: {data.get('status')} | SciFinder={data.get('scifinder') or ''} | Literature={data.get('literature') or ''}")
    return "\n".join(
        [
            "# Linked SciFinder Route",
            f"Reaction step: {step.get('id')}",
            f"Linked DOI: {link.get('doi') or ''}",
            f"Linked title: {link.get('title') or ''}",
            "",
            "## SciFinder method",
            str(step.get("original_text") or ""),
            "",
            "## Literature/SI excerpt",
            str(link.get("method_excerpt") or ""),
            "",
            "## Field differences",
            "\n".join(diff_lines) if diff_lines else "No field-level differences detected.",
        ]
    )
