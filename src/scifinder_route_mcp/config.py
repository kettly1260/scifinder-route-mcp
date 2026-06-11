from __future__ import annotations

import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .auth import UserCredential, mask_secret, parse_users


DEFAULT_SCAN_EXTENSIONS = (".pdf", ".html", ".htm", ".mhtml", ".mht", ".txt")
HOT_CONFIG_SECTIONS = {"server", "security", "ingest", "integrations", "thresholds", "queue", "extraction", "retention"}
HOT_CONFIG_KEYS = {
    "server": {"async_jobs", "max_workers", "storage_backend"},
    "security": {"allow_external_paths", "token", "users"},
    "ingest": {"scan_extensions"},
    "queue": {"backend", "redis_url"},
    "integrations": {
        "llm_endpoint",
        "llm_model",
        "llm_enabled",
        "embedding_endpoint",
        "embedding_model",
        "ocr_endpoint",
        "ocr_model",
        "document_parser_endpoint",
        "document_parser_model",
        "document_parser_fallback",
        "structure_recognition_endpoint",
        "structure_recognition_model",
        "postgres_url",
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
    sample_dir: Path | None = None
    async_jobs: bool = False
    max_workers: int = 1
    allow_external_paths: bool = True
    auth_token: str | None = None
    users: tuple[UserCredential, ...] = ()
    scan_extensions: tuple[str, ...] = DEFAULT_SCAN_EXTENSIONS
    storage_backend: str = "sqlite"
    queue_backend: str = "sqlite"
    llm_endpoint: str | None = None
    llm_model: str | None = None
    llm_enabled: bool = False
    llm_schema_version: str = "reaction_step.v1"
    llm_prompt_profile: str = "strict-reaction-json"
    llm_cost_limit_usd: float = 0.0
    embedding_endpoint: str | None = None
    embedding_model: str | None = None
    ocr_endpoint: str | None = None
    ocr_model: str | None = None
    document_parser_endpoint: str | None = None
    document_parser_model: str | None = None
    parser_fallback: bool = True
    structure_recognition_endpoint: str | None = None
    structure_recognition_model: str | None = None
    postgres_url: str | None = None
    redis_url: str | None = None
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
        sample_dir_value = os.getenv("SCIFINDER_ROUTE_SAMPLE_DIR")
        sample_dir = Path(sample_dir_value).resolve() if sample_dir_value else None
        config = cls(
            data_dir=data_dir,
            inbox_dir=inbox_dir,
            upload_dir=upload_dir,
            evidence_dir=evidence_dir,
            database_path=database_path,
            config_path=config_path,
            sample_dir=sample_dir,
            async_jobs=parse_bool(os.getenv("SCIFINDER_ROUTE_ASYNC_JOBS"), default=False),
            max_workers=max(1, int(os.getenv("SCIFINDER_ROUTE_MAX_WORKERS", "1"))),
            allow_external_paths=parse_bool(os.getenv("SCIFINDER_ROUTE_ALLOW_EXTERNAL_PATHS"), default=True),
            auth_token=os.getenv("SCIFINDER_ROUTE_TOKEN") or None,
            users=parse_users(os.getenv("SCIFINDER_ROUTE_USERS")),
            scan_extensions=parse_extensions(os.getenv("SCIFINDER_ROUTE_SCAN_EXTENSIONS"), DEFAULT_SCAN_EXTENSIONS),
            storage_backend=os.getenv("SCIFINDER_ROUTE_BACKEND", "sqlite").strip().lower() or "sqlite",
            queue_backend=os.getenv("SCIFINDER_ROUTE_QUEUE_BACKEND", "sqlite").strip().lower() or "sqlite",
            llm_endpoint=os.getenv("SCIFINDER_ROUTE_LLM_ENDPOINT") or None,
            llm_model=os.getenv("SCIFINDER_ROUTE_LLM_MODEL") or None,
            llm_enabled=parse_bool(os.getenv("SCIFINDER_ROUTE_LLM_ENABLED"), default=False),
            llm_schema_version=os.getenv("SCIFINDER_ROUTE_LLM_SCHEMA_VERSION", "reaction_step.v1"),
            llm_prompt_profile=os.getenv("SCIFINDER_ROUTE_LLM_PROMPT_PROFILE", "strict-reaction-json"),
            llm_cost_limit_usd=float(os.getenv("SCIFINDER_ROUTE_LLM_COST_LIMIT_USD", "0")),
            embedding_endpoint=os.getenv("SCIFINDER_ROUTE_EMBEDDING_ENDPOINT") or None,
            embedding_model=os.getenv("SCIFINDER_ROUTE_EMBEDDING_MODEL") or None,
            ocr_endpoint=os.getenv("SCIFINDER_ROUTE_OCR_ENDPOINT") or None,
            ocr_model=os.getenv("SCIFINDER_ROUTE_OCR_MODEL") or None,
            document_parser_endpoint=os.getenv("SCIFINDER_ROUTE_DOCUMENT_PARSER_ENDPOINT") or None,
            document_parser_model=os.getenv("SCIFINDER_ROUTE_DOCUMENT_PARSER_MODEL") or None,
            parser_fallback=parse_bool(os.getenv("SCIFINDER_ROUTE_DOCUMENT_PARSER_FALLBACK"), default=True),
            structure_recognition_endpoint=os.getenv("SCIFINDER_ROUTE_STRUCTURE_RECOGNITION_ENDPOINT") or None,
            structure_recognition_model=os.getenv("SCIFINDER_ROUTE_STRUCTURE_RECOGNITION_MODEL") or None,
            postgres_url=os.getenv("SCIFINDER_ROUTE_POSTGRES_URL") or None,
            redis_url=os.getenv("SCIFINDER_ROUTE_REDIS_URL") or None,
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

    def apply_file_overrides(self) -> "AppConfig":
        if not self.config_path.exists():
            return self
        raw = read_config_yaml(self.config_path)
        return apply_config_overrides(self, raw)

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
            },
            "ingest": {
                "scan_extensions": list(self.scan_extensions),
            },
            "queue": {
                "backend": self.queue_backend,
                "redis_url": redis_url,
            },
            "integrations": {
                "llm_endpoint": self.llm_endpoint,
                "llm_model": self.llm_model,
                "llm_enabled": self.llm_enabled,
                "embedding_endpoint": self.embedding_endpoint,
                "embedding_model": self.embedding_model,
                "ocr_endpoint": self.ocr_endpoint,
                "ocr_model": self.ocr_model,
                "document_parser_endpoint": self.document_parser_endpoint,
                "document_parser_model": self.document_parser_model,
                "document_parser_fallback": self.parser_fallback,
                "structure_recognition_endpoint": self.structure_recognition_endpoint,
                "structure_recognition_model": self.structure_recognition_model,
                "postgres_url": postgres_url,
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
            current = read_config_yaml(self.config_path) if self.config_path.exists() else self.hot_config(include_secrets=True)
            merged = merge_hot_config(current, updates)
            write_config_yaml(self.config_path, merged)

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
        if self.verification_confidence_threshold < 0 or self.verification_confidence_threshold > 1:
            warnings.append("thresholds.verification_confidence_threshold must be between 0 and 1")
        for name, endpoint in {
            "integrations.llm_endpoint": self.llm_endpoint,
            "integrations.embedding_endpoint": self.embedding_endpoint,
            "integrations.ocr_endpoint": self.ocr_endpoint,
            "integrations.document_parser_endpoint": self.document_parser_endpoint,
            "integrations.structure_recognition_endpoint": self.structure_recognition_endpoint,
        }.items():
            if endpoint and not endpoint.startswith(("http://", "https://")):
                warnings.append(f"{name} should start with http:// or https://")
        for model_name, endpoint_name, model, endpoint in [
            ("integrations.llm_model", "integrations.llm_endpoint", self.llm_model, self.llm_endpoint),
            ("integrations.embedding_model", "integrations.embedding_endpoint", self.embedding_model, self.embedding_endpoint),
            ("integrations.ocr_model", "integrations.ocr_endpoint", self.ocr_model, self.ocr_endpoint),
            (
                "integrations.document_parser_model",
                "integrations.document_parser_endpoint",
                self.document_parser_model,
                self.document_parser_endpoint,
            ),
            (
                "integrations.structure_recognition_model",
                "integrations.structure_recognition_endpoint",
                self.structure_recognition_model,
                self.structure_recognition_endpoint,
            ),
        ]:
            if model and not endpoint:
                warnings.append(f"{model_name} is set but {endpoint_name} is empty")
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
    return replace(
        config,
        async_jobs=coerce_bool(server.get("async_jobs"), config.async_jobs),
        max_workers=max(1, int(server.get("max_workers", config.max_workers))),
        storage_backend=str(server.get("storage_backend", config.storage_backend)).strip().lower() or "sqlite",
        allow_external_paths=coerce_bool(security.get("allow_external_paths"), config.allow_external_paths),
        auth_token=none_if_empty(security.get("token", config.auth_token)),
        users=parse_users(jsonish(security.get("users"))) if "users" in security else config.users,
        scan_extensions=scan_extensions,
        queue_backend=str(queue.get("backend", config.queue_backend)).strip().lower() or "sqlite",
        redis_url=none_if_empty(queue.get("redis_url", config.redis_url)),
        llm_endpoint=none_if_empty(integrations.get("llm_endpoint", config.llm_endpoint)),
        llm_model=none_if_empty(integrations.get("llm_model", config.llm_model)),
        llm_enabled=coerce_bool(integrations.get("llm_enabled"), config.llm_enabled),
        embedding_endpoint=none_if_empty(integrations.get("embedding_endpoint", config.embedding_endpoint)),
        embedding_model=none_if_empty(integrations.get("embedding_model", config.embedding_model)),
        ocr_endpoint=none_if_empty(integrations.get("ocr_endpoint", config.ocr_endpoint)),
        ocr_model=none_if_empty(integrations.get("ocr_model", config.ocr_model)),
        document_parser_endpoint=none_if_empty(integrations.get("document_parser_endpoint", config.document_parser_endpoint)),
        document_parser_model=none_if_empty(integrations.get("document_parser_model", config.document_parser_model)),
        parser_fallback=coerce_bool(integrations.get("document_parser_fallback"), config.parser_fallback),
        structure_recognition_endpoint=none_if_empty(integrations.get("structure_recognition_endpoint", config.structure_recognition_endpoint)),
        structure_recognition_model=none_if_empty(integrations.get("structure_recognition_model", config.structure_recognition_model)),
        postgres_url=none_if_empty(integrations.get("postgres_url", config.postgres_url)),
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
    result: dict[str, Any] = {}
    current_section: str | None = None
    current_list_key: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            result.setdefault(current_section, {})
            current_list_key = None
            continue
        if current_section is None:
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_list_key:
            section_value = result.setdefault(current_section, {})
            section_value.setdefault(current_list_key, []).append(parse_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        section_value = result.setdefault(current_section, {})
        if raw_value == "":
            section_value[key] = []
            current_list_key = key
        else:
            section_value[key] = parse_scalar(raw_value)
            current_list_key = None
    return result


def write_config_yaml(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for section_name in sorted(config):
        section_value = config[section_name]
        if not isinstance(section_value, dict):
            continue
        lines.append(f"{section_name}:")
        for key in sorted(section_value):
            value = section_value[key]
            if isinstance(value, (list, tuple)):
                lines.append(f"  {key}:")
                for item in value:
                    lines.append(f"    - {format_scalar(item)}")
            else:
                lines.append(f"  {key}: {format_scalar(value)}")
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
