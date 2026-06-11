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
/data/config.yaml base app config
/data/webui-config.yaml Web UI hot config
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
zotero_mcp_endpoint
literature_link_job
zotero_literature_link
zotero_writeback_log
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
ZoteroMcpClient          Streamable HTTP MCP tools/call
```

Adapters never process whole documents through the LLM. The LLM sees only candidate reaction blocks and must return strict JSON.

## Web UI Hot Config

The base config file and Web UI hot config file are separate. Environment variables and `SCIFINDER_ROUTE_CONFIG` define startup-safe defaults. `SCIFINDER_ROUTE_WEBUI_CONFIG` overlays runtime-editable settings such as Zotero MCP endpoint groups and linking toggles.

Malformed Web UI config is ignored during startup/reload so the container can still start from the base config. Web UI writes never modify Docker-owned settings such as published ports, volume mounts, or container networks.

## Zotero Literature Linking

SciFinder imports can enqueue independent literature-linking jobs after parsing. These jobs query configured Zotero MCP endpoint groups and persist reaction-step-level literature links. The service stores metadata, abstracts, short method/SI excerpts, extracted fields, field differences, candidate/confirmation status, and writeback audit entries. It does not cache complete Zotero full text.

Multiple endpoints can share a `group_name` when they are alternate routes to the same Zotero MCP server. Different groups represent different Zotero libraries and are queried as separate candidate sources.

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
