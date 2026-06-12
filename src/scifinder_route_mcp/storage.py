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


def is_sqlite_locked_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


class RouteStorage:
    def __init__(self, database_path: Path | str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
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

                CREATE TABLE IF NOT EXISTS export_batch (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    export_timestamp TEXT,
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    merge_method TEXT NOT NULL,
                    explanation TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS export_batch_document (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES export_batch(id) ON DELETE CASCADE,
                    source_document_id TEXT NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    explanation TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(batch_id, source_document_id)
                );

                CREATE TABLE IF NOT EXISTS export_batch_candidate (
                    id TEXT PRIMARY KEY,
                    source_document_id TEXT NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
                    candidate_batch_id TEXT NOT NULL REFERENCES export_batch(id) ON DELETE CASCADE,
                    confidence REAL NOT NULL,
                    explanation TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
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

                CREATE TABLE IF NOT EXISTS zotero_mcp_endpoint (
                    id TEXT PRIMARY KEY,
                    alias TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    priority INTEGER NOT NULL DEFAULT 100,
                    timeout_seconds REAL NOT NULL DEFAULT 10,
                    headers TEXT NOT NULL DEFAULT '{}',
                    write_note_enabled INTEGER NOT NULL DEFAULT 0,
                    last_status TEXT,
                    last_latency_ms INTEGER,
                    last_error TEXT,
                    last_checked_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(alias)
                );

                CREATE TABLE IF NOT EXISTS literature_link_job (
                    id TEXT PRIMARY KEY,
                    document_id TEXT REFERENCES source_document(id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS zotero_literature_link (
                    id TEXT PRIMARY KEY,
                    reaction_step_id TEXT NOT NULL REFERENCES reaction_step(id) ON DELETE CASCADE,
                    source_document_id TEXT NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
                    endpoint_id TEXT,
                    endpoint_alias TEXT,
                    endpoint_group TEXT,
                    zotero_item_key TEXT NOT NULL,
                    zotero_attachment_key TEXT,
                    doi TEXT,
                    title TEXT,
                    authors TEXT NOT NULL DEFAULT '[]',
                    year TEXT,
                    abstract TEXT,
                    source_kind TEXT NOT NULL DEFAULT 'zotero',
                    status TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    match_signals TEXT NOT NULL DEFAULT '{}',
                    method_excerpt TEXT,
                    si_excerpt TEXT,
                    extracted_fields TEXT NOT NULL DEFAULT '{}',
                    field_diff TEXT NOT NULL DEFAULT '{}',
                    user_note TEXT,
                    confirmed_by TEXT,
                    confirmed_at TEXT,
                    rejected_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(reaction_step_id, endpoint_group, zotero_item_key)
                );

                CREATE TABLE IF NOT EXISTS zotero_writeback_log (
                    id TEXT PRIMARY KEY,
                    literature_link_id TEXT NOT NULL REFERENCES zotero_literature_link(id) ON DELETE CASCADE,
                    endpoint_id TEXT,
                    zotero_item_key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL
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

                CREATE TABLE IF NOT EXISTS rdf_reaction_record (
                    id TEXT PRIMARY KEY,
                    source_document_id TEXT NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
                    record_index INTEGER NOT NULL,
                    registry TEXT,
                    scheme_id TEXT,
                    step_id TEXT,
                    reactant_count INTEGER NOT NULL DEFAULT 0,
                    product_count INTEGER NOT NULL DEFAULT 0,
                    cas_reaction_number TEXT,
                    yield_text TEXT,
                    reagents TEXT NOT NULL DEFAULT '[]',
                    catalysts TEXT NOT NULL DEFAULT '[]',
                    solvents TEXT NOT NULL DEFAULT '[]',
                    reference TEXT NOT NULL DEFAULT '{}',
                    experimental_procedure TEXT,
                    fields TEXT NOT NULL DEFAULT '{}',
                    warnings TEXT NOT NULL DEFAULT '[]',
                    deleted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_document_id, record_index)
                );

                CREATE TABLE IF NOT EXISTS rdf_structure (
                    id TEXT PRIMARY KEY,
                    rdf_reaction_id TEXT NOT NULL REFERENCES rdf_reaction_record(id) ON DELETE CASCADE,
                    source_document_id TEXT NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    role_index INTEGER NOT NULL,
                    name TEXT,
                    formula TEXT,
                    cas_rn TEXT,
                    molfile TEXT,
                    molfile_version TEXT,
                    smiles TEXT,
                    inchikey TEXT,
                    fingerprint TEXT,
                    rdkit_status TEXT NOT NULL DEFAULT 'not_indexed',
                    rdkit_error TEXT,
                    warnings TEXT NOT NULL DEFAULT '[]',
                    deleted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        reaction_columns = {row["name"] for row in conn.execute("PRAGMA table_info(reaction_step)")}
        reaction_migrations = {
            "extraction_method": "ALTER TABLE reaction_step ADD COLUMN extraction_method TEXT NOT NULL DEFAULT 'rules'",
            "schema_version": "ALTER TABLE reaction_step ADD COLUMN schema_version TEXT NOT NULL DEFAULT 'reaction_step.v1'",
            "llm_confidence": "ALTER TABLE reaction_step ADD COLUMN llm_confidence REAL",
            "metadata": "ALTER TABLE reaction_step ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'",
            "deleted_at": "ALTER TABLE reaction_step ADD COLUMN deleted_at TEXT",
        }
        for column, statement in reaction_migrations.items():
            if column not in reaction_columns:
                conn.execute(statement)
        document_columns = {row["name"] for row in conn.execute("PRAGMA table_info(source_document)")}
        document_migrations = {
            "deleted_at": "ALTER TABLE source_document ADD COLUMN deleted_at TEXT",
        }
        for column, statement in document_migrations.items():
            if column not in document_columns:
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
                    (id, file_path, file_hash, file_type, title, doi, scifinder_metadata, ingest_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (document_id, file_path, file_hash, file_type, title, doi, json.dumps({}, ensure_ascii=False), ingest_status, now, now),
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
            row = conn.execute("SELECT COUNT(*) AS total FROM source_document WHERE deleted_at IS NULL").fetchone()
        return int(row["total"])

    def count_reaction_steps(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM reaction_step WHERE deleted_at IS NULL").fetchone()
        return int(row["total"])

    def clear_document_reactions(self, document_id: str) -> None:
        with self.connect() as conn:
            step_ids = [row["id"] for row in conn.execute("SELECT id FROM reaction_step WHERE source_document_id = ?", (document_id,))]
            for step_id in step_ids:
                conn.execute("DELETE FROM reaction_step_fts WHERE reaction_step_id = ?", (step_id,))
            conn.execute("DELETE FROM reaction_step WHERE source_document_id = ?", (document_id,))
            conn.execute("DELETE FROM rdf_reaction_record WHERE source_document_id = ?", (document_id,))

    def upsert_rdf_reaction_records(self, document_id: str, records: list[dict[str, Any]]) -> dict[str, int]:
        now = utc_now()
        inserted_records = 0
        inserted_structures = 0
        with self.connect() as conn:
            conn.execute("DELETE FROM rdf_reaction_record WHERE source_document_id = ?", (document_id,))
            for record in records:
                reaction_id = new_id("rdfrec")
                conn.execute(
                    """
                    INSERT INTO rdf_reaction_record
                    (id, source_document_id, record_index, registry, scheme_id, step_id, reactant_count,
                     product_count, cas_reaction_number, yield_text, reagents, catalysts, solvents, reference,
                     experimental_procedure, fields, warnings, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reaction_id,
                        document_id,
                        record.get("record_index"),
                        record.get("registry"),
                        record.get("scheme_id"),
                        record.get("step_id"),
                        int(record.get("reactant_count") or 0),
                        int(record.get("product_count") or 0),
                        record.get("cas_reaction_number"),
                        record.get("yield_text"),
                        json.dumps(record.get("reagents") or [], ensure_ascii=False),
                        json.dumps(record.get("catalysts") or [], ensure_ascii=False),
                        json.dumps(record.get("solvents") or [], ensure_ascii=False),
                        json.dumps(record.get("reference") or {}, ensure_ascii=False, sort_keys=True),
                        record.get("experimental_procedure"),
                        json.dumps(record.get("fields") or {}, ensure_ascii=False, sort_keys=True),
                        json.dumps(record.get("warnings") or [], ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                inserted_records += 1
                for molecule in record.get("molecules") or []:
                    conn.execute(
                        """
                        INSERT INTO rdf_structure
                        (id, rdf_reaction_id, source_document_id, role, role_index, name, formula, cas_rn,
                         molfile, molfile_version, smiles, inchikey, fingerprint, rdkit_status, rdkit_error,
                         warnings, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id("rdfstr"),
                            reaction_id,
                            document_id,
                            molecule.get("role") or "unknown",
                            int(molecule.get("role_index") or 0),
                            molecule.get("name"),
                            molecule.get("formula"),
                            molecule.get("cas_rn"),
                            molecule.get("molfile"),
                            molecule.get("molfile_version"),
                            molecule.get("smiles"),
                            molecule.get("inchikey"),
                            molecule.get("fingerprint"),
                            molecule.get("rdkit_status") or "not_indexed",
                            molecule.get("rdkit_error"),
                            json.dumps(molecule.get("warnings") or [], ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
                    inserted_structures += 1
        return {"records": inserted_records, "structures": inserted_structures}

    def list_rdf_reactions(self, *, document_id: str = "", query: str = "", limit: int = 50, offset: int = 0, include_deleted: bool = False) -> list[dict[str, Any]]:
        clauses = [] if include_deleted else ["r.deleted_at IS NULL"]
        params: list[Any] = []
        if document_id:
            clauses.append("r.source_document_id = ?")
            params.append(document_id)
        if query:
            like = f"%{query}%"
            clauses.append(
                """
                (
                    r.cas_reaction_number LIKE ? OR r.scheme_id LIKE ? OR r.step_id LIKE ? OR r.reference LIKE ?
                    OR d.id LIKE ? OR d.file_path LIKE ? OR d.title LIKE ? OR d.doi LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM rdf_structure rs
                        WHERE rs.rdf_reaction_id = r.id
                          AND (? OR rs.deleted_at IS NULL)
                          AND (rs.cas_rn LIKE ? OR rs.name LIKE ? OR rs.formula LIKE ?)
                    )
                )
                """
            )
            params.extend([like, like, like, like, like, like, like, like, 1 if include_deleted else 0, like, like, like])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT r.*, d.file_path AS source_file_path, d.title AS source_title, COUNT(s.id) AS structure_count
                FROM rdf_reaction_record r
                JOIN source_document d ON d.id = r.source_document_id
                LEFT JOIN rdf_structure s ON s.rdf_reaction_id = r.id AND (? OR s.deleted_at IS NULL)
                {where}
                GROUP BY r.id
                ORDER BY r.source_document_id, r.record_index
                LIMIT ? OFFSET ?
                """,
                (1 if include_deleted else 0, *params, limit, offset),
            ).fetchall()
        return [self._rdf_reaction_row_to_dict(row) for row in rows]

    def get_rdf_reaction(self, reaction_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        deleted_clause = "" if include_deleted else " AND deleted_at IS NULL"
        with self.connect() as conn:
            row = conn.execute(f"SELECT * FROM rdf_reaction_record WHERE id = ?{deleted_clause}", (reaction_id,)).fetchone()
            if not row:
                return None
            structures = conn.execute(
                f"SELECT * FROM rdf_structure WHERE rdf_reaction_id = ?{' ' if include_deleted else ' AND deleted_at IS NULL '}ORDER BY role, role_index",
                (reaction_id,),
            ).fetchall()
        data = self._rdf_reaction_row_to_dict(row)
        data["structures"] = [self._rdf_structure_row_to_dict(item) for item in structures]
        return data

    def list_rdf_structures(self, *, document_id: str = "", query: str = "", limit: int = 50, offset: int = 0, include_deleted: bool = False) -> list[dict[str, Any]]:
        clauses = [] if include_deleted else ["s.deleted_at IS NULL", "r.deleted_at IS NULL"]
        params: list[Any] = []
        if document_id:
            clauses.append("s.source_document_id = ?")
            params.append(document_id)
        if query:
            clauses.append("(LOWER(COALESCE(s.name,'')) LIKE ? OR LOWER(COALESCE(s.cas_rn,'')) LIKE ? OR LOWER(COALESCE(s.smiles,'')) LIKE ? OR LOWER(COALESCE(s.inchikey,'')) LIKE ?)")
            params.extend([f"%{query.lower()}%"] * 4)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT s.*, r.record_index, r.scheme_id, r.step_id, r.cas_reaction_number, r.yield_text
                FROM rdf_structure s
                JOIN rdf_reaction_record r ON r.id = s.rdf_reaction_id
                {where}
                ORDER BY s.updated_at DESC, s.role, s.role_index
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return [self._rdf_structure_row_to_dict(row) for row in rows]

    def list_rdf_structures_for_search(self, *, limit: int = 10000) -> list[dict[str, Any]]:
        return self.list_rdf_structures(limit=limit)

    def rdf_structure_index_status(self) -> dict[str, Any]:
        with self.connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) AS total FROM rdf_structure WHERE deleted_at IS NULL").fetchone()["total"])
            indexed = int(conn.execute("SELECT COUNT(*) AS total FROM rdf_structure WHERE deleted_at IS NULL AND rdkit_status = 'indexed'").fetchone()["total"])
            failed = int(conn.execute("SELECT COUNT(*) AS total FROM rdf_structure WHERE deleted_at IS NULL AND rdkit_status = 'rdkit_failed'").fetchone()["total"])
            unavailable = int(conn.execute("SELECT COUNT(*) AS total FROM rdf_structure WHERE deleted_at IS NULL AND rdkit_status = 'rdkit_unavailable'").fetchone()["total"])
            reactions = int(conn.execute("SELECT COUNT(*) AS total FROM rdf_reaction_record WHERE deleted_at IS NULL").fetchone()["total"])
        if total == 0:
            status = "empty"
        elif indexed == total:
            status = "complete"
        elif indexed > 0:
            status = "partial"
        else:
            status = "unavailable"
        return {"status": status, "reaction_records": reactions, "total_structures": total, "indexed_structures": indexed, "failed_structures": failed, "rdkit_unavailable_structures": unavailable}

    def soft_delete(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            if entity_type == "document":
                conn.execute("UPDATE source_document SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, entity_id))
                conn.execute("UPDATE reaction_step SET deleted_at = ? WHERE source_document_id = ?", (now, entity_id))
                conn.execute("UPDATE rdf_reaction_record SET deleted_at = ?, updated_at = ? WHERE source_document_id = ?", (now, now, entity_id))
                conn.execute("UPDATE rdf_structure SET deleted_at = ?, updated_at = ? WHERE source_document_id = ?", (now, now, entity_id))
            elif entity_type == "rdf_reaction":
                conn.execute("UPDATE rdf_reaction_record SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, entity_id))
                conn.execute("UPDATE rdf_structure SET deleted_at = ?, updated_at = ? WHERE rdf_reaction_id = ?", (now, now, entity_id))
            elif entity_type == "rdf_structure":
                conn.execute("UPDATE rdf_structure SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, entity_id))
            elif entity_type == "reaction_step":
                conn.execute("UPDATE reaction_step SET deleted_at = ? WHERE id = ?", (now, entity_id))
            else:
                raise ValueError(f"Unsupported delete entity type: {entity_type}")
        return {"status": "trashed", "entity_type": entity_type, "entity_id": entity_id, "deleted_at": now}

    def restore_trash_item(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            if entity_type == "document":
                conn.execute("UPDATE source_document SET deleted_at = NULL, updated_at = ? WHERE id = ?", (now, entity_id))
                conn.execute("UPDATE reaction_step SET deleted_at = NULL WHERE source_document_id = ?", (entity_id,))
                conn.execute("UPDATE rdf_reaction_record SET deleted_at = NULL, updated_at = ? WHERE source_document_id = ?", (now, entity_id))
                conn.execute("UPDATE rdf_structure SET deleted_at = NULL, updated_at = ? WHERE source_document_id = ?", (now, entity_id))
            elif entity_type == "rdf_reaction":
                conn.execute("UPDATE rdf_reaction_record SET deleted_at = NULL, updated_at = ? WHERE id = ?", (now, entity_id))
                conn.execute("UPDATE rdf_structure SET deleted_at = NULL, updated_at = ? WHERE rdf_reaction_id = ?", (now, entity_id))
            elif entity_type == "rdf_structure":
                conn.execute("UPDATE rdf_structure SET deleted_at = NULL, updated_at = ? WHERE id = ?", (now, entity_id))
            elif entity_type == "reaction_step":
                conn.execute("UPDATE reaction_step SET deleted_at = NULL WHERE id = ?", (entity_id,))
            else:
                raise ValueError(f"Unsupported restore entity type: {entity_type}")
        return {"status": "restored", "entity_type": entity_type, "entity_id": entity_id}

    def list_trash(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            documents = conn.execute("SELECT id, title, file_path, deleted_at FROM source_document WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT ?", (limit,)).fetchall()
            reactions = conn.execute("SELECT id, cas_reaction_number AS title, source_document_id, deleted_at FROM rdf_reaction_record WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT ?", (limit,)).fetchall()
            structures = conn.execute("SELECT id, name AS title, cas_rn, rdf_reaction_id, deleted_at FROM rdf_structure WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC LIMIT ?", (limit,)).fetchall()
        items: list[dict[str, Any]] = []
        items.extend({"entity_type": "document", **dict(row)} for row in documents)
        items.extend({"entity_type": "rdf_reaction", **dict(row)} for row in reactions)
        items.extend({"entity_type": "rdf_structure", **dict(row)} for row in structures)
        items.sort(key=lambda item: item.get("deleted_at") or "", reverse=True)
        return items[:limit]

    def empty_trash(self) -> dict[str, int]:
        with self.connect() as conn:
            structures = conn.execute("DELETE FROM rdf_structure WHERE deleted_at IS NOT NULL").rowcount
            reactions = conn.execute("DELETE FROM rdf_reaction_record WHERE deleted_at IS NOT NULL").rowcount
            step_ids = [row["id"] for row in conn.execute("SELECT id FROM reaction_step WHERE deleted_at IS NOT NULL")]
            for step_id in step_ids:
                conn.execute("DELETE FROM reaction_step_fts WHERE reaction_step_id = ?", (step_id,))
            reaction_steps = conn.execute("DELETE FROM reaction_step WHERE deleted_at IS NOT NULL").rowcount
            documents = conn.execute("DELETE FROM source_document WHERE deleted_at IS NOT NULL").rowcount
        return {"documents": documents, "reaction_steps": reaction_steps, "rdf_reactions": reactions, "rdf_structures": structures}

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
        clauses = ["r.confidence >= ?", "r.deleted_at IS NULL"]
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
            row = conn.execute("SELECT * FROM reaction_step WHERE id = ? AND deleted_at IS NULL", (reaction_step_id,)).fetchone()
        return self._reaction_from_row(row) if row else None

    def get_provenance(self, reaction_step_id: str) -> list[Provenance]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM provenance WHERE reaction_step_id = ? ORDER BY id",
                (reaction_step_id,),
            ).fetchall()
        return [self._provenance_from_row(row) for row in rows]

    def auto_batch_document(self, document_id: str) -> dict[str, Any]:
        document = self.get_document(document_id)
        if not document:
            raise KeyError(f"Document not found: {document_id}")
        existing_links = self.list_batches_for_document(document_id)
        if existing_links:
            return {"status": "already_linked", "batch": existing_links[0]}
        candidate = self._best_batch_candidate(document)
        if candidate and candidate["confidence"] >= 0.85:
            self._link_document_to_batch(candidate["batch_id"], document_id, document_role(document.file_type), candidate["confidence"], candidate["explanation"])
            return {"status": "auto_merged", "batch_id": candidate["batch_id"], "confidence": candidate["confidence"], "explanation": candidate["explanation"]}
        if candidate and candidate["confidence"] >= 0.55:
            self._record_batch_candidate(document_id, candidate["batch_id"], candidate["confidence"], candidate["explanation"])
            return {"status": "candidate", "batch_id": candidate["batch_id"], "confidence": candidate["confidence"], "explanation": candidate["explanation"]}
        batch_id = self._create_export_batch(document.title, status="auto_merged", confidence=1.0, merge_method="single_document", explanation={"signals": [{"name": "first_document", "matched": True}]})
        self._link_document_to_batch(batch_id, document_id, document_role(document.file_type), 1.0, {"signals": [{"name": "first_document", "matched": True}]})
        return {"status": "created", "batch_id": batch_id, "confidence": 1.0}

    def list_batches_for_document(self, document_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT b.*, bd.role, bd.confidence AS document_confidence, bd.explanation AS document_explanation
                FROM export_batch_document bd
                JOIN export_batch b ON b.id = bd.batch_id
                WHERE bd.source_document_id = ?
                ORDER BY bd.confidence DESC, b.updated_at DESC
                """,
                (document_id,),
            ).fetchall()
        return [batch_row_to_dict(row) for row in rows]

    def list_export_batches(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM export_batch ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [plain_batch_row_to_dict(row) for row in rows]

    def get_export_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            batch = conn.execute("SELECT * FROM export_batch WHERE id = ?", (batch_id,)).fetchone()
            if not batch:
                return None
            documents = conn.execute(
                """
                SELECT d.*, bd.role, bd.confidence AS link_confidence, bd.explanation AS link_explanation
                FROM export_batch_document bd
                JOIN source_document d ON d.id = bd.source_document_id
                WHERE bd.batch_id = ?
                ORDER BY bd.role, d.created_at
                """,
                (batch_id,),
            ).fetchall()
        data = plain_batch_row_to_dict(batch)
        data["documents"] = [{key: json.loads(row[key]) if key in {"scifinder_metadata", "link_explanation"} and row[key] else row[key] for key in row.keys()} for row in documents]
        return data

    def unlink_document_from_batch(self, document_id: str, batch_id: str, reason: str = "") -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute("DELETE FROM export_batch_document WHERE source_document_id = ? AND batch_id = ?", (document_id, batch_id))
            row = conn.execute("SELECT explanation FROM export_batch WHERE id = ?", (batch_id,)).fetchone()
            explanation = json.loads(row["explanation"]) if row and row["explanation"] else {}
            explanation["last_unlink_reason"] = reason
            conn.execute("UPDATE export_batch SET updated_at = ?, explanation = ? WHERE id = ?", (utc_now(), json.dumps(explanation, ensure_ascii=False, sort_keys=True), batch_id))
        return {"status": "unlinked", "document_id": document_id, "batch_id": batch_id, "reason": reason}

    def _best_batch_candidate(self, document: SourceDocument) -> dict[str, Any] | None:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT b.id AS batch_id, b.title, d.file_path, d.file_type, d.title AS document_title
                FROM export_batch b
                JOIN export_batch_document bd ON bd.batch_id = b.id
                JOIN source_document d ON d.id = bd.source_document_id
                ORDER BY b.updated_at DESC
                LIMIT 100
                """
            ).fetchall()
        best: dict[str, Any] | None = None
        for row in rows:
            score, explanation = batch_match_score(document, row)
            if not best or score > best["confidence"]:
                best = {"batch_id": row["batch_id"], "confidence": score, "explanation": explanation}
        return best

    def _create_export_batch(self, title: str | None, *, status: str, confidence: float, merge_method: str, explanation: dict[str, Any]) -> str:
        batch_id = new_id("batch")
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO export_batch (id, title, export_timestamp, status, confidence, merge_method, explanation, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (batch_id, title, None, status, confidence, merge_method, json.dumps(explanation, ensure_ascii=False, sort_keys=True), now, now),
            )
        return batch_id

    def _link_document_to_batch(self, batch_id: str, document_id: str, role: str, confidence: float, explanation: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO export_batch_document (id, batch_id, source_document_id, role, confidence, explanation, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (new_id("batchdoc"), batch_id, document_id, role, confidence, json.dumps(explanation, ensure_ascii=False, sort_keys=True), now),
            )
            conn.execute("UPDATE export_batch SET updated_at = ?, confidence = MAX(confidence, ?) WHERE id = ?", (now, confidence, batch_id))

    def _record_batch_candidate(self, document_id: str, batch_id: str, confidence: float, explanation: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO export_batch_candidate (id, source_document_id, candidate_batch_id, confidence, explanation, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (new_id("batchcand"), document_id, batch_id, confidence, json.dumps(explanation, ensure_ascii=False, sort_keys=True), now, now),
            )

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
                WHERE r.deleted_at IS NULL AND d.deleted_at IS NULL
                ORDER BY d.created_at ASC, r.step_index ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            for row in rows:
                yield {key: row[key] for key in row.keys()}

    def list_reaction_steps_for_index(self, limit: int = 10000) -> list[ReactionStep]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM reaction_step WHERE deleted_at IS NULL ORDER BY created_at ASC LIMIT ?", (limit,)).fetchall()
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
            total = conn.execute("SELECT COUNT(*) AS total FROM reaction_step WHERE deleted_at IS NULL").fetchone()["total"]
            indexed = conn.execute("SELECT COUNT(*) AS total FROM vector_index v JOIN reaction_step r ON r.id = v.reaction_step_id WHERE v.error IS NULL AND r.deleted_at IS NULL").fetchone()["total"]
            last = conn.execute("SELECT * FROM vector_index ORDER BY updated_at DESC LIMIT 1").fetchone()
            errors = conn.execute("SELECT COUNT(*) AS total FROM vector_index v JOIN reaction_step r ON r.id = v.reaction_step_id WHERE v.error IS NOT NULL AND r.deleted_at IS NULL").fetchone()["total"]
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
            rows = conn.execute("SELECT v.reaction_step_id, v.embedding FROM vector_index v JOIN reaction_step r ON r.id = v.reaction_step_id WHERE v.error IS NULL AND r.deleted_at IS NULL").fetchall()
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
            return int(conn.execute("SELECT COUNT(*) AS total FROM reaction_step WHERE needs_ocr = 1 AND deleted_at IS NULL").fetchone()["total"])

    def low_confidence_doi_queue(self, threshold: float, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reaction_step WHERE deleted_at IS NULL AND (confidence < ? OR verification_status != 'doi_verified') ORDER BY confidence ASC LIMIT ?",
                (threshold, limit),
            ).fetchall()
        return [self._reaction_from_row(row).to_dict() for row in rows]

    def upsert_zotero_endpoint(self, data: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        endpoint_id = str(data.get("id") or data.get("alias") or new_id("zotep")).strip()
        alias = str(data.get("alias") or endpoint_id).strip()
        group_name = str(data.get("group_name") or data.get("group") or alias).strip()
        url = str(data.get("url") or "").strip()
        headers = data.get("headers") if isinstance(data.get("headers"), dict) else {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO zotero_mcp_endpoint
                (id, alias, group_name, url, enabled, priority, timeout_seconds, headers, write_note_enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET alias = excluded.alias, group_name = excluded.group_name,
                  url = excluded.url, enabled = excluded.enabled, priority = excluded.priority,
                  timeout_seconds = excluded.timeout_seconds, headers = excluded.headers,
                  write_note_enabled = excluded.write_note_enabled, updated_at = excluded.updated_at
                """,
                (endpoint_id, alias, group_name, url, 1 if data.get("enabled", True) else 0, int(data.get("priority") or 100), float(data.get("timeout_seconds") or 10), json.dumps(headers, ensure_ascii=False, sort_keys=True), 1 if data.get("write_note_enabled") else 0, now, now),
            )
            row = conn.execute("SELECT * FROM zotero_mcp_endpoint WHERE id = ?", (endpoint_id,)).fetchone()
        return endpoint_row_to_dict(row, include_headers=True)

    def list_zotero_endpoints(self, *, include_headers: bool = False) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM zotero_mcp_endpoint ORDER BY group_name, priority, alias").fetchall()
        return [endpoint_row_to_dict(row, include_headers=include_headers) for row in rows]

    def delete_zotero_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            count = conn.execute("DELETE FROM zotero_mcp_endpoint WHERE id = ?", (endpoint_id,)).rowcount
        return {"status": "deleted", "id": endpoint_id, "deleted": count}

    def update_zotero_endpoint_status(self, endpoint_id: str, *, status: str, latency_ms: int | None = None, error: str | None = None) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute("UPDATE zotero_mcp_endpoint SET last_status = ?, last_latency_ms = ?, last_error = ?, last_checked_at = ?, updated_at = ? WHERE id = ?", (status, latency_ms, error, now, now, endpoint_id))
            row = conn.execute("SELECT * FROM zotero_mcp_endpoint WHERE id = ?", (endpoint_id,)).fetchone()
        return endpoint_row_to_dict(row, include_headers=False) if row else None

    def create_literature_link_job(self, document_id: str | None = None, *, status: str = "queued", stage: str = "queued") -> dict[str, Any]:
        job_id = new_id("litjob")
        now = utc_now()
        with self.connect() as conn:
            conn.execute("INSERT INTO literature_link_job (id, document_id, status, stage, started_at, created_at) VALUES (?, ?, ?, ?, ?, ?)", (job_id, document_id, status, stage, now if status == "running" else None, now))
            row = conn.execute("SELECT * FROM literature_link_job WHERE id = ?", (job_id,)).fetchone()
        return job_row_to_dict(row)

    def update_literature_link_job(self, job_id: str, *, status: str, stage: str, error: str | None = None) -> dict[str, Any] | None:
        finished_at = utc_now() if status in {"completed", "failed"} else None
        started_at = utc_now() if status == "running" else None
        with self.connect() as conn:
            conn.execute("UPDATE literature_link_job SET status = ?, stage = ?, error = ?, started_at = COALESCE(started_at, ?), finished_at = COALESCE(?, finished_at) WHERE id = ?", (status, stage, error, started_at, finished_at, job_id))
            row = conn.execute("SELECT * FROM literature_link_job WHERE id = ?", (job_id,)).fetchone()
        return job_row_to_dict(row) if row else None

    def list_literature_link_jobs(self, *, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if status:
                rows = conn.execute("SELECT * FROM literature_link_job WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM literature_link_job ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [job_row_to_dict(row) for row in rows]

    def list_reaction_steps_for_document(self, document_id: str | None = None, *, limit: int = 100) -> list[ReactionStep]:
        with self.connect() as conn:
            if document_id:
                rows = conn.execute("SELECT * FROM reaction_step WHERE source_document_id = ? AND deleted_at IS NULL ORDER BY step_index ASC LIMIT ?", (document_id, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM reaction_step WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._reaction_from_row(row) for row in rows]

    def upsert_literature_link(self, data: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        link_id = str(data.get("id") or "") or new_id("litlink")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO zotero_literature_link
                (id, reaction_step_id, source_document_id, endpoint_id, endpoint_alias, endpoint_group, zotero_item_key,
                 zotero_attachment_key, doi, title, authors, year, abstract, source_kind, status, confidence,
                 match_signals, method_excerpt, si_excerpt, extracted_fields, field_diff, user_note, confirmed_by,
                 confirmed_at, rejected_reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reaction_step_id, endpoint_group, zotero_item_key) DO UPDATE SET
                  endpoint_id = excluded.endpoint_id, endpoint_alias = excluded.endpoint_alias, doi = excluded.doi,
                  title = excluded.title, authors = excluded.authors, year = excluded.year, abstract = excluded.abstract,
                  status = CASE WHEN zotero_literature_link.status = 'confirmed' THEN 'confirmed' ELSE excluded.status END,
                  confidence = MAX(zotero_literature_link.confidence, excluded.confidence), match_signals = excluded.match_signals,
                  method_excerpt = excluded.method_excerpt, si_excerpt = excluded.si_excerpt, extracted_fields = excluded.extracted_fields,
                  field_diff = excluded.field_diff, updated_at = excluded.updated_at
                """,
                (link_id, data["reaction_step_id"], data["source_document_id"], data.get("endpoint_id"), data.get("endpoint_alias"), data.get("endpoint_group"), data["zotero_item_key"], data.get("zotero_attachment_key"), data.get("doi"), data.get("title"), json.dumps(data.get("authors") or [], ensure_ascii=False), data.get("year"), data.get("abstract"), data.get("source_kind") or "zotero", data.get("status") or "candidate", float(data.get("confidence") or 0), json.dumps(data.get("match_signals") or {}, ensure_ascii=False, sort_keys=True), data.get("method_excerpt"), data.get("si_excerpt"), json.dumps(data.get("extracted_fields") or {}, ensure_ascii=False, sort_keys=True), json.dumps(data.get("field_diff") or {}, ensure_ascii=False, sort_keys=True), data.get("user_note"), data.get("confirmed_by"), data.get("confirmed_at"), data.get("rejected_reason"), now, now),
            )
            row = conn.execute("SELECT * FROM zotero_literature_link WHERE reaction_step_id = ? AND endpoint_group = ? AND zotero_item_key = ?", (data["reaction_step_id"], data.get("endpoint_group"), data["zotero_item_key"])).fetchone()
        return literature_link_row_to_dict(row)

    def list_literature_links(self, *, status: str = "", reaction_step_id: str = "", document_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if reaction_step_id:
            clauses.append("reaction_step_id = ?")
            params.append(reaction_step_id)
        if document_id:
            clauses.append("source_document_id = ?")
            params.append(document_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM zotero_literature_link {where} ORDER BY confidence DESC, updated_at DESC LIMIT ?", (*params, limit)).fetchall()
        return [literature_link_row_to_dict(row) for row in rows]

    def update_literature_link_status(self, link_id: str, *, status: str, confirmed_by: str | None = None, reason: str | None = None) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            conn.execute("UPDATE zotero_literature_link SET status = ?, confirmed_by = COALESCE(?, confirmed_by), confirmed_at = CASE WHEN ? = 'confirmed' THEN ? ELSE confirmed_at END, rejected_reason = ?, updated_at = ? WHERE id = ?", (status, confirmed_by, status, now, reason, now, link_id))
            row = conn.execute("SELECT * FROM zotero_literature_link WHERE id = ?", (link_id,)).fetchone()
        if not row:
            raise KeyError(f"Literature link not found: {link_id}")
        return literature_link_row_to_dict(row)

    def record_zotero_writeback(self, *, literature_link_id: str, endpoint_id: str | None, zotero_item_key: str, operation: str, payload: dict[str, Any], status: str, error: str | None = None) -> dict[str, Any]:
        log_id = new_id("zwb")
        created_at = utc_now()
        with self.connect() as conn:
            conn.execute("INSERT INTO zotero_writeback_log (id, literature_link_id, endpoint_id, zotero_item_key, operation, payload, status, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (log_id, literature_link_id, endpoint_id, zotero_item_key, operation, json.dumps(payload, ensure_ascii=False, sort_keys=True), status, error, created_at))
        return {"id": log_id, "literature_link_id": literature_link_id, "endpoint_id": endpoint_id, "zotero_item_key": zotero_item_key, "operation": operation, "payload": payload, "status": status, "error": error, "created_at": created_at}

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
            scifinder_metadata=json.loads(row["scifinder_metadata"]) if "scifinder_metadata" in row.keys() and row["scifinder_metadata"] else {},
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

    def _rdf_reaction_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = {key: row[key] for key in row.keys()}
        for key in ["reagents", "catalysts", "solvents", "warnings"]:
            data[key] = json.loads(data[key]) if data.get(key) else []
        for key in ["reference", "fields"]:
            data[key] = json.loads(data[key]) if data.get(key) else {}
        return data

    def _rdf_structure_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = {key: row[key] for key in row.keys()}
        data["warnings"] = json.loads(data["warnings"]) if data.get("warnings") else []
        return data


def document_role(file_type: str) -> str:
    mapping = {
        "rdf": "structured_rdf",
        "pdf": "readable_pdf",
        "rtf": "readable_rtf",
        "html": "readable_html",
        "htm": "readable_html",
        "mhtml": "readable_html",
        "mht": "readable_html",
        "md": "markdown",
        "markdown": "markdown",
        "txt": "text",
    }
    return mapping.get(file_type.lower(), "unknown")


def batch_match_score(document: SourceDocument, row: sqlite3.Row) -> tuple[float, dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    score = 0.0
    document_stem = Path(document.file_path).stem.lower()
    other_stem = Path(row["file_path"]).stem.lower()
    if document_stem and other_stem:
        similarity = stem_similarity(document_stem, other_stem)
        if similarity >= 0.75:
            weight = 0.7
            score += weight
            signals.append({"name": "basename_similarity", "weight": weight, "matched": True, "detail": f"{document_stem} ~ {other_stem}"})
    if document.title and row["document_title"] and normalize_for_match(document.title) == normalize_for_match(row["document_title"]):
        weight = 0.25
        score += weight
        signals.append({"name": "title_exact_match", "weight": weight, "matched": True, "detail": document.title})
    if document.file_type != row["file_type"] and {document.file_type, row["file_type"]} & {"rdf"}:
        weight = 0.2
        score += weight
        signals.append({"name": "rdf_readable_pair", "weight": weight, "matched": True, "detail": f"{document.file_type}+{row['file_type']}"})
    explanation = {"signals": signals, "score": round(min(score, 1.0), 3)}
    return min(score, 1.0), explanation


def stem_similarity(left: str, right: str) -> float:
    left_tokens = {token for token in re_split_stem(left) if token}
    right_tokens = {token for token in re_split_stem(right) if token}
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def re_split_stem(value: str) -> list[str]:
    import re

    return re.split(r"[^a-z0-9]+", value.lower())


def normalize_for_match(value: str) -> str:
    return " ".join(value.lower().split())


def plain_batch_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = {key: row[key] for key in row.keys()}
    data["explanation"] = json.loads(data["explanation"]) if data.get("explanation") else {}
    return data


def batch_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = plain_batch_row_to_dict(row)
    if data.get("document_explanation"):
        data["document_explanation"] = json.loads(data["document_explanation"])
    return data


def endpoint_row_to_dict(row: sqlite3.Row, *, include_headers: bool = False) -> dict[str, Any]:
    headers = json.loads(row["headers"]) if row["headers"] else {}
    return {
        "id": row["id"],
        "alias": row["alias"],
        "group_name": row["group_name"],
        "url": row["url"],
        "enabled": bool(row["enabled"]),
        "priority": row["priority"],
        "timeout_seconds": row["timeout_seconds"],
        "headers": headers if include_headers else {key: "****" for key in headers},
        "write_note_enabled": bool(row["write_note_enabled"]),
        "last_status": row["last_status"],
        "last_latency_ms": row["last_latency_ms"],
        "last_error": row["last_error"],
        "last_checked_at": row["last_checked_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def job_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def literature_link_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    json_fields = {"authors", "match_signals", "extracted_fields", "field_diff"}
    data = {key: json.loads(row[key]) if key in json_fields and row[key] else row[key] for key in row.keys()}
    return data


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
