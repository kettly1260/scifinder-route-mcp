from __future__ import annotations

import hashlib
import json
import shutil
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .config import AppConfig
from .extractor import extract_reaction_steps
from .parsers import detect_file_type, parse_document
from .storage import RouteStorage


class RouteService:
    def __init__(self, config: AppConfig | None = None, storage: RouteStorage | None = None):
        self.config = config or AppConfig.from_env()
        self.config.ensure_directories()
        self.storage = storage or RouteStorage(self.config.database_path)
        self._executor: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future[None]] = {}
        self._futures_lock = threading.Lock()
        if self.config.async_jobs:
            self._executor = ThreadPoolExecutor(max_workers=self.config.max_workers, thread_name_prefix="route-parser")

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
        if old_async != self.config.async_jobs or old_workers != self.config.max_workers:
            self._restart_executor()
        return self.get_config()

    def validate_config(self) -> dict[str, Any]:
        warnings = self.config.validate()
        return {
            "valid": not warnings,
            "warnings": warnings,
            "hot_reloadable_sections": ["server", "security", "ingest", "integrations", "thresholds"],
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
            return {
                "document": existing.to_dict(),
                "job": None,
                "deduplicated": True,
            }
        parsed = None if self.config.async_jobs else parse_document(path)
        document, job = self.storage.create_queued_document_job(
            file_path=str(path),
            file_hash=file_hash,
            file_type=parsed.file_type if parsed else detect_file_type(path),
            title=parsed.title if parsed else None,
            doi=parsed.doi if parsed else None,
        )
        self._start_or_run_job(document.id, job.id, parsed=parsed, reparse=reparse)
        completed_job = self.storage.get_job(job.id)
        return {
            "document": self.storage.get_document(document.id).to_dict(),
            "job": completed_job.to_dict() if completed_job else job.to_dict(),
        }

    def upload_document(self, source_path: str, filename: str | None = None, reparse: bool = False) -> dict[str, Any]:
        source = Path(source_path).resolve()
        if not source.exists():
            raise FileNotFoundError(f"Upload source does not exist: {source}")
        safe_name = safe_filename(filename or source.name)
        target = unique_target(self.config.upload_dir / safe_name)
        shutil.copy2(source, target)
        result = self.register_document(str(target), reparse=reparse)
        result["uploaded_path"] = str(target)
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

    def health_check(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "database": str(self.config.database_path),
            "data_dir": str(self.config.data_dir),
            "inbox_dir": str(self.config.inbox_dir),
            "upload_dir": str(self.config.upload_dir),
            "config_path": str(self.config.config_path),
            "async_jobs": self.config.async_jobs,
            "scan_extensions": list(self.config.scan_extensions),
            "config_warnings": self.config.validate(),
            "documents": self.storage.count_documents(),
            "reaction_steps": self.storage.count_reaction_steps(),
        }

    def shutdown(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None

    def _restart_executor(self) -> None:
        self.shutdown()
        if self.config.async_jobs:
            self._executor = ThreadPoolExecutor(max_workers=self.config.max_workers, thread_name_prefix="route-parser")

    def search_reaction_steps(
        self,
        query: str = "",
        reagent: str = "",
        solvent: str = "",
        document_id: str = "",
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        steps = self.storage.search_reaction_steps(
            query=query,
            reagent=reagent,
            solvent=solvent,
            document_id=document_id,
            min_confidence=min_confidence,
            limit=limit,
        )
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
        return {
            "document": self.storage.get_document(document.id).to_dict(),
            "job": completed_job.to_dict() if completed_job else job.to_dict(),
        }

    def record_doi_verification(
        self,
        reaction_step_id: str,
        doi: str,
        verified_fields: dict[str, Any],
        paper_title: str | None = None,
        original_paper_excerpt: str | None = None,
        verification_confidence: float = 0.0,
        verifier_agent: str | None = None,
    ) -> dict[str, Any]:
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

    def _process_document(self, document_id: str, job_id: str, *, parsed: Any | None = None, reparse: bool = False) -> None:
        document = self.storage.get_document(document_id)
        if not document:
            raise KeyError(f"Document not found: {document_id}")
        try:
            self.storage.update_job(job_id, status="running", stage="document_parse")
            parsed_document = parsed or parse_document(document.file_path)
            self.storage.update_document_metadata(
                document_id,
                file_type=parsed_document.file_type or detect_file_type(document.file_path),
                title=parsed_document.title,
                doi=parsed_document.doi,
            )
            if reparse:
                self.storage.clear_document_reactions(document_id)
            self.storage.update_job(job_id, status="running", stage="reaction_extraction")
            extracted = extract_reaction_steps(parsed_document, document_id)
            for step, provenance in extracted:
                self.storage.insert_reaction_step(step, provenance)
            status = "parsed" if extracted else "parsed_no_reactions"
            self.storage.set_document_status(document_id, status)
            self.storage.update_job(job_id, status="completed", stage="completed")
        except Exception as exc:
            self.storage.set_document_status(document_id, "failed")
            self.storage.update_job(job_id, status="failed", stage="failed", error=str(exc))
            raise

    def _start_or_run_job(self, document_id: str, job_id: str, *, parsed: Any | None = None, reparse: bool = False) -> None:
        if not self._executor:
            self._process_document(document_id, job_id, parsed=parsed, reparse=reparse)
            return
        future = self._executor.submit(self._process_document, document_id, job_id, parsed=parsed, reparse=reparse)
        with self._futures_lock:
            self._futures[job_id] = future
        future.add_done_callback(lambda _future: self._drop_future(job_id))

    def _drop_future(self, job_id: str) -> None:
        with self._futures_lock:
            self._futures.pop(job_id, None)

    def _assert_allowed_path(self, path: Path) -> None:
        if self.config.allow_external_paths:
            return
        allowed_roots = [self.config.inbox_dir.resolve(), self.config.upload_dir.resolve(), self.config.data_dir.resolve()]
        if not any(path == root or path.is_relative_to(root) for root in allowed_roots):
            raise ValueError(f"Path is outside allowed NAS roots: {path}")


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
