from __future__ import annotations

import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .auth import UserCredential, mask_secret, parse_users
from .models import AiProvider


DEFAULT_SCAN_EXTENSIONS = (".pdf", ".rtf", ".rdf", ".html", ".htm", ".mhtml", ".mht", ".md", ".markdown", ".txt")
DEFAULT_ZOTERO_MCP_ENDPOINT = "http://127.0.0.1:23120/mcp"
DEFAULT_ZOTERO_MCP_ENDPOINTS = (
    {
        "id": "local-zotero",
        "alias": "local-zotero",
        "group_name": "local-zotero",
        "url": DEFAULT_ZOTERO_MCP_ENDPOINT,
        "enabled": True,
        "priority": 100,
        "timeout_seconds": 10,
        "headers": {},
        "write_note_enabled": False,
    },
)
HOT_CONFIG_SECTIONS = {"server", "security", "ingest", "integrations", "thresholds", "queue", "extraction", "retention"}
HOT_CONFIG_KEYS = {
    "server": {"async_jobs", "max_workers", "storage_backend"},
    "security": {"allow_external_paths", "token", "users", "upload_av_scan_enabled", "upload_av_engine", "upload_av_endpoint", "upload_av_fail_closed"},
    "ingest": {"scan_extensions", "upload_extensions", "upload_max_bytes", "reject_file_type_mismatch", "extract_visual_evidence", "render_visual_pages", "visual_page_dpi", "max_visual_pages_per_document", "max_embedded_images_per_document"},
    "queue": {"backend", "redis_url"},
    "integrations": {
        "ai_providers",
        "extraction_provider_id",
        "extraction_model",
        "embedding_provider_id",
        "embedding_model",
        "ocr_provider_id",
        "ocr_model",
        "document_parser_provider_id",
        "document_parser_model",
        "document_parser_fallback",
        "structure_recognition_provider_id",
        "structure_recognition_model",
        "reranker_provider_id",
        "reranker_model",
                                "postgres_url",
        "zotero_mcp_endpoints",
        "zotero_linking_enabled",
        "zotero_linking_on_import",
        "zotero_extraction_strategy",
        "zotero_llm_priority_terms",
    },
    "extraction": {"llm_schema_version", "llm_prompt_profile", "llm_cost_limit_usd"},
    "thresholds": {"verification_confidence_threshold"},
    "retention": {"evidence_retention_days", "cache_retention_days"},
}
CONFIG_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    inbox_dir: Path
    upload_dir: Path
    evidence_dir: Path
    database_path: Path
    config_path: Path
    webui_config_path: Path | None = None
    sample_dir: Path | None = None
    async_jobs: bool = False
    max_workers: int = 1
    allow_external_paths: bool = True
    auth_token: str | None = None
    users: tuple[UserCredential, ...] = ()
    scan_extensions: tuple[str, ...] = DEFAULT_SCAN_EXTENSIONS
    upload_extensions: tuple[str, ...] = DEFAULT_SCAN_EXTENSIONS
    upload_max_bytes: int = 50 * 1024 * 1024
    reject_file_type_mismatch: bool = True
    extract_visual_evidence: bool = True
    render_visual_pages: bool = True
    visual_page_dpi: int = 160
    max_visual_pages_per_document: int = 20
    max_embedded_images_per_document: int = 100
    upload_av_scan_enabled: bool = False
    upload_av_engine: str = "clamav"
    upload_av_endpoint: str | None = None
    upload_av_fail_closed: bool = True
    storage_backend: str = "sqlite"
    queue_backend: str = "sqlite"
    ai_providers: tuple[AiProvider, ...] = ()
    extraction_provider_id: str | None = None
    extraction_model: str | None = None
    embedding_provider_id: str | None = None
    embedding_model: str | None = None
    ocr_provider_id: str | None = None
    ocr_model: str | None = None
    document_parser_provider_id: str | None = None
    document_parser_model: str | None = None
    document_parser_fallback: bool = True
    structure_recognition_provider_id: str | None = None
    structure_recognition_model: str | None = None
    reranker_provider_id: str | None = None
    reranker_model: str | None = None
    postgres_url: str | None = None
    zotero_mcp_endpoints: tuple[dict[str, Any], ...] = ()
    zotero_linking_enabled: bool = False
    zotero_linking_on_import: bool = True
    zotero_extraction_strategy: str = "rules_first"
    zotero_llm_priority_terms: tuple[str, ...] = ()
    redis_url: str | None = None
    llm_schema_version: str = "reaction_step.v1"
    llm_prompt_profile: str = "strict-reaction-json"
    llm_cost_limit_usd: float = 0.50
    verification_confidence_threshold: float = 0.65
    evidence_retention_days: int = 90
    cache_retention_days: int = 30

    @classmethod
    def from_env(cls) -> "AppConfig":
        data_dir = Path(os.getenv("SCIFINDER_ROUTE_DATA_DIR", ".data")).resolve()
        inbox_dir = Path(os.getenv("SCIFINDER_ROUTE_INBOX_DIR", data_dir / "inbox")).resolve()
        upload_dir = Path(os.getenv("SCIFINDER_ROUTE_UPLOAD_DIR", data_dir / "uploads")).resolve()
        evidence_dir = Path(os.getenv("SCIFINDER_ROUTE_EVIDENCE_DIR", data_dir / "evidence")).resolve()
        database_path = Path(os.getenv("SCIFINDER_ROUTE_DATABASE", data_dir / "scifinder_routes.sqlite3")).resolve()
        config_path = Path(os.getenv("SCIFINDER_ROUTE_CONFIG", data_dir / "config.yaml")).resolve()
        webui_config_path = Path(os.getenv("SCIFINDER_ROUTE_WEBUI_CONFIG", data_dir / "webui-config.yaml")).resolve()
        sample_dir_value = os.getenv("SCIFINDER_ROUTE_SAMPLE_DIR")
        sample_dir = Path(sample_dir_value).resolve() if sample_dir_value else None


        ai_providers_dict: dict[str, AiProvider] = {}
        
        # Parse new variable if present
        raw_providers = parse_ai_providers(os.getenv("SCIFINDER_ROUTE_AI_PROVIDERS"))
        for p in raw_providers:
            ai_providers_dict[p.id] = p
            
        def _migrate_legacy(prefix: str, feature_id: str, default_format: str = "openai_compatible") -> tuple[str | None, str | None]:
            legacy_endpoint = os.getenv(f"SCIFINDER_ROUTE_{prefix}_ENDPOINT")
            if not legacy_endpoint:
                return None, None
            legacy_key = os.getenv(f"SCIFINDER_ROUTE_{prefix}_API_KEY")
            if prefix == "LLM" and not legacy_key:
                legacy_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("GEMINI_API_KEY")
            legacy_model = os.getenv(f"SCIFINDER_ROUTE_{prefix}_MODEL")
            legacy_format = os.getenv(f"SCIFINDER_ROUTE_{prefix}_PROVIDER", default_format).strip() or default_format
            
            # Create a provider ID
            provider_id = f"legacy-{feature_id}"
            if provider_id not in ai_providers_dict:
                ai_providers_dict[provider_id] = AiProvider(
                    id=provider_id,
                    name=f"Legacy {feature_id.upper()} Provider",
                    format=legacy_format,
                    endpoint=legacy_endpoint,
                    api_key=legacy_key,
                )
            return provider_id, legacy_model
            
        ext_provider, ext_model = _migrate_legacy("LLM", "extraction")
        emb_provider, emb_model = _migrate_legacy("EMBEDDING", "embedding")
        ocr_provider, ocr_model = _migrate_legacy("OCR", "ocr", "generic")
        parser_provider, parser_model = _migrate_legacy("DOCUMENT_PARSER", "document_parser", "generic")
        struct_provider, struct_model = _migrate_legacy("STRUCTURE_RECOGNITION", "structure_recognition", "generic")
        rerank_provider, rerank_model = _migrate_legacy("RERANKER", "reranker", "generic")

        config = cls(
            data_dir=data_dir,
            inbox_dir=inbox_dir,
            upload_dir=upload_dir,
            evidence_dir=evidence_dir,
            database_path=database_path,
            config_path=config_path,
            webui_config_path=webui_config_path,
            sample_dir=sample_dir,
            async_jobs=parse_bool(os.getenv("SCIFINDER_ROUTE_ASYNC_JOBS"), default=False),
            max_workers=max(1, int(os.getenv("SCIFINDER_ROUTE_MAX_WORKERS", "1"))),
            allow_external_paths=parse_bool(os.getenv("SCIFINDER_ROUTE_ALLOW_EXTERNAL_PATHS"), default=True),
            auth_token=os.getenv("SCIFINDER_ROUTE_TOKEN") or None,
            users=parse_users(os.getenv("SCIFINDER_ROUTE_USERS")),
            scan_extensions=parse_extensions(os.getenv("SCIFINDER_ROUTE_SCAN_EXTENSIONS"), DEFAULT_SCAN_EXTENSIONS),
            upload_extensions=parse_extensions(os.getenv("SCIFINDER_ROUTE_UPLOAD_EXTENSIONS"), DEFAULT_SCAN_EXTENSIONS),
            upload_max_bytes=max(1, int(os.getenv("SCIFINDER_ROUTE_UPLOAD_MAX_BYTES", str(50 * 1024 * 1024)))),
            reject_file_type_mismatch=parse_bool(os.getenv("SCIFINDER_ROUTE_REJECT_FILE_TYPE_MISMATCH"), default=True),
            extract_visual_evidence=parse_bool(os.getenv("SCIFINDER_ROUTE_EXTRACT_VISUAL_EVIDENCE"), default=True),
            render_visual_pages=parse_bool(os.getenv("SCIFINDER_ROUTE_RENDER_VISUAL_PAGES"), default=True),
            visual_page_dpi=max(72, int(os.getenv("SCIFINDER_ROUTE_VISUAL_PAGE_DPI", "160"))),
            max_visual_pages_per_document=max(0, int(os.getenv("SCIFINDER_ROUTE_MAX_VISUAL_PAGES_PER_DOCUMENT", "20"))),
            max_embedded_images_per_document=max(0, int(os.getenv("SCIFINDER_ROUTE_MAX_EMBEDDED_IMAGES_PER_DOCUMENT", "100"))),
            upload_av_scan_enabled=parse_bool(os.getenv("SCIFINDER_ROUTE_UPLOAD_AV_SCAN_ENABLED"), default=False),
            upload_av_engine=os.getenv("SCIFINDER_ROUTE_UPLOAD_AV_ENGINE", "clamav").strip().lower() or "clamav",
            upload_av_endpoint=os.getenv("SCIFINDER_ROUTE_UPLOAD_AV_ENDPOINT") or None,
            upload_av_fail_closed=parse_bool(os.getenv("SCIFINDER_ROUTE_UPLOAD_AV_FAIL_CLOSED"), default=True),
            storage_backend=os.getenv("SCIFINDER_ROUTE_BACKEND", "sqlite").strip().lower() or "sqlite",
            queue_backend=os.getenv("SCIFINDER_ROUTE_QUEUE_BACKEND", "sqlite").strip().lower() or "sqlite",
            
            ai_providers=tuple(ai_providers_dict.values()),
            extraction_provider_id=os.getenv("SCIFINDER_ROUTE_EXTRACTION_PROVIDER_ID") or ext_provider,
            extraction_model=os.getenv("SCIFINDER_ROUTE_EXTRACTION_MODEL") or ext_model,
            embedding_provider_id=os.getenv("SCIFINDER_ROUTE_EMBEDDING_PROVIDER_ID") or emb_provider,
            embedding_model=os.getenv("SCIFINDER_ROUTE_EMBEDDING_MODEL") or emb_model,
            ocr_provider_id=os.getenv("SCIFINDER_ROUTE_OCR_PROVIDER_ID") or ocr_provider,
            ocr_model=os.getenv("SCIFINDER_ROUTE_OCR_MODEL") or ocr_model,
            document_parser_provider_id=os.getenv("SCIFINDER_ROUTE_DOCUMENT_PARSER_PROVIDER_ID") or parser_provider,
            document_parser_model=os.getenv("SCIFINDER_ROUTE_DOCUMENT_PARSER_MODEL") or parser_model,
            document_parser_fallback=parse_bool(os.getenv("SCIFINDER_ROUTE_DOCUMENT_PARSER_FALLBACK"), default=True),
            structure_recognition_provider_id=os.getenv("SCIFINDER_ROUTE_STRUCTURE_RECOGNITION_PROVIDER_ID") or struct_provider,
            structure_recognition_model=os.getenv("SCIFINDER_ROUTE_STRUCTURE_RECOGNITION_MODEL") or struct_model,
            reranker_provider_id=os.getenv("SCIFINDER_ROUTE_RERANKER_PROVIDER_ID") or rerank_provider,
            reranker_model=os.getenv("SCIFINDER_ROUTE_RERANKER_MODEL") or rerank_model,

            postgres_url=os.getenv("SCIFINDER_ROUTE_POSTGRES_URL") or None,
            zotero_mcp_endpoints=parse_json_list(os.getenv("SCIFINDER_ROUTE_ZOTERO_MCP_ENDPOINTS")),
            zotero_linking_enabled=parse_bool(os.getenv("SCIFINDER_ROUTE_ZOTERO_LINKING_ENABLED"), default=False),
            zotero_linking_on_import=parse_bool(os.getenv("SCIFINDER_ROUTE_ZOTERO_LINKING_ON_IMPORT"), default=True),
            zotero_extraction_strategy=os.getenv("SCIFINDER_ROUTE_ZOTERO_EXTRACTION_STRATEGY", "rules_first"),
            zotero_llm_priority_terms=tuple(item.strip() for item in os.getenv("SCIFINDER_ROUTE_ZOTERO_LLM_PRIORITY_TERMS", "").split(",") if item.strip()),
            redis_url=os.getenv("SCIFINDER_ROUTE_REDIS_URL") or None,
            llm_schema_version=os.getenv("SCIFINDER_ROUTE_LLM_SCHEMA_VERSION", "reaction_step.v1"),
            llm_prompt_profile=os.getenv("SCIFINDER_ROUTE_LLM_PROMPT_PROFILE", "strict-reaction-json"),
            llm_cost_limit_usd=float(os.getenv("SCIFINDER_ROUTE_LLM_COST_LIMIT_USD", "0.50")),
            verification_confidence_threshold=float(os.getenv("SCIFINDER_ROUTE_VERIFICATION_CONFIDENCE_THRESHOLD", "0.65")),
            evidence_retention_days=int(os.getenv("SCIFINDER_ROUTE_EVIDENCE_RETENTION_DAYS", "90")),
            cache_retention_days=int(os.getenv("SCIFINDER_ROUTE_CACHE_RETENTION_DAYS", "30")),
        )
        return config.apply_file_overrides()

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if self.webui_config_path:
            self.webui_config_path.parent.mkdir(parents=True, exist_ok=True)

    def apply_file_overrides(self) -> "AppConfig":
        config = self
        if self.config_path.exists():
            config = apply_config_overrides(config, read_config_yaml(self.config_path))
        webui_path = config.webui_config_path or self.webui_config_path or config.data_dir / "webui-config.yaml"
        if webui_path and webui_path.exists():
            try:
                config = apply_config_overrides(config, read_config_yaml(webui_path))
            except Exception:
                return config
        return config

    def effective_config(self, *, include_secrets: bool = False) -> dict[str, Any]:
        token = self.auth_token if include_secrets else mask_secret(self.auth_token)
        postgres_url = self.postgres_url if include_secrets else mask_secret(self.postgres_url)
        redis_url = self.redis_url if include_secrets else mask_secret(self.redis_url)
        return {
            "paths": {
                "data_dir": str(self.data_dir),
                "inbox_dir": str(self.inbox_dir),
                "upload_dir": str(self.upload_dir),
                "evidence_dir": str(self.evidence_dir),
                "database_path": str(self.database_path),
                "config_path": str(self.config_path),
                "webui_config_path": str(self.webui_config_path or self.data_dir / "webui-config.yaml"),
            },
            "server": {
                "async_jobs": self.async_jobs,
                "max_workers": self.max_workers,
                "storage_backend": self.storage_backend,
            },
            "security": {
                "allow_external_paths": self.allow_external_paths,
                "token": token,
                "users": [user.__dict__ if include_secrets else user.masked() for user in self.users],
                "upload_av_scan_enabled": self.upload_av_scan_enabled,
                "upload_av_engine": self.upload_av_engine,
                "upload_av_endpoint": self.upload_av_endpoint if include_secrets else mask_secret(self.upload_av_endpoint),
                "upload_av_fail_closed": self.upload_av_fail_closed,
            },
            "ingest": {
                "scan_extensions": list(self.scan_extensions),
                "upload_extensions": list(self.upload_extensions),
                "upload_max_bytes": self.upload_max_bytes,
                "reject_file_type_mismatch": self.reject_file_type_mismatch,
                "extract_visual_evidence": self.extract_visual_evidence,
                "render_visual_pages": self.render_visual_pages,
                "visual_page_dpi": self.visual_page_dpi,
                "max_visual_pages_per_document": self.max_visual_pages_per_document,
                "max_embedded_images_per_document": self.max_embedded_images_per_document,
            },
            "queue": {
                "backend": self.queue_backend,
                "redis_url": redis_url,
            },
            "integrations": {
                "ai_providers": [provider.to_dict() if include_secrets else mask_provider_secrets(provider.to_dict()) for provider in self.ai_providers],
                "extraction_provider_id": self.extraction_provider_id,
                "extraction_model": self.extraction_model,
                "embedding_provider_id": self.embedding_provider_id,
                "embedding_model": self.embedding_model,
                "ocr_provider_id": self.ocr_provider_id,
                "ocr_model": self.ocr_model,
                "document_parser_provider_id": self.document_parser_provider_id,
                "document_parser_model": self.document_parser_model,
                "document_parser_fallback": self.document_parser_fallback,
                "structure_recognition_provider_id": self.structure_recognition_provider_id,
                "structure_recognition_model": self.structure_recognition_model,
                "reranker_provider_id": self.reranker_provider_id,
                "reranker_model": self.reranker_model,
                "postgres_url": postgres_url,
                "zotero_mcp_endpoints": mask_zotero_endpoints(self.zotero_mcp_endpoints, include_secrets=include_secrets),
                "zotero_linking_enabled": self.zotero_linking_enabled,
                "zotero_linking_on_import": self.zotero_linking_on_import,
                "zotero_extraction_strategy": self.zotero_extraction_strategy,
                "zotero_llm_priority_terms": list(self.zotero_llm_priority_terms),
            },
            "extraction": {
                "llm_schema_version": self.llm_schema_version,
                "llm_prompt_profile": self.llm_prompt_profile,
                "llm_cost_limit_usd": self.llm_cost_limit_usd,
            },
            "thresholds": {
                "verification_confidence_threshold": self.verification_confidence_threshold,
            },
            "retention": {
                "evidence_retention_days": self.evidence_retention_days,
                "cache_retention_days": self.cache_retention_days,
            },
        }

    def hot_config(self, *, include_secrets: bool = False) -> dict[str, Any]:
        effective = self.effective_config(include_secrets=include_secrets)
        return {key: effective[key] for key in HOT_CONFIG_SECTIONS}

    def write_hot_config(self, updates: dict[str, Any]) -> None:
        with CONFIG_WRITE_LOCK:
            target = self.webui_config_path or self.data_dir / "webui-config.yaml"
            current = read_config_yaml(target) if target.exists() else self.hot_config(include_secrets=True)
            merged = merge_hot_config(current, updates)
            write_config_yaml(target, merged)

    def validate(self) -> list[str]:
        warnings: list[str] = []
        if self.max_workers < 1:
            warnings.append("server.max_workers must be >= 1")
        if self.storage_backend not in {"sqlite", "postgres"}:
            warnings.append("server.storage_backend must be sqlite or postgres")
        if self.storage_backend == "postgres" and not self.postgres_url:
            warnings.append("server.storage_backend=postgres requires integrations.postgres_url")
        if self.queue_backend not in {"sqlite", "redis"}:
            warnings.append("queue.backend must be sqlite or redis")
        if self.queue_backend == "redis" and not self.redis_url:
            warnings.append("queue.backend=redis requires queue.redis_url; sqlite queue remains the safe fallback")
        if not self.scan_extensions:
            warnings.append("ingest.scan_extensions is empty; scan_inbox will skip every file")
        for extension in self.scan_extensions:
            if not extension.startswith("."):
                warnings.append(f"ingest.scan_extensions entry must start with '.': {extension}")
        if not self.upload_extensions:
            warnings.append("ingest.upload_extensions is empty; MCP content uploads will reject every file")
        for extension in self.upload_extensions:
            if not extension.startswith("."):
                warnings.append(f"ingest.upload_extensions entry must start with '.': {extension}")
        if self.upload_max_bytes < 1:
            warnings.append("ingest.upload_max_bytes must be >= 1")
        if self.upload_av_scan_enabled and self.upload_av_engine != "clamav":
            warnings.append("security.upload_av_engine currently supports clamav only")
        if self.upload_av_scan_enabled and not self.upload_av_endpoint:
            warnings.append("security.upload_av_scan_enabled=true requires security.upload_av_endpoint")
        if self.verification_confidence_threshold < 0 or self.verification_confidence_threshold > 1:
            warnings.append("thresholds.verification_confidence_threshold must be between 0 and 1")
        
        provider_ids = {p.id for p in self.ai_providers}
        for provider in self.ai_providers:
            if provider.endpoint and not provider.endpoint.startswith(("http://", "https://")):
                warnings.append(f"AI Provider {provider.id} endpoint should start with http:// or https://")
                
        for feature, pid in [
            ("extraction", self.extraction_provider_id),
            ("embedding", self.embedding_provider_id),
            ("ocr", self.ocr_provider_id),
            ("document_parser", self.document_parser_provider_id),
            ("structure_recognition", self.structure_recognition_provider_id),
            ("reranker", self.reranker_provider_id)
        ]:
            if pid and pid not in provider_ids:
                warnings.append(f"Feature {feature} references unknown provider_id: {pid}")

        for index, endpoint in enumerate(self.zotero_mcp_endpoints):
            url = str(endpoint.get("url") or "")
            if url and not url.startswith(("http://", "https://")):
                warnings.append(f"integrations.zotero_mcp_endpoints[{index}].url should start with http:// or https://")
            if endpoint.get("enabled", True) and not url:
                warnings.append(f"integrations.zotero_mcp_endpoints[{index}].url is required when enabled")
        if self.zotero_extraction_strategy not in {"rules_first", "llm_first", "rules_only"}:
            warnings.append("integrations.zotero_extraction_strategy must be rules_first, llm_first, or rules_only")
        return warnings


def parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_extensions(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    extensions = tuple(normalize_extension(item) for item in value.split(",") if item.strip())
    return extensions or default


def normalize_extension(value: str) -> str:
    extension = value.strip().lower()
    return extension if extension.startswith(".") else f".{extension}"


def parse_json_list(value: str | None) -> tuple[dict[str, Any], ...]:
    if not value:
        return ()
    try:
        parsed = __import__("json").loads(value)
    except Exception:
        return ()
    return normalize_endpoint_list(parsed)


def apply_config_overrides(config: AppConfig, raw: dict[str, Any]) -> AppConfig:
    server = section(raw, "server")
    security = section(raw, "security")
    ingest = section(raw, "ingest")
    integrations = section(raw, "integrations")
    thresholds = section(raw, "thresholds")
    queue = section(raw, "queue")
    extraction = section(raw, "extraction")
    retention = section(raw, "retention")
    scan_extensions = ingest.get("scan_extensions", config.scan_extensions)
    if isinstance(scan_extensions, str):
        scan_extensions = parse_extensions(scan_extensions, config.scan_extensions)
    else:
        scan_extensions = tuple(normalize_extension(str(item)) for item in scan_extensions)
    upload_extensions = ingest.get("upload_extensions", config.upload_extensions)
    if isinstance(upload_extensions, str):
        upload_extensions = parse_extensions(upload_extensions, config.upload_extensions)
    else:
        upload_extensions = tuple(normalize_extension(str(item)) for item in upload_extensions)
    priority_terms = integrations.get("zotero_llm_priority_terms", config.zotero_llm_priority_terms)
    if isinstance(priority_terms, str):
        priority_terms = tuple(item.strip() for item in priority_terms.split(",") if item.strip())
    else:
        priority_terms = tuple(str(item).strip() for item in priority_terms if str(item).strip())
    return replace(
        config,
        async_jobs=coerce_bool(server.get("async_jobs"), config.async_jobs),
        max_workers=max(1, int(server.get("max_workers", config.max_workers))),
        storage_backend=str(server.get("storage_backend", config.storage_backend)).strip().lower() or "sqlite",
        allow_external_paths=coerce_bool(security.get("allow_external_paths"), config.allow_external_paths),
        auth_token=none_if_empty(security.get("token", config.auth_token)),
        users=parse_users(jsonish(security.get("users"))) if "users" in security else config.users,
        upload_av_scan_enabled=coerce_bool(security.get("upload_av_scan_enabled"), config.upload_av_scan_enabled),
        upload_av_engine=str(security.get("upload_av_engine", config.upload_av_engine)).strip().lower() or "clamav",
        upload_av_endpoint=none_if_empty(security.get("upload_av_endpoint", config.upload_av_endpoint)),
        upload_av_fail_closed=coerce_bool(security.get("upload_av_fail_closed"), config.upload_av_fail_closed),
        scan_extensions=scan_extensions,
        upload_extensions=upload_extensions,
        upload_max_bytes=max(1, int(ingest.get("upload_max_bytes", config.upload_max_bytes))),
        reject_file_type_mismatch=coerce_bool(ingest.get("reject_file_type_mismatch"), config.reject_file_type_mismatch),
        extract_visual_evidence=coerce_bool(ingest.get("extract_visual_evidence"), config.extract_visual_evidence),
        render_visual_pages=coerce_bool(ingest.get("render_visual_pages"), config.render_visual_pages),
        visual_page_dpi=max(72, int(ingest.get("visual_page_dpi", config.visual_page_dpi))),
        max_visual_pages_per_document=max(0, int(ingest.get("max_visual_pages_per_document", config.max_visual_pages_per_document))),
        max_embedded_images_per_document=max(0, int(ingest.get("max_embedded_images_per_document", config.max_embedded_images_per_document))),
        queue_backend=str(queue.get("backend", config.queue_backend)).strip().lower() or "sqlite",
        redis_url=none_if_empty(queue.get("redis_url", config.redis_url)),
        ai_providers=parse_ai_providers(integrations.get("ai_providers", [p.to_dict() for p in config.ai_providers])),
        extraction_provider_id=none_if_empty(integrations.get("extraction_provider_id", config.extraction_provider_id)),
        extraction_model=none_if_empty(integrations.get("extraction_model", config.extraction_model)),
        embedding_provider_id=none_if_empty(integrations.get("embedding_provider_id", config.embedding_provider_id)),
        embedding_model=none_if_empty(integrations.get("embedding_model", config.embedding_model)),
        ocr_provider_id=none_if_empty(integrations.get("ocr_provider_id", config.ocr_provider_id)),
        ocr_model=none_if_empty(integrations.get("ocr_model", config.ocr_model)),
        document_parser_provider_id=none_if_empty(integrations.get("document_parser_provider_id", config.document_parser_provider_id)),
        document_parser_model=integrations.get("document_parser_model", config.document_parser_model) or None,
        document_parser_fallback=coerce_bool(integrations.get("document_parser_fallback"), config.document_parser_fallback),
        structure_recognition_provider_id=none_if_empty(integrations.get("structure_recognition_provider_id", config.structure_recognition_provider_id)),
        structure_recognition_model=none_if_empty(integrations.get("structure_recognition_model", config.structure_recognition_model)),
        reranker_provider_id=none_if_empty(integrations.get("reranker_provider_id", config.reranker_provider_id)),
        reranker_model=none_if_empty(integrations.get("reranker_model", config.reranker_model)),
        postgres_url=none_if_empty(integrations.get("postgres_url", config.postgres_url)),
        zotero_mcp_endpoints=normalize_endpoint_list(integrations.get("zotero_mcp_endpoints", config.zotero_mcp_endpoints)),
        zotero_linking_enabled=coerce_bool(integrations.get("zotero_linking_enabled"), config.zotero_linking_enabled),
        zotero_linking_on_import=coerce_bool(integrations.get("zotero_linking_on_import"), config.zotero_linking_on_import),
        zotero_extraction_strategy=str(integrations.get("zotero_extraction_strategy", config.zotero_extraction_strategy)).strip() or "rules_first",
        zotero_llm_priority_terms=priority_terms,
        llm_schema_version=str(extraction.get("llm_schema_version", config.llm_schema_version)),
        llm_prompt_profile=str(extraction.get("llm_prompt_profile", config.llm_prompt_profile)),
        llm_cost_limit_usd=float(extraction.get("llm_cost_limit_usd", config.llm_cost_limit_usd)),
        verification_confidence_threshold=float(thresholds.get("verification_confidence_threshold", config.verification_confidence_threshold)),
        evidence_retention_days=int(retention.get("evidence_retention_days", config.evidence_retention_days)),
        cache_retention_days=int(retention.get("cache_retention_days", config.cache_retention_days)),
    )


def jsonish(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return __import__("json").dumps(value)


def section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    return value if isinstance(value, dict) else {}


def coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return parse_bool(str(value), default=default)


def none_if_empty(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return None
    text = str(value).strip()
    return text or None


def normalize_endpoint_list(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        try:
            value = __import__("json").loads(value)
        except Exception:
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    endpoints: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias") or f"zotero-{index + 1}").strip()
        endpoint = {
            "id": str(item.get("id") or alias).strip(),
            "alias": alias,
            "group_name": str(item.get("group_name") or item.get("group") or alias).strip(),
            "url": str(item.get("url") or "").strip(),
            "enabled": coerce_bool(item.get("enabled"), True),
            "priority": int(item.get("priority") or 100),
            "timeout_seconds": float(item.get("timeout_seconds") or 10),
            "headers": item.get("headers") if isinstance(item.get("headers"), dict) else {},
            "write_note_enabled": coerce_bool(item.get("write_note_enabled"), False),
        }
        endpoints.append(endpoint)
    return tuple(endpoints)


def mask_zotero_endpoints(endpoints: tuple[dict[str, Any], ...], *, include_secrets: bool) -> list[dict[str, Any]]:
    masked: list[dict[str, Any]] = []
    for endpoint in endpoints:
        item = dict(endpoint)
        headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
        item["headers"] = headers if include_secrets else {key: mask_secret(str(value)) for key, value in headers.items()}
        masked.append(item)
    return masked


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}***{value[-2:]}"


def merge_hot_config(current: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    rejected = sorted(set(updates) - HOT_CONFIG_SECTIONS)
    if rejected:
        raise ValueError(f"Only hot config sections can be updated: {', '.join(rejected)}")
    merged = {key: value.copy() if isinstance(value, dict) else value for key, value in current.items()}
    for key, value in updates.items():
        if not isinstance(value, dict):
            raise ValueError(f"Config section must be an object: {key}")
        rejected_keys = sorted(set(value) - HOT_CONFIG_KEYS[key])
        if rejected_keys:
            raise ValueError(f"Unsupported config keys in {key}: {', '.join(rejected_keys)}")
        existing = merged.get(key, {})
        if not isinstance(existing, dict):
            existing = {}
        existing.update(value)
        merged[key] = existing
    return {key: merged.get(key, {}) for key in sorted(HOT_CONFIG_SECTIONS)}


def read_config_yaml(path: Path) -> dict[str, Any]:
    lines = [line.split("#", 1)[0].rstrip() for line in path.read_text(encoding="utf-8").splitlines()]
    result: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if not line.startswith(" ") and line.endswith(":"):
            section_name = line[:-1].strip()
            section_value, index = parse_yaml_mapping(lines, index + 1, indent=2)
            result[section_name] = section_value
            continue
        index += 1
    return result


def write_config_yaml(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for section_name in sorted(config):
        section_value = config[section_name]
        if not isinstance(section_value, dict):
            continue
        lines.append(f"{section_name}:")
        lines.extend(format_yaml_mapping(section_value, indent=2))
        lines.append("")
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"null", "None", ""}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def parse_yaml_mapping(lines: list[str], index: int, *, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        current_indent = len(line) - len(line.lstrip(" "))
        if current_indent < indent:
            break
        if current_indent > indent:
            index += 1
            continue
        stripped = line.strip()
        if ":" not in stripped:
            index += 1
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value:
            result[key] = parse_scalar(raw_value)
            index += 1
            continue
        next_index = next_content_index(lines, index + 1)
        if next_index < len(lines) and lines[next_index].startswith(" " * (indent + 2) + "- "):
            result[key], index = parse_yaml_list(lines, index + 1, indent=indent + 2)
        else:
            result[key], index = parse_yaml_mapping(lines, index + 1, indent=indent + 2)
    return result, index


def parse_yaml_list(lines: list[str], index: int, *, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        current_indent = len(line) - len(line.lstrip(" "))
        if current_indent < indent:
            break
        if current_indent != indent or not line.strip().startswith("- "):
            index += 1
            continue
        item_text = line.strip()[2:].strip()
        if item_text and ":" not in item_text:
            result.append(parse_scalar(item_text))
            index += 1
            continue
        item: dict[str, Any] = {}
        if item_text:
            key, raw_value = item_text.split(":", 1)
            item[key.strip()] = parse_scalar(raw_value.strip()) if raw_value.strip() else None
        nested, index = parse_yaml_mapping(lines, index + 1, indent=indent + 2)
        item.update(nested)
        result.append(item)
    return result, index


def next_content_index(lines: list[str], index: int) -> int:
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index


def format_yaml_mapping(mapping: dict[str, Any], *, indent: int) -> list[str]:
    lines: list[str] = []
    prefix = " " * indent
    for key in sorted(mapping):
        value = mapping[key]
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.extend(format_yaml_mapping(value, indent=indent + 2))
        elif isinstance(value, (list, tuple)):
            lines.append(f"{prefix}{key}:")
            lines.extend(format_yaml_list(value, indent=indent + 2))
        else:
            lines.append(f"{prefix}{key}: {format_scalar(value)}")
    return lines


def format_yaml_list(values: Any, *, indent: int) -> list[str]:
    lines: list[str] = []
    prefix = " " * indent
    for item in values:
        if isinstance(item, dict):
            keys = sorted(item)
            if not keys:
                lines.append(f"{prefix}- {{}}")
                continue
            first, *rest = keys
            first_value = item[first]
            if isinstance(first_value, (dict, list, tuple)):
                lines.append(f"{prefix}- {first}:")
                lines.extend(format_yaml_value(first_value, indent=indent + 4))
            else:
                lines.append(f"{prefix}- {first}: {format_scalar(first_value)}")
            for key in rest:
                value = item[key]
                if isinstance(value, (dict, list, tuple)):
                    lines.append(f"{prefix}  {key}:")
                    lines.extend(format_yaml_value(value, indent=indent + 4))
                else:
                    lines.append(f"{prefix}  {key}: {format_scalar(value)}")
        else:
            lines.append(f"{prefix}- {format_scalar(item)}")
    return lines


def format_yaml_value(value: Any, *, indent: int) -> list[str]:
    if isinstance(value, dict):
        return format_yaml_mapping(value, indent=indent)
    if isinstance(value, (list, tuple)):
        return format_yaml_list(value, indent=indent)
    return [" " * indent + format_scalar(value)]


def format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text or any(char in text for char in [":", "#", "\n"]):
        return '"' + text.replace('"', '\\"') + '"'
    return text

def parse_ai_providers(value: Any) -> tuple[AiProvider, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        if not value.strip():
            return ()
        try:
            value = __import__("json").loads(value)
        except Exception:
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    providers: list[AiProvider] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        provider_id = str(item.get("id") or "").strip()
        if not provider_id:
            continue
        providers.append(
            AiProvider(
                id=provider_id,
                name=str(item.get("name") or provider_id).strip(),
                format=str(item.get("format") or "openai_compatible").strip(),
                endpoint=none_if_empty(item.get("endpoint")),
                api_key=none_if_empty(item.get("api_key")),
            )
        )
    return tuple(providers)


def mask_provider_secrets(provider: dict[str, Any]) -> dict[str, Any]:
    masked = provider.copy()
    if masked.get("api_key"):
        masked["api_key"] = mask_secret(masked["api_key"])
    return masked
