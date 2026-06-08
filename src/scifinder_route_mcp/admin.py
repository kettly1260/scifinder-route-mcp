from __future__ import annotations

import html
import json
import os
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .service import RouteService


@dataclass(frozen=True)
class AdminRunConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8001

    @classmethod
    def from_env(cls) -> "AdminRunConfig":
        return cls(
            enabled=os.getenv("SCIFINDER_ROUTE_ADMIN_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
            host=os.getenv("SCIFINDER_ROUTE_ADMIN_HOST", "0.0.0.0"),
            port=int(os.getenv("SCIFINDER_ROUTE_ADMIN_PORT", "8001")),
        )


class AdminServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], service: RouteService):
        super().__init__(server_address, AdminHandler)
        self.service = service


class AdminHandler(BaseHTTPRequestHandler):
    server: AdminServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(render_dashboard(self.server.service))
            return
        if parsed.path == "/api/state":
            self._send_json(admin_state(self.server.service))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/config":
                self._require_token()
                payload = self._read_json()
                self._send_json(self.server.service.update_config(payload))
                return
            if parsed.path == "/api/scan":
                self._require_token()
                self._send_json(self.server.service.scan_inbox())
                return
            if parsed.path == "/api/reload":
                self._require_token()
                self._send_json(self.server.service.reload_config())
                return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _require_token(self) -> None:
        configured = self.server.service.config.auth_token
        if not configured:
            return
        token = self.headers.get("X-Scifinder-Route-Token") or ""
        if token != configured:
            raise PermissionError("Invalid or missing admin token")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def start_admin_server(service: RouteService, config: AdminRunConfig | None = None) -> ThreadingHTTPServer | None:
    run_config = config or AdminRunConfig.from_env()
    if not run_config.enabled:
        return None
    server = AdminServer((run_config.host, run_config.port), service)
    thread = threading.Thread(target=server.serve_forever, name="route-admin-ui", daemon=True)
    thread.start()
    return server


def admin_state(service: RouteService) -> dict[str, Any]:
    return {
        "health": service.health_check(),
        "config": service.get_config(),
        "validation": service.validate_config(),
        "jobs": service.list_parse_jobs(limit=20),
        "future_webui_sections": future_webui_sections(),
    }


def future_webui_sections() -> list[dict[str, str]]:
    return [
        {"name": "Compound registry", "status": "planned", "why": "CAS/SMILES/InChIKey normalization and alias review"},
        {"name": "Vector index", "status": "planned", "why": "Embedding model, vector API, index rebuild, and recall diagnostics"},
        {"name": "OCR pipeline", "status": "planned", "why": "MinerU/PaddleOCR endpoint checks, queue routing, and OCR confidence review"},
        {"name": "Document parser", "status": "planned", "why": "PDF/HTML/MHTML parser selection and parser health tests"},
        {"name": "LLM extraction", "status": "planned", "why": "Strict JSON extraction model, schema version, and cost controls"},
        {"name": "Evaluation set", "status": "planned", "why": "40-file gold set export, annotation coverage, and regression metrics"},
        {"name": "DOI verification", "status": "planned", "why": "Low-confidence trigger threshold and verified-field review"},
        {"name": "Backup and retention", "status": "planned", "why": "SQLite/Postgres backups, evidence cache retention, and NAS storage limits"},
    ]


def render_dashboard(service: RouteService) -> str:
    state = admin_state(service)
    config_json = json.dumps(state["config"], ensure_ascii=False)
    future_cards = "".join(
        f"<article class='mini-card'><span>{escape(item['status'])}</span><h3>{escape(item['name'])}</h3><p>{escape(item['why'])}</p></article>"
        for item in state["future_webui_sections"]
    )
    jobs_rows = "".join(
        f"<tr><td>{escape(job['id'])}</td><td>{escape(job['status'])}</td><td>{escape(job['stage'])}</td><td>{escape(job.get('error') or '')}</td></tr>"
        for job in state["jobs"]
    ) or "<tr><td colspan='4'>No parse jobs yet</td></tr>"
    health = state["health"]
    validation = state["validation"]
    warnings = "".join(f"<li>{escape(item)}</li>" for item in validation["warnings"]) or "<li>No config warnings</li>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SciFinder Route Admin</title>
  <style>{ADMIN_CSS}{RESPONSIVE_CSS}</style>
</head>
<body>
  <div class="orb orb-a"></div>
  <div class="orb orb-b"></div>
  <main class="shell">
    <section class="hero glass">
      <div>
        <p class="eyebrow">NAS Control Surface</p>
        <h1>SciFinder Route MCP</h1>
        <p class="lede">Configure extraction APIs, vector services, OCR workers, parser endpoints, and scan policy without editing Docker files.</p>
      </div>
      <div class="status-pill">{escape(health['status']).upper()}</div>
    </section>

    <section class="grid metrics">
      <article class="glass metric"><span>Documents</span><strong>{health['documents']}</strong></article>
      <article class="glass metric"><span>Reaction steps</span><strong>{health['reaction_steps']}</strong></article>
      <article class="glass metric"><span>Async jobs</span><strong>{str(health['async_jobs']).lower()}</strong></article>
      <article class="glass metric"><span>Config</span><strong>{escape(short_path(health['config_path']))}</strong></article>
    </section>

    <section class="panel glass">
      <div class="panel-title"><div><p class="eyebrow">Admin Token</p><h2>Secure Changes</h2></div><button onclick="scanInbox()">Scan Inbox</button></div>
      <label>Token <input id="token" type="password" placeholder="Required only if configured"></label>
      <p class="hint">The UI never edits Docker socket or compose files. Port and volume changes still belong in .env / Docker Compose.</p>
    </section>

    <section class="grid two">
      <form class="panel glass" onsubmit="saveConfig(event)">
        <div class="panel-title"><div><p class="eyebrow">Integrations</p><h2>API Settings</h2></div><button type="submit">Save & Reload</button></div>
        <div class="form-grid">
          <label>LLM endpoint <input name="llm_endpoint" data-section="integrations" placeholder="https://api.openai.com/v1"></label>
          <label>LLM model <input name="llm_model" data-section="integrations" placeholder="gpt-4o-mini"></label>
          <label>Embedding endpoint <input name="embedding_endpoint" data-section="integrations" placeholder="http://embedding:8000/v1"></label>
          <label>Embedding model <input name="embedding_model" data-section="integrations" placeholder="bge-m3"></label>
          <label>OCR endpoint <input name="ocr_endpoint" data-section="integrations" placeholder="http://mineru:9000"></label>
          <label>OCR model <input name="ocr_model" data-section="integrations" placeholder="mineru-layout"></label>
          <label>Document parser endpoint <input name="document_parser_endpoint" data-section="integrations" placeholder="http://parser:9100"></label>
          <label>Document parser model <input name="document_parser_model" data-section="integrations" placeholder="pymupdf|mineru"></label>
          <label>PostgreSQL URL <input name="postgres_url" data-section="integrations" placeholder="postgresql://user:pass@host/db"></label>
        </div>
      </form>

      <form class="panel glass" onsubmit="saveConfig(event)">
        <div class="panel-title"><div><p class="eyebrow">Runtime</p><h2>Scan & Thresholds</h2></div><button type="submit">Save & Reload</button></div>
        <div class="form-grid">
          <label>Scan extensions <input name="scan_extensions" data-section="ingest" placeholder=".pdf,.html,.mhtml"></label>
          <label>Max workers <input name="max_workers" data-section="server" type="number" min="1"></label>
          <label>Async jobs <select name="async_jobs" data-section="server"><option value="true">true</option><option value="false">false</option></select></label>
          <label>Allow external paths <select name="allow_external_paths" data-section="security"><option value="false">false</option><option value="true">true</option></select></label>
          <label>Config token <input name="token" data-section="security" type="password" placeholder="Leave blank to clear"></label>
          <label>Verification threshold <input name="verification_confidence_threshold" data-section="thresholds" type="number" min="0" max="1" step="0.01"></label>
        </div>
      </form>
    </section>

    <section class="grid two">
      <section class="panel glass"><p class="eyebrow">Validation</p><h2>Config Warnings</h2><ul>{warnings}</ul></section>
      <section class="panel glass"><p class="eyebrow">Paths</p><h2>Mounted Storage</h2><pre>{escape(json.dumps(health, indent=2, ensure_ascii=False))}</pre></section>
    </section>

    <section class="panel glass"><p class="eyebrow">Jobs</p><h2>Recent Parse Jobs</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>Status</th><th>Stage</th><th>Error</th></tr></thead><tbody>{jobs_rows}</tbody></table></div></section>

    <section class="panel glass"><p class="eyebrow">Roadmap</p><h2>Good Web UI Candidates</h2><div class="future-grid">{future_cards}</div></section>
  </main>
  <script>window.__CONFIG__ = {config_json};{ADMIN_JS}</script>
</body>
</html>"""


def escape(value: Any) -> str:
    return html.escape(str(value))


def short_path(value: str) -> str:
    parts = value.replace("\\", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else value


ADMIN_CSS = r"""
:root{color-scheme:dark;--bg:#11131f;--text:#f6f3ff;--muted:#c9c1dc;--line:rgba(255,255,255,.2);--glass:rgba(255,255,255,.14);--glass-strong:rgba(255,255,255,.22);--primary:#d7bbff;--primary-2:#9ef0ff;--accent:#ffb4cf;--shadow:0 24px 80px rgba(0,0,0,.36)}
*{box-sizing:border-box}body{margin:0;min-height:100vh;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(circle at 15% 10%,#624c8f 0,#11131f 34%),radial-gradient(circle at 85% 0,#0d5866 0,transparent 28%),linear-gradient(135deg,#11131f,#1b1628 55%,#101a25);color:var(--text);overflow-x:hidden}.orb{position:fixed;border-radius:999px;filter:blur(6px);opacity:.65;pointer-events:none}.orb-a{width:420px;height:420px;right:-120px;top:60px;background:linear-gradient(135deg,#9ef0ff,#d7bbff)}.orb-b{width:280px;height:280px;left:-80px;bottom:10%;background:linear-gradient(135deg,#ffb4cf,#ffd8a8)}.shell{position:relative;width:min(1220px,calc(100% - 32px));margin:0 auto;padding:32px 0 56px}.glass{border:1px solid var(--line);background:linear-gradient(135deg,rgba(255,255,255,.18),rgba(255,255,255,.08));box-shadow:var(--shadow);backdrop-filter:blur(24px) saturate(1.3);-webkit-backdrop-filter:blur(24px) saturate(1.3)}.hero{display:flex;justify-content:space-between;align-items:center;gap:24px;padding:34px;border-radius:36px}.eyebrow{margin:0 0 8px;color:var(--primary-2);font-weight:760;text-transform:uppercase;letter-spacing:.14em;font-size:12px}h1,h2,h3{margin:0}h1{font-size:clamp(38px,6vw,76px);line-height:.92;letter-spacing:-.06em}h2{font-size:24px;letter-spacing:-.03em}h3{font-size:16px}.lede{max-width:720px;color:var(--muted);font-size:18px;line-height:1.55}.status-pill,button{border:0;border-radius:999px;background:linear-gradient(135deg,var(--primary),var(--primary-2));color:#1a1328;font-weight:850;padding:12px 18px;box-shadow:0 12px 32px rgba(158,240,255,.18)}button{cursor:pointer;transition:transform .2s,filter .2s}button:hover{transform:translateY(-1px);filter:saturate(1.2)}.grid{display:grid;gap:18px;margin-top:18px}.metrics{grid-template-columns:repeat(4,minmax(0,1fr))}.two{grid-template-columns:1fr 1fr}.metric{border-radius:28px;padding:22px}.metric span{display:block;color:var(--muted);font-size:13px}.metric strong{display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em}.panel{margin-top:18px;border-radius:32px;padding:24px}.panel-title{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:18px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}label{display:flex;flex-direction:column;gap:8px;color:var(--muted);font-size:13px}input,select{width:100%;border:1px solid rgba(255,255,255,.18);border-radius:18px;background:rgba(7,9,18,.46);color:var(--text);padding:12px 14px;outline:none}input:focus,select:focus{border-color:var(--primary-2);box-shadow:0 0 0 3px rgba(158,240,255,.16)}.hint{color:var(--muted)}pre{white-space:pre-wrap;max-height:320px;overflow:auto;color:#e8ddff;background:rgba(7,9,18,.34);padding:16px;border-radius:18px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid rgba(255,255,255,.12);padding:12px;color:var(--muted);font-size:13px}th{color:var(--text)}.future-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.mini-card{border:1px solid rgba(255,255,255,.15);border-radius:24px;background:rgba(255,255,255,.09);padding:16px}.mini-card span{color:var(--accent);font-size:11px;text-transform:uppercase;letter-spacing:.12em;font-weight:800}.mini-card p{color:var(--muted);font-size:13px;line-height:1.45}@media (max-width:900px){.hero,.panel-title{align-items:flex-start;flex-direction:column}.metrics,.two,.future-grid,.form-grid{grid-template-columns:1fr}.shell{width:min(100% - 20px,1220px);padding-top:16px}.hero,.panel{border-radius:26px}}
"""


RESPONSIVE_CSS = r"""
@media (min-width: 1440px){
  .shell{width:min(1440px,calc(100% - 72px));padding-top:44px}.hero{padding:44px}.metrics{grid-template-columns:repeat(4,minmax(220px,1fr))}.two{grid-template-columns:1.18fr .82fr;align-items:start}.form-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.future-grid{grid-template-columns:repeat(4,minmax(0,1fr))}.panel{padding:30px}.lede{max-width:820px}.table-wrap{max-height:420px}
}
@media (min-width: 1024px) and (max-width: 1439px){
  .shell{width:min(1180px,calc(100% - 40px))}.metrics{grid-template-columns:repeat(4,minmax(0,1fr))}.two{grid-template-columns:1fr 1fr}.form-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.future-grid{grid-template-columns:repeat(4,minmax(0,1fr))}
}
@media (min-width: 700px) and (max-width: 1023px){
  .shell{width:min(900px,calc(100% - 32px));padding:24px 0 44px}.hero{display:grid;grid-template-columns:1fr auto;padding:30px;border-radius:32px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.two{grid-template-columns:1fr}.form-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.future-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.panel{padding:24px;border-radius:28px}.panel-title{flex-direction:row;align-items:center}.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}table{min-width:720px}button,input,select{min-height:46px}
}
@media (max-width: 699px){
  body{background:radial-gradient(circle at 20% 0,#624c8f 0,transparent 32%),linear-gradient(155deg,#11131f,#171325 58%,#0d1b24)}.orb-a{width:260px;height:260px;right:-120px;top:20px}.orb-b{width:190px;height:190px;left:-90px;bottom:16%}.shell{width:calc(100% - 20px);padding:12px 0 32px}.hero{padding:22px;border-radius:28px;display:flex;flex-direction:column;align-items:flex-start}.status-pill{align-self:flex-start}.lede{font-size:15px;line-height:1.5}h1{font-size:clamp(36px,13vw,54px)}h2{font-size:21px}.grid{gap:12px;margin-top:12px}.metrics,.two,.form-grid,.future-grid{grid-template-columns:1fr}.metric{padding:18px;border-radius:24px}.metric strong{font-size:25px}.panel{margin-top:12px;padding:18px;border-radius:24px}.panel-title{flex-direction:column;align-items:stretch}.panel-title button{width:100%}button,input,select{min-height:48px;font-size:16px}label{font-size:12px}.hint{font-size:13px}.table-wrap{margin-inline:-8px;padding-inline:8px;overflow-x:auto;-webkit-overflow-scrolling:touch}table{min-width:680px}th,td{padding:10px;font-size:12px}pre{max-height:260px;font-size:12px}.mini-card{border-radius:20px}.glass{backdrop-filter:blur(18px) saturate(1.2);-webkit-backdrop-filter:blur(18px) saturate(1.2)}
}
@media (max-width: 380px){
  .shell{width:calc(100% - 14px)}.hero,.panel{padding:16px;border-radius:22px}.eyebrow{font-size:10px;letter-spacing:.1em}.metric strong{font-size:22px}button,input,select{border-radius:16px}.future-grid{gap:10px}
}
@media (hover: none) and (pointer: coarse){
  button:hover{transform:none}button,input,select{min-height:48px}.mini-card,.metric{touch-action:manipulation}
}
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{scroll-behavior:auto!important;transition:none!important}.orb{filter:blur(8px)}
}
"""


ADMIN_JS = r"""
const config = window.__CONFIG__ || {};
function valueFor(section, name){const value=(config[section]||{})[name];return Array.isArray(value)?value.join(','):value ?? ''}
document.querySelectorAll('[data-section]').forEach(el=>{el.value=valueFor(el.dataset.section,el.name)});
function token(){return document.getElementById('token').value}
function coerce(el){if(el.name==='scan_extensions')return el.value.split(',').map(v=>v.trim()).filter(Boolean);if(el.type==='number')return Number(el.value);if(el.tagName==='SELECT')return el.value==='true';return el.value.trim() || null}
async function post(url,payload={}){const res=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Scifinder-Route-Token':token()},body:JSON.stringify(payload)});const data=await res.json();if(!res.ok||data.error)throw new Error(data.error||res.statusText);return data}
async function saveConfig(event){event.preventDefault();const payload={};event.target.querySelectorAll('[data-section]').forEach(el=>{payload[el.dataset.section] ||= {};payload[el.dataset.section][el.name]=coerce(el)});try{await post('/api/config',payload);location.reload()}catch(err){alert(err.message)}}
async function scanInbox(){try{const data=await post('/api/scan',{});alert(`Registered ${data.registered_count}, skipped ${data.skipped_count}`);location.reload()}catch(err){alert(err.message)}}
"""
