# Architecture

## Runtime Shape

The service is a single Python package exposing:

```text
scifinder-route-mcp     MCP server + Admin Web UI
scifinder-route-sidecar client-side polling uploader
```

NAS deployment runs one container with:

```text
/inbox          read-only source exports
/data           mutable database, uploads, evidence, config, backups
/data/config.yaml hot app config
```

## Storage

SQLite is the default backend and remains the reliable fallback for NAS.

Core tables:

```text
source_document
parse_job
reaction_step
provenance
doi_verification
reaction_step_fts
vector_index
integration_status
evaluation_metric
compound
compound_alias
reaction_compound_role
```

PostgreSQL is represented by backend configuration and health checks. If `SCIFINDER_ROUTE_BACKEND=postgres` is configured but unavailable, the service reports degraded status and continues on SQLite.

## Queue

The durable queue is stored in `parse_job`.

```text
queued -> running -> completed
queued -> running -> failed
```

Worker threads claim queued jobs from SQLite. On service startup, interrupted `running` jobs are moved back to `queued`.

Redis can be configured as an external queue backend intent, but SQLite remains the active fallback to avoid breaking single-container NAS deployments.

## Parsing Pipeline

1. Register document from read-only inbox, upload staging, or sidecar HTTP upload.
2. Create `source_document` and durable `parse_job`.
3. Parse through external document parser if configured.
4. Fall back to built-in PDF/HTML/MHTML/text parser unless fallback is disabled.
5. For image-only/low-text PDFs, call OCR endpoint if configured.
6. Detect reaction-like candidate blocks.
7. Extract rule-based structured fields.
8. Optionally call OpenAI-compatible LLM JSON adapter on candidate blocks only.
9. Persist reaction steps and provenance.
10. Index CAS/SMILES/InChIKey mentions into the compound registry.

## Integration Adapters

All integration adapters are optional and fail closed/degraded:

```text
EmbeddingAdapter           POST /embeddings
LLMStructuringAdapter      POST /chat/completions
OCRAdapter                 POST /ocr
ExternalParserAdapter      POST /parse
StructureRecognitionAdapter POST /recognize
```

Adapters never process whole documents through the LLM. The LLM sees only candidate reaction blocks and must return strict JSON.

## Vector Index

SQLite fallback stores embeddings as JSON in `vector_index` and uses cosine similarity in Python. This gives a runnable semantic search path without pgvector. Postgres/pgvector can be introduced later behind the same service methods.

## Compound Registry

Text identifiers are normalized into:

```text
compound
compound_alias
reaction_compound_role
```

RDKit is optional. Without RDKit, registry still stores text aliases and hash fingerprints. With RDKit installed, canonical SMILES, InChIKey, and Morgan fingerprints are filled.

## Authorization

Legacy `SCIFINDER_ROUTE_TOKEN` maps to admin. Multi-user config supports `viewer`, `operator`, and `admin`. MCP tools and Admin API actions enforce role checks.
