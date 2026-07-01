from __future__ import annotations

import json
import math
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from .orm import (
    Base, AiProviderModel, SourceDocumentModel, ParseJobModel,
    ReactionStepModel, ProvenanceModel, ParsedChunkModel,
    ReactionSourceLinkModel, PdfReactionEvidenceModel, StructureEvidenceCandidateModel
)

from .models import Compound, ParseJob, Provenance, ReactionStep, SourceDocument, AiProvider


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def is_sqlite_locked_error(exc: BaseException) -> bool:
    from sqlalchemy.exc import DBAPIError
    orig = exc
    if isinstance(exc, DBAPIError) and exc.orig is not None:
        orig = exc.orig
    return isinstance(orig, sqlite3.OperationalError) and "locked" in str(orig).lower()



class RouteStorage:
    def __init__(self, db_uri_or_path: Path | str):
        db_str = str(db_uri_or_path).strip()
        if db_str.startswith(("postgresql://", "postgres://")):
            self.database_path = None
            self.engine = create_engine(db_str)
        else:
            self.database_path = Path(db_uri_or_path)
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(
                f"sqlite:///{self.database_path}",
                connect_args={"timeout": 30}
            )
            
            @event.listens_for(self.engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()
                
        self._SessionFactory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.init_schema()

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        s = self._SessionFactory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        if self.engine.dialect.name == "sqlite" and self.database_path:
            import sqlite3
            try:
                conn = sqlite3.connect(self.database_path)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                self._migrate_schema(conn)
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS reaction_step_fts USING fts5(
                        reaction_step_id,
                        content
                    );
                    """
                )
                conn.commit()
                conn.close()
            except Exception:
                pass

        # Initialize default Zotero endpoint if empty
        from .orm import ZoteroMcpEndpointModel
        from sqlalchemy import select, func
        with self.session() as s:
            count = s.scalar(select(func.count(ZoteroMcpEndpointModel.id))) or 0
            if count == 0:
                now = utc_now()
                ep = ZoteroMcpEndpointModel(
                    id="local-zotero",
                    alias="Local Zotero",
                    group_name="local",
                    url="http://127.0.0.1:23120/mcp",
                    enabled=1,
                    priority=100,
                    timeout_seconds=10.0,
                    headers={},
                    write_note_enabled=1,
                    created_at=now,
                    updated_at=now
                )
                s.add(ep)
                s.commit()

    # --- AI Providers ---
    def list_ai_providers(self) -> list[AiProviderModel]:
        from sqlalchemy import select
        with self.session() as s:
            return list(s.scalars(select(AiProviderModel).order_by(AiProviderModel.created_at.asc())).all())

    def get_ai_provider(self, provider_id: str) -> AiProviderModel | None:
        with self.session() as s:
            return s.get(AiProviderModel, provider_id)

    def upsert_ai_provider(self, provider: Any) -> None:
        now = utc_now()
        with self.session() as s:
            db_provider = s.get(AiProviderModel, provider.id)
            if not db_provider:
                db_provider = AiProviderModel(
                    id=provider.id,
                    created_at=now
                )
                s.add(db_provider)
            db_provider.name = provider.name
            db_provider.format = provider.format
            db_provider.endpoint = provider.endpoint
            db_provider.api_key = provider.api_key
            db_provider.models_endpoint = provider.models_endpoint
            db_provider.available_models = list(provider.available_models)
            db_provider.enabled_models = list(provider.enabled_models)
            db_provider.updated_at = now

    def delete_ai_provider(self, provider_id: str) -> bool:
        from sqlalchemy import delete
        with self.session() as s:
            res = s.execute(delete(AiProviderModel).where(AiProviderModel.id == provider_id))
            return res.rowcount > 0
    def _migrate_schema(self, conn) -> None:
        if self.engine.dialect.name != "sqlite":
            return
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
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(ParseJobModel).where(ParseJobModel.status == "running")
            jobs = s.scalars(stmt).all()
            for job in jobs:
                job.status = status
                job.stage = "queued" if status == "queued" else "failed"
                job.error = error
                job.finished_at = None
            s.commit()
            return len(jobs)

    def claim_next_job(self) -> ParseJobModel | None:
        from sqlalchemy import select, func, text
        if self.engine.dialect.name == "sqlite":
            with self.session() as s:
                s.execute(text("BEGIN IMMEDIATE"))
                stmt = select(ParseJobModel).where(ParseJobModel.status == "queued").order_by(
                    func.coalesce(ParseJobModel.started_at, "").asc(),
                    ParseJobModel.id.asc()
                ).limit(1)
                job = s.scalars(stmt).first()
                if not job:
                    return None
                job.status = "running"
                job.stage = "document_parse"
                job.error = None
                job.started_at = utc_now()
                job.finished_at = None
                s.commit()
                return job
        else:
            with self.session() as s:
                stmt = select(ParseJobModel).where(ParseJobModel.status == "queued").order_by(
                    func.coalesce(ParseJobModel.started_at, "").asc(),
                    ParseJobModel.id.asc()
                ).limit(1).with_for_update(skip_locked=True)
                job = s.scalars(stmt).first()
                if not job:
                    return None
                job.status = "running"
                job.stage = "document_parse"
                job.error = None
                job.started_at = utc_now()
                job.finished_at = None
                s.commit()
                return job

    def retry_job(self, job_id: str) -> ParseJobModel:
        with self.session() as s:
            job = s.get(ParseJobModel, job_id)
            if not job:
                raise KeyError(f"Parse job not found: {job_id}")
            if job.status not in {"failed", "completed"}:
                raise ValueError(f"Only failed/completed jobs can be retried; current status is {job.status}")
            job.status = "queued"
            job.stage = "queued"
            job.error = None
            job.finished_at = None
            s.commit()
            return job

    def retry_failed_jobs(self, limit: int = 100) -> list[ParseJobModel]:
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(ParseJobModel).where(ParseJobModel.status == "failed").order_by(
                ParseJobModel.finished_at.desc()
            ).limit(limit)
            jobs = s.scalars(stmt).all()
            job_ids = [job.id for job in jobs]
        return [self.retry_job(jid) for jid in job_ids]
    def upsert_document(
        self,
        *,
        file_path: str,
        file_hash: str,
        file_type: str,
        title: str | None,
        doi: str | None,
        ingest_status: str,
    ) -> SourceDocumentModel:
        now = utc_now()
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(SourceDocumentModel).where(
                SourceDocumentModel.file_hash == file_hash,
                SourceDocumentModel.file_path == file_path
            )
            doc = s.scalars(stmt).first()
            if doc:
                doc.file_type = file_type
                doc.title = title
                doc.doi = doi
                doc.ingest_status = ingest_status
                doc.updated_at = now
            else:
                doc = SourceDocumentModel(
                    id=new_id("doc"),
                    file_path=file_path,
                    file_hash=file_hash,
                    file_type=file_type,
                    title=title,
                    doi=doi,
                    scifinder_metadata={},
                    ingest_status=ingest_status,
                    created_at=now,
                    updated_at=now
                )
                s.add(doc)
            _ = doc.id
            return doc

    def set_document_status(self, document_id: str, status: str) -> None:
        with self.session() as s:
            doc = s.get(SourceDocumentModel, document_id)
            if doc:
                doc.ingest_status = status
                doc.updated_at = utc_now()

    def update_document_metadata(self, document_id: str, *, file_type: str, title: str | None, doi: str | None) -> None:
        with self.session() as s:
            doc = s.get(SourceDocumentModel, document_id)
            if doc:
                doc.file_type = file_type
                doc.title = title
                doc.doi = doi
                doc.updated_at = utc_now()

    def update_document_scifinder_metadata(self, document_id: str, metadata: dict[str, Any]) -> None:
        with self.session() as s:
            doc = s.get(SourceDocumentModel, document_id)
            if doc:
                existing = doc.scifinder_metadata if isinstance(doc.scifinder_metadata, dict) else {}
                doc.scifinder_metadata = {**existing, **metadata}
                doc.updated_at = utc_now()

    def create_job(self, document_id: str, *, status: str = "queued", stage: str = "queued") -> ParseJobModel:
        job_id = new_id("job")
        now = utc_now()
        with self.session() as s:
            job = ParseJobModel(
                id=job_id,
                document_id=document_id,
                status=status,
                stage=stage,
                error=None,
                started_at=now if status == "running" else None,
                finished_at=None
            )
            s.add(job)
            s.commit()
            return job

    def create_queued_document_job(
        self,
        *,
        file_path: str,
        file_hash: str,
        file_type: str,
        title: str | None,
        doi: str | None,
    ) -> tuple[SourceDocumentModel, ParseJobModel]:
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
        with self.session() as s:
            job = s.get(ParseJobModel, job_id)
            if job:
                job.status = status
                job.stage = stage
                job.error = error
                if started_at:
                    if job.started_at is None:
                        job.started_at = started_at
                else:
                    if finished_at is not None:
                        job.finished_at = finished_at
                s.commit()

    def get_job(self, job_id: str) -> ParseJobModel | None:
        with self.session() as s:
            return s.get(ParseJobModel, job_id)

    def list_jobs(self, *, status: str = "", limit: int = 100) -> list[ParseJobModel]:
        with self.session() as s:
            from sqlalchemy import select, func
            stmt = select(ParseJobModel)
            if status:
                stmt = stmt.where(ParseJobModel.status == status)
            stmt = stmt.order_by(
                func.coalesce(ParseJobModel.started_at, "").desc(),
                ParseJobModel.id.desc()
            ).limit(limit)
            return list(s.scalars(stmt).all())

    def get_latest_job_for_document(self, document_id: str) -> ParseJobModel | None:
        with self.session() as s:
            from sqlalchemy import select, func
            stmt = select(ParseJobModel).where(
                ParseJobModel.document_id == document_id
            ).order_by(
                func.coalesce(ParseJobModel.finished_at, ParseJobModel.started_at, "").desc(),
                ParseJobModel.id.desc()
            ).limit(1)
            return s.scalars(stmt).first()

    def get_document(self, document_id: str) -> SourceDocumentModel | None:
        with self.session() as s:
            return s.get(SourceDocumentModel, document_id)

    def list_documents(self, *, query: str = "", file_type: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        clauses = ["d.deleted_at IS NULL"]
        params: dict[str, Any] = {}
        if query:
            like = f"%{query}%"
            clauses.append("(d.title LIKE :like OR d.file_path LIKE :like OR d.doi LIKE :like OR d.id LIKE :like)")
            params["like"] = like
        if file_type:
            clauses.append("d.file_type = :file_type")
            params["file_type"] = file_type
        where = " AND ".join(clauses)
        params["limit"] = limit
        params["offset"] = offset

        from sqlalchemy import text
        sql = f"""
            SELECT d.*,
                   COUNT(DISTINCT pc.id) AS parsed_chunk_count,
                   COUNT(DISTINCT rs.id) AS reaction_step_count,
                   MAX(j.finished_at) AS last_job_finished_at,
                   (
                     SELECT j2.status FROM parse_job j2
                     WHERE j2.document_id = d.id
                     ORDER BY COALESCE(j2.started_at, '') DESC, j2.id DESC
                     LIMIT 1
                   ) AS last_job_status,
                   (
                     SELECT j3.error FROM parse_job j3
                     WHERE j3.document_id = d.id AND j3.error IS NOT NULL
                     ORDER BY COALESCE(j3.finished_at, j3.started_at, '') DESC, j3.id DESC
                     LIMIT 1
                   ) AS last_job_error
            FROM source_document d
            LEFT JOIN parsed_chunk pc ON pc.source_document_id = d.id
            LEFT JOIN reaction_step rs ON rs.source_document_id = d.id AND rs.deleted_at IS NULL
            LEFT JOIN parse_job j ON j.document_id = d.id
            WHERE {where}
            GROUP BY d.id
            ORDER BY d.updated_at DESC, d.created_at DESC
            LIMIT :limit OFFSET :offset
        """

        with self.session() as s:
            result = s.execute(text(sql), params).mappings().all()

        summaries = []
        for row in result:
            meta = row["scifinder_metadata"]
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            elif meta is None:
                meta = {}

            doc_dict = {
                "id": row["id"],
                "file_path": row["file_path"],
                "file_hash": row["file_hash"],
                "file_type": row["file_type"],
                "title": row["title"],
                "doi": row["doi"],
                "scifinder_metadata": meta,
                "ingest_status": row["ingest_status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "deleted_at": row.get("deleted_at"),
                "file_name": Path(row["file_path"]).name,
                "parsed_chunk_count": int(row["parsed_chunk_count"] or 0),
                "reaction_step_count": int(row["reaction_step_count"] or 0),
                "last_job_status": row["last_job_status"],
                "last_job_error": row["last_job_error"],
                "last_job_finished_at": row["last_job_finished_at"],
            }
            summaries.append(doc_dict)
        return summaries

    def get_document_by_hash_path(self, *, file_hash: str, file_path: str) -> SourceDocumentModel | None:
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(SourceDocumentModel).where(
                SourceDocumentModel.file_hash == file_hash,
                SourceDocumentModel.file_path == file_path
            )
            return s.scalars(stmt).first()

    def get_document_by_hash(self, file_hash: str) -> SourceDocumentModel | None:
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(SourceDocumentModel).where(SourceDocumentModel.file_hash == file_hash).order_by(SourceDocumentModel.created_at.asc()).limit(1)
            return s.scalars(stmt).first()

    def count_documents(self) -> int:
        from sqlalchemy import select, func
        with self.session() as s:
            stmt = select(func.count(SourceDocumentModel.id)).where(SourceDocumentModel.deleted_at.is_(None))
            return s.scalar(stmt) or 0

    def count_reaction_steps(self) -> int:
        from sqlalchemy import select, func
        with self.session() as s:
            stmt = select(func.count(ReactionStepModel.id)).where(ReactionStepModel.deleted_at.is_(None))
            return s.scalar(stmt) or 0

    def clear_document_reactions(self, document_id: str) -> None:
        from sqlalchemy import delete, select, text, or_
        with self.session() as s:
            step_ids = s.scalars(select(ReactionStepModel.id).where(ReactionStepModel.source_document_id == document_id)).all()
            if s.bind.dialect.name == "sqlite":
                for step_id in step_ids:
                    s.execute(text("DELETE FROM reaction_step_fts WHERE reaction_step_id = :step_id"), {"step_id": step_id})
            s.execute(delete(ReactionStepModel).where(ReactionStepModel.source_document_id == document_id))
            from .orm import RdfReactionRecordModel, ReactionSourceLinkModel, PdfReactionEvidenceModel
            s.execute(delete(RdfReactionRecordModel).where(RdfReactionRecordModel.source_document_id == document_id))
            
            # Clean up reaction source links and pdf evidence on reparse
            s.execute(delete(PdfReactionEvidenceModel).where(PdfReactionEvidenceModel.source_document_id == document_id))
            
            links_to_check = s.query(ReactionSourceLinkModel).where(or_(
                ReactionSourceLinkModel.rdf_document_id == document_id,
                ReactionSourceLinkModel.pdf_document_id == document_id
            )).all()
            
            for link in links_to_check:
                if link.rdf_document_id == document_id and link.pdf_document_id == document_id:
                    s.delete(link)
                elif link.rdf_document_id == document_id and link.pdf_document_id:
                    # Downgrade to pdf_only
                    link.source_mode = "pdf_only"
                    link.rdf_document_id = None
                    link.rdf_reaction_id = None
                    link.link_method = "unlinked_from_rdf"
                    link.link_confidence = 0.8
                    link.needs_review = 1
                elif link.pdf_document_id == document_id and link.rdf_document_id:
                    # Downgrade to rdf_only
                    link.source_mode = "rdf_only"
                    link.pdf_document_id = None
                    link.primary_pdf_page = None
                    link.pdf_pages_json = "[]"
                    link.link_method = "unlinked_from_pdf"
                    link.link_confidence = 1.0
                else:
                    s.delete(link)

    def replace_parsed_chunks(self, document_id: str, chunks: Iterable[Any]) -> int:
        from sqlalchemy import delete
        now = utc_now()
        inserted = 0
        with self.session() as s:
            s.execute(delete(ParsedChunkModel).where(ParsedChunkModel.source_document_id == document_id))
            for index, chunk in enumerate(chunks):
                text_val = str(getattr(chunk, "text", "") or "")
                if not text_val.strip():
                    continue
                db_chunk = ParsedChunkModel(
                    id=new_id("chunk"),
                    source_document_id=document_id,
                    chunk_index=index,
                    page_number=getattr(chunk, "page_number", None),
                    text=text_val,
                    parser_name=str(getattr(chunk, "parser_name", "parser") or "parser"),
                    parser_version=str(getattr(chunk, "parser_version", "unknown") or "unknown"),
                    created_at=now
                )
                s.add(db_chunk)
                inserted += 1
        return inserted

    def list_parsed_chunks(self, document_id: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        from sqlalchemy import select, func
        with self.session() as s:
            total_stmt = select(func.count(ParsedChunkModel.id)).where(ParsedChunkModel.source_document_id == document_id)
            total = s.scalar(total_stmt) or 0
            stmt = select(ParsedChunkModel).where(ParsedChunkModel.source_document_id == document_id).order_by(ParsedChunkModel.chunk_index.asc()).limit(limit).offset(offset)
            rows = s.scalars(stmt).all()
        return {"total": total, "limit": limit, "offset": offset, "chunks": [row.to_dict() for row in rows]}

    def upsert_rdf_reaction_records(self, document_id: str, records: list[dict[str, Any]]) -> dict[str, int]:
        from .orm import RdfReactionRecordModel, RdfStructureModel
        from sqlalchemy import delete
        now = utc_now()
        inserted_records = 0
        inserted_structures = 0
        with self.session() as s:
            s.execute(delete(RdfReactionRecordModel).where(RdfReactionRecordModel.source_document_id == document_id))
            for record in records:
                reaction_id = new_id("rdfrec")
                rxn = RdfReactionRecordModel(
                    id=reaction_id,
                    source_document_id=document_id,
                    record_index=record.get("record_index"),
                    registry=record.get("registry"),
                    scheme_id=record.get("scheme_id"),
                    step_id=record.get("step_id"),
                    reactant_count=int(record.get("reactant_count") or 0),
                    product_count=int(record.get("product_count") or 0),
                    cas_reaction_number=record.get("cas_reaction_number"),
                    yield_text=record.get("yield_text"),
                    reagents=record.get("reagents") or [],
                    catalysts=record.get("catalysts") or [],
                    solvents=record.get("solvents") or [],
                    reference=record.get("reference") or {},
                    experimental_procedure=record.get("experimental_procedure"),
                    fields=record.get("fields") or {},
                    warnings=record.get("warnings") or [],
                    created_at=now,
                    updated_at=now
                )
                s.add(rxn)
                s.flush()
                inserted_records += 1
                
                for molecule in record.get("molecules") or []:
                    mol = RdfStructureModel(
                        id=new_id("rdfstr"),
                        rdf_reaction_id=reaction_id,
                        source_document_id=document_id,
                        role=molecule.get("role") or "unknown",
                        role_index=int(molecule.get("role_index") or 0),
                        name=molecule.get("name"),
                        formula=molecule.get("formula"),
                        cas_rn=molecule.get("cas_rn"),
                        molfile=molecule.get("molfile"),
                        molfile_version=molecule.get("molfile_version"),
                        smiles=molecule.get("smiles"),
                        inchikey=molecule.get("inchikey"),
                        fingerprint=molecule.get("fingerprint"),
                        rdkit_status=molecule.get("rdkit_status") or "not_indexed",
                        rdkit_error=molecule.get("rdkit_error"),
                        warnings=molecule.get("warnings") or [],
                        created_at=now,
                        updated_at=now
                    )
                    s.add(mol)
                    inserted_structures += 1
            s.commit()
        return {"records": inserted_records, "structures": inserted_structures}

    def list_rdf_reactions(self, *, document_id: str = "", query: str = "", limit: int = 50, offset: int = 0, include_deleted: bool = False) -> list[dict[str, Any]]:
        from .orm import RdfReactionRecordModel, RdfStructureModel, SourceDocumentModel
        from sqlalchemy import select, func, or_, and_, cast, String
        
        with self.session() as s:
            stmt = select(
                RdfReactionRecordModel,
                SourceDocumentModel.file_path.label("source_file_path"),
                SourceDocumentModel.title.label("source_title"),
                func.count(RdfStructureModel.id).label("structure_count")
            ).join(
                SourceDocumentModel,
                SourceDocumentModel.id == RdfReactionRecordModel.source_document_id
            ).outerjoin(
                RdfStructureModel,
                and_(
                    RdfStructureModel.rdf_reaction_id == RdfReactionRecordModel.id,
                    or_(include_deleted, RdfStructureModel.deleted_at.is_(None))
                )
            )
            
            if not include_deleted:
                stmt = stmt.where(RdfReactionRecordModel.deleted_at.is_(None))
                
            if document_id:
                stmt = stmt.where(RdfReactionRecordModel.source_document_id == document_id)
                
            if query:
                like = f"%{query}%"
                exists_cond = select(1).select_from(RdfStructureModel).where(
                    RdfStructureModel.rdf_reaction_id == RdfReactionRecordModel.id,
                    or_(include_deleted, RdfStructureModel.deleted_at.is_(None)),
                    or_(
                        RdfStructureModel.cas_rn.like(like),
                        RdfStructureModel.name.like(like),
                        RdfStructureModel.formula.like(like)
                    )
                ).exists()
                
                stmt = stmt.where(
                    or_(
                        RdfReactionRecordModel.cas_reaction_number.like(like),
                        RdfReactionRecordModel.scheme_id.like(like),
                        RdfReactionRecordModel.step_id.like(like),
                        cast(RdfReactionRecordModel.reference, String).like(like),
                        SourceDocumentModel.id.like(like),
                        SourceDocumentModel.file_path.like(like),
                        SourceDocumentModel.title.like(like),
                        SourceDocumentModel.doi.like(like),
                        exists_cond
                    )
                )
                
            stmt = stmt.group_by(RdfReactionRecordModel.id, SourceDocumentModel.file_path, SourceDocumentModel.title)
            stmt = stmt.order_by(RdfReactionRecordModel.source_document_id, RdfReactionRecordModel.record_index)
            stmt = stmt.limit(limit).offset(offset)
            
            results = s.execute(stmt).all()
            out = []
            for rxn, file_path, title, count in results:
                d = rxn.to_dict()
                d.update({
                    "source_file_path": file_path,
                    "source_title": title,
                    "structure_count": count
                })
                out.append(d)
            return out

    def get_rdf_reaction(self, reaction_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        from .orm import RdfReactionRecordModel, RdfStructureModel
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(RdfReactionRecordModel).where(RdfReactionRecordModel.id == reaction_id)
            if not include_deleted:
                stmt = stmt.where(RdfReactionRecordModel.deleted_at.is_(None))
            rxn = s.scalars(stmt).first()
            if not rxn:
                return None
            
            struct_stmt = select(RdfStructureModel).where(RdfStructureModel.rdf_reaction_id == reaction_id)
            if not include_deleted:
                struct_stmt = struct_stmt.where(RdfStructureModel.deleted_at.is_(None))
            struct_stmt = struct_stmt.order_by(RdfStructureModel.role, RdfStructureModel.role_index)
            structures = s.scalars(struct_stmt).all()
            
            data = rxn.to_dict()
            data["structures"] = [item.to_dict() for item in structures]
            return data

    def list_rdf_structures(self, *, document_id: str = "", query: str = "", limit: int = 50, offset: int = 0, include_deleted: bool = False) -> list[dict[str, Any]]:
        from .orm import RdfStructureModel, RdfReactionRecordModel
        from sqlalchemy import select, func, or_
        with self.session() as s:
            stmt = select(
                RdfStructureModel,
                RdfReactionRecordModel.record_index,
                RdfReactionRecordModel.scheme_id,
                RdfReactionRecordModel.step_id,
                RdfReactionRecordModel.cas_reaction_number,
                RdfReactionRecordModel.yield_text
            ).join(
                RdfReactionRecordModel,
                RdfReactionRecordModel.id == RdfStructureModel.rdf_reaction_id
            )
            
            if not include_deleted:
                stmt = stmt.where(
                    RdfStructureModel.deleted_at.is_(None),
                    RdfReactionRecordModel.deleted_at.is_(None)
                )
            if document_id:
                stmt = stmt.where(RdfStructureModel.source_document_id == document_id)
            if query:
                like = f"%{query.lower()}%"
                stmt = stmt.where(
                    or_(
                        func.lower(func.coalesce(RdfStructureModel.name, "")).like(like),
                        func.lower(func.coalesce(RdfStructureModel.cas_rn, "")).like(like),
                        func.lower(func.coalesce(RdfStructureModel.smiles, "")).like(like),
                        func.lower(func.coalesce(RdfStructureModel.inchikey, "")).like(like)
                    )
                )
            stmt = stmt.order_by(RdfStructureModel.updated_at.desc(), RdfStructureModel.role, RdfStructureModel.role_index)
            stmt = stmt.limit(limit).offset(offset)
            
            results = s.execute(stmt).all()
            out = []
            for struct, record_index, scheme_id, step_id, cas_reaction_number, yield_text in results:
                d = struct.to_dict()
                d.update({
                    "record_index": record_index,
                    "scheme_id": scheme_id,
                    "step_id": step_id,
                    "cas_reaction_number": cas_reaction_number,
                    "yield_text": yield_text
                })
                out.append(d)
            return out

    def get_rdf_structure(self, structure_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        from .orm import RdfStructureModel, RdfReactionRecordModel
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(
                RdfStructureModel,
                RdfReactionRecordModel.record_index,
                RdfReactionRecordModel.scheme_id,
                RdfReactionRecordModel.step_id,
                RdfReactionRecordModel.cas_reaction_number,
                RdfReactionRecordModel.yield_text
            ).join(
                RdfReactionRecordModel,
                RdfReactionRecordModel.id == RdfStructureModel.rdf_reaction_id
            ).where(
                RdfStructureModel.id == structure_id
            )
            if not include_deleted:
                stmt = stmt.where(
                    RdfStructureModel.deleted_at.is_(None),
                    RdfReactionRecordModel.deleted_at.is_(None)
                )
            res = s.execute(stmt).first()
            if not res:
                return None
            struct, record_index, scheme_id, step_id, cas_reaction_number, yield_text = res
            d = struct.to_dict()
            d.update({
                "record_index": record_index,
                "scheme_id": scheme_id,
                "step_id": step_id,
                "cas_reaction_number": cas_reaction_number,
                "yield_text": yield_text
            })
            return d

    def list_rdf_structures_for_search(self, *, limit: int = 10000) -> list[dict[str, Any]]:
        return self.list_rdf_structures(limit=limit)

    def rdf_structure_index_status(self) -> dict[str, Any]:
        from .orm import RdfStructureModel, RdfReactionRecordModel
        from sqlalchemy import select, func
        with self.session() as s:
            total = s.scalar(select(func.count(RdfStructureModel.id)).where(RdfStructureModel.deleted_at.is_(None))) or 0
            indexed = s.scalar(select(func.count(RdfStructureModel.id)).where(RdfStructureModel.deleted_at.is_(None), RdfStructureModel.rdkit_status == 'indexed')) or 0
            failed = s.scalar(select(func.count(RdfStructureModel.id)).where(RdfStructureModel.deleted_at.is_(None), RdfStructureModel.rdkit_status == 'rdkit_failed')) or 0
            unavailable = s.scalar(select(func.count(RdfStructureModel.id)).where(RdfStructureModel.deleted_at.is_(None), RdfStructureModel.rdkit_status == 'rdkit_unavailable')) or 0
            reactions = s.scalar(select(func.count(RdfReactionRecordModel.id)).where(RdfReactionRecordModel.deleted_at.is_(None))) or 0
            
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
        from .orm import SourceDocumentModel, ReactionStepModel, RdfReactionRecordModel, RdfStructureModel
        from sqlalchemy import update
        now = utc_now()
        with self.session() as s:
            if entity_type == "document":
                s.execute(update(SourceDocumentModel).where(SourceDocumentModel.id == entity_id).values(deleted_at=now, updated_at=now))
                s.execute(update(ReactionStepModel).where(ReactionStepModel.source_document_id == entity_id).values(deleted_at=now))
                s.execute(update(RdfReactionRecordModel).where(RdfReactionRecordModel.source_document_id == entity_id).values(deleted_at=now, updated_at=now))
                s.execute(update(RdfStructureModel).where(RdfStructureModel.source_document_id == entity_id).values(deleted_at=now, updated_at=now))
            elif entity_type == "rdf_reaction":
                s.execute(update(RdfReactionRecordModel).where(RdfReactionRecordModel.id == entity_id).values(deleted_at=now, updated_at=now))
                s.execute(update(RdfStructureModel).where(RdfStructureModel.rdf_reaction_id == entity_id).values(deleted_at=now, updated_at=now))
            elif entity_type == "rdf_structure":
                s.execute(update(RdfStructureModel).where(RdfStructureModel.id == entity_id).values(deleted_at=now, updated_at=now))
            elif entity_type == "reaction_step":
                s.execute(update(ReactionStepModel).where(ReactionStepModel.id == entity_id).values(deleted_at=now))
            else:
                raise ValueError(f"Unsupported delete entity type: {entity_type}")
            s.commit()
        return {"status": "trashed", "entity_type": entity_type, "entity_id": entity_id, "deleted_at": now}

    def restore_trash_item(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        from .orm import SourceDocumentModel, ReactionStepModel, RdfReactionRecordModel, RdfStructureModel
        from sqlalchemy import update
        now = utc_now()
        with self.session() as s:
            if entity_type == "document":
                s.execute(update(SourceDocumentModel).where(SourceDocumentModel.id == entity_id).values(deleted_at=None, updated_at=now))
                s.execute(update(ReactionStepModel).where(ReactionStepModel.source_document_id == entity_id).values(deleted_at=None))
                s.execute(update(RdfReactionRecordModel).where(RdfReactionRecordModel.source_document_id == entity_id).values(deleted_at=None, updated_at=now))
                s.execute(update(RdfStructureModel).where(RdfStructureModel.source_document_id == entity_id).values(deleted_at=None, updated_at=now))
            elif entity_type == "rdf_reaction":
                s.execute(update(RdfReactionRecordModel).where(RdfReactionRecordModel.id == entity_id).values(deleted_at=None, updated_at=now))
                s.execute(update(RdfStructureModel).where(RdfStructureModel.rdf_reaction_id == entity_id).values(deleted_at=None, updated_at=now))
            elif entity_type == "rdf_structure":
                s.execute(update(RdfStructureModel).where(RdfStructureModel.id == entity_id).values(deleted_at=None, updated_at=now))
            elif entity_type == "reaction_step":
                s.execute(update(ReactionStepModel).where(ReactionStepModel.id == entity_id).values(deleted_at=None))
            else:
                raise ValueError(f"Unsupported restore entity type: {entity_type}")
            s.commit()
        return {"status": "restored", "entity_type": entity_type, "entity_id": entity_id}

    def list_trash(self, limit: int = 100) -> list[dict[str, Any]]:
        from .orm import SourceDocumentModel, RdfReactionRecordModel, RdfStructureModel
        from sqlalchemy import select
        with self.session() as s:
            doc_stmt = select(
                SourceDocumentModel.id,
                SourceDocumentModel.title,
                SourceDocumentModel.file_path,
                SourceDocumentModel.deleted_at
            ).where(
                SourceDocumentModel.deleted_at.is_not(None)
            ).order_by(
                SourceDocumentModel.deleted_at.desc()
            ).limit(limit)
            documents = s.execute(doc_stmt).all()
            
            rxn_stmt = select(
                RdfReactionRecordModel.id,
                RdfReactionRecordModel.cas_reaction_number.label("title"),
                RdfReactionRecordModel.source_document_id,
                RdfReactionRecordModel.deleted_at
            ).where(
                RdfReactionRecordModel.deleted_at.is_not(None)
            ).order_by(
                RdfReactionRecordModel.deleted_at.desc()
            ).limit(limit)
            reactions = s.execute(rxn_stmt).all()
            
            struct_stmt = select(
                RdfStructureModel.id,
                RdfStructureModel.name.label("title"),
                RdfStructureModel.cas_rn,
                RdfStructureModel.rdf_reaction_id,
                RdfStructureModel.deleted_at
            ).where(
                RdfStructureModel.deleted_at.is_not(None)
            ).order_by(
                RdfStructureModel.deleted_at.desc()
            ).limit(limit)
            structures = s.execute(struct_stmt).all()
            
        items: list[dict[str, Any]] = []
        items.extend({"entity_type": "document", "id": r.id, "title": r.title, "file_path": r.file_path, "deleted_at": r.deleted_at} for r in documents)
        items.extend({"entity_type": "rdf_reaction", "id": r.id, "title": r.title, "source_document_id": r.source_document_id, "deleted_at": r.deleted_at} for r in reactions)
        items.extend({"entity_type": "rdf_structure", "id": r.id, "title": r.title, "cas_rn": r.cas_rn, "rdf_reaction_id": r.rdf_reaction_id, "deleted_at": r.deleted_at} for r in structures)
        items.sort(key=lambda item: item.get("deleted_at") or "", reverse=True)
        return items[:limit]

    def empty_trash(self) -> dict[str, int]:
        from .orm import RdfStructureModel, RdfReactionRecordModel, ReactionStepModel, SourceDocumentModel
        from sqlalchemy import delete, select
        with self.session() as s:
            structures = s.execute(delete(RdfStructureModel).where(RdfStructureModel.deleted_at.is_not(None))).rowcount
            reactions = s.execute(delete(RdfReactionRecordModel).where(RdfReactionRecordModel.deleted_at.is_not(None))).rowcount
            
            step_ids_stmt = select(ReactionStepModel.id).where(ReactionStepModel.deleted_at.is_not(None))
            step_ids = s.scalars(step_ids_stmt).all()
            if step_ids:
                if s.bind.dialect.name == "sqlite":
                    from sqlalchemy import text
                    for step_id in step_ids:
                        s.execute(
                            text("DELETE FROM reaction_step_fts WHERE reaction_step_id = :step_id"),
                            {"step_id": step_id}
                        )
            reaction_steps = s.execute(delete(ReactionStepModel).where(ReactionStepModel.deleted_at.is_not(None))).rowcount
            documents = s.execute(delete(SourceDocumentModel).where(SourceDocumentModel.deleted_at.is_not(None))).rowcount
            s.commit()
            
        return {"documents": documents, "reaction_steps": reaction_steps, "rdf_reactions": reactions, "rdf_structures": structures}

    def insert_reaction_step(self, step: dict[str, Any], provenance: dict[str, Any]) -> ReactionStepModel:
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
        with self.session() as s:
            rxn = ReactionStepModel(
                id=step_id,
                source_document_id=values["source_document_id"],
                step_index=values["step_index"],
                reaction_name=values["reaction_name"],
                substrate_text=values["substrate_text"],
                product_text=values["product_text"],
                reagent_text=values["reagent_text"],
                catalyst_text=values["catalyst_text"],
                solvent_text=values["solvent_text"],
                temperature=values["temperature"],
                time=values["time"],
                atmosphere=values["atmosphere"],
                yield_text=values["yield_text"],
                scale=values["scale"],
                workup=values["workup"],
                purification=values["purification"],
                original_text=values["original_text"],
                confidence=values["confidence"],
                verification_status=values["verification_status"],
                needs_ocr=bool(values["needs_ocr"]),
                extraction_method=values.get("extraction_method", "rules"),
                schema_version=values.get("schema_version", "reaction_step.v1"),
                llm_confidence=values.get("llm_confidence"),
                metadata_=values.get("metadata") or {},
                created_at=created_at,
                deleted_at=None
            )
            s.add(rxn)
            s.flush()

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
            if s.bind.dialect.name == "sqlite":
                from sqlalchemy import text
                s.execute(
                    text("INSERT INTO reaction_step_fts (reaction_step_id, content) VALUES (:step_id, :content)"),
                    {"step_id": step_id, "content": fts_content}
                )

            prov = ProvenanceModel(
                id=provenance_id,
                reaction_step_id=step_id,
                source_document_id=values["source_document_id"],
                page_number=provenance.get("page_number"),
                text_span=provenance["text_span"],
                image_region_path=provenance.get("image_region_path"),
                ocr_output=provenance.get("ocr_output"),
                parser_name=provenance["parser_name"],
                parser_version=provenance["parser_version"],
                confidence=provenance["confidence"]
            )
            s.add(prov)
            s.commit()
            return rxn

    def search_reaction_steps(
        self,
        *,
        query: str = "",
        reagent: str = "",
        solvent: str = "",
        document_id: str = "",
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[ReactionStepModel]:
        with self.session() as s:
            from sqlalchemy import select, text
            stmt = select(ReactionStepModel).where(
                ReactionStepModel.confidence >= min_confidence,
                ReactionStepModel.deleted_at.is_(None)
            )
            if reagent:
                from sqlalchemy import func
                stmt = stmt.where(func.lower(func.coalesce(ReactionStepModel.reagent_text, "")).like(f"%{reagent.lower()}%"))
            if solvent:
                from sqlalchemy import func
                stmt = stmt.where(func.lower(func.coalesce(ReactionStepModel.solvent_text, "")).like(f"%{solvent.lower()}%"))
            if document_id:
                stmt = stmt.where(ReactionStepModel.source_document_id == document_id)

            if query:
                if s.bind.dialect.name == "sqlite":
                    stmt = stmt.where(text("reaction_step.id IN (SELECT reaction_step_id FROM reaction_step_fts WHERE content MATCH :q)")).params(q=self._fts_query(query))
                else:
                    concat_cols = "coalesce(reaction_name, '') || ' ' || coalesce(substrate_text, '') || ' ' || coalesce(product_text, '') || ' ' || coalesce(reagent_text, '') || ' ' || coalesce(catalyst_text, '') || ' ' || coalesce(solvent_text, '') || ' ' || coalesce(temperature, '') || ' ' || coalesce(time, '') || ' ' || coalesce(yield_text, '') || ' ' || coalesce(scale, '') || ' ' || coalesce(workup, '') || ' ' || coalesce(purification, '') || ' ' || coalesce(original_text, '')"
                    stmt = stmt.where(text(f"to_tsvector('english', {concat_cols}) @@ plainto_tsquery('english', :q)")).params(q=query)

            stmt = stmt.order_by(ReactionStepModel.confidence.desc(), ReactionStepModel.step_index.asc()).limit(limit)
            return list(s.scalars(stmt).all())

    def get_reaction_step(self, reaction_step_id: str) -> ReactionStepModel | None:
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(ReactionStepModel).where(
                ReactionStepModel.id == reaction_step_id,
                ReactionStepModel.deleted_at.is_(None)
            )
            return s.scalars(stmt).first()

    def get_provenance(self, reaction_step_id: str) -> list[ProvenanceModel]:
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(ProvenanceModel).where(
                ProvenanceModel.reaction_step_id == reaction_step_id
            ).order_by(ProvenanceModel.id)
            return list(s.scalars(stmt).all())

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
        from .orm import ExportBatchDocumentModel, ExportBatchModel
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(
                ExportBatchModel,
                ExportBatchDocumentModel.role,
                ExportBatchDocumentModel.confidence.label("document_confidence"),
                ExportBatchDocumentModel.explanation.label("document_explanation")
            ).join(
                ExportBatchModel,
                ExportBatchModel.id == ExportBatchDocumentModel.batch_id
            ).where(
                ExportBatchDocumentModel.source_document_id == document_id
            ).order_by(
                ExportBatchDocumentModel.confidence.desc(),
                ExportBatchModel.updated_at.desc()
            )
            results = s.execute(stmt).all()
            out = []
            for b, role, doc_conf, doc_expl in results:
                d = b.to_dict()
                d.update({
                    "role": role,
                    "document_confidence": doc_conf,
                    "document_explanation": doc_expl
                })
                out.append(d)
            return out

    def list_export_batches(self, limit: int = 100) -> list[dict[str, Any]]:
        from .orm import ExportBatchModel
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(ExportBatchModel).order_by(ExportBatchModel.updated_at.desc()).limit(limit)
            return [b.to_dict() for b in s.scalars(stmt).all()]

    def get_export_batch(self, batch_id: str) -> dict[str, Any] | None:
        from .orm import ExportBatchModel, ExportBatchDocumentModel, SourceDocumentModel
        with self.session() as s:
            batch = s.get(ExportBatchModel, batch_id)
            if not batch:
                return None
            from sqlalchemy import select
            stmt = select(
                SourceDocumentModel,
                ExportBatchDocumentModel.role,
                ExportBatchDocumentModel.confidence.label("link_confidence"),
                ExportBatchDocumentModel.explanation.label("link_explanation")
            ).join(
                SourceDocumentModel,
                SourceDocumentModel.id == ExportBatchDocumentModel.source_document_id
            ).where(
                ExportBatchDocumentModel.batch_id == batch_id
            ).order_by(
                ExportBatchDocumentModel.role,
                SourceDocumentModel.created_at
            )
            docs = s.execute(stmt).all()
            
            data = batch.to_dict()
            doc_list = []
            for d, role, link_conf, link_expl in docs:
                doc_dict = d.to_dict()
                doc_dict.update({
                    "role": role,
                    "link_confidence": link_conf,
                    "link_explanation": link_expl
                })
                doc_list.append(doc_dict)
            data["documents"] = doc_list
            return data

    def unlink_document_from_batch(self, document_id: str, batch_id: str, reason: str = "") -> dict[str, Any]:
        from .orm import ExportBatchDocumentModel, ExportBatchModel
        from sqlalchemy import delete
        with self.session() as s:
            s.execute(
                delete(ExportBatchDocumentModel).where(
                    ExportBatchDocumentModel.source_document_id == document_id,
                    ExportBatchDocumentModel.batch_id == batch_id
                )
            )
            batch = s.get(ExportBatchModel, batch_id)
            if batch:
                explanation = dict(batch.explanation or {})
                explanation["last_unlink_reason"] = reason
                batch.explanation = explanation
                batch.updated_at = utc_now()
            s.commit()
        return {"status": "unlinked", "document_id": document_id, "batch_id": batch_id, "reason": reason}

    def _best_batch_candidate(self, document: SourceDocumentModel) -> dict[str, Any] | None:
        from .orm import ExportBatchModel, ExportBatchDocumentModel, SourceDocumentModel
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(
                ExportBatchModel.id.label("batch_id"),
                ExportBatchModel.title,
                SourceDocumentModel.file_path,
                SourceDocumentModel.file_type,
                SourceDocumentModel.title.label("document_title")
            ).join(
                ExportBatchDocumentModel,
                ExportBatchDocumentModel.batch_id == ExportBatchModel.id
            ).join(
                SourceDocumentModel,
                SourceDocumentModel.id == ExportBatchDocumentModel.source_document_id
            ).order_by(
                ExportBatchModel.updated_at.desc()
            ).limit(100)
            rows = s.execute(stmt).all()
            
        best: dict[str, Any] | None = None
        for row in rows:
            score, explanation = batch_match_score(document, row._mapping)
            if not best or score > best["confidence"]:
                best = {"batch_id": row._mapping["batch_id"], "confidence": score, "explanation": explanation}
        return best

    def _create_export_batch(self, title: str | None, *, status: str, confidence: float, merge_method: str, explanation: dict[str, Any]) -> str:
        batch_id = new_id("batch")
        now = utc_now()
        from .orm import ExportBatchModel
        with self.session() as s:
            batch = ExportBatchModel(
                id=batch_id,
                title=title,
                export_timestamp=None,
                status=status,
                confidence=confidence,
                merge_method=merge_method,
                explanation=explanation,
                created_at=now,
                updated_at=now
            )
            s.add(batch)
            s.commit()
        return batch_id

    def _link_document_to_batch(self, batch_id: str, document_id: str, role: str, confidence: float, explanation: dict[str, Any]) -> None:
        now = utc_now()
        from .orm import ExportBatchDocumentModel, ExportBatchModel
        with self.session() as s:
            if s.bind.dialect.name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                stmt = sqlite_insert(ExportBatchDocumentModel).values(
                    id=new_id("batchdoc"),
                    batch_id=batch_id,
                    source_document_id=document_id,
                    role=role,
                    confidence=confidence,
                    explanation=explanation,
                    created_at=now
                ).on_conflict_do_nothing()
                s.execute(stmt)
            else:
                from sqlalchemy.dialects.postgresql import insert as pg_insert
                stmt = pg_insert(ExportBatchDocumentModel).values(
                    id=new_id("batchdoc"),
                    batch_id=batch_id,
                    source_document_id=document_id,
                    role=role,
                    confidence=confidence,
                    explanation=explanation,
                    created_at=now
                ).on_conflict_do_nothing(index_elements=["batch_id", "source_document_id"])
                s.execute(stmt)
            
            batch = s.get(ExportBatchModel, batch_id)
            if batch:
                batch.updated_at = now
                batch.confidence = max(batch.confidence or 0.0, confidence)
            s.commit()

    def _record_batch_candidate(self, document_id: str, batch_id: str, confidence: float, explanation: dict[str, Any]) -> None:
        now = utc_now()
        from .orm import ExportBatchCandidateModel
        with self.session() as s:
            cand = ExportBatchCandidateModel(
                id=new_id("batchcand"),
                source_document_id=document_id,
                candidate_batch_id=batch_id,
                confidence=confidence,
                explanation=explanation,
                status="pending",
                created_at=now,
                updated_at=now
            )
            s.add(cand)
            s.commit()

    def add_provenance(self, reaction_step_id: str, source_document_id: str, *, text_span: str, parser_name: str, parser_version: str = "external", page_number: int | None = None, image_region_path: str | None = None, ocr_output: str | None = None, confidence: float = 0.0) -> ProvenanceModel:
        provenance_id = new_id("prov")
        with self.session() as s:
            prov = ProvenanceModel(
                id=provenance_id,
                reaction_step_id=reaction_step_id,
                source_document_id=source_document_id,
                page_number=page_number,
                text_span=text_span,
                image_region_path=image_region_path,
                ocr_output=ocr_output,
                parser_name=parser_name,
                parser_version=parser_version,
                confidence=confidence
            )
            s.add(prov)
            s.commit()
            return prov

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
        from .orm import DoiVerificationModel
        with self.session() as s:
            ver = DoiVerificationModel(
                id=verification_id,
                reaction_step_id=reaction_step_id,
                doi=doi,
                paper_title=paper_title,
                verified_fields=verified_fields,
                original_paper_excerpt=original_paper_excerpt,
                verification_confidence=verification_confidence,
                verifier_agent=verifier_agent,
                created_at=created_at
            )
            s.add(ver)
            rxn = s.get(ReactionStepModel, reaction_step_id)
            if rxn:
                rxn.verification_status = "doi_verified"
            s.commit()
            return ver.to_dict()

    def export_evaluation_rows(self, limit: int = 500) -> Iterable[dict[str, Any]]:
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(
                ReactionStepModel,
                SourceDocumentModel.file_path,
                SourceDocumentModel.title,
                SourceDocumentModel.doi
            ).join(
                SourceDocumentModel,
                SourceDocumentModel.id == ReactionStepModel.source_document_id
            ).where(
                ReactionStepModel.deleted_at.is_(None),
                SourceDocumentModel.deleted_at.is_(None)
            ).order_by(
                SourceDocumentModel.created_at.asc(),
                ReactionStepModel.step_index.asc()
            ).limit(limit)
            results = s.execute(stmt).all()
            for rxn, file_path, title, doi in results:
                row_dict = rxn.to_dict()
                row_dict.update({
                    "file_path": file_path,
                    "title": title,
                    "doi": doi
                })
                yield row_dict

    def list_reaction_steps_for_index(self, limit: int = 10000) -> list[ReactionStepModel]:
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(ReactionStepModel).where(
                ReactionStepModel.deleted_at.is_(None)
            ).order_by(
                ReactionStepModel.created_at.asc()
            ).limit(limit)
            return list(s.scalars(stmt).all())

    def upsert_embedding(self, reaction_step_id: str, *, model: str, embedding: list[float], error: str | None = None) -> None:
        from .orm import VectorIndexModel
        now = utc_now()
        with self.session() as s:
            idx = s.get(VectorIndexModel, reaction_step_id)
            if idx:
                idx.model = model
                idx.embedding = embedding
                idx.dimensions = len(embedding)
                idx.updated_at = now
                idx.error = error
            else:
                idx = VectorIndexModel(
                    reaction_step_id=reaction_step_id,
                    model=model,
                    embedding=embedding,
                    dimensions=len(embedding),
                    updated_at=now,
                    error=error
                )
                s.add(idx)
            s.commit()

    def vector_index_status(self) -> dict[str, Any]:
        from .orm import ReactionStepModel, VectorIndexModel
        from sqlalchemy import select, func
        with self.session() as s:
            total = s.scalar(select(func.count(ReactionStepModel.id)).where(ReactionStepModel.deleted_at.is_(None))) or 0
            indexed_stmt = select(func.count(VectorIndexModel.reaction_step_id)).join(
                ReactionStepModel,
                ReactionStepModel.id == VectorIndexModel.reaction_step_id
            ).where(
                VectorIndexModel.error.is_(None),
                ReactionStepModel.deleted_at.is_(None)
            )
            indexed = s.scalar(indexed_stmt) or 0
            last_stmt = select(VectorIndexModel).order_by(VectorIndexModel.updated_at.desc()).limit(1)
            last = s.scalars(last_stmt).first()
            errors_stmt = select(func.count(VectorIndexModel.reaction_step_id)).join(
                ReactionStepModel,
                ReactionStepModel.id == VectorIndexModel.reaction_step_id
            ).where(
                VectorIndexModel.error.is_not(None),
                ReactionStepModel.deleted_at.is_(None)
            )
            errors = s.scalar(errors_stmt) or 0
            
        return {
            "total_steps": int(total),
            "indexed_steps": int(indexed),
            "error_count": int(errors),
            "last_updated_at": last.updated_at if last else None,
            "last_error": last.error if last and last.error else None,
            "model": last.model if last else None,
        }

    def semantic_search(self, embedding: list[float], *, limit: int = 10) -> list[tuple[ReactionStepModel, float]]:
        from .orm import VectorIndexModel, ReactionStepModel
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(VectorIndexModel.reaction_step_id, VectorIndexModel.embedding).join(
                ReactionStepModel,
                ReactionStepModel.id == VectorIndexModel.reaction_step_id
            ).where(
                VectorIndexModel.error.is_(None),
                ReactionStepModel.deleted_at.is_(None)
            )
            rows = s.execute(stmt).all()
            
        scored: list[tuple[ReactionStepModel, float]] = []
        for step_id, emb in rows:
            if not isinstance(emb, list):
                continue
            try:
                candidate = [float(item) for item in emb]
            except (TypeError, ValueError):
                continue
            score = cosine_similarity(embedding, candidate)
            step = self.get_reaction_step(step_id)
            if step:
                scored.append((step, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def record_integration_status(self, kind: str, *, configured: bool, status: str, detail: str | None = None) -> dict[str, Any]:
        from .orm import IntegrationStatusModel
        checked_at = utc_now()
        configured_val = 1 if configured else 0
        with self.session() as s:
            item = s.get(IntegrationStatusModel, kind)
            if item:
                item.configured = configured_val
                item.status = status
                item.detail = detail
                item.checked_at = checked_at
            else:
                item = IntegrationStatusModel(
                    kind=kind,
                    configured=configured_val,
                    status=status,
                    detail=detail,
                    checked_at=checked_at
                )
                s.add(item)
            s.commit()
        return {"kind": kind, "configured": configured, "status": status, "detail": detail, "checked_at": checked_at}

    def list_integration_statuses(self) -> list[dict[str, Any]]:
        from .orm import IntegrationStatusModel
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(IntegrationStatusModel).order_by(IntegrationStatusModel.kind)
            rows = s.scalars(stmt).all()
            return [
                {"kind": row.kind, "configured": bool(row.configured), "status": row.status, "detail": row.detail, "checked_at": row.checked_at}
                for row in rows
            ]

    def count_ocr_backlog(self) -> int:
        from .orm import ReactionStepModel
        from sqlalchemy import select, func
        with self.session() as s:
            stmt = select(func.count(ReactionStepModel.id)).where(
                ReactionStepModel.needs_ocr.is_(True),
                ReactionStepModel.deleted_at.is_(None)
            )
            return s.scalar(stmt) or 0

    def low_confidence_doi_queue(self, threshold: float, limit: int = 50) -> list[dict[str, Any]]:
        from .orm import ReactionStepModel
        from sqlalchemy import select, or_
        with self.session() as s:
            stmt = select(ReactionStepModel).where(
                ReactionStepModel.deleted_at.is_(None),
                or_(
                    ReactionStepModel.confidence < threshold,
                    ReactionStepModel.verification_status != 'doi_verified'
                )
            ).order_by(
                ReactionStepModel.confidence.asc()
            ).limit(limit)
            steps = s.scalars(stmt).all()
            return [step.to_dict() for step in steps]

    def upsert_zotero_endpoint(self, data: dict[str, Any]) -> dict[str, Any]:
        from .orm import ZoteroMcpEndpointModel
        now = utc_now()
        endpoint_id = str(data.get("id") or data.get("alias") or new_id("zotep")).strip()
        alias = str(data.get("alias") or endpoint_id).strip()
        group_name = str(data.get("group_name") or data.get("group") or alias).strip()
        url = str(data.get("url") or "").strip()
        headers = data.get("headers") if isinstance(data.get("headers"), dict) else {}
        enabled = 1 if data.get("enabled", True) else 0
        priority = int(data.get("priority") or 100)
        timeout_seconds = float(data.get("timeout_seconds") or 10.0)
        write_note_enabled = 1 if data.get("write_note_enabled") else 0
        
        with self.session() as s:
            ep = s.get(ZoteroMcpEndpointModel, endpoint_id)
            if ep:
                ep.alias = alias
                ep.group_name = group_name
                ep.url = url
                ep.enabled = enabled
                ep.priority = priority
                ep.timeout_seconds = timeout_seconds
                ep.headers = headers
                ep.write_note_enabled = write_note_enabled
                ep.updated_at = now
            else:
                ep = ZoteroMcpEndpointModel(
                    id=endpoint_id,
                    alias=alias,
                    group_name=group_name,
                    url=url,
                    enabled=enabled,
                    priority=priority,
                    timeout_seconds=timeout_seconds,
                    headers=headers,
                    write_note_enabled=write_note_enabled,
                    created_at=now,
                    updated_at=now
                )
                s.add(ep)
            s.commit()
            return _endpoint_to_dict(ep, include_headers=True)

    def list_zotero_endpoints(self, *, include_headers: bool = False) -> list[dict[str, Any]]:
        from .orm import ZoteroMcpEndpointModel
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(ZoteroMcpEndpointModel).order_by(
                ZoteroMcpEndpointModel.group_name,
                ZoteroMcpEndpointModel.priority,
                ZoteroMcpEndpointModel.alias
            )
            endpoints = s.scalars(stmt).all()
            return [_endpoint_to_dict(ep, include_headers=include_headers) for ep in endpoints]

    def delete_zotero_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        from .orm import ZoteroMcpEndpointModel
        from sqlalchemy import delete
        with self.session() as s:
            count = s.execute(delete(ZoteroMcpEndpointModel).where(ZoteroMcpEndpointModel.id == endpoint_id)).rowcount
            s.commit()
        return {"status": "deleted", "id": endpoint_id, "deleted": count}

    def update_zotero_endpoint_status(self, endpoint_id: str, *, status: str, latency_ms: int | None = None, error: str | None = None) -> dict[str, Any] | None:
        from .orm import ZoteroMcpEndpointModel
        now = utc_now()
        with self.session() as s:
            ep = s.get(ZoteroMcpEndpointModel, endpoint_id)
            if not ep:
                return None
            ep.last_status = status
            ep.last_latency_ms = latency_ms
            ep.last_error = error
            ep.last_checked_at = now
            ep.updated_at = now
            s.commit()
            return _endpoint_to_dict(ep, include_headers=False)

    def create_literature_link_job(self, document_id: str | None = None, *, status: str = "queued", stage: str = "queued") -> dict[str, Any]:
        from .orm import LiteratureLinkJobModel
        job_id = new_id("litjob")
        now = utc_now()
        with self.session() as s:
            job = LiteratureLinkJobModel(
                id=job_id,
                document_id=document_id,
                status=status,
                stage=stage,
                started_at=now if status == "running" else None,
                created_at=now
            )
            s.add(job)
            s.commit()
            return job.to_dict()

    def update_literature_link_job(self, job_id: str, *, status: str, stage: str, error: str | None = None) -> dict[str, Any] | None:
        from .orm import LiteratureLinkJobModel
        now = utc_now()
        with self.session() as s:
            job = s.get(LiteratureLinkJobModel, job_id)
            if not job:
                return None
            job.status = status
            job.stage = stage
            job.error = error
            if status == "running" and job.started_at is None:
                job.started_at = now
            if status in {"completed", "failed"} and job.finished_at is None:
                job.finished_at = now
            s.commit()
            return job.to_dict()

    def list_literature_link_jobs(self, *, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        from .orm import LiteratureLinkJobModel
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(LiteratureLinkJobModel)
            if status:
                stmt = stmt.where(LiteratureLinkJobModel.status == status)
            stmt = stmt.order_by(LiteratureLinkJobModel.created_at.desc()).limit(limit)
            jobs = s.scalars(stmt).all()
            return [job.to_dict() for job in jobs]

    def list_reaction_steps_for_document(self, document_id: str | None = None, *, limit: int = 100) -> list[ReactionStepModel]:
        with self.session() as s:
            from sqlalchemy import select
            stmt = select(ReactionStepModel).where(ReactionStepModel.deleted_at.is_(None))
            if document_id:
                stmt = stmt.where(ReactionStepModel.source_document_id == document_id).order_by(ReactionStepModel.step_index.asc())
            else:
                stmt = stmt.order_by(ReactionStepModel.created_at.desc())
            stmt = stmt.limit(limit)
            return list(s.scalars(stmt).all())

    def upsert_literature_link(self, data: dict[str, Any]) -> dict[str, Any]:
        from .orm import ZoteroLiteratureLinkModel
        from sqlalchemy import select
        now = utc_now()
        link_id = str(data.get("id") or "") or new_id("litlink")
        
        rxn_step_id = data["reaction_step_id"]
        group = data.get("endpoint_group")
        item_key = data["zotero_item_key"]
        
        with self.session() as s:
            stmt = select(ZoteroLiteratureLinkModel).where(
                ZoteroLiteratureLinkModel.reaction_step_id == rxn_step_id,
                ZoteroLiteratureLinkModel.endpoint_group == group,
                ZoteroLiteratureLinkModel.zotero_item_key == item_key
            )
            link = s.scalars(stmt).first()
            
            new_status = data.get("status") or "candidate"
            new_confidence = float(data.get("confidence") or 0.0)
            
            if link:
                link.endpoint_id = data.get("endpoint_id")
                link.endpoint_alias = data.get("endpoint_alias")
                link.doi = data.get("doi")
                link.title = data.get("title")
                link.authors = data.get("authors") or []
                link.year = data.get("year")
                link.abstract = data.get("abstract")
                
                if link.status != "confirmed":
                    link.status = new_status
                
                link.confidence = max(link.confidence, new_confidence)
                link.match_signals = data.get("match_signals") or {}
                link.method_excerpt = data.get("method_excerpt")
                link.si_excerpt = data.get("si_excerpt")
                link.extracted_fields = data.get("extracted_fields") or {}
                link.field_diff = data.get("field_diff") or {}
                link.updated_at = now
            else:
                link = ZoteroLiteratureLinkModel(
                    id=link_id,
                    reaction_step_id=rxn_step_id,
                    source_document_id=data["source_document_id"],
                    endpoint_id=data.get("endpoint_id"),
                    endpoint_alias=data.get("endpoint_alias"),
                    endpoint_group=group,
                    zotero_item_key=item_key,
                    zotero_attachment_key=data.get("zotero_attachment_key"),
                    doi=data.get("doi"),
                    title=data.get("title"),
                    authors=data.get("authors") or [],
                    year=data.get("year"),
                    abstract=data.get("abstract"),
                    source_kind=data.get("source_kind") or "zotero",
                    status=new_status,
                    confidence=new_confidence,
                    match_signals=data.get("match_signals") or {},
                    method_excerpt=data.get("method_excerpt"),
                    si_excerpt=data.get("si_excerpt"),
                    extracted_fields=data.get("extracted_fields") or {},
                    field_diff=data.get("field_diff") or {},
                    user_note=data.get("user_note"),
                    confirmed_by=data.get("confirmed_by"),
                    confirmed_at=data.get("confirmed_at"),
                    rejected_reason=data.get("rejected_reason"),
                    created_at=now,
                    updated_at=now
                )
                s.add(link)
            s.commit()
            return link.to_dict()

    def list_literature_links(self, *, status: str = "", reaction_step_id: str = "", document_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        from .orm import ZoteroLiteratureLinkModel
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(ZoteroLiteratureLinkModel)
            if status:
                stmt = stmt.where(ZoteroLiteratureLinkModel.status == status)
            if reaction_step_id:
                stmt = stmt.where(ZoteroLiteratureLinkModel.reaction_step_id == reaction_step_id)
            if document_id:
                stmt = stmt.where(ZoteroLiteratureLinkModel.source_document_id == document_id)
            stmt = stmt.order_by(ZoteroLiteratureLinkModel.confidence.desc(), ZoteroLiteratureLinkModel.updated_at.desc()).limit(limit)
            links = s.scalars(stmt).all()
            return [link.to_dict() for link in links]

    def update_literature_link_status(self, link_id: str, *, status: str, confirmed_by: str | None = None, reason: str | None = None) -> dict[str, Any]:
        from .orm import ZoteroLiteratureLinkModel
        now = utc_now()
        with self.session() as s:
            link = s.get(ZoteroLiteratureLinkModel, link_id)
            if not link:
                raise KeyError(f"Literature link not found: {link_id}")
            link.status = status
            if confirmed_by is not None:
                link.confirmed_by = confirmed_by
            if status == "confirmed":
                link.confirmed_at = now
            link.rejected_reason = reason
            link.updated_at = now
            s.commit()
            return link.to_dict()

    def record_zotero_writeback(self, *, literature_link_id: str, endpoint_id: str | None, zotero_item_key: str, operation: str, payload: dict[str, Any], status: str, error: str | None = None) -> dict[str, Any]:
        from .orm import ZoteroWritebackLogModel
        log_id = new_id("zwb")
        created_at = utc_now()
        with self.session() as s:
            log = ZoteroWritebackLogModel(
                id=log_id,
                literature_link_id=literature_link_id,
                endpoint_id=endpoint_id,
                zotero_item_key=zotero_item_key,
                operation=operation,
                payload=payload,
                status=status,
                error=error,
                created_at=created_at
            )
            s.add(log)
            s.commit()
        return {"id": log_id, "literature_link_id": literature_link_id, "endpoint_id": endpoint_id, "zotero_item_key": zotero_item_key, "operation": operation, "payload": payload, "status": status, "error": error, "created_at": created_at}

    def record_evaluation_metrics(self, gold_set_path: str, metrics: dict[str, Any]) -> dict[str, Any]:
        from .orm import EvaluationMetricModel
        metric_id = new_id("metric")
        created_at = utc_now()
        with self.session() as s:
            metric = EvaluationMetricModel(
                id=metric_id,
                gold_set_path=gold_set_path,
                metrics=metrics,
                created_at=created_at
            )
            s.add(metric)
            s.commit()
        return {"id": metric_id, "gold_set_path": gold_set_path, "metrics": metrics, "created_at": created_at}

    def latest_evaluation_metrics(self) -> dict[str, Any] | None:
        from .orm import EvaluationMetricModel
        from sqlalchemy import select
        with self.session() as s:
            stmt = select(EvaluationMetricModel).order_by(EvaluationMetricModel.created_at.desc()).limit(1)
            row = s.scalars(stmt).first()
            if not row:
                return None
            return row.to_dict()

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
    ) -> CompoundModel:
        from .orm import CompoundModel, CompoundAliasModel
        from sqlalchemy import select, func
        now = utc_now()
        
        with self.session() as s:
            comp = None
            if cas:
                comp = s.scalars(select(CompoundModel).where(CompoundModel.cas == cas)).first()
            if not comp and inchikey:
                comp = s.scalars(select(CompoundModel).where(CompoundModel.inchikey == inchikey)).first()
            if not comp:
                comp_stmt = select(CompoundModel).join(
                    CompoundAliasModel,
                    CompoundAliasModel.compound_id == CompoundModel.id
                ).where(
                    func.lower(CompoundAliasModel.alias) == primary_name.lower()
                ).limit(1)
                comp = s.scalars(comp_stmt).first()
                
            if comp:
                if primary_name is not None:
                    comp.primary_name = primary_name
                if cas is not None:
                    comp.cas = cas
                if smiles is not None:
                    comp.smiles = smiles
                if canonical_smiles is not None:
                    comp.canonical_smiles = canonical_smiles
                if inchikey is not None:
                    comp.inchikey = inchikey
                if fingerprint is not None:
                    comp.fingerprint = fingerprint
                comp.confidence = max(comp.confidence, confidence)
                comp.updated_at = now
            else:
                comp_id = new_id("cmpd")
                comp = CompoundModel(
                    id=comp_id,
                    primary_name=primary_name,
                    cas=cas,
                    smiles=smiles,
                    canonical_smiles=canonical_smiles,
                    inchikey=inchikey,
                    fingerprint=fingerprint,
                    source=source,
                    confidence=confidence,
                    created_at=now,
                    updated_at=now
                )
                s.add(comp)
                
            s.flush()
            
            alias_list = aliases or [(primary_name, "name")]
            for alias_name, alias_type in alias_list:
                exist = s.scalar(select(1).select_from(CompoundAliasModel).where(
                    CompoundAliasModel.compound_id == comp.id,
                    CompoundAliasModel.alias == alias_name,
                    CompoundAliasModel.alias_type == alias_type
                ).limit(1))
                if not exist:
                    new_alias = CompoundAliasModel(
                        id=new_id("alias"),
                        compound_id=comp.id,
                        alias=alias_name,
                        alias_type=alias_type
                    )
                    s.add(new_alias)
                    
            s.commit()
            return comp

    def link_compound_to_reaction(self, reaction_step_id: str, compound_id: str, *, role: str, confidence: float, source: str) -> None:
        from .orm import ReactionCompoundRoleModel
        with self.session() as s:
            role_item = ReactionCompoundRoleModel(
                id=new_id("role"),
                reaction_step_id=reaction_step_id,
                compound_id=compound_id,
                role=role,
                confidence=confidence,
                source=source
            )
            s.add(role_item)
            s.commit()

    def search_compounds(self, query: str = "", limit: int = 20) -> list[CompoundModel]:
        from .orm import CompoundModel, CompoundAliasModel
        from sqlalchemy import select, or_, func
        with self.session() as s:
            if query:
                like = f"%{query.lower()}%"
                stmt = select(CompoundModel).outerjoin(
                    CompoundAliasModel,
                    CompoundAliasModel.compound_id == CompoundModel.id
                ).where(
                    or_(
                        func.lower(CompoundModel.primary_name).like(like),
                        func.lower(func.coalesce(CompoundModel.cas, "")).like(like),
                        func.lower(func.coalesce(CompoundModel.smiles, "")).like(like),
                        func.lower(func.coalesce(CompoundModel.inchikey, "")).like(like),
                        func.lower(func.coalesce(CompoundAliasModel.alias, "")).like(like)
                    )
                ).distinct().order_by(CompoundModel.updated_at.desc()).limit(limit)
            else:
                stmt = select(CompoundModel).order_by(CompoundModel.updated_at.desc()).limit(limit)
            return list(s.scalars(stmt).all())

    def get_compound(self, compound_id: str) -> dict[str, Any] | None:
        from .orm import CompoundModel, CompoundAliasModel, ReactionCompoundRoleModel
        from sqlalchemy import select
        with self.session() as s:
            comp = s.get(CompoundModel, compound_id)
            if not comp:
                return None
            
            alias_stmt = select(CompoundAliasModel.alias, CompoundAliasModel.alias_type).where(
                CompoundAliasModel.compound_id == compound_id
            ).order_by(CompoundAliasModel.alias)
            aliases = s.execute(alias_stmt).all()
            
            reactions_stmt = select(
                ReactionCompoundRoleModel.reaction_step_id,
                ReactionCompoundRoleModel.role,
                ReactionCompoundRoleModel.confidence,
                ReactionCompoundRoleModel.source
            ).where(
                ReactionCompoundRoleModel.compound_id == compound_id
            )
            reactions = s.execute(reactions_stmt).all()
            
            data = comp.to_dict()
            data["aliases"] = [{"alias": a, "alias_type": t} for a, t in aliases]
            data["reaction_roles"] = [{
                "reaction_step_id": step_id,
                "role": role,
                "confidence": conf,
                "source": src
            } for step_id, role, conf, src in reactions]
            return data

    def merge_compounds(self, source_compound_id: str, target_compound_id: str) -> dict[str, Any]:
        from .orm import CompoundAliasModel, ReactionCompoundRoleModel, CompoundModel
        from sqlalchemy import update, delete
        with self.session() as s:
            s.execute(update(CompoundAliasModel).where(CompoundAliasModel.compound_id == source_compound_id).values(compound_id=target_compound_id))
            s.execute(update(ReactionCompoundRoleModel).where(ReactionCompoundRoleModel.compound_id == source_compound_id).values(compound_id=target_compound_id))
            s.execute(delete(CompoundModel).where(CompoundModel.id == source_compound_id))
            s.commit()
            
        target = self.get_compound(target_compound_id)
        if not target:
            raise KeyError(f"Target compound not found: {target_compound_id}")
        return target

    def _fts_query(self, query: str) -> str:
        terms = [term.strip().replace('"', "") for term in query.split() if term.strip()]
        return " OR ".join(f'"{term}"' for term in terms) if terms else '""'

    def create_reaction_source_link(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.session() as session:
            model = ReactionSourceLinkModel(
                id=data.get("id", new_id("rsl")),
                cas_reaction_number=data.get("cas_reaction_number"),
                source_mode=data["source_mode"],
                rdf_reaction_id=data.get("rdf_reaction_id"),
                rdf_document_id=data.get("rdf_document_id"),
                pdf_document_id=data.get("pdf_document_id"),
                primary_pdf_page=data.get("primary_pdf_page"),
                pdf_pages_json=json.dumps(data.get("pdf_pages_json") if data.get("pdf_pages_json") is not None else []),
                link_confidence=data.get("link_confidence", 0.0),
                link_method=data.get("link_method", "manual"),
                needs_review=data.get("needs_review", 0),
                conflict_flags_json=json.dumps(data.get("conflict_flags_json") if data.get("conflict_flags_json") is not None else {}),
                created_at=utc_now(),
                updated_at=utc_now()
            )
            session.add(model)
            session.commit()
            return model.to_dict()

    def update_reaction_source_link(self, link_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.session() as session:
            model = session.query(ReactionSourceLinkModel).filter_by(id=link_id, deleted_at=None).first()
            if not model:
                return None
            for key, value in data.items():
                if hasattr(model, key):
                    if key in ("pdf_pages_json", "conflict_flags_json"):
                        if value is None:
                            val_str = "[]" if key == "pdf_pages_json" else "{}"
                            setattr(model, key, val_str)
                        else:
                            setattr(model, key, json.dumps(value) if not isinstance(value, str) else value)
                    else:
                        setattr(model, key, value)
            model.updated_at = utc_now()
            session.commit()
            return model.to_dict()

    def get_reaction_source_link(self, link_id: str) -> dict[str, Any] | None:
        with self.session() as session:
            model = session.query(ReactionSourceLinkModel).filter_by(id=link_id, deleted_at=None).first()
            return model.to_dict() if model else None

    def get_reaction_source_link_by_rdf(self, rdf_reaction_id: str) -> dict[str, Any] | None:
        with self.session() as session:
            model = session.query(ReactionSourceLinkModel).filter_by(rdf_reaction_id=rdf_reaction_id, deleted_at=None).first()
            return model.to_dict() if model else None

    def list_reaction_source_links(
        self,
        document_id: str = "",
        source_mode: str = "",
        needs_review: bool | None = None,
        cas_reaction_number: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self.session() as session:
            query = session.query(ReactionSourceLinkModel).filter_by(deleted_at=None)
            if document_id:
                from sqlalchemy import or_
                query = query.filter(or_(
                    ReactionSourceLinkModel.pdf_document_id == document_id,
                    ReactionSourceLinkModel.rdf_document_id == document_id
                ))
            if source_mode:
                modes = [item.strip() for item in source_mode.split(",") if item.strip()]
                if len(modes) > 1:
                    query = query.filter(ReactionSourceLinkModel.source_mode.in_(modes))
                else:
                    query = query.filter(ReactionSourceLinkModel.source_mode == source_mode)
            if needs_review is not None:
                val = 1 if needs_review else 0
                query = query.filter(ReactionSourceLinkModel.needs_review == val)
            if cas_reaction_number:
                query = query.filter(ReactionSourceLinkModel.cas_reaction_number == cas_reaction_number)
            models = query.order_by(ReactionSourceLinkModel.created_at.desc()).limit(limit).offset(offset).all()
            return [m.to_dict() for m in models]

    def delete_reaction_source_link(self, link_id: str) -> None:
        with self.session() as session:
            model = session.query(ReactionSourceLinkModel).filter_by(id=link_id, deleted_at=None).first()
            if model:
                model.deleted_at = utc_now()
                session.commit()

    def search_pdf_only_reaction_links(
        self,
        document_id: str = "",
        limit: int = 20,
    ) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
        # Returns list of (link_dict, list of evidence_dicts)
        with self.session() as session:
            query = session.query(ReactionSourceLinkModel).filter_by(deleted_at=None).filter(
                ReactionSourceLinkModel.source_mode.in_(["pdf_only", "pdf_only_low_confidence"])
            )
            if document_id:
                query = query.filter(ReactionSourceLinkModel.pdf_document_id == document_id)
            
            models = query.order_by(ReactionSourceLinkModel.created_at.desc()).limit(limit).all()
            
            results = []
            for m in models:
                # fetch evidence (PdfReactionEvidenceModel has no deleted_at)
                evidences = session.query(PdfReactionEvidenceModel).filter_by(
                    reaction_source_link_id=m.id
                ).all()
                results.append((m.to_dict(), [e.to_dict() for e in evidences]))
            return results

    def reassign_evidence_link(self, old_link_id: str, new_link_id: str) -> int:
        """Re-point all PdfReactionEvidence rows from old_link_id to new_link_id."""
        with self.session() as session:
            count = session.query(PdfReactionEvidenceModel).filter_by(
                reaction_source_link_id=old_link_id
            ).update({"reaction_source_link_id": new_link_id})
            session.commit()
            return count

    def create_pdf_reaction_evidence(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.session() as session:
            model = PdfReactionEvidenceModel(
                id=data.get("id", new_id("pre")),
                source_document_id=data["source_document_id"],
                reaction_source_link_id=data.get("reaction_source_link_id"),
                cas_reaction_number=data.get("cas_reaction_number"),
                page_number=data["page_number"],
                is_primary=data.get("is_primary", 0),
                page_text=data["page_text"],
                procedure_text=data.get("procedure_text"),
                products_text=data.get("products_text"),
                reactants_text=data.get("reactants_text"),
                conditions_text=data.get("conditions_text"),
                yield_text=data.get("yield_text"),
                reference_text=data.get("reference_text"),
                doi=data.get("doi"),
                rendered_page_image_path=data.get("rendered_page_image_path"),
                block_start_hint=data.get("block_start_hint"),
                block_end_hint=data.get("block_end_hint"),
                match_confidence=data.get("match_confidence", 0.0),
                extraction_method=data.get("extraction_method", "manual"),
                needs_review=data.get("needs_review", 0),
                created_at=utc_now(),
                updated_at=utc_now()
            )
            session.add(model)
            session.commit()
            return model.to_dict()

    def update_pdf_reaction_evidence(self, evidence_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self.session() as session:
            model = session.get(PdfReactionEvidenceModel, evidence_id)
            if not model:
                raise KeyError(f"PdfReactionEvidenceModel {evidence_id} not found")
            for k, v in data.items():
                if hasattr(model, k):
                    setattr(model, k, v)
            model.updated_at = utc_now()
            session.commit()
            return model.to_dict()

    def list_pdf_reaction_evidence(self, document_id: str = "", cas_reaction_number: str = "", reaction_source_link_id: str = "", limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self.session() as session:
            query = session.query(PdfReactionEvidenceModel)
            if document_id:
                query = query.filter(PdfReactionEvidenceModel.source_document_id == document_id)
            if cas_reaction_number:
                query = query.filter(PdfReactionEvidenceModel.cas_reaction_number == cas_reaction_number)
            if reaction_source_link_id:
                query = query.filter(PdfReactionEvidenceModel.reaction_source_link_id == reaction_source_link_id)
            models = query.order_by(PdfReactionEvidenceModel.page_number.asc()).limit(limit).offset(offset).all()
            return [m.to_dict() for m in models]

    def create_structure_evidence_candidate(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.session() as session:
            model = StructureEvidenceCandidateModel(
                id=data.get("id", new_id("sec")),
                pdf_evidence_id=data["pdf_evidence_id"],
                source_document_id=data["source_document_id"],
                page_number=data["page_number"],
                image_path=data.get("image_path"),
                candidate_smiles=data.get("candidate_smiles"),
                candidate_inchikey=data.get("candidate_inchikey"),
                candidate_formula=data.get("candidate_formula"),
                role_hint=data.get("role_hint"),
                model_name=data.get("model_name"),
                confidence=data.get("confidence", 0.0),
                validation_status=data.get("validation_status", "candidate"),
                validation_signals_json=json.dumps(data.get("validation_signals_json", {})),
                created_at=utc_now(),
                updated_at=utc_now()
            )
            session.add(model)
            session.commit()
            return model.to_dict()

    def update_structure_evidence_candidate(self, candidate_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        with self.session() as session:
            model = session.query(StructureEvidenceCandidateModel).filter_by(id=candidate_id).first()
            if not model:
                return None
            for key, value in data.items():
                if hasattr(model, key):
                    if key == "validation_signals_json":
                        setattr(model, key, json.dumps(value) if not isinstance(value, str) else value)
                    else:
                        setattr(model, key, value)
            model.updated_at = utc_now()
            session.commit()
            return model.to_dict()


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


def batch_match_score(document: SourceDocument, row: Any) -> tuple[float, dict[str, Any]]:
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


def _endpoint_to_dict(ep, *, include_headers: bool = False) -> dict[str, Any]:
    d = ep.to_dict()
    if not include_headers:
        d["headers"] = {key: "****" for key in d["headers"]}
    return d


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
