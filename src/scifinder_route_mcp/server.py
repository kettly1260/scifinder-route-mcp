from __future__ import annotations

import os
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from collections.abc import AsyncIterator
from typing import Any, Callable

from .auth import authenticate_token, role_allows
from .admin import start_admin_server
from .service import RouteService


@dataclass(frozen=True)
class ServerRunConfig:
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = ""
    mcp_path: str = "/mcp"
    sse_path: str = "/sse"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "ServerRunConfig":
        transport = os.getenv("SCIFINDER_ROUTE_TRANSPORT", "stdio").lower()
        mcp_path = os.getenv("SCIFINDER_ROUTE_MCP_PATH", "/mcp")
        sse_path = os.getenv("SCIFINDER_ROUTE_SSE_PATH", "/sse")
        if transport == "sse":
            path = sse_path
        else:
            path = mcp_path
        return cls(
            transport=transport,
            host=os.getenv("SCIFINDER_ROUTE_HOST", "127.0.0.1"),
            port=int(os.getenv("SCIFINDER_ROUTE_PORT", "8000")),
            path=path,
            mcp_path=mcp_path,
            sse_path=sse_path,
            log_level=os.getenv("SCIFINDER_ROUTE_LOG_LEVEL", "INFO"),
        )


class LocalMCP:
    """Tiny decorator-compatible fallback for tests without FastMCP installed."""

    def __init__(self, name: str):
        self.name = name
        self.tools: dict[str, Callable[..., Any]] = {}
        self.resources: dict[str, Callable[..., Any]] = {}
        self.prompts: dict[str, Callable[..., Any]] = {}

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[func.__name__] = func
            return func

        return decorator

    def resource(self, uri: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.resources[uri] = func
            return func

        return decorator

    def prompt(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.prompts[func.__name__] = func
            return func

        return decorator

    def run(self, **_kwargs: Any) -> None:
        raise RuntimeError("FastMCP is not installed. Install project dependencies to run the MCP server.")


def create_mcp(service: RouteService | None = None) -> Any:
    route_service = service

    def get_service() -> RouteService:
        nonlocal route_service
        if route_service is None:
            route_service = RouteService()
        return route_service

    def require_role(token: str | None, role: str = "viewer") -> None:
        service = get_service()
        if not service.config.auth_token and not service.config.users:
            return
        user = authenticate_token(service.config.users, service.config.auth_token, token)
        if not user:
            raise PermissionError("Invalid or missing SCIFINDER_ROUTE_TOKEN")
        if not role_allows(user.role, role):
            raise PermissionError(f"Token role '{user.role}' cannot perform '{role}' operations")

    try:
        from fastmcp import FastMCP  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - fallback is mainly for tests/minimal environments
        mcp: Any = LocalMCP("scifinder-route-mcp")
    else:
        mcp = FastMCP("scifinder-route-mcp")

    @mcp.tool()
    def register_document(file_path: str, reparse: bool = False, token: str | None = None) -> dict[str, Any]:
        """Register and parse a local SciFinder export file already visible to the server."""
        require_role(token, "operator")
        return get_service().register_document(file_path=file_path, reparse=reparse)

    @mcp.tool()
    def upload_document(source_path: str, filename: str | None = None, reparse: bool = False, token: str | None = None) -> dict[str, Any]:
        """Copy a server-visible file into the upload area, then register and parse it."""
        require_role(token, "operator")
        return get_service().upload_document(source_path=source_path, filename=filename, reparse=reparse)

    @mcp.tool()
    def upload_document_content(filename: str, content_base64: str, reparse: bool = False, token: str | None = None) -> dict[str, Any]:
        """Upload base64-encoded SciFinder export content through MCP after safety validation."""
        require_role(token, "operator")
        return get_service().upload_document_content(filename=filename, content_base64=content_base64, reparse=reparse)

    @mcp.tool()
    def scan_inbox(reparse: bool = False, limit: int = 500, token: str | None = None) -> dict[str, Any]:
        """Scan the NAS inbox for supported SciFinder exports and queue/register new files."""
        require_role(token, "operator")
        return get_service().scan_inbox(reparse=reparse, limit=limit)

    @mcp.tool()
    def get_parse_job_status(job_id: str, token: str | None = None) -> dict[str, Any]:
        """Return parse job status, stage, and error details."""
        require_role(token, "viewer")
        return get_service().get_parse_job_status(job_id=job_id)

    @mcp.tool()
    def list_parse_jobs(status: str = "", limit: int = 100, token: str | None = None) -> list[dict[str, Any]]:
        """List recent parse jobs, optionally filtered by status."""
        require_role(token, "viewer")
        return get_service().list_parse_jobs(status=status, limit=limit)

    @mcp.tool()
    def health_check(token: str | None = None) -> dict[str, Any]:
        """Return server health, configured paths, and indexed object counts."""
        require_role(token, "viewer")
        return get_service().health_check()

    @mcp.tool()
    def get_config(include_secrets: bool = False, token: str | None = None) -> dict[str, Any]:
        """Return the effective application config. Secrets are masked unless include_secrets is true."""
        require_role(token, "admin" if include_secrets else "viewer")
        return get_service().get_config(include_secrets=include_secrets)

    @mcp.tool()
    def update_config(updates: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        """Merge hot-reloadable application config updates into config.yaml and reload them."""
        require_role(token, "admin")
        return get_service().update_config(updates=updates)

    @mcp.tool()
    def validate_config(token: str | None = None) -> dict[str, Any]:
        """Validate the current application config and report settings that require container restart."""
        require_role(token, "viewer")
        return get_service().validate_config()

    @mcp.tool()
    def reload_config(token: str | None = None) -> dict[str, Any]:
        """Reload hot application config from config.yaml without restarting the container."""
        require_role(token, "admin")
        return get_service().reload_config()

    @mcp.tool()
    def search_reaction_steps(
        query: str = "",
        reagent: str = "",
        solvent: str = "",
        document_id: str = "",
        min_confidence: float = 0.0,
        limit: int = 10,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search extracted reaction steps by text and structured condition filters."""
        require_role(token, "viewer")
        return get_service().search_reaction_steps(
            query=query,
            reagent=reagent,
            solvent=solvent,
            document_id=document_id,
            min_confidence=min_confidence,
            limit=limit,
        )

    @mcp.tool()
    def get_reaction_step(reaction_step_id: str, token: str | None = None) -> dict[str, Any]:
        """Return one structured reaction step."""
        require_role(token, "viewer")
        return get_service().get_reaction_step(reaction_step_id=reaction_step_id)

    @mcp.tool()
    def get_reaction_provenance(reaction_step_id: str, token: str | None = None) -> list[dict[str, Any]]:
        """Return source text/page/parser provenance for a reaction step."""
        require_role(token, "viewer")
        return get_service().get_reaction_provenance(reaction_step_id=reaction_step_id)

    @mcp.tool()
    def reparse_document(document_id: str, token: str | None = None) -> dict[str, Any]:
        """Clear extracted reactions for a document and parse it again."""
        require_role(token, "operator")
        return get_service().reparse_document(document_id=document_id)

    @mcp.tool()
    def record_doi_verification(
        reaction_step_id: str,
        doi: str,
        verified_fields: dict[str, Any],
        paper_title: str | None = None,
        original_paper_excerpt: str | None = None,
        verification_confidence: float = 0.0,
        verifier_agent: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Record DOI source verification performed by an agent or browser workflow."""
        require_role(token, "operator")
        return get_service().record_doi_verification(
            reaction_step_id=reaction_step_id,
            doi=doi,
            verified_fields=verified_fields,
            paper_title=paper_title,
            original_paper_excerpt=original_paper_excerpt,
            verification_confidence=verification_confidence,
            verifier_agent=verifier_agent,
        )

    @mcp.tool()
    def export_evaluation_set(output_path: str | None = None, limit: int = 500, token: str | None = None) -> dict[str, Any]:
        """Export extracted reaction steps as JSONL for manual labeling and regression checks."""
        require_role(token, "operator")
        return get_service().export_evaluation_set(output_path=output_path, limit=limit)

    @mcp.tool()
    def retry_parse_job(job_id: str, token: str | None = None) -> dict[str, Any]:
        """Retry a failed or completed parse job by moving it back to the durable queue."""
        require_role(token, "operator")
        return get_service().retry_parse_job(job_id)

    @mcp.tool()
    def retry_failed_jobs(limit: int = 100, token: str | None = None) -> dict[str, Any]:
        """Retry recent failed parse jobs."""
        require_role(token, "operator")
        return get_service().retry_failed_jobs(limit=limit)

    @mcp.tool()
    def rebuild_vector_index(limit: int = 10000, token: str | None = None) -> dict[str, Any]:
        """Generate embeddings for reaction steps using the configured embedding endpoint."""
        require_role(token, "operator")
        return get_service().rebuild_vector_index(limit=limit)

    @mcp.tool()
    def get_vector_index_status(token: str | None = None) -> dict[str, Any]:
        """Return vector index coverage, model, and last error."""
        require_role(token, "viewer")
        return get_service().get_vector_index_status()

    @mcp.tool()
    def semantic_search_reaction_steps(query: str, limit: int = 10, token: str | None = None) -> list[dict[str, Any]]:
        """Search reaction steps semantically using the configured embedding endpoint."""
        require_role(token, "viewer")
        return get_service().semantic_search_reaction_steps(query=query, limit=limit)

    @mcp.tool()
    def search_compounds(query: str = "", limit: int = 20, token: str | None = None) -> list[dict[str, Any]]:
        """Search the compound registry by name, CAS, SMILES, or InChIKey."""
        require_role(token, "viewer")
        return get_service().search_compounds(query=query, limit=limit)

    @mcp.tool()
    def get_compound(compound_id: str, token: str | None = None) -> dict[str, Any]:
        """Return compound metadata, aliases, and linked reactions."""
        require_role(token, "viewer")
        return get_service().get_compound(compound_id)

    @mcp.tool()
    def merge_compounds(source_compound_id: str, target_compound_id: str, token: str | None = None) -> dict[str, Any]:
        """Merge source compound aliases/reaction links into a target compound."""
        require_role(token, "operator")
        return get_service().merge_compounds(source_compound_id, target_compound_id)

    @mcp.tool()
    def search_by_smiles(smiles: str, limit: int = 20, token: str | None = None) -> list[dict[str, Any]]:
        """Normalize a SMILES string when RDKit is available, then search compounds."""
        require_role(token, "viewer")
        return get_service().search_by_smiles(smiles, limit=limit)

    @mcp.tool()
    def recognize_structure_image(image_path: str, reaction_step_id: str | None = None, token: str | None = None) -> dict[str, Any]:
        """Send an image region to a configured MolScribe/DECIMER/OSRA-style endpoint and register candidate SMILES."""
        require_role(token, "operator")
        return get_service().recognize_structure_image(image_path=image_path, reaction_step_id=reaction_step_id)

    @mcp.tool()
    def get_chem_status(token: str | None = None) -> dict[str, Any]:
        """Return RDKit availability and RDF structure index coverage."""
        require_role(token, "viewer")
        return get_service().get_chem_status()

    @mcp.tool()
    def list_rdf_reactions(document_id: str = "", limit: int = 50, offset: int = 0, token: str | None = None) -> list[dict[str, Any]]:
        """List indexed SciFinder RDF reaction records with scheme/step metadata."""
        require_role(token, "viewer")
        return get_service().list_rdf_reactions(document_id=document_id, limit=limit, offset=offset)

    @mcp.tool()
    def get_rdf_reaction(reaction_id: str, token: str | None = None) -> dict[str, Any]:
        """Return one RDF reaction record with V2000/V3000 molfile structures."""
        require_role(token, "viewer")
        return get_service().get_rdf_reaction(reaction_id)

    @mcp.tool()
    def search_rdf_structures(query: str = "", document_id: str = "", limit: int = 50, offset: int = 0, token: str | None = None) -> list[dict[str, Any]]:
        """Search indexed RDF structures by name, CAS, SMILES, or InChIKey."""
        require_role(token, "viewer")
        return get_service().list_rdf_structures(document_id=document_id, query=query, limit=limit, offset=offset)

    @mcp.tool()
    def similarity_search_structures(query: str, query_type: str = "smiles", min_similarity: float = 0.2, limit: int = 20, token: str | None = None) -> dict[str, Any]:
        """Run RDKit fingerprint similarity search over indexed RDF compounds."""
        require_role(token, "viewer")
        return get_service().similarity_search_structures(query=query, query_type=query_type, min_similarity=min_similarity, limit=limit)

    @mcp.tool()
    def substructure_search_structures(query: str, query_type: str = "smarts", limit: int = 20, token: str | None = None) -> dict[str, Any]:
        """Run RDKit substructure search over indexed RDF molfile structures."""
        require_role(token, "viewer")
        return get_service().substructure_search_structures(query=query, query_type=query_type, limit=limit)

    @mcp.tool()
    def trash_item(entity_type: str, entity_id: str, token: str | None = None) -> dict[str, Any]:
        """Move a document, RDF reaction, RDF structure, or reaction step to trash."""
        require_role(token, "operator")
        return get_service().trash_item(entity_type, entity_id)

    @mcp.tool()
    def restore_trash_item(entity_type: str, entity_id: str, token: str | None = None) -> dict[str, Any]:
        """Restore a trashed document, RDF reaction, RDF structure, or reaction step."""
        require_role(token, "operator")
        return get_service().restore_trash_item(entity_type, entity_id)

    @mcp.tool()
    def list_trash(limit: int = 100, token: str | None = None) -> list[dict[str, Any]]:
        """List items in the recycle bin."""
        require_role(token, "viewer")
        return get_service().list_trash(limit=limit)

    @mcp.tool()
    def empty_trash(token: str | None = None) -> dict[str, int]:
        """Permanently delete all trashed records and their dependent indexes."""
        require_role(token, "admin")
        return get_service().empty_trash()

    @mcp.tool()
    def compute_evaluation_metrics(gold_set_path: str, token: str | None = None) -> dict[str, Any]:
        """Compute regression metrics from a gold-set JSONL file."""
        require_role(token, "operator")
        return get_service().compute_evaluation_metrics(gold_set_path)

    @mcp.tool()
    def get_evaluation_status(token: str | None = None) -> dict[str, Any]:
        """Return the latest evaluation metrics."""
        require_role(token, "viewer")
        return get_service().get_evaluation_status()

    @mcp.tool()
    def backup_database(output_path: str | None = None, token: str | None = None) -> dict[str, Any]:
        """Create a SQLite database backup, or report Postgres backup guidance."""
        require_role(token, "admin")
        return get_service().backup_database(output_path=output_path)

    @mcp.tool()
    def get_storage_usage(token: str | None = None) -> dict[str, Any]:
        """Return NAS data/upload/evidence storage usage."""
        require_role(token, "viewer")
        return get_service().get_storage_usage()

    @mcp.tool()
    def cleanup_evidence_cache(dry_run: bool = True, max_age_days: int | None = None, token: str | None = None) -> dict[str, Any]:
        """Clean generated evidence/cache files without deleting source documents."""
        require_role(token, "admin")
        return get_service().cleanup_evidence_cache(dry_run=dry_run, max_age_days=max_age_days)

    @mcp.tool()
    def test_integration_endpoint(kind: str, token: str | None = None) -> dict[str, Any]:
        """Test one configured integration endpoint: llm, embedding, ocr, document_parser, structure_recognition, postgres, zotero_mcp."""
        require_role(token, "operator")
        return get_service().test_integration_endpoint(kind)

    @mcp.tool()
    def list_zotero_mcp_endpoints(token: str | None = None) -> list[dict[str, Any]]:
        """List Zotero MCP endpoints from the Web UI hot config plus latest health status."""
        require_role(token, "viewer")
        return get_service().list_zotero_mcp_endpoints()

    @mcp.tool()
    def upsert_zotero_mcp_endpoint(endpoint: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        """Create or update a Zotero MCP endpoint in the separate Web UI config YAML."""
        require_role(token, "admin")
        return get_service().upsert_zotero_mcp_endpoint(endpoint)

    @mcp.tool()
    def delete_zotero_mcp_endpoint(endpoint_id: str, token: str | None = None) -> dict[str, Any]:
        """Delete a Zotero MCP endpoint from the separate Web UI config YAML."""
        require_role(token, "admin")
        return get_service().delete_zotero_mcp_endpoint(endpoint_id)

    @mcp.tool()
    def enqueue_literature_linking(document_id: str = "", token: str | None = None) -> dict[str, Any]:
        """Start a background Zotero literature linking job for one document or recent reaction steps."""
        require_role(token, "operator")
        return get_service().enqueue_literature_linking(document_id=document_id or None)

    @mcp.tool()
    def list_literature_links(status: str = "", reaction_step_id: str = "", document_id: str = "", limit: int = 50, token: str | None = None) -> list[dict[str, Any]]:
        """List Zotero literature candidates, auto-links, confirmed links, and rejected links."""
        require_role(token, "viewer")
        return get_service().list_literature_links(status=status, reaction_step_id=reaction_step_id, document_id=document_id, limit=limit)

    @mcp.tool()
    def confirm_literature_link(link_id: str, token: str | None = None) -> dict[str, Any]:
        """Confirm a candidate Zotero literature link for a reaction step."""
        require_role(token, "operator")
        return get_service().confirm_literature_link(link_id)

    @mcp.tool()
    def reject_literature_link(link_id: str, reason: str = "", token: str | None = None) -> dict[str, Any]:
        """Reject an incorrect candidate Zotero literature link."""
        require_role(token, "operator")
        return get_service().reject_literature_link(link_id, reason=reason)

    @mcp.tool()
    def get_reaction_literature_context(reaction_step_id: str, token: str | None = None) -> dict[str, Any]:
        """Return a reaction step with linked Zotero literature, excerpts, and field differences."""
        require_role(token, "viewer")
        return get_service().get_reaction_literature_context(reaction_step_id)

    @mcp.tool()
    def write_zotero_link_note(link_id: str, token: str | None = None) -> dict[str, Any]:
        """Optionally create a Zotero note for a confirmed literature link when endpoint writeback is enabled."""
        require_role(token, "operator")
        return get_service().write_zotero_link_note(link_id)

    @mcp.tool()
    def list_export_batches(limit: int = 100, token: str | None = None) -> list[dict[str, Any]]:
        """List explainable SciFinder export batches linking RDF with readable/visual files."""
        require_role(token, "viewer")
        return get_service().storage.list_export_batches(limit=limit)

    @mcp.tool()
    def get_export_batch(batch_id: str, token: str | None = None) -> dict[str, Any]:
        """Return one export batch, linked documents, confidence, and merge explanation."""
        require_role(token, "viewer")
        batch = get_service().storage.get_export_batch(batch_id)
        if not batch:
            raise KeyError(f"Export batch not found: {batch_id}")
        return batch

    @mcp.tool()
    def unlink_document_from_batch(document_id: str, batch_id: str, reason: str = "", token: str | None = None) -> dict[str, Any]:
        """Remove an incorrectly merged document from an export batch with an audit reason."""
        require_role(token, "operator")
        return get_service().storage.unlink_document_from_batch(document_id=document_id, batch_id=batch_id, reason=reason)

    register_guidance_interfaces(mcp)

    return mcp


SCIFINDER_IMPORT_GUIDANCE = """SciFinder Route MCP import guidance:
- Prefer Streamable HTTP /mcp for MCP clients; /sse is legacy compatibility only.
- The Admin UI is a separate operational HTTP interface, not an MCP transport.
- Ask the user before importing files they provide.
- Use upload_document_content for client-local/chat attachment content when available; it requires operator/admin token permission.
- Supported import formats are PDF, RTF, MDL RDfile/RDF, HTML/MHTML, Markdown, and plain text. ODF/ODT/ODS/ODP are not supported.
- Uploads must pass size, extension, content-type sniffing, format-specific safety checks, and optional ClamAV scanning before writing to upload_dir.
- RDF/RDfile is the preferred structured reaction source for CAS Reaction Number, molecule CTAB, CAS RN fields, yield, reagents, catalysts, solvents, and references.
- RDF may not contain full experimental procedures. Link RDF-derived records to PDF/RTF/HTML readable or visual provenance when available.
- Auto-merge export batches only with explainable high-confidence signals; keep low-confidence matches as candidates and ask for confirmation.
"""


def register_guidance_interfaces(mcp: Any) -> None:
    if hasattr(mcp, "resource"):
        try:
            @mcp.resource("docs://scifinder-import-guidance")
            def scifinder_import_guidance_resource() -> str:
                return SCIFINDER_IMPORT_GUIDANCE
        except Exception:
            pass
    if hasattr(mcp, "prompt"):
        try:
            @mcp.prompt()
            def scifinder_import_guidance() -> str:
                return SCIFINDER_IMPORT_GUIDANCE
        except Exception:
            pass


mcp = create_mcp()


def run_mcp_server(server: Any, config: ServerRunConfig | None = None) -> None:
    run_config = config or ServerRunConfig.from_env()
    if run_config.transport == "stdio":
        try:
            server.run(transport="stdio")
        except TypeError:
            server.run()
        return

    if run_config.transport in {"auto", "dual"}:
        run_dual_transport_server(server, run_config)
        return

    if run_config.transport not in {"sse", "streamable-http", "http"}:
        raise ValueError(f"Unsupported MCP transport: {run_config.transport}")

    path = run_config.path or ("/sse" if run_config.transport == "sse" else "/mcp")
    fastmcp_transport = "http" if run_config.transport == "streamable-http" else run_config.transport

    attempts = [
        {
            "transport": fastmcp_transport,
            "host": run_config.host,
            "port": run_config.port,
            "path": path,
            "log_level": run_config.log_level,
        },
        {
            "transport": fastmcp_transport,
            "host": run_config.host,
            "port": run_config.port,
        },
        {"transport": fastmcp_transport},
    ]
    last_error: TypeError | None = None
    for kwargs in attempts:
        try:
            server.run(**kwargs)
            return
        except TypeError as exc:
            last_error = exc
    raise RuntimeError(
        "Installed FastMCP does not support the requested SSE/HTTP run signature. "
        "Upgrade FastMCP in the Docker image or switch SCIFINDER_ROUTE_TRANSPORT=stdio. "
        "Use SCIFINDER_ROUTE_TRANSPORT=http for Streamable HTTP."
    ) from last_error


def create_dual_transport_app(server: Any, config: ServerRunConfig | None = None) -> Any:
    """Create one ASGI app exposing Streamable HTTP and legacy SSE endpoints."""
    run_config = config or ServerRunConfig.from_env()
    if not hasattr(server, "http_app"):
        raise RuntimeError("Installed FastMCP does not support ASGI http_app(). Upgrade FastMCP to use dual MCP transports.")

    try:
        from starlette.applications import Starlette
    except ImportError as exc:  # pragma: no cover - dependency is provided by FastMCP HTTP extras in production
        raise RuntimeError("Starlette is required for dual MCP transport mode. Upgrade FastMCP or install project dependencies.") from exc

    http_app = server.http_app(path=run_config.mcp_path, transport="http")
    sse_app = server.http_app(path=run_config.sse_path, transport="sse")

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(http_app.lifespan(app))
            await stack.enter_async_context(sse_app.lifespan(app))
            yield

    return Starlette(routes=[*http_app.routes, *sse_app.routes], lifespan=lifespan)


def run_dual_transport_server(server: Any, config: ServerRunConfig | None = None) -> None:
    run_config = config or ServerRunConfig.from_env()
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - production image installs uvicorn explicitly
        raise RuntimeError("Uvicorn is required for dual MCP transport mode. Install project dependencies or use a single transport.") from exc

    app = create_dual_transport_app(server, run_config)
    uvicorn.run(app, host=run_config.host, port=run_config.port, log_level=run_config.log_level.lower())


def main() -> None:
    service = RouteService()
    start_admin_server(service)
    run_mcp_server(create_mcp(service))


if __name__ == "__main__":
    main()
