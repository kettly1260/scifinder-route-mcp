from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from .admin import start_admin_server
from .service import RouteService


@dataclass(frozen=True)
class ServerRunConfig:
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/sse"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "ServerRunConfig":
        return cls(
            transport=os.getenv("SCIFINDER_ROUTE_TRANSPORT", "stdio").lower(),
            host=os.getenv("SCIFINDER_ROUTE_HOST", "127.0.0.1"),
            port=int(os.getenv("SCIFINDER_ROUTE_PORT", "8000")),
            path=os.getenv("SCIFINDER_ROUTE_SSE_PATH", "/sse"),
            log_level=os.getenv("SCIFINDER_ROUTE_LOG_LEVEL", "INFO"),
        )


class LocalMCP:
    """Tiny decorator-compatible fallback for tests without FastMCP installed."""

    def __init__(self, name: str):
        self.name = name
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[func.__name__] = func
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

    def require_token(token: str | None) -> None:
        configured = get_service().config.auth_token
        if configured and token != configured:
            raise PermissionError("Invalid or missing SCIFINDER_ROUTE_TOKEN")

    try:
        from fastmcp import FastMCP  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - fallback is mainly for tests/minimal environments
        mcp: Any = LocalMCP("scifinder-route-mcp")
    else:
        mcp = FastMCP("scifinder-route-mcp")

    @mcp.tool()
    def register_document(file_path: str, reparse: bool = False, token: str | None = None) -> dict[str, Any]:
        """Register and parse a local SciFinder export file already visible to the server."""
        require_token(token)
        return get_service().register_document(file_path=file_path, reparse=reparse)

    @mcp.tool()
    def upload_document(source_path: str, filename: str | None = None, reparse: bool = False, token: str | None = None) -> dict[str, Any]:
        """Copy a server-visible file into the upload area, then register and parse it."""
        require_token(token)
        return get_service().upload_document(source_path=source_path, filename=filename, reparse=reparse)

    @mcp.tool()
    def scan_inbox(reparse: bool = False, limit: int = 500, token: str | None = None) -> dict[str, Any]:
        """Scan the NAS inbox for supported SciFinder exports and queue/register new files."""
        require_token(token)
        return get_service().scan_inbox(reparse=reparse, limit=limit)

    @mcp.tool()
    def get_parse_job_status(job_id: str, token: str | None = None) -> dict[str, Any]:
        """Return parse job status, stage, and error details."""
        require_token(token)
        return get_service().get_parse_job_status(job_id=job_id)

    @mcp.tool()
    def list_parse_jobs(status: str = "", limit: int = 100, token: str | None = None) -> list[dict[str, Any]]:
        """List recent parse jobs, optionally filtered by status."""
        require_token(token)
        return get_service().list_parse_jobs(status=status, limit=limit)

    @mcp.tool()
    def health_check(token: str | None = None) -> dict[str, Any]:
        """Return server health, configured paths, and indexed object counts."""
        require_token(token)
        return get_service().health_check()

    @mcp.tool()
    def get_config(include_secrets: bool = False, token: str | None = None) -> dict[str, Any]:
        """Return the effective application config. Secrets are masked unless include_secrets is true."""
        require_token(token)
        return get_service().get_config(include_secrets=include_secrets)

    @mcp.tool()
    def update_config(updates: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        """Merge hot-reloadable application config updates into config.yaml and reload them."""
        require_token(token)
        return get_service().update_config(updates=updates)

    @mcp.tool()
    def validate_config(token: str | None = None) -> dict[str, Any]:
        """Validate the current application config and report settings that require container restart."""
        require_token(token)
        return get_service().validate_config()

    @mcp.tool()
    def reload_config(token: str | None = None) -> dict[str, Any]:
        """Reload hot application config from config.yaml without restarting the container."""
        require_token(token)
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
        require_token(token)
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
        require_token(token)
        return get_service().get_reaction_step(reaction_step_id=reaction_step_id)

    @mcp.tool()
    def get_reaction_provenance(reaction_step_id: str, token: str | None = None) -> list[dict[str, Any]]:
        """Return source text/page/parser provenance for a reaction step."""
        require_token(token)
        return get_service().get_reaction_provenance(reaction_step_id=reaction_step_id)

    @mcp.tool()
    def reparse_document(document_id: str, token: str | None = None) -> dict[str, Any]:
        """Clear extracted reactions for a document and parse it again."""
        require_token(token)
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
        require_token(token)
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
        require_token(token)
        return get_service().export_evaluation_set(output_path=output_path, limit=limit)

    return mcp


mcp = create_mcp()


def run_mcp_server(server: Any, config: ServerRunConfig | None = None) -> None:
    run_config = config or ServerRunConfig.from_env()
    if run_config.transport == "stdio":
        try:
            server.run(transport="stdio")
        except TypeError:
            server.run()
        return

    if run_config.transport not in {"sse", "streamable-http", "http"}:
        raise ValueError(f"Unsupported MCP transport: {run_config.transport}")

    attempts = [
        {
            "transport": run_config.transport,
            "host": run_config.host,
            "port": run_config.port,
            "path": run_config.path,
            "log_level": run_config.log_level,
        },
        {
            "transport": run_config.transport,
            "host": run_config.host,
            "port": run_config.port,
        },
        {"transport": run_config.transport},
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
        "Upgrade FastMCP in the Docker image or switch SCIFINDER_ROUTE_TRANSPORT=stdio."
    ) from last_error


def main() -> None:
    service = RouteService()
    start_admin_server(service)
    run_mcp_server(create_mcp(service))


if __name__ == "__main__":
    main()
