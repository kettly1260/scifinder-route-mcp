from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .config import AppConfig
from .extractor import extract_reaction_steps
from .integrations import EmbeddingAdapter, ExternalParserAdapter, LLMStructuringAdapter, OCRAdapter, StructureRecognitionAdapter, test_http_endpoint
from .parsers import ParsedDocument, TextChunk, detect_file_type, parse_document
from .registry import index_reaction_compounds, normalize_with_rdkit
from .storage import RouteStorage


class RouteService:
    def __init__(self, config: AppConfig | None = None, storage: RouteStorage | None = None):
        self.config = config or AppConfig.from_env()
        self.config.ensure_directories()
        self.storage_backend_status = self._resolve_storage_backend_status()
        self.storage = storage or RouteStorage(self.config.database_path)
        self._stop_event = threading.Event()
        self._workers: list[threading.Thread] = []
        self._active_jobs: set[str] = set()
        self._active_jobs_lock = threading.Lock()
        if self.config.async_jobs:
            self._start_workers()

    def get_config(self, include_secrets: bool = False) -> dict[str, Any]:
        return self.config.effective_config(include_secrets=include_secrets)

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
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
        return {
            "valid": not warnings,
            "warnings": warnings,
            "hot_reloadable_sections": ["server", "security", "ingest", "integrations", "thresholds", "queue", "extraction", "retention"],
            "restart_required_for": [
                "SCIFINDER_ROUTE_PUBLISHED_PORT",
                "SCIFINDER_ROUTE_PORT",
                "SCIFINDER_ROUTE_TRANSPORT",
                "volume mounts",
                "container network",
            ],
        }

    def register_document(self, file_path: str, reparse: bool = False) -> dict[str, Any]:
        path = Path(file_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Document does not exist: {path}")
        self._assert_allowed_path(path)
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
        self._start_or_run_job(document.id, job.id, parsed=parsed, reparse=reparse)
        completed_job = self.storage.get_job(job.id)
        return {"document": self.storage.get_document(document.id).to_dict(), "job": completed_job.to_dict() if completed_job else job.to_dict()}

    def upload_document(self, source_path: str, filename: str | None = None, reparse: bool = False) -> dict[str, Any]:
        source = Path(source_path).resolve()
        if not source.exists():
            raise FileNotFoundError(f"Upload source does not exist: {source}")
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

    def retry_parse_job(self, job_id: str) -> dict[str, Any]:
        job = self.storage.retry_job(job_id)
        return job.to_dict()

    def retry_failed_jobs(self, limit: int = 100) -> dict[str, Any]:
        jobs = self.storage.retry_failed_jobs(limit=limit)
        return {"retried": [job.to_dict() for job in jobs], "count": len(jobs)}

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "database": str(self.config.database_path),
            "data_dir": str(self.config.data_dir),
            "inbox_dir": str(self.config.inbox_dir),
            "upload_dir": str(self.config.upload_dir),
            "config_path": str(self.config.config_path),
            "async_jobs": self.config.async_jobs,
            "queue_backend": self.config.queue_backend,
            "storage_backend": self.config.storage_backend,
            "storage_backend_status": self.storage_backend_status,
            "scan_extensions": list(self.config.scan_extensions),
            "config_warnings": self.config.validate(),
            "documents": self.storage.count_documents(),
            "reaction_steps": self.storage.count_reaction_steps(),
            "vector_index": self.storage.vector_index_status(),
            "ocr_backlog": self.storage.count_ocr_backlog(),
            "integrations": self.storage.list_integration_statuses(),
        }

    def shutdown(self) -> None:
        self._stop_event.set()
        for worker in self._workers:
            worker.join(timeout=5)
        self._workers = []

    def search_reaction_steps(self, query: str = "", reagent: str = "", solvent: str = "", document_id: str = "", min_confidence: float = 0.0, limit: int = 10) -> list[dict[str, Any]]:
        steps = self.storage.search_reaction_steps(query=query, reagent=reagent, solvent=solvent, document_id=document_id, min_confidence=min_confidence, limit=limit)
        return [step.to_dict() for step in steps]

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
        adapter = EmbeddingAdapter(self.config.embedding_endpoint, self.config.embedding_model)
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
        return {"configured": bool(self.config.embedding_endpoint), **self.storage.vector_index_status()}

    def semantic_search_reaction_steps(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        adapter = EmbeddingAdapter(self.config.embedding_endpoint, self.config.embedding_model)
        if not adapter.configured:
            return []
        vector = adapter.embed([query])[0]
        return [{**step.to_dict(), "semantic_score": score} for step, score in self.storage.semantic_search(vector, limit=limit)]

    def test_integration_endpoint(self, kind: str) -> dict[str, Any]:
        endpoints = {
            "llm": (self.config.llm_endpoint, self.config.llm_model),
            "embedding": (self.config.embedding_endpoint, self.config.embedding_model),
            "ocr": (self.config.ocr_endpoint, self.config.ocr_model),
            "document_parser": (self.config.document_parser_endpoint, self.config.document_parser_model),
            "structure_recognition": (self.config.structure_recognition_endpoint, self.config.structure_recognition_model),
        }
        if kind == "postgres":
            result = self._test_postgres()
            return self.storage.record_integration_status("postgres", **result)
        if kind not in endpoints:
            raise ValueError(f"Unknown integration kind: {kind}")
        endpoint, model = endpoints[kind]
        result = test_http_endpoint(endpoint, model=model)
        return self.storage.record_integration_status(kind, configured=result.configured, status=result.status, detail=result.detail)

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

    def recognize_structure_image(self, image_path: str, reaction_step_id: str | None = None) -> dict[str, Any]:
        adapter = StructureRecognitionAdapter(self.config.structure_recognition_endpoint, self.config.structure_recognition_model)
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
        return {
            "health": self.health_check(),
            "vector_index": self.get_vector_index_status(),
            "evaluation": self.get_evaluation_status(),
            "storage_usage": self.get_storage_usage(),
            "doi_low_confidence_queue": self.storage.low_confidence_doi_queue(self.config.verification_confidence_threshold, limit=20),
            "compound_count": len(self.search_compounds(limit=1000)),
        }

    def _process_document(self, document_id: str, job_id: str, *, parsed: ParsedDocument | None = None, reparse: bool = False) -> None:
        document = self.storage.get_document(document_id)
        if not document:
            raise KeyError(f"Document not found: {document_id}")
        try:
            self.storage.update_job(job_id, status="running", stage="document_parse")
            parsed_document = parsed or self._parse_with_optional_external(Path(document.file_path))
            if self._should_run_ocr(parsed_document) and self.config.ocr_endpoint:
                self.storage.update_job(job_id, status="running", stage="ocr")
                parsed_document = self._augment_with_ocr(document.file_path, parsed_document)
            self.storage.update_document_metadata(document_id, file_type=parsed_document.file_type or detect_file_type(document.file_path), title=parsed_document.title, doi=parsed_document.doi)
            if reparse:
                self.storage.clear_document_reactions(document_id)
            self.storage.update_job(job_id, status="running", stage="reaction_extraction")
            extracted = extract_reaction_steps(parsed_document, document_id)
            inserted = []
            for step, provenance in extracted:
                step = self._structure_with_llm(step)
                inserted_step = self.storage.insert_reaction_step(step, provenance)
                inserted.append(inserted_step)
                index_reaction_compounds(self.storage, inserted_step.id, inserted_step.original_text)
            status = "parsed" if inserted else "parsed_no_reactions"
            self.storage.set_document_status(document_id, status)
            self.storage.update_job(job_id, status="completed", stage="completed")
        except Exception as exc:
            self.storage.set_document_status(document_id, "failed")
            self.storage.update_job(job_id, status="failed", stage="failed", error=str(exc))
            raise

    def _parse_with_optional_external(self, path: Path) -> ParsedDocument:
        adapter = ExternalParserAdapter(self.config.document_parser_endpoint, self.config.document_parser_model)
        if adapter.configured:
            try:
                return adapter.parse(str(path))
            except Exception:
                if not self.config.parser_fallback:
                    raise
        return parse_document(path)

    def _should_run_ocr(self, parsed: ParsedDocument) -> bool:
        return parsed.file_type == "pdf" and len(parsed.full_text.strip()) < 80

    def _augment_with_ocr(self, file_path: str, parsed: ParsedDocument) -> ParsedDocument:
        adapter = OCRAdapter(self.config.ocr_endpoint, self.config.ocr_model)
        payload = adapter.ocr_document(file_path)
        text = str(payload.get("text") or "")
        confidence = payload.get("confidence")
        if not text.strip():
            raise RuntimeError("OCR endpoint returned no text for image-only document")
        chunk = TextChunk(text=text, page_number=None, parser_name="ocr-external", parser_version=str(self.config.ocr_model or "external"))
        return ParsedDocument(file_type=parsed.file_type, title=parsed.title, doi=parsed.doi, chunks=[*parsed.chunks, chunk])

    def _structure_with_llm(self, step: dict[str, Any]) -> dict[str, Any]:
        adapter = LLMStructuringAdapter(
            self.config.llm_endpoint,
            self.config.llm_model,
            enabled=self.config.llm_enabled,
            schema_version=self.config.llm_schema_version,
            prompt_profile=self.config.llm_prompt_profile,
        )
        step.setdefault("extraction_method", "rules")
        step.setdefault("schema_version", self.config.llm_schema_version)
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
            step["schema_version"] = self.config.llm_schema_version
            step["metadata"] = {**dict(step.get("metadata") or {}), "llm_prompt_profile": self.config.llm_prompt_profile}
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
            job = self.storage.claim_next_job()
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

    def _test_postgres(self) -> dict[str, Any]:
        if not self.config.postgres_url:
            return {"configured": False, "status": "unknown", "detail": "PostgreSQL URL is not configured"}
        try:
            import psycopg  # type: ignore[import-not-found]

            with psycopg.connect(self.config.postgres_url, connect_timeout=3) as conn:
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
