from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .parsers import ParsedDocument, TextChunk, detect_file_type, extract_doi, extract_title, normalize_text


@dataclass(frozen=True)
class EndpointResult:
    configured: bool
    status: str
    detail: str | None = None
    payload: Any | None = None


def post_json(url: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-configured trusted endpoint
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-configured trusted endpoint
        body = response.read().decode("utf-8")
        return json.loads(body) if body.strip() else {"status": "ok"}


def test_http_endpoint(endpoint: str | None, *, model: str | None = None) -> EndpointResult:
    if not endpoint:
        return EndpointResult(configured=False, status="unknown", detail="Endpoint is not configured")
    health_url = endpoint.rstrip("/") + "/health"
    try:
        payload = get_json(health_url)
        return EndpointResult(configured=True, status="ok", detail=json.dumps(payload, ensure_ascii=False)[:500], payload=payload)
    except Exception as exc:
        # Some OpenAI-compatible APIs do not expose /health. A configured endpoint is still testable later.
        return EndpointResult(configured=True, status="error", detail=f"{type(exc).__name__}: {exc}; model={model or ''}".strip())


class EmbeddingAdapter:
    def __init__(self, endpoint: str | None, model: str | None):
        self.endpoint = endpoint
        self.model = model or "default"

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.endpoint:
            raise RuntimeError("Embedding endpoint is not configured")
        url = self.endpoint.rstrip("/") + "/embeddings"
        payload = post_json(url, {"model": self.model, "input": texts})
        data = payload.get("data")
        if isinstance(data, list):
            vectors = [item.get("embedding") for item in data if isinstance(item, dict)]
        else:
            vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise RuntimeError("Embedding endpoint returned an unexpected schema")
        return [[float(value) for value in vector] for vector in vectors]


class LLMStructuringAdapter:
    def __init__(self, endpoint: str | None, model: str | None, *, enabled: bool, schema_version: str, prompt_profile: str):
        self.endpoint = endpoint
        self.model = model or "default"
        self.enabled = enabled
        self.schema_version = schema_version
        self.prompt_profile = prompt_profile

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.enabled)

    def structure(self, candidate_text: str, rule_fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self.configured:
            return None
        url = self.endpoint.rstrip("/") + "/chat/completions"
        system = (
            "Return only strict JSON for one synthesis reaction step. Preserve exact chemical strings. "
            "Do not invent missing fields; use null when absent."
        )
        schema_keys = [
            "reaction_name", "substrate_text", "product_text", "reagent_text", "catalyst_text", "solvent_text",
            "temperature", "time", "atmosphere", "yield_text", "scale", "workup", "purification", "confidence",
        ]
        user = json.dumps(
            {"schema_version": self.schema_version, "prompt_profile": self.prompt_profile, "allowed_keys": schema_keys, "rule_fields": rule_fields, "candidate_text": candidate_text},
            ensure_ascii=False,
        )
        payload = post_json(
            url,
            {"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        )
        content = payload.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str):
            raise RuntimeError("LLM endpoint returned no message content")
        return parse_strict_json(content)


class OCRAdapter:
    def __init__(self, endpoint: str | None, model: str | None):
        self.endpoint = endpoint
        self.model = model or "default"

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def ocr_document(self, file_path: str) -> dict[str, Any]:
        if not self.endpoint:
            raise RuntimeError("OCR endpoint is not configured")
        return post_json(self.endpoint.rstrip("/") + "/ocr", {"model": self.model, "file_path": file_path})


class ExternalParserAdapter:
    def __init__(self, endpoint: str | None, model: str | None):
        self.endpoint = endpoint
        self.model = model or "default"

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def parse(self, file_path: str) -> ParsedDocument:
        if not self.endpoint:
            raise RuntimeError("Document parser endpoint is not configured")
        payload = post_json(self.endpoint.rstrip("/") + "/parse", {"model": self.model, "file_path": file_path})
        chunks: list[TextChunk] = []
        for item in payload.get("chunks", []):
            if not isinstance(item, dict):
                continue
            text = normalize_text(str(item.get("text") or ""))
            if text:
                chunks.append(
                    TextChunk(
                        text=text,
                        page_number=item.get("page_number"),
                        parser_name=str(item.get("parser_name") or "external-parser"),
                        parser_version=str(item.get("parser_version") or "external"),
                    )
                )
        full_text = "\n\n".join(chunk.text for chunk in chunks)
        path = Path(file_path)
        return ParsedDocument(
            file_type=str(payload.get("file_type") or detect_file_type(path)),
            title=payload.get("title") or extract_title(full_text),
            doi=payload.get("doi") or extract_doi(full_text),
            chunks=chunks,
        )


class StructureRecognitionAdapter:
    def __init__(self, endpoint: str | None, model: str | None):
        self.endpoint = endpoint
        self.model = model or "default"

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def recognize(self, image_path: str) -> list[dict[str, Any]]:
        if not self.endpoint:
            raise RuntimeError("Structure recognition endpoint is not configured")
        payload = post_json(self.endpoint.rstrip("/") + "/recognize", {"model": self.model, "image_path": image_path})
        results = payload.get("structures", [])
        return results if isinstance(results, list) else []


def parse_strict_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM JSON response must be an object")
    return parsed


def degraded_result(kind: str, exc: Exception) -> dict[str, str]:
    return {"kind": kind, "status": "error", "detail": f"{type(exc).__name__}: {exc}"}
