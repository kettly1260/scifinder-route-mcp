Project agent instructions for scifinder-route-mcp

This repository implements a NAS/Docker MCP server for indexing and searching SciFinder reaction-step exports. Treat it as a long-running trusted LAN/VPN service with durable SQLite fallback, optional external OCR/LLM/vector/parser/structure-recognition integrations, and a separate Admin Web UI.

Core transport rules:
- Prefer MCP Streamable HTTP at `/mcp` for new clients.
- `/sse` is legacy compatibility only.
- The Admin Web UI is a separate HTTP operational interface, not an MCP transport.

Import rules:
- Ask before importing any user-provided file.
- Prefer authenticated MCP `upload_document_content` for client-local files or chat attachments when available.
- Upload/import mutations require an operator/admin token. Viewer tokens must not upload, register, reparse, rebuild indexes, or mutate server state.
- Supported import formats are `.pdf`, `.rtf`, `.rdf`, `.html`, `.htm`, `.mhtml`, `.mht`, `.md`, `.markdown`, and `.txt`.
- Do not document ODF/ODT/ODS/ODP as supported formats.
- Uploads must pass size limits, extension allowlist, content-type sniffing, format-specific safety checks, and optional ClamAV scanning before writing to `upload_dir`.
- Do not bypass upload validation by registering arbitrary local paths unless the file is already server-visible and intentionally trusted.

SciFinder evidence rules:
- RDF/RDfile is the preferred structured source for CAS Reaction Number, molecule CTAB, CAS RN fields, yield, reagents, catalysts, solvents, and references.
- RDF may not include complete experimental procedures. Link RDF-derived reactions to PDF/RTF/HTML readable or visual provenance when available.
- PDF/RTF/HTML may include chemical structures, formulas, reaction schemes, and page layout evidence. Do not rely only on extracted text for structure-sensitive chemical judgments.
- If a result is RDF-only or lacks linked readable/visual provenance, warn before making final chemical claims.

Batching rules:
- Preserve SciFinder export batch context when multiple files are imported.
- Automatically merge only with explainable high-confidence evidence such as same upload session, close filenames, export timestamp, SciFinder title, and CAS Reaction Number overlap.
- Low-confidence matches should remain candidate batches and require confirmation.
- Batch links must be explainable and reversible; use `unlink_document_from_batch` for incorrect merges.

Implementation map:
- MCP tools and transport: `src/scifinder_route_mcp/server.py`
- Admin Web UI: `src/scifinder_route_mcp/admin.py`
- Service orchestration: `src/scifinder_route_mcp/service.py`
- Parsing: `src/scifinder_route_mcp/parsers.py`
- Extraction rules: `src/scifinder_route_mcp/extractor.py`
- Storage/schema: `src/scifinder_route_mcp/storage.py`
- Config: `src/scifinder_route_mcp/config.py`

Validation:
- Run `pytest` after code changes.
- Do not commit `.env`, `nas-data`, `.data`, uploaded documents, generated evidence, or SQLite databases.
