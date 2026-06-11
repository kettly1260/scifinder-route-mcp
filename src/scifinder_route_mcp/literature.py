from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from difflib import SequenceMatcher
from typing import Any


DIFF_FIELDS = [
    "yield_text",
    "reagent_text",
    "catalyst_text",
    "solvent_text",
    "temperature",
    "time",
    "scale",
    "atmosphere",
    "workup",
    "purification",
]


class ZoteroMcpClient:
    def __init__(self, endpoint: dict[str, Any]):
        self.endpoint = endpoint
        self.url = str(endpoint.get("url") or "").strip()
        self.timeout = float(endpoint.get("timeout_seconds") or 10)
        self.headers = endpoint.get("headers") if isinstance(endpoint.get("headers"), dict) else {}

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def test(self) -> dict[str, Any]:
        if not self.configured:
            return {"configured": False, "status": "unknown", "detail": "Zotero MCP URL is not configured"}
        started = time.perf_counter()
        try:
            self.call("semantic_status", {})
        except Exception as exc:
            return {"configured": True, "status": "error", "detail": f"{type(exc).__name__}: {exc}", "latency_ms": int((time.perf_counter() - started) * 1000)}
        return {"configured": True, "status": "ok", "detail": "Zotero MCP endpoint responded", "latency_ms": int((time.perf_counter() - started) * 1000)}

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": f"scifinder-{int(time.time() * 1000)}", "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        headers = {"Content-Type": "application/json", "Accept": "application/json", **{str(k): str(v) for k, v in self.headers.items() if v is not None}}
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - user-configured LAN/VPN endpoint
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(json.dumps(data["error"], ensure_ascii=False))
        result = data.get("result") if isinstance(data, dict) else data
        if isinstance(result, dict) and "content" in result:
            return parse_mcp_content(result["content"])
        return result

    def search_library(self, *, doi: str = "", query: str = "", limit: int = 5) -> list[dict[str, Any]]:
        if doi:
            args = {"q": doi, "mode": "preview", "relevanceScoring": True, "sort": "relevance", "limit": limit}
        else:
            args = {"q": query, "mode": "preview", "relevanceScoring": True, "sort": "relevance", "limit": limit}
        return normalize_items(self.call("search_library", args))

    def get_item_details(self, item_key: str) -> dict[str, Any]:
        result = self.call("get_item_details", {"itemKey": item_key, "mode": "standard"})
        if isinstance(result, dict):
            return result
        return {}

    def get_item_abstract(self, item_key: str) -> str:
        try:
            result = self.call("get_item_abstract", {"itemKey": item_key, "format": "text"})
        except Exception:
            return ""
        return text_from_any(result)

    def search_fulltext(self, item_key: str, query: str) -> str:
        if not query.strip():
            return ""
        try:
            result = self.call("search_fulltext", {"q": query[:200], "itemKeys": [item_key], "mode": "preview", "maxResults": 3})
        except Exception:
            return ""
        return trim_text(text_from_any(result), 1600)

    def write_note(self, item_key: str, content: str) -> Any:
        return self.call("write_note", {"action": "create", "parentKey": item_key, "content": content, "tags": ["scifinder-linked"]})


def parse_mcp_content(content: Any) -> Any:
    if isinstance(content, list) and content:
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    texts.append(text)
        return "\n".join(texts)
    return content


def normalize_items(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, dict):
        for key in ("items", "results", "data"):
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if result.get("itemKey") or result.get("key"):
            return [result]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    return []


def item_key(item: dict[str, Any]) -> str:
    return str(item.get("itemKey") or item.get("key") or item.get("id") or "")


def item_title(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("name") or item.get("data", {}).get("title") or "")


def item_doi(item: dict[str, Any]) -> str:
    return normalize_doi(str(item.get("DOI") or item.get("doi") or item.get("data", {}).get("DOI") or item.get("data", {}).get("doi") or ""))


def item_year(item: dict[str, Any]) -> str | None:
    raw = str(item.get("year") or item.get("date") or item.get("data", {}).get("date") or "")
    match = re.search(r"\b(19|20)\d{2}\b", raw)
    return match.group(0) if match else None


def normalize_doi(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = text.removeprefix("doi:").strip()
    return text.rstrip(".,; ")


def title_similarity(a: str, b: str) -> float:
    left = normalize_text(a)
    right = normalize_text(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def build_query(step: dict[str, Any], document: dict[str, Any] | None = None) -> str:
    values = [
        (document or {}).get("doi"),
        (document or {}).get("title"),
        step.get("reaction_name"),
        step.get("product_text"),
        step.get("substrate_text"),
        step.get("reagent_text"),
        step.get("solvent_text"),
    ]
    return " ".join(str(value) for value in values if value)[:300]


def extract_method_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    if not text:
        return fields
    yield_match = re.search(r"(?:yield(?:ed)?|give|gave|afford(?:ed)?)\D{0,40}((?:\d{1,3}(?:\.\d+)?)\s*%)", text, re.I)
    if yield_match:
        fields["yield_text"] = yield_match.group(1)
    temp_match = re.search(r"(-?\d{1,3}\s*(?:°\s*C|deg\s*C|C\b|K\b))", text, re.I)
    if temp_match:
        fields["temperature"] = temp_match.group(1)
    time_match = re.search(r"(\d+(?:\.\d+)?\s*(?:h|hr|hrs|hour|hours|min|minutes))", text, re.I)
    if time_match:
        fields["time"] = time_match.group(1)
    for field, words in {
        "solvent_text": ["dichloromethane", "methanol", "ethanol", "toluene", "thf", "dmf", "dmso", "acetonitrile", "water"],
        "reagent_text": ["triethylamine", "hydrazine", "sodium", "potassium", "lithium", "borohydride", "chloride", "bromide"],
        "purification": ["column chromatography", "recrystallization", "flash chromatography", "distillation"],
    }.items():
        hits = [word for word in words if re.search(rf"\b{re.escape(word)}\b", text, re.I)]
        if hits:
            fields[field] = ", ".join(dict.fromkeys(hits))
    return fields


def diff_reaction_fields(step: dict[str, Any], literature_fields: dict[str, Any]) -> dict[str, dict[str, Any]]:
    diff: dict[str, dict[str, Any]] = {}
    for field in DIFF_FIELDS:
        scifinder_value = compact(step.get(field))
        literature_value = compact(literature_fields.get(field))
        if scifinder_value and literature_value:
            similarity = title_similarity(scifinder_value, literature_value)
            status = "same" if similarity >= 0.8 or scifinder_value.lower() in literature_value.lower() or literature_value.lower() in scifinder_value.lower() else "conflict"
        elif not scifinder_value and literature_value:
            status = "missing_in_scifinder"
        elif scifinder_value and not literature_value:
            status = "missing_in_literature"
        else:
            continue
        if scifinder_value and literature_value and len(literature_value) > len(scifinder_value) * 1.5 and status == "same":
            status = "more_detail_in_literature"
        diff[field] = {"status": status, "scifinder": scifinder_value, "literature": literature_value}
    return diff


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def trim_text(text: str, limit: int) -> str:
    text = compact(text)
    return text[:limit].rstrip() if len(text) > limit else text


def text_from_any(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
