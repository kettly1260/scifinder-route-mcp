from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .parsers import ParsedDocument, TextChunk, detect_file_type, extract_doi, extract_title, normalize_text


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EndpointResult:
    configured: bool
    status: str
    detail: str | None = None
    payload: Any | None = None


def post_json(url: str, payload: dict[str, Any], *, timeout: float = 30.0, headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-configured trusted endpoint
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, *, timeout: float = 10.0, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-configured trusted endpoint
        body = response.read().decode("utf-8")
        return json.loads(body) if body.strip() else {"status": "ok"}


def auth_headers(provider: str = "openai_compatible", api_key: str | None = None) -> dict[str, str]:
    if not api_key:
        return {}
    if provider == "claude":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {api_key}"}


def _display_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(parsed._replace(query="", fragment=""))


def _http_error_detail(exc: Exception, *, url: str, model: str | None = None) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        parts = [f"HTTPError: HTTP Error {exc.code}: {exc.reason}", f"url={_display_url(url)}"]
        if model:
            parts.append(f"model={model}")
        if body:
            parts.append(f"body={body[:500]}")
        return "; ".join(parts)
    parts = [f"{type(exc).__name__}: {exc}", f"url={_display_url(url)}"]
    if model:
        parts.append(f"model={model}")
    return "; ".join(parts)


def _resource_url(endpoint: str, resource: str, known_suffixes: tuple[str, ...]) -> str:
    base = endpoint.rstrip("/")
    for suffix in known_suffixes:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base + "/" + resource.lstrip("/")


def _openai_resource_url(endpoint: str, resource: str) -> str:
    return _resource_url(endpoint, resource, ("/chat/completions", "/responses", "/embeddings", "/models"))


def _integration_resource_url(endpoint: str, resource: str) -> str:
    return _resource_url(endpoint, resource, ("/health", "/ocr", "/parse", "/recognize", "/rerank"))


def _mineru_resource_url(endpoint: str, resource: str) -> str:
    return _resource_url(endpoint, resource, ("/file_parse", "/tasks", "/task", "/health"))


def _gemini_base_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith("/models"):
        return base[: -len("/models")]
    return base


def _looks_like_paddleocr_job_endpoint(endpoint: str | None, model: str | None = None) -> bool:
    value = (endpoint or "").rstrip("/").lower()
    return value.endswith("/ocr/jobs") or "paddleocr" in value or (model or "").lower().startswith("paddleocr")


def _extract_mineru_text(payload: dict[str, Any]) -> str:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        candidates.extend(
            [
                payload.get("text"),
                payload.get("markdown"),
                payload.get("md"),
                payload.get("md_content"),
                payload.get("content"),
            ]
        )
        data = payload.get("data")
        if isinstance(data, dict):
            candidates.extend(
                [
                    data.get("text"),
                    data.get("markdown"),
                    data.get("md"),
                    data.get("md_content"),
                    data.get("content"),
                ]
            )
            if isinstance(data.get("pages"), list):
                for page in data["pages"]:
                    if isinstance(page, dict):
                        candidates.extend(
                            [
                                page.get("text"),
                                page.get("markdown"),
                                page.get("md"),
                                page.get("content"),
                            ]
                        )
        pages = payload.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if isinstance(page, dict):
                    candidates.extend([page.get("text"), page.get("markdown"), page.get("md"), page.get("content")])
    texts = [normalize_text(str(item)) for item in candidates if isinstance(item, str) and item.strip()]
    return "\n\n".join(text for text in texts if text)


def _test_llm_endpoint(endpoint: str, *, model: str | None, provider: str, api_key: str | None) -> EndpointResult:
    if not model:
        return list_http_models(endpoint, provider=provider, api_key=api_key, kind="llm", model=model)
    base = endpoint.rstrip("/")
    if provider == "openai_responses":
        url = _openai_resource_url(base, "responses")
        payload = {"model": model, "input": "Reply with OK."}
        response = post_json(url, payload, timeout=30, headers=auth_headers(provider, api_key))
    elif provider == "gemini":
        url = _gemini_base_url(base) + f"/models/{model}:generateContent"
        if api_key:
            url += "?" + urllib.parse.urlencode({"key": api_key})
        response = post_json(url, {"contents": [{"role": "user", "parts": [{"text": "Reply with OK."}]}]}, timeout=30, headers={})
    elif provider == "claude":
        url = _resource_url(base, "messages", ("/messages",))
        response = post_json(url, {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "Reply with OK."}]}, timeout=30, headers=auth_headers(provider, api_key))
    else:
        url = _openai_resource_url(base, "chat/completions")
        response = post_json(url, {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}]}, timeout=30, headers=auth_headers(provider, api_key))
    return EndpointResult(configured=True, status="ok", detail=f"LLM endpoint accepted a test request at {_display_url(url)}", payload=response)


def test_http_endpoint(endpoint: str | None, *, model: str | None = None, provider: str = "openai_compatible", api_key: str | None = None, kind: str = "generic") -> EndpointResult:
    if not endpoint:
        return EndpointResult(configured=False, status="unknown", detail="Endpoint is not configured")
    if kind == "ocr" and provider == "paddleocr_vl":
        return EndpointResult(configured=True, status="ok", detail="PaddleOCR-VL job endpoint configured. This provider has no lightweight health endpoint; submit a document to verify job execution.")
    if kind in {"ocr", "document_parser"} and provider == "mineru":
        return EndpointResult(configured=True, status="ok", detail="MinerU document parser endpoint configured. This provider may not expose /health; submit a document to verify file_parse execution.")
    if kind in {"document_parser", "structure_recognition"} and _looks_like_paddleocr_job_endpoint(endpoint, model):
        return EndpointResult(configured=True, status="ok", detail="PaddleOCR-VL job endpoint configured. It does not expose /health or /models; runtime calls will submit OCR jobs and use configured fallbacks where available.")
    if kind == "extraction":
        try:
            return _test_llm_endpoint(endpoint, model=model, provider=provider, api_key=api_key)
        except Exception as exc:
            url = model_list_url(endpoint, provider, api_key) if not model else endpoint
            return EndpointResult(configured=True, status="error", detail=_http_error_detail(exc, url=url, model=model))
    if kind == "embedding":
        embeddings_url = _openai_resource_url(endpoint, "embeddings")
        try:
            payload = post_json(embeddings_url, {"model": model or "default", "input": ["ping"]}, timeout=30, headers=auth_headers("openai_compatible", api_key))
            return EndpointResult(configured=True, status="ok", detail=f"Embedding endpoint accepted a test request at {_display_url(embeddings_url)}", payload=payload)
        except Exception as exc:
            return EndpointResult(configured=True, status="error", detail=_http_error_detail(exc, url=embeddings_url, model=model))
    if kind == "reranker":
        rerank_url = _integration_resource_url(endpoint, "rerank")
        try:
            payload = post_json(rerank_url, {"model": model or "default", "query": "ping", "texts": ["ping"], "documents": ["ping"]}, timeout=30, headers=auth_headers("openai_compatible", api_key))
            return EndpointResult(configured=True, status="ok", detail=f"Reranker endpoint accepted a test request at {_display_url(rerank_url)}", payload=payload)
        except Exception as exc:
            return EndpointResult(configured=True, status="error", detail=_http_error_detail(exc, url=rerank_url, model=model))
    health_url = _integration_resource_url(endpoint, "health")
    try:
        payload = get_json(health_url, headers=auth_headers(provider, api_key))
        return EndpointResult(configured=True, status="ok", detail=json.dumps(payload, ensure_ascii=False)[:500], payload=payload)
    except Exception as exc:
        # Some OpenAI-compatible APIs do not expose /health. A configured endpoint is still testable later.
        return EndpointResult(configured=True, status="error", detail=_http_error_detail(exc, url=health_url, model=model))


def model_list_url(endpoint: str, provider: str, api_key: str | None = None) -> str:
    base = endpoint.rstrip("/")
    if provider == "gemini":
        url = _gemini_base_url(base) + "/models"
        return url + (("?" + urllib.parse.urlencode({"key": api_key})) if api_key else "")
    return _openai_resource_url(base, "models")


def list_http_models(endpoint: str | None, *, provider: str = "openai_compatible", api_key: str | None = None, kind: str = "generic", model: str | None = None, models_endpoint: str | None = None) -> EndpointResult:
    if not endpoint:
        return EndpointResult(configured=False, status="unknown", detail="Endpoint is not configured", payload={"models": []})
    if provider in {"paddleocr_vl", "mineru"} or (kind in {"ocr", "document_parser"} and _looks_like_paddleocr_job_endpoint(endpoint, model)):
        models = [model] if model else []
        return EndpointResult(configured=True, status="ok", detail="Provider does not expose a model-list endpoint; using the configured model value.", payload={"models": models})
    if kind in {"document_parser", "structure_recognition"}:
        models = [model] if model else []
        return EndpointResult(configured=True, status="ok", detail="This integration type does not define a standard model-list endpoint; using the configured model value.", payload={"models": models})
    models_url = models_endpoint if models_endpoint else model_list_url(endpoint, provider, api_key)
    try:
        payload = get_json(models_url, headers=auth_headers(provider, api_key))
    except Exception as exc:
        LOGGER.warning("Integration model listing failed provider=%s kind=%s url=%s detail=%s", provider, kind, _display_url(models_url), exc)
        return EndpointResult(configured=True, status="error", detail=_http_error_detail(exc, url=models_url), payload={"models": []})
    raw_models = payload.get("data") if isinstance(payload, dict) else None
    if raw_models is None and isinstance(payload, dict):
        raw_models = payload.get("models")
    models: list[str] = []
    if isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, dict):
                model_id = item.get("id") or item.get("name") or item.get("model")
                if provider == "gemini" and isinstance(model_id, str) and model_id.startswith("models/"):
                    model_id = model_id.split("/", 1)[1]
            else:
                model_id = item
            if model_id:
                models.append(str(model_id))
    models = sorted(dict.fromkeys(models))
    return EndpointResult(configured=True, status="ok", detail=f"Loaded {len(models)} models", payload={"models": models})


class EmbeddingAdapter:
    def __init__(self, endpoint: str | None, model: str | None, api_key: str | None = None):
        self.endpoint = endpoint
        self.model = model or "default"
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.endpoint:
            raise RuntimeError("Embedding endpoint is not configured")
        url = _openai_resource_url(self.endpoint, "embeddings")
        payload = post_json(url, {"model": self.model, "input": texts}, headers=auth_headers("openai_compatible", self.api_key))
        data = payload.get("data")
        if isinstance(data, list):
            vectors = [item.get("embedding") for item in data if isinstance(item, dict)]
        else:
            vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise RuntimeError("Embedding endpoint returned an unexpected schema")
        return [[float(value) for value in vector] for vector in vectors]


class LLMStructuringAdapter:
    def __init__(self, endpoint: str | None, model: str | None, *, enabled: bool, schema_version: str, prompt_profile: str, provider: str = "openai_compatible", api_key: str | None = None):
        self.endpoint = endpoint
        self.model = model or "default"
        self.enabled = enabled
        self.schema_version = schema_version
        self.prompt_profile = prompt_profile
        self.provider = provider
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.enabled)

    def structure(self, candidate_text: str, rule_fields: dict[str, Any]) -> dict[str, Any] | None:
        if not self.configured:
            return None
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
        content = self._complete(system, user)
        if not isinstance(content, str):
            raise RuntimeError("LLM endpoint returned no message content")
        return parse_strict_json(content)

    def review_reaction_evidence(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.configured:
            return None
        system = (
            "Return only strict JSON for evidence review of one chemical reaction. "
            "Use only the supplied RDF/PDF evidence. Do not invent missing chemistry. "
            "Treat RDF as structured SciFinder evidence, paper SI as stronger procedure evidence, "
            "and patent text as patent reaction-process evidence. "
            "If evidence is incomplete or conflicting, recommend needs_review."
        )
        allowed_keys = [
            "recommendation",
            "confidence",
            "extracted_fields",
            "agreement",
            "conflict_flags",
            "rationale",
            "cited_evidence",
        ]
        user = json.dumps(
            {
                "schema_version": "reaction_evidence_review.v1",
                "prompt_profile": self.prompt_profile,
                "allowed_keys": allowed_keys,
                "recommendations": ["confirm", "needs_review", "reject"],
                "payload": payload,
            },
            ensure_ascii=False,
        )
        content = self._complete(system, user)
        if not isinstance(content, str):
            raise RuntimeError("LLM endpoint returned no message content")
        return parse_strict_json(content)

    def _complete(self, system: str, user: str) -> str | None:
        base = self.endpoint.rstrip("/") if self.endpoint else ""
        if self.provider == "openai_responses":
            payload = post_json(_openai_resource_url(base, "responses"), {"model": self.model, "input": [{"role": "system", "content": system}, {"role": "user", "content": user}], "temperature": 0}, headers=auth_headers(self.provider, self.api_key))
            if isinstance(payload.get("output_text"), str):
                return payload["output_text"]
            output = payload.get("output") or []
            for item in output if isinstance(output, list) else []:
                for content in item.get("content", []) if isinstance(item, dict) else []:
                    if isinstance(content, dict) and isinstance(content.get("text"), str):
                        return content["text"]
            return None
        if self.provider == "gemini":
            url = _gemini_base_url(base) + f"/models/{self.model}:generateContent"
            if self.api_key:
                url += "?" + urllib.parse.urlencode({"key": self.api_key})
            payload = post_json(url, {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": {"temperature": 0}}, headers={})
            candidates = payload.get("candidates") or []
            parts = candidates[0].get("content", {}).get("parts", []) if candidates and isinstance(candidates[0], dict) else []
            return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)) or None
        if self.provider == "claude":
            payload = post_json(_resource_url(base, "messages", ("/messages",)), {"model": self.model, "system": system, "max_tokens": 2048, "temperature": 0, "messages": [{"role": "user", "content": user}]}, headers=auth_headers(self.provider, self.api_key))
            content = payload.get("content") or []
            return "".join(str(item.get("text") or "") for item in content if isinstance(item, dict)) or None
        payload = post_json(_openai_resource_url(base, "chat/completions"), {"model": self.model, "temperature": 0, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, headers=auth_headers(self.provider, self.api_key))
        return payload.get("choices", [{}])[0].get("message", {}).get("content")


class OCRAdapter:
    def __init__(self, endpoint: str | None, model: str | None, api_key: str | None = None, provider: str = "generic"):
        self.endpoint = endpoint
        self.model = model or "default"
        self.api_key = api_key
        self.provider = provider

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def ocr_document(self, file_path: str) -> dict[str, Any]:
        if not self.endpoint:
            raise RuntimeError("OCR endpoint is not configured")
        if self.provider == "paddleocr_vl":
            return self._ocr_paddleocr_vl(file_path)
        if self.provider == "mineru":
            return self._ocr_mineru(file_path)
        return post_json(_integration_resource_url(self.endpoint, "ocr"), {"model": self.model, "file_path": file_path}, headers=auth_headers("openai_compatible", self.api_key))

    def _ocr_mineru(self, file_path: str) -> dict[str, Any]:
        endpoint = _mineru_resource_url(self.endpoint or "", "file_parse")
        fields = {
            "return_md": "true",
            "return_content_list": "false",
            "return_layout": "false",
            "parse_method": "auto",
        }
        if self.model and self.model != "default":
            fields["model"] = self.model
        payload = post_multipart(endpoint, file_path, fields, headers=auth_headers("openai_compatible", self.api_key), timeout=300)
        text = _extract_mineru_text(payload)
        if not text:
            detail = payload.get("msg") or payload.get("message") or payload.get("error") or "MinerU endpoint returned no markdown/text"
            raise RuntimeError(str(detail))
        return {"text": text, "confidence": None, "provider": "mineru", "payload": payload}

    def _ocr_paddleocr_vl(self, file_path: str) -> dict[str, Any]:
        endpoint = self.endpoint.rstrip("/")
        headers = {"Authorization": f"bearer {self.api_key or ''}"}
        payload = {"model": self.model, "optionalPayload": {"useDocOrientationClassify": False, "useDocUnwarping": False, "useChartRecognition": False}}
        if file_path.startswith(("http://", "https://")):
            job = post_json(endpoint, {"fileUrl": file_path, **payload}, headers={**headers, "Content-Type": "application/json"})
        else:
            job = post_multipart(endpoint, file_path, {"model": self.model, "optionalPayload": json.dumps(payload["optionalPayload"])}, headers=headers)
        job_id = ((job.get("data") or {}).get("jobId") if isinstance(job, dict) else None)
        if not job_id:
            raise RuntimeError("PaddleOCR endpoint returned no jobId")
        result_url = ""
        for _ in range(120):
            state_payload = get_json(f"{endpoint}/{job_id}", headers=headers, timeout=30)
            data = state_payload.get("data") or {}
            state = data.get("state")
            if state == "done":
                result_url = ((data.get("resultUrl") or {}).get("jsonUrl") or "")
                break
            if state == "failed":
                raise RuntimeError(str(data.get("errorMsg") or "PaddleOCR job failed"))
            time.sleep(5)
        if not result_url:
            raise RuntimeError("PaddleOCR job timed out before resultUrl was available")
        with urllib.request.urlopen(result_url, timeout=60) as response:  # noqa: S310 - provider returned result URL
            lines = response.read().decode("utf-8").strip().splitlines()
        texts: list[str] = []
        for line in lines:
            item = json.loads(line)
            for result in ((item.get("result") or {}).get("layoutParsingResults") or []):
                markdown = result.get("markdown") or {}
                if markdown.get("text"):
                    texts.append(str(markdown["text"]))
        return {"text": "\n\n".join(texts), "confidence": None, "provider": "paddleocr_vl", "job_id": job_id}


def post_multipart(url: str, file_path: str, fields: dict[str, str], *, headers: dict[str, str] | None = None, timeout: float = 60.0) -> dict[str, Any]:
    boundary = f"----scifinderRoute{int(time.time() * 1000)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    filename = os.path.basename(file_path)
    chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode("utf-8"))
    with open(file_path, "rb") as handle:
        chunks.append(handle.read())
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    request_headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", **(headers or {})}
    request = urllib.request.Request(url, data=b"".join(chunks), headers=request_headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-configured trusted endpoint
        return json.loads(response.read().decode("utf-8"))


class ExternalParserAdapter:
    def __init__(self, endpoint: str | None, model: str | None, api_key: str | None = None, provider: str = "generic"):
        self.endpoint = endpoint
        self.model = model or "default"
        self.api_key = api_key
        self.provider = provider

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def parse(self, file_path: str) -> ParsedDocument:
        if not self.endpoint:
            raise RuntimeError("Document parser endpoint is not configured")
        if self.provider == "mineru":
            return self._parse_mineru(file_path)
        if self.provider == "paddleocr_vl":
            payload = OCRAdapter(self.endpoint, self.model, self.api_key, provider=self.provider).ocr_document(file_path)
            text = normalize_text(str(payload.get("text") or ""))
            if not text:
                raise RuntimeError("PaddleOCR parser endpoint returned no text")
            path = Path(file_path)
            return ParsedDocument(
                file_type=detect_file_type(path),
                title=extract_title(text),
                doi=extract_doi(text),
                chunks=[TextChunk(text=text, page_number=None, parser_name="paddleocr_vl", parser_version=str(self.model or "external"))],
            )
        payload = post_json(_integration_resource_url(self.endpoint, "parse"), {"model": self.model, "file_path": file_path}, headers=auth_headers(self.provider, self.api_key))
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

    def _parse_mineru(self, file_path: str) -> ParsedDocument:
        endpoint = _mineru_resource_url(self.endpoint or "", "file_parse")
        fields = {
            "return_md": "true",
            "return_content_list": "false",
            "return_layout": "false",
            "parse_method": "auto",
        }
        if self.model and self.model != "default":
            fields["model"] = self.model
        payload = post_multipart(endpoint, file_path, fields, headers=auth_headers("openai_compatible", self.api_key), timeout=300)
        text = _extract_mineru_text(payload)
        if not text:
            detail = payload.get("msg") or payload.get("message") or payload.get("error") or "MinerU endpoint returned no markdown/text"
            raise RuntimeError(str(detail))
        path = Path(file_path)
        return ParsedDocument(
            file_type=str(payload.get("file_type") or detect_file_type(path)),
            title=payload.get("title") or extract_title(text),
            doi=payload.get("doi") or extract_doi(text),
            chunks=[TextChunk(text=text, page_number=None, parser_name="mineru", parser_version=str(self.model or "external"))],
        )


class StructureRecognitionAdapter:
    def __init__(self, endpoint: str | None, model: str | None, api_key: str | None = None, provider: str = "generic"):
        self.endpoint = endpoint
        self.model = model or "default"
        self.api_key = api_key
        self.provider = provider

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def recognize(self, image_path: str) -> list[dict[str, Any]]:
        if not self.endpoint:
            raise RuntimeError("Structure recognition endpoint is not configured")
        
        if self.provider != "generic":
            return self._recognize_vision_llm(image_path)
            
        payload = post_json(_integration_resource_url(self.endpoint, "recognize"), {"model": self.model, "image_path": image_path}, headers=auth_headers("openai_compatible", self.api_key))
        results = payload.get("structures", [])
        return results if isinstance(results, list) else []

    def _recognize_vision_llm(self, image_path: str) -> list[dict[str, Any]]:
        import base64
        with open(image_path, "rb") as f:
            b64_image = base64.b64encode(f.read()).decode("utf-8")
        
        system = "You are a chemical structure extractor. Return ONLY strict JSON in this exact format: {\"structures\": [{\"smiles\": \"...\"}]}. Do not include markdown blocks, text, or explanations. If no structures are found, return {\"structures\": []}."
        user = "Extract all chemical structures from this image and return them as a list of SMILES."
        
        base = self.endpoint.rstrip("/")
        content: str | None = None
        if self.provider == "gemini":
            url = _gemini_base_url(base) + f"/models/{self.model}:generateContent"
            if self.api_key:
                url += "?" + urllib.parse.urlencode({"key": self.api_key})
            payload = post_json(url, {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}, {"inlineData": {"mimeType": "image/jpeg", "data": b64_image}}]}]}, headers={})
            candidates = payload.get("candidates") or []
            parts = candidates[0].get("content", {}).get("parts", []) if candidates and isinstance(candidates[0], dict) else []
            content = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)) or None
        elif self.provider == "claude":
            payload = post_json(_resource_url(base, "messages", ("/messages",)), {"model": self.model, "system": system, "max_tokens": 2048, "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_image}}, {"type": "text", "text": user}]}]}, headers=auth_headers(self.provider, self.api_key))
            parts = payload.get("content") or []
            content = "".join(str(item.get("text") or "") for item in parts if isinstance(item, dict)) or None
        else:
            payload = post_json(_openai_resource_url(base, "chat/completions"), {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}]}]}, headers=auth_headers(self.provider, self.api_key))
            content = payload.get("choices", [{}])[0].get("message", {}).get("content")
            
        if not content:
            return []
            
        try:
            parsed = parse_strict_json(content)
            results = parsed.get("structures", [])
            return results if isinstance(results, list) else []
        except Exception:
            return []


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


class RerankerAdapter:
    def __init__(self, endpoint: str | None, model: str | None, api_key: str | None = None):
        self.endpoint = endpoint
        self.model = model or "default"
        self.api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        if not self.endpoint:
            raise RuntimeError("Reranker endpoint is not configured")
        
        payload = post_json(
            _integration_resource_url(self.endpoint, "rerank"),
            {"model": self.model, "query": query, "texts": texts, "documents": texts},
            headers=auth_headers("openai_compatible", self.api_key)
        )
        
        results = payload.get("results", [])
        scores = [0.0] * len(texts)
        for r in results:
            idx = r.get("index")
            if isinstance(idx, int) and 0 <= idx < len(texts):
                score = r.get("relevance_score")
                if score is None:
                    score = r.get("score", 0.0)
                scores[idx] = float(score)
        
        return scores
