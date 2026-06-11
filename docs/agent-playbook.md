# Agent Playbook

Use this playbook in Codex, Cherry Studio, Antigravity, Kilo, and other AI clients when working with `scifinder-route-mcp`.

## Client Setup

Prefer the MCP Streamable HTTP endpoint:

```text
http://<nas-host>:8000/mcp
```

Use `/sse` only for legacy clients that do not support Streamable HTTP. The Admin Web UI on port `8001` is a separate operational interface and should not be described as an MCP transport.

## Importing Files

When the user provides a SciFinder file, ask for confirmation before importing it. Prefer `upload_document_content` for files that exist only on the client machine or in chat attachments. This tool requires an operator/admin token and validates content before writing to the server upload directory.

Supported formats are PDF, RTF, MDL RDfile/RDF, HTML/MHTML, Markdown, and plain text. ODF/ODT/ODS/ODP are not supported.

If MCP content upload is unavailable, use one of these fallback paths: Admin HTTP upload, sidecar watcher, or ask the user to place the file in the server inbox. Do not pass a local client path to `register_document` unless the server can actually see that path.

## Evidence Interpretation

RDF/RDfile is the best structured source for reaction records, molecule CTAB blocks, CAS Reaction Number, CAS RN fields, yield, reagents, catalysts, solvents, and references. It may not contain the full experimental procedure.

PDF, RTF, and HTML are readable or visual provenance. They may contain chemical structures, formulas, reaction schemes, and page-layout context that are not visible in extracted text. If a result has visual evidence or is RDF-only, state the uncertainty and cite linked provenance when available.

## Export Batches

Multiple SciFinder files should be linked into an export batch only when there is explainable evidence. High-confidence evidence can include same upload session, similar filenames, export timestamp, matching SciFinder title, and CAS Reaction Number overlap. Low-confidence matches should remain candidates and require confirmation.

Use batch tools to inspect and reverse merges:

```text
list_export_batches
get_export_batch
unlink_document_from_batch
```
