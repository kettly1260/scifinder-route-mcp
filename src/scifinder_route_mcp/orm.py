from __future__ import annotations
import json
from typing import Any
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, ForeignKey,
    UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB

class Base(DeclarativeBase):
    pass

class JSONDialectType(TypeDecorator):
    """Platform-independent JSON type.
    Uses PostgreSQL's JSONB, and TEXT (with manual json loads/dumps) on SQLite.
    """
    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

class MetadataDescriptor:
    def __get__(self, instance, owner):
        if instance is None:
            return owner.registry.metadata
        return instance.metadata_

    def __set__(self, instance, value):
        instance.metadata_ = value

class SourceDocumentModel(Base):
    __tablename__ = "source_document"
    id = Column(String, primary_key=True)
    file_path = Column(String, nullable=False)
    file_hash = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    title = Column(String, nullable=True)
    doi = Column(String, nullable=True)
    scifinder_metadata = Column(JSONDialectType, nullable=False, default=dict)
    ingest_status = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    deleted_at = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("file_hash", "file_path", name="uq_source_document_file_hash_path"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "file_hash": self.file_hash,
            "file_type": self.file_type,
            "title": self.title,
            "doi": self.doi,
            "scifinder_metadata": self.scifinder_metadata,
            "ingest_status": self.ingest_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
        }

class ParseJobModel(Base):
    __tablename__ = "parse_job"
    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False)
    status = Column(String, nullable=False)
    stage = Column(String, nullable=False)
    error = Column(String, nullable=True)
    started_at = Column(String, nullable=True)
    finished_at = Column(String, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

class ReactionStepModel(Base):
    __tablename__ = "reaction_step"
    id = Column(String, primary_key=True)
    source_document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False)
    step_index = Column(Integer, nullable=False)
    reaction_name = Column(String, nullable=True)
    substrate_text = Column(String, nullable=True)
    product_text = Column(String, nullable=True)
    reagent_text = Column(String, nullable=True)
    catalyst_text = Column(String, nullable=True)
    solvent_text = Column(String, nullable=True)
    temperature = Column(String, nullable=True)
    time = Column(String, nullable=True)
    atmosphere = Column(String, nullable=True)
    yield_text = Column(String, nullable=True)
    scale = Column(String, nullable=True)
    workup = Column(String, nullable=True)
    purification = Column(String, nullable=True)
    original_text = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    verification_status = Column(String, nullable=False)
    needs_ocr = Column(Boolean, nullable=False, default=False)
    extraction_method = Column(String, nullable=False, default="rules")
    schema_version = Column(String, nullable=False, default="reaction_step.v1")
    llm_confidence = Column(Float, nullable=True)
    metadata_ = Column("metadata", JSONDialectType, nullable=False, default=dict)
    metadata = MetadataDescriptor()
    created_at = Column(String, nullable=False)
    deleted_at = Column(String, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_document_id": self.source_document_id,
            "step_index": self.step_index,
            "reaction_name": self.reaction_name,
            "substrate_text": self.substrate_text,
            "product_text": self.product_text,
            "reagent_text": self.reagent_text,
            "catalyst_text": self.catalyst_text,
            "solvent_text": self.solvent_text,
            "temperature": self.temperature,
            "time": self.time,
            "atmosphere": self.atmosphere,
            "yield_text": self.yield_text,
            "scale": self.scale,
            "workup": self.workup,
            "purification": self.purification,
            "original_text": self.original_text,
            "confidence": self.confidence,
            "verification_status": self.verification_status,
            "needs_ocr": bool(self.needs_ocr),
            "extraction_method": self.extraction_method,
            "schema_version": self.schema_version,
            "llm_confidence": self.llm_confidence,
            "metadata": self.metadata_,
            "created_at": self.created_at,
            "deleted_at": self.deleted_at,
        }

class ProvenanceModel(Base):
    __tablename__ = "provenance"
    id = Column(String, primary_key=True)
    reaction_step_id = Column(String, ForeignKey("reaction_step.id", ondelete="CASCADE"), nullable=False)
    source_document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False)
    page_number = Column(Integer, nullable=True)
    text_span = Column(String, nullable=False)
    image_region_path = Column(String, nullable=True)
    ocr_output = Column(String, nullable=True)
    parser_name = Column(String, nullable=False)
    parser_version = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reaction_step_id": self.reaction_step_id,
            "source_document_id": self.source_document_id,
            "page_number": self.page_number,
            "text_span": self.text_span,
            "image_region_path": self.image_region_path,
            "ocr_output": self.ocr_output,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "confidence": self.confidence,
        }

class ParsedChunkModel(Base):
    __tablename__ = "parsed_chunk"
    id = Column(String, primary_key=True)
    source_document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    page_number = Column(Integer, nullable=True)
    text = Column(String, nullable=False)
    parser_name = Column(String, nullable=False)
    parser_version = Column(String, nullable=False)
    created_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("source_document_id", "chunk_index", name="uq_parsed_chunk_doc_index"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_document_id": self.source_document_id,
            "chunk_index": self.chunk_index,
            "page_number": self.page_number,
            "text": self.text,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "created_at": self.created_at,
        }

class ExportBatchModel(Base):
    __tablename__ = "export_batch"
    id = Column(String, primary_key=True)
    title = Column(String, nullable=True)
    export_timestamp = Column(String, nullable=True)
    status = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    merge_method = Column(String, nullable=False)
    explanation = Column(JSONDialectType, nullable=False, default=dict)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "export_timestamp": self.export_timestamp,
            "status": self.status,
            "confidence": self.confidence,
            "merge_method": self.merge_method,
            "explanation": self.explanation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class ExportBatchDocumentModel(Base):
    __tablename__ = "export_batch_document"
    id = Column(String, primary_key=True)
    batch_id = Column(String, ForeignKey("export_batch.id", ondelete="CASCADE"), nullable=False)
    source_document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    explanation = Column(JSONDialectType, nullable=False, default=dict)
    created_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("batch_id", "source_document_id", name="uq_batch_doc"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "source_document_id": self.source_document_id,
            "role": self.role,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "created_at": self.created_at,
        }

class ExportBatchCandidateModel(Base):
    __tablename__ = "export_batch_candidate"
    id = Column(String, primary_key=True)
    source_document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False)
    candidate_batch_id = Column(String, ForeignKey("export_batch.id", ondelete="CASCADE"), nullable=False)
    confidence = Column(Float, nullable=False)
    explanation = Column(JSONDialectType, nullable=False, default=dict)
    status = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_document_id": self.source_document_id,
            "candidate_batch_id": self.candidate_batch_id,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class AiProviderModel(Base):
    __tablename__ = "ai_provider"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    format = Column(String, nullable=False)
    endpoint = Column(String, nullable=True)
    api_key = Column(String, nullable=True)
    models_endpoint = Column(String, nullable=True)
    available_models = Column(JSONDialectType, nullable=False, default=list)
    enabled_models = Column(JSONDialectType, nullable=False, default=list)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "format": self.format,
            "endpoint": self.endpoint,
            "api_key": self.api_key,
            "models_endpoint": self.models_endpoint,
            "available_models": tuple(self.available_models) if isinstance(self.available_models, list) else self.available_models,
            "enabled_models": tuple(self.enabled_models) if isinstance(self.enabled_models, list) else self.enabled_models,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class DoiVerificationModel(Base):
    __tablename__ = "doi_verification"
    id = Column(String, primary_key=True)
    reaction_step_id = Column(String, ForeignKey("reaction_step.id", ondelete="CASCADE"), nullable=False)
    doi = Column(String, nullable=False)
    paper_title = Column(String, nullable=True)
    verified_fields = Column(JSONDialectType, nullable=False)
    original_paper_excerpt = Column(String, nullable=True)
    verification_confidence = Column(Float, nullable=False)
    verifier_agent = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reaction_step_id": self.reaction_step_id,
            "doi": self.doi,
            "paper_title": self.paper_title,
            "verified_fields": self.verified_fields,
            "original_paper_excerpt": self.original_paper_excerpt,
            "verification_confidence": self.verification_confidence,
            "verifier_agent": self.verifier_agent,
            "created_at": self.created_at,
        }

class VectorIndexModel(Base):
    __tablename__ = "vector_index"
    reaction_step_id = Column(String, ForeignKey("reaction_step.id", ondelete="CASCADE"), primary_key=True)
    model = Column(String, nullable=False)
    embedding = Column(JSONDialectType, nullable=False)
    dimensions = Column(Integer, nullable=False)
    updated_at = Column(String, nullable=False)
    error = Column(String, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reaction_step_id": self.reaction_step_id,
            "model": self.model,
            "embedding": self.embedding,
            "dimensions": self.dimensions,
            "updated_at": self.updated_at,
            "error": self.error,
        }

class IntegrationStatusModel(Base):
    __tablename__ = "integration_status"
    kind = Column(String, primary_key=True)
    configured = Column(Integer, nullable=False)
    status = Column(String, nullable=False)
    detail = Column(String, nullable=True)
    checked_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "configured": bool(self.configured),
            "status": self.status,
            "detail": self.detail,
            "checked_at": self.checked_at,
        }

class ZoteroMcpEndpointModel(Base):
    __tablename__ = "zotero_mcp_endpoint"
    id = Column(String, primary_key=True)
    alias = Column(String, nullable=False)
    group_name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    enabled = Column(Integer, nullable=False, default=1)
    priority = Column(Integer, nullable=False, default=100)
    timeout_seconds = Column(Float, nullable=False, default=10.0)
    headers = Column(JSONDialectType, nullable=False, default=dict)
    write_note_enabled = Column(Integer, nullable=False, default=0)
    last_status = Column(String, nullable=True)
    last_latency_ms = Column(Integer, nullable=True)
    last_error = Column(String, nullable=True)
    last_checked_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("alias", name="uq_zotero_mcp_endpoint_alias"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "alias": self.alias,
            "group_name": self.group_name,
            "url": self.url,
            "enabled": bool(self.enabled),
            "priority": self.priority,
            "timeout_seconds": self.timeout_seconds,
            "headers": self.headers,
            "write_note_enabled": bool(self.write_note_enabled),
            "last_status": self.last_status,
            "last_latency_ms": self.last_latency_ms,
            "last_error": self.last_error,
            "last_checked_at": self.last_checked_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class LiteratureLinkJobModel(Base):
    __tablename__ = "literature_link_job"
    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=True)
    status = Column(String, nullable=False)
    stage = Column(String, nullable=False)
    error = Column(String, nullable=True)
    started_at = Column(String, nullable=True)
    finished_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "created_at": self.created_at,
        }

class ZoteroLiteratureLinkModel(Base):
    __tablename__ = "zotero_literature_link"
    id = Column(String, primary_key=True)
    reaction_step_id = Column(String, ForeignKey("reaction_step.id", ondelete="CASCADE"), nullable=False)
    source_document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False)
    endpoint_id = Column(String, nullable=True)
    endpoint_alias = Column(String, nullable=True)
    endpoint_group = Column(String, nullable=True)
    zotero_item_key = Column(String, nullable=False)
    zotero_attachment_key = Column(String, nullable=True)
    doi = Column(String, nullable=True)
    title = Column(String, nullable=True)
    authors = Column(JSONDialectType, nullable=False, default=list)
    year = Column(String, nullable=True)
    abstract = Column(String, nullable=True)
    source_kind = Column(String, nullable=False, default="zotero")
    status = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    match_signals = Column(JSONDialectType, nullable=False, default=dict)
    method_excerpt = Column(String, nullable=True)
    si_excerpt = Column(String, nullable=True)
    extracted_fields = Column(JSONDialectType, nullable=False, default=dict)
    field_diff = Column(JSONDialectType, nullable=False, default=dict)
    user_note = Column(String, nullable=True)
    confirmed_by = Column(String, nullable=True)
    confirmed_at = Column(String, nullable=True)
    rejected_reason = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("reaction_step_id", "endpoint_group", "zotero_item_key", name="uq_zotero_lit_link"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reaction_step_id": self.reaction_step_id,
            "source_document_id": self.source_document_id,
            "endpoint_id": self.endpoint_id,
            "endpoint_alias": self.endpoint_alias,
            "endpoint_group": self.endpoint_group,
            "zotero_item_key": self.zotero_item_key,
            "zotero_attachment_key": self.zotero_attachment_key,
            "doi": self.doi,
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "abstract": self.abstract,
            "source_kind": self.source_kind,
            "status": self.status,
            "confidence": self.confidence,
            "match_signals": self.match_signals,
            "method_excerpt": self.method_excerpt,
            "si_excerpt": self.si_excerpt,
            "extracted_fields": self.extracted_fields,
            "field_diff": self.field_diff,
            "user_note": self.user_note,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at,
            "rejected_reason": self.rejected_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class ZoteroWritebackLogModel(Base):
    __tablename__ = "zotero_writeback_log"
    id = Column(String, primary_key=True)
    literature_link_id = Column(String, ForeignKey("zotero_literature_link.id", ondelete="CASCADE"), nullable=False)
    endpoint_id = Column(String, nullable=True)
    zotero_item_key = Column(String, nullable=False)
    operation = Column(String, nullable=False)
    payload = Column(JSONDialectType, nullable=False, default=dict)
    status = Column(String, nullable=False)
    error = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "literature_link_id": self.literature_link_id,
            "endpoint_id": self.endpoint_id,
            "zotero_item_key": self.zotero_item_key,
            "operation": self.operation,
            "payload": self.payload,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
        }

class EvaluationMetricModel(Base):
    __tablename__ = "evaluation_metric"
    id = Column(String, primary_key=True)
    gold_set_path = Column(String, nullable=False)
    metrics = Column(JSONDialectType, nullable=False)
    created_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "gold_set_path": self.gold_set_path,
            "metrics": self.metrics,
            "created_at": self.created_at,
        }

class CompoundModel(Base):
    __tablename__ = "compound"
    id = Column(String, primary_key=True)
    primary_name = Column(String, nullable=False)
    cas = Column(String, nullable=True)
    smiles = Column(String, nullable=True)
    canonical_smiles = Column(String, nullable=True)
    inchikey = Column(String, nullable=True)
    fingerprint = Column(String, nullable=True)
    source = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "primary_name": self.primary_name,
            "cas": self.cas,
            "smiles": self.smiles,
            "canonical_smiles": self.canonical_smiles,
            "inchikey": self.inchikey,
            "fingerprint": self.fingerprint,
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class CompoundAliasModel(Base):
    __tablename__ = "compound_alias"
    id = Column(String, primary_key=True)
    compound_id = Column(String, ForeignKey("compound.id", ondelete="CASCADE"), nullable=False)
    alias = Column(String, nullable=False)
    alias_type = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("compound_id", "alias", "alias_type", name="uq_compound_alias"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "compound_id": self.compound_id,
            "alias": self.alias,
            "alias_type": self.alias_type,
        }

class ReactionCompoundRoleModel(Base):
    __tablename__ = "reaction_compound_role"
    id = Column(String, primary_key=True)
    reaction_step_id = Column(String, ForeignKey("reaction_step.id", ondelete="CASCADE"), nullable=False)
    compound_id = Column(String, ForeignKey("compound.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    source = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reaction_step_id": self.reaction_step_id,
            "compound_id": self.compound_id,
            "role": self.role,
            "confidence": self.confidence,
            "source": self.source,
        }

class RdfReactionRecordModel(Base):
    __tablename__ = "rdf_reaction_record"
    id = Column(String, primary_key=True)
    source_document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False)
    record_index = Column(Integer, nullable=False)
    registry = Column(String, nullable=True)
    scheme_id = Column(String, nullable=True)
    step_id = Column(String, nullable=True)
    reactant_count = Column(Integer, nullable=False, default=0)
    product_count = Column(Integer, nullable=False, default=0)
    cas_reaction_number = Column(String, nullable=True)
    yield_text = Column(String, nullable=True)
    reagents = Column(JSONDialectType, nullable=False, default=list)
    catalysts = Column(JSONDialectType, nullable=False, default=list)
    solvents = Column(JSONDialectType, nullable=False, default=list)
    reference = Column(JSONDialectType, nullable=False, default=dict)
    experimental_procedure = Column(String, nullable=True)
    fields = Column(JSONDialectType, nullable=False, default=dict)
    warnings = Column(JSONDialectType, nullable=False, default=list)
    deleted_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("source_document_id", "record_index", name="uq_rdf_reaction_doc_index"),
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_document_id": self.source_document_id,
            "record_index": self.record_index,
            "registry": self.registry,
            "scheme_id": self.scheme_id,
            "step_id": self.step_id,
            "reactant_count": self.reactant_count,
            "product_count": self.product_count,
            "cas_reaction_number": self.cas_reaction_number,
            "yield_text": self.yield_text,
            "reagents": self.reagents,
            "catalysts": self.catalysts,
            "solvents": self.solvents,
            "reference": self.reference,
            "experimental_procedure": self.experimental_procedure,
            "fields": self.fields,
            "warnings": self.warnings,
            "deleted_at": self.deleted_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class RdfStructureModel(Base):
    __tablename__ = "rdf_structure"
    id = Column(String, primary_key=True)
    rdf_reaction_id = Column(String, ForeignKey("rdf_reaction_record.id", ondelete="CASCADE"), nullable=False)
    source_document_id = Column(String, ForeignKey("source_document.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, nullable=False)
    role_index = Column(Integer, nullable=False)
    name = Column(String, nullable=True)
    formula = Column(String, nullable=True)
    cas_rn = Column(String, nullable=True)
    molfile = Column(String, nullable=True)
    molfile_version = Column(String, nullable=True)
    smiles = Column(String, nullable=True)
    inchikey = Column(String, nullable=True)
    fingerprint = Column(String, nullable=True)
    rdkit_status = Column(String, nullable=False, default="not_indexed")
    rdkit_error = Column(String, nullable=True)
    warnings = Column(JSONDialectType, nullable=False, default=list)
    deleted_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rdf_reaction_id": self.rdf_reaction_id,
            "source_document_id": self.source_document_id,
            "role": self.role,
            "role_index": self.role_index,
            "name": self.name,
            "formula": self.formula,
            "cas_rn": self.cas_rn,
            "molfile": self.molfile,
            "molfile_version": self.molfile_version,
            "smiles": self.smiles,
            "inchikey": self.inchikey,
            "fingerprint": self.fingerprint,
            "rdkit_status": self.rdkit_status,
            "rdkit_error": self.rdkit_error,
            "warnings": self.warnings,
            "deleted_at": self.deleted_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class ReactionSourceLinkModel(Base):
    __tablename__ = "reaction_source_link"
    id = Column(String, primary_key=True)
    cas_reaction_number = Column(String, index=True)
    source_mode = Column(String, nullable=False) # rdf_pdf_linked, rdf_only, pdf_only, pdf_only_low_confidence, pdf_only_verified
    rdf_reaction_id = Column(String, index=True, nullable=True)
    rdf_document_id = Column(String, nullable=True)
    pdf_document_id = Column(String, index=True, nullable=True)
    primary_pdf_page = Column(Integer, nullable=True)
    pdf_pages_json = Column(String, nullable=False, default="[]") # JSON list of pages
    link_confidence = Column(Float, nullable=False, default=0.0)
    link_method = Column(String, nullable=False)
    needs_review = Column(Integer, nullable=False, default=0) # SQLite boolean/int
    conflict_flags_json = Column(String, nullable=False, default="{}")
    created_at = Column(String)
    updated_at = Column(String)
    deleted_at = Column(String, nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cas_reaction_number": self.cas_reaction_number,
            "source_mode": self.source_mode,
            "rdf_reaction_id": self.rdf_reaction_id,
            "rdf_document_id": self.rdf_document_id,
            "pdf_document_id": self.pdf_document_id,
            "primary_pdf_page": self.primary_pdf_page,
            "pdf_pages_json": self.pdf_pages_json,
            "link_confidence": self.link_confidence,
            "link_method": self.link_method,
            "needs_review": self.needs_review,
            "conflict_flags_json": self.conflict_flags_json,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
        }

class PdfReactionEvidenceModel(Base):
    __tablename__ = "pdf_reaction_evidence"
    id = Column(String, primary_key=True)
    source_document_id = Column(String, nullable=False)
    reaction_source_link_id = Column(String, index=True, nullable=True)
    cas_reaction_number = Column(String, index=True, nullable=True)
    page_number = Column(Integer, nullable=False)
    is_primary = Column(Integer, nullable=False, default=0)
    page_text = Column(String, nullable=False)
    procedure_text = Column(String, nullable=True)
    products_text = Column(String, nullable=True)
    reactants_text = Column(String, nullable=True)
    conditions_text = Column(String, nullable=True)
    yield_text = Column(String, nullable=True)
    reference_text = Column(String, nullable=True)
    doi = Column(String, nullable=True)
    rendered_page_image_path = Column(String, nullable=True)
    block_start_hint = Column(String, nullable=True)
    block_end_hint = Column(String, nullable=True)
    match_confidence = Column(Float, nullable=False, default=0.0)
    extraction_method = Column(String, nullable=False)
    needs_review = Column(Integer, nullable=False, default=0)
    created_at = Column(String)
    updated_at = Column(String)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_document_id": self.source_document_id,
            "reaction_source_link_id": self.reaction_source_link_id,
            "cas_reaction_number": self.cas_reaction_number,
            "page_number": self.page_number,
            "is_primary": self.is_primary,
            "page_text": self.page_text,
            "procedure_text": self.procedure_text,
            "products_text": self.products_text,
            "reactants_text": self.reactants_text,
            "conditions_text": self.conditions_text,
            "yield_text": self.yield_text,
            "reference_text": self.reference_text,
            "doi": self.doi,
            "rendered_page_image_path": self.rendered_page_image_path,
            "block_start_hint": self.block_start_hint,
            "block_end_hint": self.block_end_hint,
            "match_confidence": self.match_confidence,
            "extraction_method": self.extraction_method,
            "needs_review": self.needs_review,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

class StructureEvidenceCandidateModel(Base):
    __tablename__ = "structure_evidence_candidate"
    id = Column(String, primary_key=True)
    pdf_evidence_id = Column(String, nullable=False)
    source_document_id = Column(String, nullable=False)
    page_number = Column(Integer, nullable=False)
    image_path = Column(String, nullable=True)
    candidate_smiles = Column(String, nullable=True)
    candidate_inchikey = Column(String, nullable=True)
    candidate_formula = Column(String, nullable=True)
    role_hint = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    validation_status = Column(String, nullable=False, default='candidate')
    validation_signals_json = Column(String, nullable=False, default='{}')
    created_at = Column(String)
    updated_at = Column(String)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pdf_evidence_id": self.pdf_evidence_id,
            "source_document_id": self.source_document_id,
            "page_number": self.page_number,
            "image_path": self.image_path,
            "candidate_smiles": self.candidate_smiles,
            "candidate_inchikey": self.candidate_inchikey,
            "candidate_formula": self.candidate_formula,
            "role_hint": self.role_hint,
            "model_name": self.model_name,
            "confidence": self.confidence,
            "validation_status": self.validation_status,
            "validation_signals_json": self.validation_signals_json,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
