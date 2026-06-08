from __future__ import annotations

import json
import math
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import Compound, ParseJob, Provenance, ReactionStep, SourceDocument


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class RouteStorage:
    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_document (
                    id TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    title TEXT,
                    doi TEXT,
                    scifinder_metadata TEXT NOT NULL DEFAULT '{}',
                    ingest_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(file_hash, file_path)
                );

                CREATE TABLE IF NOT EXISTS parse_job (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS reaction_step (
                    id TEXT PRIMARY KEY,
                    source_document_id TEXT NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
                    step_index INTEGER NOT NULL,
                    reaction_name TEXT,
                    substrate_text TEXT,
                    product_text TEXT,
                    reagent_text TEXT,
                    catalyst_text TEXT,
                    solvent_text TEXT,
                    temperature TEXT,
                    time TEXT,
                    atmosphere TEXT,
                    yield_text TEXT,
                    scale TEXT,
                    workup TEXT,
                    purification TEXT,
                    original_text TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    verification_status TEXT NOT NULL,
                    needs_ocr INTEGER NOT NULL DEFAULT 0,
                    extraction_method TEXT NOT NULL DEFAULT 'rules',
                    schema_version TEXT NOT NULL DEFAULT 'reaction_step.v1',
                    llm_confidence REAL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS provenance (
                    id TEXT PRIMARY KEY,
                    reaction_step_id TEXT NOT NULL REFERENCES reaction_step(id) ON DELETE CASCADE,
                    source_document_id TEXT NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
                    page_number INTEGER,
                    text_span TEXT NOT NULL,
                    image_region_path TEXT,
                    ocr_output TEXT,
                    parser_name TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    confidence REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS doi_verification (
                    id TEXT PRIMARY KEY,
                    reaction_step_id TEXT NOT NULL REFERENCES reaction_step(id) ON DELETE CASCADE,
                    doi TEXT NOT NULL,
                    paper_title TEXT,
                    verified_fields TEXT NOT NULL,
                    original_paper_excerpt TEXT,
                    verification_confidence REAL NOT NULL,
                    verifier_agent TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS reaction_step_fts USING fts5(
                    reaction_step_id UNINDEXED,
                    content
                );

                CREATE TABLE IF NOT EXISTS vector_index (
                    reaction_step_id TEXT PRIMARY KEY REFERENCES reaction_step(id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS integration_status (
                    kind TEXT PRIMARY KEY,
                    configured INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    checked_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evaluation_metric (
                    id TEXT PRIMARY KEY,
                    gold_set_path TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS compound (
                    id TEXT PRIMARY KEY,
                    primary_name TEXT NOT NULL,
                    cas TEXT,
                    smiles TEXT,
                    canonical_smiles TEXT,
                    inchikey TEXT,
                    fingerprint TEXT,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS compound_alias (
                    id TEXT PRIMARY KEY,
                    compound_id TEXT NOT NULL REFERENCES compound(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL,
                    alias_type TEXT NOT NULL,
                    UNIQUE(compound_id, alias, alias_type)
                );

                CREATE TABLE IF NOT EXISTS reaction_compound_role (
                    id TEXT PRIMARY KEY,
                    reaction_step_id TEXT NOT NULL REFERENCES reaction_step(id) ON DELETE CASCADE,
                    compound_id TEXT NOT NULL REFERENCES compound(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source TEXT NOT NULL
                );
                """
            )
            self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(reaction_step)")}
        migrations = {
            "extraction_method": "ALTER TABLE reaction_step ADD COLUMN extraction_method TEXT NOT NULL DEFAULT 'rules'",
            "schema_version": "ALTER TABLE reaction_step ADD COLUMN schema_version TEXT NOT NULL DEFAULT 'reaction_step.v1'",
            "llm_confidence": "ALTER TABLE reaction_step ADD COLUMN llm_confidence REAL",
            "metadata": "ALTER TABLE reaction_step ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'",
        }
        for column, statement in migrations.items():
            if column not in columns:
                conn.execute(statement)

    def recover_interrupted_jobs(self, *, mode: str = "queued") -> int:
        status = "queued" if mode == "queued" else "failed"
        error = None if status == "queued" else "Job was interrupted by service shutdown"
        with self.connect() as conn:
            rows = conn.execute("SELECT id FROM parse_job WHERE status = 'running'").fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE parse_job SET status = ?, stage = ?, error = ?, finished_at = NULL WHERE id = ?",
                    (status, "queued" if status == "queued" else "failed", error, row["id"]),
                )
            return len(rows)

    def claim_next_job(self) -> ParseJob | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM parse_job WHERE status = 'queued' ORDER BY COALESCE(started_at, ''), id LIMIT 1"
            ).fetchone()
            if not row:
                conn.commit()
                return None
            conn.execute(
                "UPDATE parse_job SET status = 'running', stage = 'document_parse', error = NULL, started_at = ?, finished_at = NULL WHERE id = ?",
                (utc_now(), row["id"]),
            )
            conn.commit()
            claimed = conn.execute("SELECT * FROM parse_job WHERE id = ?", (row["id"],)).fetchone()
        return self._job_from_row(claimed)

    def retry_job(self, job_id: str) -> ParseJob:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM parse_job WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise KeyError(f"Parse job not found: {job_id}")
            if row["status"] not in {"failed", "completed"}:
                raise ValueError(f"Only failed/completed jobs can be retried; current status is {row['status']}")
            conn.execute(
                "UPDATE parse_job SET status = 'queued', stage = 'queued', error = NULL, finished_at = NULL WHERE id = ?",
                (job_id,),
            )
            updated = conn.execute("SELECT * FROM parse_job WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(updated)

    def retry_failed_jobs(self, limit: int = 100) -> list[ParseJob]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id FROM parse_job WHERE status = 'failed' ORDER BY COALESCE(finished_at, '') DESC LIMIT ?", (limit,)).fetchall()
        return [self.retry_job(row["id"]) for row in rows]

    def upsert_document(
        self,
        *,
        file_path: str,
        file_hash: str,
        file_type: str,
        title: str | None,
        doi: str | None,
        ingest_status: str,
    ) -> SourceDocument:
        now = utc_now()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT * FROM source_document WHERE file_hash = ? AND file_path = ?",
                (file_hash, file_path),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE source_document
                    SET file_type = ?, title = ?, doi = ?, ingest_status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (file_type, title, doi, ingest_status, now, existing["id"]),
                )
                row = conn.execute("SELECT * FROM source_document WHERE id = ?", (existing["id"],)).fetchone()
            else:
                document_id = new_id("doc")
                conn.execute(
                    """
                    INSERT INTO source_document
                    (id, file_path, file_hash, file_type, title, doi, ingest_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (document_id, file_path, file_hash, file_type, title, doi, ingest_status, now, now),
                )
                row = conn.execute("SELECT * FROM source_document WHERE id = ?", (document_id,)).fetchone()
        return self._document_from_row(row)

    def set_document_status(self, document_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE source_document SET ingest_status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), document_id),
            )

    def update_document_metadata(self, document_id: str, *, file_type: str, title: str | None, doi: str | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE source_document SET file_type = ?, title = ?, doi = ?, updated_at = ? WHERE id = ?",
                (file_type, title, doi, utc_now(), document_id),
            )

    def create_job(self, document_id: str, *, status: str = "queued", stage: str = "queued") -> ParseJob:
        job_id = new_id("job")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO parse_job (id, document_id, status, stage, started_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, document_id, status, stage, utc_now() if status == "running" else None),
            )
            row = conn.execute("SELECT * FROM parse_job WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row)

    def create_queued_document_job(
        self,
        *,
        file_path: str,
        file_hash: str,
        file_type: str,
        title: str | None,
        doi: str | None,
    ) -> tuple[SourceDocument, ParseJob]:
        document = self.upsert_document(
            file_path=file_path,
            file_hash=file_hash,
            file_type=file_type,
            title=title,
            doi=doi,
            ingest_status="queued",
        )
        job = self.create_job(document.id)
        return document, job

    def update_job(self, job_id: str, *, status: str, stage: str, error: str | None = None) -> None:
        finished_at = utc_now() if status in {"completed", "failed"} else None
        started_at = utc_now() if status == "running" else None
        with self.connect() as conn:
            if started_at:
                conn.execute(
                    "UPDATE parse_job SET status = ?, stage = ?, error = ?, started_at = COALESCE(started_at, ?) WHERE id = ?",
                    (status, stage, error, started_at, job_id),
                )
            else:
                conn.execute(
                    "UPDATE parse_job SET status = ?, stage = ?, error = ?, finished_at = COALESCE(?, finished_at) WHERE id = ?",
                    (status, stage, error, finished_at, job_id),
                )

    def get_job(self, job_id: str) -> ParseJob | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM parse_job WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def list_jobs(self, *, status: str = "", limit: int = 100) -> list[ParseJob]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM parse_job WHERE status = ? ORDER BY COALESCE(started_at, '') DESC, id DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM parse_job ORDER BY COALESCE(started_at, '') DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def get_document(self, document_id: str) -> SourceDocument | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM source_document WHERE id = ?", (document_id,)).fetchone()
        return self._document_from_row(row) if row else None

    def get_document_by_hash_path(self, *, file_hash: str, file_path: str) -> SourceDocument | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_document WHERE file_hash = ? AND file_path = ?",
                (file_hash, file_path),
            ).fetchone()
        return self._document_from_row(row) if row else None

    def get_document_by_hash(self, file_hash: str) -> SourceDocument | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_document WHERE file_hash = ? ORDER BY created_at ASC LIMIT 1",
                (file_hash,),
            ).fetchone()
        return self._document_from_row(row) if row else None

    def count_documents(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM source_document").fetchone()
        return int(row["total"])

    def count_reaction_steps(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM reaction_step").fetchone()
        return int(row["total"])

    def clear_document_reactions(self, document_id: str) -> None:
        with self.connect() as conn:
            step_ids = [row["id"] for row in conn.execute("SELECT id FROM reaction_step WHERE source_document_id = ?", (document_id,))]
            for step_id in step_ids:
                conn.execute("DELETE FROM reaction_step_fts WHERE reaction_step_id = ?", (step_id,))
            conn.execute("DELETE FROM reaction_step WHERE source_document_id = ?", (document_id,))

    def insert_reaction_step(self, step: dict[str, Any], provenance: dict[str, Any]) -> ReactionStep:
        step_id = new_id("rxnstep")
        provenance_id = new_id("prov")
        created_at = utc_now()
        values = {
            "id": step_id,
            "reaction_name": None,
            "substrate_text": None,
            "product_text": None,
            "reagent_text": None,
            "catalyst_text": None,
            "solvent_text": None,
            "temperature": None,
            "time": None,
            "atmosphere": None,
            "yield_text": None,
            "scale": None,
            "workup": None,
            "purification": None,
            "verification_status": "unverified",
            "needs_ocr": False,
            **step,
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO reaction_step
                (id, source_document_id, step_index, reaction_name, substrate_text, product_text,
                 reagent_text, catalyst_text, solvent_text, temperature, time, atmosphere, yield_text,
                 scale, workup, purification, original_text, confidence, verification_status, needs_ocr,
                 extraction_method, schema_version, llm_confidence, metadata, created_at)
                 VALUES
                 (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["id"],
                    values["source_document_id"],
                    values["step_index"],
                    values["reaction_name"],
                    values["substrate_text"],
                    values["product_text"],
                    values["reagent_text"],
                    values["catalyst_text"],
                    values["solvent_text"],
                    values["temperature"],
                    values["time"],
                    values["atmosphere"],
                    values["yield_text"],
                    values["scale"],
                    values["workup"],
                    values["purification"],
                    values["original_text"],
                    values["confidence"],
                    values["verification_status"],
                    1 if values["needs_ocr"] else 0,
                    values.get("extraction_method", "rules"),
                    values.get("schema_version", "reaction_step.v1"),
                    values.get("llm_confidence"),
                    json.dumps(values.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
            fts_content = "\n".join(str(values.get(key) or "") for key in (
                "reaction_name",
                "substrate_text",
                "product_text",
                "reagent_text",
                "catalyst_text",
                "solvent_text",
                "temperature",
                "time",
                "yield_text",
                "scale",
                "workup",
                "purification",
                "original_text",
            ))
            conn.execute(
                "INSERT INTO reaction_step_fts (reaction_step_id, content) VALUES (?, ?)",
                (step_id, fts_content),
            )
            conn.execute(
                """
                INSERT INTO provenance
                (id, reaction_step_id, source_document_id, page_number, text_span, image_region_path,
                 ocr_output, parser_name, parser_version, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provenance_id,
                    step_id,
                    values["source_document_id"],
                    provenance.get("page_number"),
                    provenance["text_span"],
                    provenance.get("image_region_path"),
                    provenance.get("ocr_output"),
                    provenance["parser_name"],
                    provenance["parser_version"],
                    provenance["confidence"],
                ),
            )
            row = conn.execute("SELECT * FROM reaction_step WHERE id = ?", (step_id,)).fetchone()
        return self._reaction_from_row(row)

    def search_reaction_steps(
        self,
        *,
        query: str = "",
        reagent: str = "",
        solvent: str = "",
        document_id: str = "",
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[ReactionStep]:
        clauses = ["r.confidence >= ?"]
        params: list[Any] = [min_confidence]
        if reagent:
            clauses.append("LOWER(COALESCE(r.reagent_text, '')) LIKE ?")
            params.append(f"%{reagent.lower()}%")
        if solvent:
            clauses.append("LOWER(COALESCE(r.solvent_text, '')) LIKE ?")
            params.append(f"%{solvent.lower()}%")
        if document_id:
            clauses.append("r.source_document_id = ?")
            params.append(document_id)

        with self.connect() as conn:
            if query:
                rows = conn.execute(
                    f"""
                    SELECT r.*
                    FROM reaction_step r
                    JOIN reaction_step_fts f ON f.reaction_step_id = r.id
                    WHERE {' AND '.join(clauses)} AND f.content MATCH ?
                    ORDER BY r.confidence DESC, r.step_index ASC
                    LIMIT ?
                    """,
                    (*params, self._fts_query(query), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT r.* FROM reaction_step r
                    WHERE {' AND '.join(clauses)}
                    ORDER BY r.confidence DESC, r.step_index ASC
                    LIMIT ?
                    """,
                    (*params, limit),
                ).fetchall()
        return [self._reaction_from_row(row) for row in rows]

    def get_reaction_step(self, reaction_step_id: str) -> ReactionStep | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM reaction_step WHERE id = ?", (reaction_step_id,)).fetchone()
        return self._reaction_from_row(row) if row else None

    def get_provenance(self, reaction_step_id: str) -> list[Provenance]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM provenance WHERE reaction_step_id = ? ORDER BY id",
                (reaction_step_id,),
            ).fetchall()
        return [self._provenance_from_row(row) for row in rows]

    def add_provenance(self, reaction_step_id: str, source_document_id: str, *, text_span: str, parser_name: str, parser_version: str = "external", page_number: int | None = None, image_region_path: str | None = None, ocr_output: str | None = None, confidence: float = 0.0) -> Provenance:
        provenance_id = new_id("prov")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO provenance (id, reaction_step_id, source_document_id, page_number, text_span, image_region_path, ocr_output, parser_name, parser_version, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (provenance_id, reaction_step_id, source_document_id, page_number, text_span, image_region_path, ocr_output, parser_name, parser_version, confidence),
            )
            row = conn.execute("SELECT * FROM provenance WHERE id = ?", (provenance_id,)).fetchone()
        return self._provenance_from_row(row)

    def record_doi_verification(
        self,
        *,
        reaction_step_id: str,
        doi: str,
        verified_fields: dict[str, Any],
        paper_title: str | None = None,
        original_paper_excerpt: str | None = None,
        verification_confidence: float = 0.0,
        verifier_agent: str | None = None,
    ) -> dict[str, Any]:
        verification_id = new_id("doiver")
        created_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO doi_verification
                (id, reaction_step_id, doi, paper_title, verified_fields, original_paper_excerpt,
                 verification_confidence, verifier_agent, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verification_id,
                    reaction_step_id,
                    doi,
                    paper_title,
                    json.dumps(verified_fields, ensure_ascii=False, sort_keys=True),
                    original_paper_excerpt,
                    verification_confidence,
                    verifier_agent,
                    created_at,
                ),
            )
            conn.execute(
                "UPDATE reaction_step SET verification_status = ? WHERE id = ?",
                ("doi_verified", reaction_step_id),
            )
        return {
            "id": verification_id,
            "reaction_step_id": reaction_step_id,
            "doi": doi,
            "paper_title": paper_title,
            "verified_fields": verified_fields,
            "original_paper_excerpt": original_paper_excerpt,
            "verification_confidence": verification_confidence,
            "verifier_agent": verifier_agent,
            "created_at": created_at,
        }

    def export_evaluation_rows(self, limit: int = 500) -> Iterable[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, d.file_path, d.title, d.doi
                FROM reaction_step r
                JOIN source_document d ON d.id = r.source_document_id
                ORDER BY d.created_at ASC, r.step_index ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            for row in rows:
                yield {key: row[key] for key in row.keys()}

    def list_reaction_steps_for_index(self, limit: int = 10000) -> list[ReactionStep]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM reaction_step ORDER BY created_at ASC LIMIT ?", (limit,)).fetchall()
        return [self._reaction_from_row(row) for row in rows]

    def upsert_embedding(self, reaction_step_id: str, *, model: str, embedding: list[float], error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO vector_index (reaction_step_id, model, embedding, dimensions, updated_at, error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(reaction_step_id) DO UPDATE SET
                  model = excluded.model, embedding = excluded.embedding, dimensions = excluded.dimensions,
                  updated_at = excluded.updated_at, error = excluded.error
                """,
                (reaction_step_id, model, json.dumps(embedding), len(embedding), utc_now(), error),
            )

    def vector_index_status(self) -> dict[str, Any]:
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS total FROM reaction_step").fetchone()["total"]
            indexed = conn.execute("SELECT COUNT(*) AS total FROM vector_index WHERE error IS NULL").fetchone()["total"]
            last = conn.execute("SELECT * FROM vector_index ORDER BY updated_at DESC LIMIT 1").fetchone()
            errors = conn.execute("SELECT COUNT(*) AS total FROM vector_index WHERE error IS NOT NULL").fetchone()["total"]
        return {
            "total_steps": int(total),
            "indexed_steps": int(indexed),
            "error_count": int(errors),
            "last_updated_at": last["updated_at"] if last else None,
            "last_error": last["error"] if last and last["error"] else None,
            "model": last["model"] if last else None,
        }

    def semantic_search(self, embedding: list[float], *, limit: int = 10) -> list[tuple[ReactionStep, float]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT reaction_step_id, embedding FROM vector_index WHERE error IS NULL").fetchall()
        scored: list[tuple[ReactionStep, float]] = []
        for row in rows:
            try:
                candidate = [float(item) for item in json.loads(row["embedding"])]
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            score = cosine_similarity(embedding, candidate)
            step = self.get_reaction_step(row["reaction_step_id"])
            if step:
                scored.append((step, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def record_integration_status(self, kind: str, *, configured: bool, status: str, detail: str | None = None) -> dict[str, Any]:
        checked_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO integration_status (kind, configured, status, detail, checked_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(kind) DO UPDATE SET configured = excluded.configured, status = excluded.status,
                  detail = excluded.detail, checked_at = excluded.checked_at
                """,
                (kind, 1 if configured else 0, status, detail, checked_at),
            )
        return {"kind": kind, "configured": configured, "status": status, "detail": detail, "checked_at": checked_at}

    def list_integration_statuses(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM integration_status ORDER BY kind").fetchall()
        return [
            {"kind": row["kind"], "configured": bool(row["configured"]), "status": row["status"], "detail": row["detail"], "checked_at": row["checked_at"]}
            for row in rows
        ]

    def count_ocr_backlog(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS total FROM reaction_step WHERE needs_ocr = 1").fetchone()["total"])

    def low_confidence_doi_queue(self, threshold: float, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reaction_step WHERE confidence < ? OR verification_status != 'doi_verified' ORDER BY confidence ASC LIMIT ?",
                (threshold, limit),
            ).fetchall()
        return [self._reaction_from_row(row).to_dict() for row in rows]

    def record_evaluation_metrics(self, gold_set_path: str, metrics: dict[str, Any]) -> dict[str, Any]:
        metric_id = new_id("metric")
        created_at = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO evaluation_metric (id, gold_set_path, metrics, created_at) VALUES (?, ?, ?, ?)",
                (metric_id, gold_set_path, json.dumps(metrics, ensure_ascii=False, sort_keys=True), created_at),
            )
        return {"id": metric_id, "gold_set_path": gold_set_path, "metrics": metrics, "created_at": created_at}

    def latest_evaluation_metrics(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM evaluation_metric ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {"id": row["id"], "gold_set_path": row["gold_set_path"], "metrics": json.loads(row["metrics"]), "created_at": row["created_at"]}

    def backup_sqlite(self, output_path: Path) -> dict[str, Any]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.database_path, output_path)
        return {"output_path": str(output_path), "bytes": output_path.stat().st_size}

    def upsert_compound(
        self,
        *,
        primary_name: str,
        cas: str | None = None,
        smiles: str | None = None,
        canonical_smiles: str | None = None,
        inchikey: str | None = None,
        fingerprint: str | None = None,
        source: str = "text",
        confidence: float = 0.5,
        aliases: list[tuple[str, str]] | None = None,
    ) -> Compound:
        now = utc_now()
        with self.connect() as conn:
            row = None
            if cas:
                row = conn.execute("SELECT * FROM compound WHERE cas = ?", (cas,)).fetchone()
            if not row and inchikey:
                row = conn.execute("SELECT * FROM compound WHERE inchikey = ?", (inchikey,)).fetchone()
            if not row:
                row = conn.execute("SELECT c.* FROM compound c JOIN compound_alias a ON a.compound_id = c.id WHERE LOWER(a.alias) = LOWER(?) LIMIT 1", (primary_name,)).fetchone()
            if row:
                compound_id = row["id"]
                conn.execute(
                    """
                    UPDATE compound SET primary_name = COALESCE(?, primary_name), cas = COALESCE(?, cas), smiles = COALESCE(?, smiles),
                      canonical_smiles = COALESCE(?, canonical_smiles), inchikey = COALESCE(?, inchikey), fingerprint = COALESCE(?, fingerprint),
                      confidence = MAX(confidence, ?), updated_at = ? WHERE id = ?
                    """,
                    (primary_name, cas, smiles, canonical_smiles, inchikey, fingerprint, confidence, now, compound_id),
                )
            else:
                compound_id = new_id("cmpd")
                conn.execute(
                    """
                    INSERT INTO compound (id, primary_name, cas, smiles, canonical_smiles, inchikey, fingerprint, source, confidence, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (compound_id, primary_name, cas, smiles, canonical_smiles, inchikey, fingerprint, source, confidence, now, now),
                )
            for alias, alias_type in aliases or [(primary_name, "name")]:
                conn.execute(
                    "INSERT OR IGNORE INTO compound_alias (id, compound_id, alias, alias_type) VALUES (?, ?, ?, ?)",
                    (new_id("alias"), compound_id, alias, alias_type),
                )
            updated = conn.execute("SELECT * FROM compound WHERE id = ?", (compound_id,)).fetchone()
        return self._compound_from_row(updated)

    def link_compound_to_reaction(self, reaction_step_id: str, compound_id: str, *, role: str, confidence: float, source: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO reaction_compound_role (id, reaction_step_id, compound_id, role, confidence, source) VALUES (?, ?, ?, ?, ?, ?)",
                (new_id("role"), reaction_step_id, compound_id, role, confidence, source),
            )

    def search_compounds(self, query: str = "", limit: int = 20) -> list[Compound]:
        with self.connect() as conn:
            if query:
                rows = conn.execute(
                    """
                    SELECT DISTINCT c.* FROM compound c LEFT JOIN compound_alias a ON a.compound_id = c.id
                    WHERE LOWER(c.primary_name) LIKE ? OR LOWER(COALESCE(c.cas,'')) LIKE ? OR LOWER(COALESCE(c.smiles,'')) LIKE ? OR LOWER(COALESCE(c.inchikey,'')) LIKE ? OR LOWER(COALESCE(a.alias,'')) LIKE ?
                    ORDER BY c.updated_at DESC LIMIT ?
                    """,
                    tuple([f"%{query.lower()}%"] * 5 + [limit]),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM compound ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._compound_from_row(row) for row in rows]

    def get_compound(self, compound_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM compound WHERE id = ?", (compound_id,)).fetchone()
            if not row:
                return None
            aliases = conn.execute("SELECT alias, alias_type FROM compound_alias WHERE compound_id = ? ORDER BY alias", (compound_id,)).fetchall()
            reactions = conn.execute("SELECT reaction_step_id, role, confidence, source FROM reaction_compound_role WHERE compound_id = ?", (compound_id,)).fetchall()
        data = self._compound_from_row(row).to_dict()
        data["aliases"] = [{key: item[key] for key in item.keys()} for item in aliases]
        data["reaction_roles"] = [{key: item[key] for key in item.keys()} for item in reactions]
        return data

    def merge_compounds(self, source_compound_id: str, target_compound_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute("UPDATE compound_alias SET compound_id = ? WHERE compound_id = ?", (target_compound_id, source_compound_id))
            conn.execute("UPDATE reaction_compound_role SET compound_id = ? WHERE compound_id = ?", (target_compound_id, source_compound_id))
            conn.execute("DELETE FROM compound WHERE id = ?", (source_compound_id,))
        target = self.get_compound(target_compound_id)
        if not target:
            raise KeyError(f"Target compound not found: {target_compound_id}")
        return target

    def _fts_query(self, query: str) -> str:
        terms = [term.strip().replace('"', "") for term in query.split() if term.strip()]
        return " OR ".join(f'"{term}"' for term in terms) if terms else '""'

    def _document_from_row(self, row: sqlite3.Row) -> SourceDocument:
        return SourceDocument(
            id=row["id"],
            file_path=row["file_path"],
            file_hash=row["file_hash"],
            file_type=row["file_type"],
            title=row["title"],
            doi=row["doi"],
            ingest_status=row["ingest_status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _job_from_row(self, row: sqlite3.Row) -> ParseJob:
        return ParseJob(
            id=row["id"],
            document_id=row["document_id"],
            status=row["status"],
            stage=row["stage"],
            error=row["error"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    def _reaction_from_row(self, row: sqlite3.Row) -> ReactionStep:
        return ReactionStep(
            id=row["id"],
            source_document_id=row["source_document_id"],
            step_index=row["step_index"],
            reaction_name=row["reaction_name"],
            substrate_text=row["substrate_text"],
            product_text=row["product_text"],
            reagent_text=row["reagent_text"],
            catalyst_text=row["catalyst_text"],
            solvent_text=row["solvent_text"],
            temperature=row["temperature"],
            time=row["time"],
            atmosphere=row["atmosphere"],
            yield_text=row["yield_text"],
            scale=row["scale"],
            workup=row["workup"],
            purification=row["purification"],
            original_text=row["original_text"],
            confidence=row["confidence"],
            verification_status=row["verification_status"],
            needs_ocr=bool(row["needs_ocr"]),
            extraction_method=row["extraction_method"] if "extraction_method" in row.keys() else "rules",
            schema_version=row["schema_version"] if "schema_version" in row.keys() else "reaction_step.v1",
            llm_confidence=row["llm_confidence"] if "llm_confidence" in row.keys() else None,
            metadata=json.loads(row["metadata"]) if "metadata" in row.keys() and row["metadata"] else {},
        )

    def _provenance_from_row(self, row: sqlite3.Row) -> Provenance:
        return Provenance(
            id=row["id"],
            reaction_step_id=row["reaction_step_id"],
            source_document_id=row["source_document_id"],
            page_number=row["page_number"],
            text_span=row["text_span"],
            image_region_path=row["image_region_path"],
            ocr_output=row["ocr_output"],
            parser_name=row["parser_name"],
            parser_version=row["parser_version"],
            confidence=row["confidence"],
        )

    def _compound_from_row(self, row: sqlite3.Row) -> Compound:
        return Compound(
            id=row["id"],
            primary_name=row["primary_name"],
            cas=row["cas"],
            smiles=row["smiles"],
            canonical_smiles=row["canonical_smiles"],
            inchikey=row["inchikey"],
            fingerprint=row["fingerprint"],
            source=row["source"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
