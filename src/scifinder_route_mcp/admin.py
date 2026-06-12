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

from .auth import authenticate_token, role_allows
from .service import RouteService


@dataclass(frozen=True)
class AdminRunConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8001

    @classmethod
    def from_env(cls) -> "AdminRunConfig":
        return cls(
            enabled=os.getenv("SCIFINDER_ROUTE_ADMIN_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"},
            host=os.getenv("SCIFINDER_ROUTE_ADMIN_HOST", "127.0.0.1"),
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
            try:
                self._require_role("viewer")
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return
            self._send_json(admin_state(self.server.service))
            return
        if parsed.path == "/api/rdf/reactions":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(self.server.service.list_rdf_reactions(document_id=first_query(query, "document_id"), limit=int(first_query(query, "limit", "50")), offset=int(first_query(query, "offset", "0"))))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/rdf/structures":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(self.server.service.list_rdf_structures(document_id=first_query(query, "document_id"), query=first_query(query, "q"), limit=int(first_query(query, "limit", "50")), offset=int(first_query(query, "offset", "0"))))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path.startswith("/api/rdf/reactions/"):
            try:
                self._require_role("viewer")
                reaction_id = parsed.path.rsplit("/", 1)[-1]
                self._send_json(self.server.service.get_rdf_reaction(reaction_id))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/chem/status":
            try:
                self._require_role("viewer")
                self._send_json(self.server.service.get_chem_status())
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            return
        if parsed.path == "/api/trash":
            try:
                self._require_role("viewer")
                query = parse_qs(parsed.query)
                self._send_json(self.server.service.list_trash(limit=int(first_query(query, "limit", "100"))))
            except PermissionError as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/config":
                self._require_role("admin")
                payload = self._read_json()
                self._send_json(self.server.service.update_config(payload))
                return
            if parsed.path == "/api/scan":
                self._require_role("operator")
                self._send_json(self.server.service.scan_inbox())
                return
            if parsed.path == "/api/reload":
                self._require_role("admin")
                self._send_json(self.server.service.reload_config())
                return
            if parsed.path == "/api/upload":
                self._require_role("operator")
                filename, content = self._read_upload()
                self._send_json(self.server.service.upload_document_bytes(content, filename))
                return
            if parsed.path == "/api/retry-failed":
                self._require_role("operator")
                self._send_json(self.server.service.retry_failed_jobs())
                return
            if parsed.path == "/api/vector/rebuild":
                self._require_role("operator")
                self._send_json(self.server.service.rebuild_vector_index())
                return
            if parsed.path == "/api/backup":
                self._require_role("admin")
                self._send_json(self.server.service.backup_database())
                return
            if parsed.path == "/api/cleanup":
                self._require_role("admin")
                payload = self._read_json()
                self._send_json(self.server.service.cleanup_evidence_cache(dry_run=bool(payload.get("dry_run", True))))
                return
            if parsed.path == "/api/integration/test":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.test_integration_endpoint(str(payload.get("kind") or "")))
                return
            if parsed.path == "/api/chem/install-rdkit":
                self._require_role("admin")
                self._send_json(self.server.service.install_rdkit_async())
                return
            if parsed.path == "/api/chem/similarity-search":
                self._require_role("viewer")
                payload = self._read_json()
                self._send_json(self.server.service.similarity_search_structures(str(payload.get("query") or ""), query_type=str(payload.get("query_type") or "smiles"), min_similarity=float(payload.get("min_similarity") or 0.2), limit=int(payload.get("limit") or 20)))
                return
            if parsed.path == "/api/chem/substructure-search":
                self._require_role("viewer")
                payload = self._read_json()
                self._send_json(self.server.service.substructure_search_structures(str(payload.get("query") or ""), query_type=str(payload.get("query_type") or "smarts"), limit=int(payload.get("limit") or 20)))
                return
            if parsed.path == "/api/trash/delete":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.trash_item(str(payload.get("entity_type") or ""), str(payload.get("entity_id") or "")))
                return
            if parsed.path == "/api/trash/restore":
                self._require_role("operator")
                payload = self._read_json()
                self._send_json(self.server.service.restore_trash_item(str(payload.get("entity_type") or ""), str(payload.get("entity_id") or "")))
                return
            if parsed.path == "/api/trash/empty":
                self._require_role("admin")
                self._send_json(self.server.service.empty_trash())
                return
        except PermissionError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _require_role(self, role: str) -> None:
        config = self.server.service.config
        if not config.auth_token and not config.users:
            return
        token = self.headers.get("X-Scifinder-Route-Token") or ""
        user = authenticate_token(config.users, config.auth_token, token)
        if not user:
            raise PermissionError("Invalid or missing admin token")
        if not role_allows(user.role, role):
            raise PermissionError(f"Token role '{user.role}' cannot perform '{role}' operations")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _read_upload(self) -> tuple[str, bytes]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if "multipart/form-data" in content_type:
            return parse_multipart_upload(content_type, body)
        filename = safe_upload_name(self.headers.get("X-Filename") or "upload.bin")
        return filename, body

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
        "production": service.get_production_status(),
    }


def render_dashboard(service: RouteService) -> str:
    state = admin_state(service)
    config_json = json.dumps(state["config"], ensure_ascii=False)
    production_json = json.dumps(state["production"], ensure_ascii=False, indent=2)
    jobs_rows = "".join(
        f"<tr><td>{escape(job['id'])}</td><td>{escape(job['status'])}</td><td>{escape(job['stage'])}</td><td>{escape(job.get('error') or '')}</td></tr>"
        for job in state["jobs"]
    ) or "<tr><td colspan='4'>暂无解析任务</td></tr>"
    health = state["health"]
    validation = state["validation"]
    production = state["production"]
    vector = production["vector_index"]
    chem = production.get("chem", {})
    usage_rows = "".join(f"<tr><td>{escape(name)}</td><td>{escape(item['files'])}</td><td>{escape(item['bytes'])}</td></tr>" for name, item in production["storage_usage"].items())
    endpoint_buttons = "".join(f"<button type='button' onclick=\"testEndpoint('{kind}')\">测试 {label}</button>" for kind, label in [("llm", "LLM"), ("embedding", "Embedding"), ("ocr", "OCR"), ("document_parser", "文档解析"), ("structure_recognition", "结构识别"), ("postgres", "Postgres"), ("zotero_mcp", "Zotero MCP")])
    warnings = "".join(f"<li>{escape(item)}</li>" for item in validation["warnings"]) or "<li>暂无配置警告</li>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SciFinder Route 管理控制台</title>
  <style>{ADMIN_CSS}{RESPONSIVE_CSS}</style>
</head>
<body>
  <div class="orb orb-a"></div>
  <div class="orb orb-b"></div>
  <main class="shell">
    <section class="hero glass">
      <div>
        <p class="eyebrow">NAS 控制台</p>
        <h1>SciFinder Route MCP</h1>
        <p class="lede">无需编辑 Docker 文件，即可配置抽取 API、向量服务、OCR 工作者、文档解析端点和扫描策略。</p>
      </div>
      <div class="hero-actions"><a class="nav-link primary-link" href="#rdf-viewer">打开 RDF 查看器</a><div class="status-pill">{escape(health['status']).upper()}</div></div>
    </section>

    <nav class="quick-nav glass" aria-label="管理控制台分区导航">
      <a class="nav-link" href="#secure-changes">安全变更</a>
      <a class="nav-link" href="#integrations">集成设置</a>
      <a class="nav-link" href="#runtime">运行配置</a>
      <a class="nav-link" href="#zotero">Zotero 文献</a>
      <a class="nav-link" href="#chem-search">化学检索</a>
      <a class="nav-link primary-link" href="#rdf-viewer">RDF 查看器</a>
      <a class="nav-link" href="#trash">回收站</a>
      <a class="nav-link" href="#jobs">任务队列</a>
      <a class="nav-link" href="#diagnostics">诊断</a>
    </nav>

    <section class="grid metrics">
      <article class="glass metric"><span>文档数</span><strong>{health['documents']}</strong></article>
      <article class="glass metric"><span>反应步骤</span><strong>{health['reaction_steps']}</strong></article>
      <article class="glass metric"><span>异步任务</span><strong>{'启用' if health['async_jobs'] else '停用'}</strong></article>
      <article class="glass metric"><span>配置文件</span><strong>{escape(short_path(health['config_path']))}</strong></article>
    </section>

    <section id="secure-changes" class="panel glass">
      <div class="panel-title"><div><p class="eyebrow">管理员令牌</p><h2>安全变更</h2></div><button onclick="scanInbox()">扫描收件箱</button></div>
      <label>令牌 <input id="token" type="password" placeholder="仅在已配置鉴权时需要"></label>
      <p class="hint">此界面不会编辑 Docker socket 或 Compose 文件。端口、卷挂载等变更仍应在 .env / Docker Compose 中完成。</p>
    </section>

    <section class="grid two">
      <form id="integrations" class="panel glass" onsubmit="saveConfig(event)">
        <div class="panel-title"><div><p class="eyebrow">集成服务</p><h2>API 设置</h2></div><button type="submit">保存并重载</button></div>
        <div class="form-grid">
          <label>LLM 端点 <input name="llm_endpoint" data-section="integrations" placeholder="https://api.openai.com/v1"></label>
          <label>LLM 模型 <input name="llm_model" data-section="integrations" placeholder="gpt-4o-mini"></label>
          <label>嵌入端点 <input name="embedding_endpoint" data-section="integrations" placeholder="http://embedding:8000/v1"></label>
          <label>嵌入模型 <input name="embedding_model" data-section="integrations" placeholder="bge-m3"></label>
          <label>OCR 端点 <input name="ocr_endpoint" data-section="integrations" placeholder="http://mineru:9000"></label>
          <label>OCR 模型 <input name="ocr_model" data-section="integrations" placeholder="mineru-layout"></label>
          <label>文档解析端点 <input name="document_parser_endpoint" data-section="integrations" placeholder="http://parser:9100"></label>
          <label>文档解析模型 <input name="document_parser_model" data-section="integrations" placeholder="pymupdf|mineru"></label>
          <label>解析失败回退 <select name="document_parser_fallback" data-section="integrations" data-type="bool"><option value="true">启用</option><option value="false">停用</option></select></label>
          <label>PostgreSQL URL <input name="postgres_url" data-section="integrations" data-secret="true" placeholder="留空则不变"></label>
          <label>结构识别端点 <input name="structure_recognition_endpoint" data-section="integrations" placeholder="http://decimer:9300"></label>
          <label>结构识别模型 <input name="structure_recognition_model" data-section="integrations" placeholder="decimer|molscribe|osra"></label>
          <label>启用 LLM <select name="llm_enabled" data-section="integrations" data-type="bool"><option value="false">停用</option><option value="true">启用</option></select></label>
          <label>Zotero 文献链接 <select name="zotero_linking_enabled" data-section="integrations" data-type="bool"><option value="false">停用</option><option value="true">启用</option></select></label>
          <label>导入后链接 Zotero <select name="zotero_linking_on_import" data-section="integrations" data-type="bool"><option value="true">启用</option><option value="false">停用</option></select></label>
          <label>Zotero 抽取策略 <select name="zotero_extraction_strategy" data-section="integrations" data-type="enum"><option value="rules_first">规则优先</option><option value="llm_first">LLM 优先</option><option value="rules_only">仅规则</option></select></label>
          <label>LLM 优先术语 <input name="zotero_llm_priority_terms" data-section="integrations" data-type="list" placeholder="scale,purification,SI"></label>
        </div>
        <p class="hint">Web UI 变更会保存到 {escape((state['config'].get('paths') or {}).get('webui_config_path'))}，并与容器启动配置分离。</p>
      </form>

      <form id="runtime" class="panel glass" onsubmit="saveConfig(event)">
        <div class="panel-title"><div><p class="eyebrow">运行时</p><h2>扫描与阈值</h2></div><button type="submit">保存并重载</button></div>
        <div class="form-grid">
          <label>扫描扩展名 <input name="scan_extensions" data-section="ingest" data-type="list" placeholder=".pdf,.html,.mhtml"></label>
          <label>最大工作线程 <input name="max_workers" data-section="server" type="number" min="1"></label>
          <label>异步任务 <select name="async_jobs" data-section="server" data-type="bool"><option value="true">启用</option><option value="false">停用</option></select></label>
          <label>允许外部路径 <select name="allow_external_paths" data-section="security" data-type="bool"><option value="false">禁止</option><option value="true">允许</option></select></label>
          <label>配置令牌 <input name="token" data-section="security" data-secret="true" type="password" placeholder="留空则不变"></label>
          <label>验证置信阈值 <input name="verification_confidence_threshold" data-section="thresholds" type="number" min="0" max="1" step="0.01"></label>
          <label>队列后端 <select name="backend" data-section="queue" data-type="enum"><option value="sqlite">sqlite</option><option value="redis">redis</option></select></label>
          <label>Redis URL <input name="redis_url" data-section="queue" data-secret="true" placeholder="留空则不变"></label>
          <label>存储后端 <select name="storage_backend" data-section="server" data-type="enum"><option value="sqlite">sqlite</option><option value="postgres">postgres</option></select></label>
          <label>LLM Schema 版本 <input name="llm_schema_version" data-section="extraction" placeholder="reaction_step.v1"></label>
          <label>LLM 提示词配置 <input name="llm_prompt_profile" data-section="extraction" placeholder="strict-reaction-json"></label>
          <label>LLM 成本上限 USD <input name="llm_cost_limit_usd" data-section="extraction" type="number" min="0" step="0.01"></label>
          <label>证据保留天数 <input name="evidence_retention_days" data-section="retention" type="number" min="1"></label>
          <label>缓存保留天数 <input name="cache_retention_days" data-section="retention" type="number" min="1"></label>
        </div>
      </form>
    </section>

    <section class="grid two">
      <section class="panel glass"><div class="panel-title"><div><p class="eyebrow">向量索引</p><h2>嵌入召回</h2></div><button onclick="rebuildVector()">重建</button></div><pre>{escape(json.dumps(vector, indent=2))}</pre></section>
      <section class="panel glass"><div class="panel-title"><div><p class="eyebrow">端点健康</p><h2>外部 API</h2></div></div><div class="button-row">{endpoint_buttons}</div><pre>{escape(json.dumps(health.get('integrations', []), indent=2))}</pre></section>
    </section>

    <section id="zotero" class="panel glass"><div class="panel-title"><div><p class="eyebrow">Zotero MCP</p><h2>文献端点</h2></div><div><button onclick="saveZoteroEndpoint()">保存端点</button> <button onclick="loadZoteroEndpoints()">重新加载</button></div></div><div class="form-grid"><label>别名 <input id="zoteroAlias" placeholder="lab-zotero"></label><label>分组 <input id="zoteroGroup" placeholder="primary-library"></label><label>URL <input id="zoteroUrl" placeholder="http://host:23120/mcp"></label><label>优先级 <input id="zoteroPriority" type="number" value="100"></label><label>超时秒数 <input id="zoteroTimeout" type="number" value="10" step="0.5"></label><label>启用 <select id="zoteroEnabled"><option value="true">启用</option><option value="false">停用</option></select></label><label>允许写回笔记 <select id="zoteroWriteNote"><option value="false">禁止</option><option value="true">允许</option></select></label><label>请求头 JSON <input id="zoteroHeaders" placeholder='{{"Authorization":"Bearer ..."}}'></label></div><div id="zoteroEndpoints" class="table-wrap"></div></section>

    <section class="grid two">
      <section id="chem-search" class="panel glass"><div class="panel-title"><div><p class="eyebrow">化学检索</p><h2>RDKit 结构</h2></div><button onclick="installRdkit()">安装 RDKit</button></div><pre>{escape(json.dumps(chem, indent=2))}</pre><div class="form-grid"><label>查询 <input id="chemQuery" placeholder="SMILES、SMARTS、CAS 或名称"></label><label>模式 <select id="chemMode"><option value="similarity">相似度</option><option value="substructure">子结构</option><option value="text">文本过滤</option></select></label></div><button onclick="runChemSearch()">检索结构</button><div id="chemResults" class="table-wrap"></div></section>
      <section id="rdf-viewer" class="panel glass featured-panel"><div class="panel-title"><div><p class="eyebrow">RDF 查看器</p><h2>反应记录</h2></div><button onclick="loadRdfReactions()">加载 RDF 反应</button></div><p class="hint">从 RDF/RDfile 结构化记录中查看 CAS 反应号、方案、步骤、收率和 molfile 结构块。</p><div class="form-grid"><label>文档 ID <input id="rdfDocumentId" placeholder="可选"></label><label>数量限制 <input id="rdfLimit" type="number" value="25" min="1"></label></div><div id="rdfReactions" class="table-wrap"></div><pre id="rdfDetail">选择一个反应以查看 molfile 块。</pre></section>
    </section>

    <section id="trash" class="panel glass"><div class="panel-title"><div><p class="eyebrow">回收站</p><h2>已删除项目</h2></div><div><button onclick="loadTrash()">加载回收站</button> <button onclick="emptyTrash()">清空回收站</button></div></div><div id="trashList" class="table-wrap"></div></section>

    <section class="grid two">
      <section id="literature" class="panel glass"><div class="panel-title"><div><p class="eyebrow">OCR / DOI / 文献</p><h2>积压队列</h2></div><div><button onclick="startLiteratureLinking()">启动 Zotero 链接</button> <button onclick="loadLiterature()">加载链接</button></div></div><pre>OCR 积压: {escape(health['ocr_backlog'])}\n低置信 DOI 队列: {escape(len(production['doi_low_confidence_queue']))}\n文献候选: {escape(len(production.get('literature_candidates', [])))}</pre><div class="form-grid"><label>文档 ID <input id="literatureDocumentId" placeholder="可选"></label></div><h3>文献任务</h3><div id="literatureJobs" class="table-wrap"></div><h3>候选链接</h3><div id="literatureLinks" class="table-wrap"></div></section>
      <section class="panel glass"><p class="eyebrow">评估</p><h2>最新指标</h2><pre>{escape(json.dumps(production['evaluation'], indent=2))}</pre></section>
    </section>

    <section class="grid two">
      <section class="panel glass"><div class="panel-title"><div><p class="eyebrow">备份与保留</p><h2>NAS 存储</h2></div><button onclick="backupDb()">备份数据库</button></div><div class="table-wrap"><table><thead><tr><th>路径</th><th>文件数</th><th>字节数</th></tr></thead><tbody>{usage_rows}</tbody></table></div><button onclick="cleanupDryRun()">清理试运行</button></section>
      <section class="panel glass"><p class="eyebrow">化合物注册表</p><h2>审核队列</h2><pre>已索引化合物: {escape(production['compound_count'])}</pre></section>
    </section>

    <section class="grid two">
      <section class="panel glass"><p class="eyebrow">校验</p><h2>配置警告</h2><ul>{warnings}</ul></section>
      <section class="panel glass"><p class="eyebrow">路径</p><h2>已挂载存储</h2><pre>{escape(json.dumps(health, indent=2, ensure_ascii=False))}</pre></section>
    </section>

    <section id="jobs" class="panel glass"><p class="eyebrow">任务</p><h2>最近解析任务</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>状态</th><th>阶段</th><th>错误</th></tr></thead><tbody>{jobs_rows}</tbody></table></div></section>

    <section id="diagnostics" class="panel glass"><p class="eyebrow">生产状态</p><h2>诊断快照</h2><pre>{escape(production_json)}</pre></section>
  </main>
  <script>window.__CONFIG__ = {config_json};{ADMIN_JS}</script>
</body>
</html>"""


def escape(value: Any) -> str:
    return html.escape(str(value))


def short_path(value: str) -> str:
    parts = value.replace("\\", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else value


def safe_upload_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in os.path.basename(value)) or "upload.bin"


def first_query(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return values[0] if values else default


def parse_multipart_upload(content_type: str, body: bytes) -> tuple[str, bytes]:
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("multipart upload is missing boundary")
    boundary = content_type.split(marker, 1)[1].split(";", 1)[0].strip().strip('"')
    delimiter = ("--" + boundary).encode("utf-8")
    for part in body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--" or b"\r\n\r\n" not in part:
            continue
        raw_headers, content = part.split(b"\r\n\r\n", 1)
        headers = raw_headers.decode("utf-8", errors="ignore")
        if 'name="file"' not in headers:
            continue
        filename = "upload.bin"
        if "filename=" in headers:
            filename = headers.split("filename=", 1)[1].split(";", 1)[0].split("\r\n", 1)[0].strip().strip('"')
        if content.endswith(b"\r\n--"):
            content = content[:-4]
        return safe_upload_name(filename), content.rstrip(b"\r\n")
    raise ValueError("multipart upload requires a file field")


ADMIN_CSS = r"""
:root{
  color-scheme:light dark;
  --bg:#f7f3ff;--text:#21182f;--muted:#615571;--line:rgba(48,35,72,.18);
  --glass:rgba(255,255,255,.72);--glass-strong:rgba(255,255,255,.88);
  --primary:#8c5be8;--primary-2:#007f91;--accent:#c04d7a;
  --button-text:#fff7ff;--field-bg:rgba(255,255,255,.72);--pre-bg:rgba(255,255,255,.62);
  --pre-text:#2f2441;--row-line:rgba(48,35,72,.12);--mini-bg:rgba(255,255,255,.58);
  --body-bg:radial-gradient(circle at 15% 10%,rgba(215,187,255,.95) 0,rgba(247,243,255,.86) 34%),radial-gradient(circle at 85% 0,rgba(158,240,255,.7) 0,transparent 28%),linear-gradient(135deg,#fff9ff,#f7f3ff 55%,#eefbff);
  --mobile-bg:radial-gradient(circle at 20% 0,rgba(215,187,255,.9) 0,transparent 32%),linear-gradient(155deg,#fff9ff,#f7f3ff 58%,#eefbff);
  --shadow:0 24px 80px rgba(66,45,101,.18);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#11131f;--text:#f6f3ff;--muted:#c9c1dc;--line:rgba(255,255,255,.2);
    --glass:rgba(255,255,255,.14);--glass-strong:rgba(255,255,255,.22);
    --primary:#d7bbff;--primary-2:#9ef0ff;--accent:#ffb4cf;
    --button-text:#1a1328;--field-bg:rgba(7,9,18,.46);--pre-bg:rgba(7,9,18,.34);
    --pre-text:#e8ddff;--row-line:rgba(255,255,255,.12);--mini-bg:rgba(255,255,255,.09);
    --body-bg:radial-gradient(circle at 15% 10%,#624c8f 0,#11131f 34%),radial-gradient(circle at 85% 0,#0d5866 0,transparent 28%),linear-gradient(135deg,#11131f,#1b1628 55%,#101a25);
    --mobile-bg:radial-gradient(circle at 20% 0,#624c8f 0,transparent 32%),linear-gradient(155deg,#11131f,#171325 58%,#0d1b24);
    --shadow:0 24px 80px rgba(0,0,0,.36);
  }
}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:96px}body{margin:0;min-height:100vh;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--body-bg);color:var(--text);overflow-x:hidden}.orb{position:fixed;border-radius:999px;filter:blur(6px);opacity:.65;pointer-events:none}.orb-a{width:420px;height:420px;right:-120px;top:60px;background:linear-gradient(135deg,#9ef0ff,#d7bbff)}.orb-b{width:280px;height:280px;left:-80px;bottom:10%;background:linear-gradient(135deg,#ffb4cf,#ffd8a8)}.shell{position:relative;width:min(1220px,calc(100% - 32px));margin:0 auto;padding:32px 0 56px}.glass{border:1px solid var(--line);background:linear-gradient(135deg,var(--glass-strong),var(--glass));box-shadow:var(--shadow);backdrop-filter:blur(24px) saturate(1.3);-webkit-backdrop-filter:blur(24px) saturate(1.3)}.hero{display:flex;justify-content:space-between;align-items:center;gap:24px;padding:34px;border-radius:36px}.hero-actions{display:flex;flex-direction:column;align-items:flex-end;gap:12px}.quick-nav{position:sticky;top:12px;z-index:5;display:flex;gap:10px;overflow-x:auto;margin-top:18px;padding:10px;border-radius:999px;scrollbar-width:thin}.nav-link{flex:0 0 auto;border:1px solid var(--line);border-radius:999px;color:var(--text);background:var(--mini-bg);font-size:13px;font-weight:800;text-decoration:none;padding:10px 14px}.nav-link:hover,.nav-link:focus{outline:none;border-color:var(--primary-2);box-shadow:0 0 0 3px rgba(158,240,255,.16)}.primary-link{background:linear-gradient(135deg,var(--primary),var(--primary-2));color:var(--button-text);border-color:transparent}.featured-panel{outline:2px solid color-mix(in srgb,var(--primary-2) 45%,transparent);outline-offset:3px}.eyebrow{margin:0 0 8px;color:var(--primary-2);font-weight:760;text-transform:uppercase;letter-spacing:.14em;font-size:12px}h1,h2,h3{margin:0}h1{font-size:clamp(38px,6vw,76px);line-height:.92;letter-spacing:-.06em}h2{font-size:24px;letter-spacing:-.03em}h3{font-size:16px}.lede{max-width:720px;color:var(--muted);font-size:18px;line-height:1.55}.status-pill,button{border:0;border-radius:999px;background:linear-gradient(135deg,var(--primary),var(--primary-2));color:var(--button-text);font-weight:850;padding:12px 18px;box-shadow:0 12px 32px rgba(158,240,255,.18)}button{cursor:pointer;transition:transform .2s,filter .2s}button:hover{transform:translateY(-1px);filter:saturate(1.2)}.grid{display:grid;gap:18px;margin-top:18px}.metrics{grid-template-columns:repeat(4,minmax(0,1fr))}.two{grid-template-columns:1fr 1fr}.metric{border-radius:28px;padding:22px}.metric span{display:block;color:var(--muted);font-size:13px}.metric strong{display:block;margin-top:8px;font-size:28px;letter-spacing:-.04em}.panel{margin-top:18px;border-radius:32px;padding:24px}.panel-title{display:flex;justify-content:space-between;gap:16px;align-items:center;margin-bottom:18px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.button-row{display:flex;flex-wrap:wrap;gap:10px}label{display:flex;flex-direction:column;gap:8px;color:var(--muted);font-size:13px}input,select{width:100%;border:1px solid var(--line);border-radius:18px;background:var(--field-bg);color:var(--text);padding:12px 14px;outline:none}input:focus,select:focus{border-color:var(--primary-2);box-shadow:0 0 0 3px rgba(158,240,255,.16)}.hint{color:var(--muted)}pre{white-space:pre-wrap;max-height:320px;overflow:auto;color:var(--pre-text);background:var(--pre-bg);padding:16px;border-radius:18px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid var(--row-line);padding:12px;color:var(--muted);font-size:13px}th{color:var(--text)}.future-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.mini-card{border:1px solid var(--line);border-radius:24px;background:var(--mini-bg);padding:16px}.mini-card span{color:var(--accent);font-size:11px;text-transform:uppercase;letter-spacing:.12em;font-weight:800}.mini-card p{color:var(--muted);font-size:13px;line-height:1.45}@media (max-width:900px){.hero,.panel-title{align-items:flex-start;flex-direction:column}.hero-actions{align-items:flex-start}.metrics,.two,.future-grid,.form-grid{grid-template-columns:1fr}.shell{width:min(100% - 20px,1220px);padding-top:16px}.hero,.panel{border-radius:26px}}
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
  html{scroll-padding-top:82px}body{background:var(--mobile-bg)}.orb-a{width:260px;height:260px;right:-120px;top:20px}.orb-b{width:190px;height:190px;left:-90px;bottom:16%}.shell{width:calc(100% - 20px);padding:12px 0 32px}.hero{padding:22px;border-radius:28px;display:flex;flex-direction:column;align-items:flex-start}.hero-actions{width:100%;align-items:stretch}.hero-actions .nav-link,.status-pill{align-self:flex-start}.quick-nav{top:6px;margin-top:12px;border-radius:22px}.nav-link{font-size:12px;padding:9px 12px}.lede{font-size:15px;line-height:1.5}h1{font-size:clamp(36px,13vw,54px)}h2{font-size:21px}.grid{gap:12px;margin-top:12px}.metrics,.two,.form-grid,.future-grid{grid-template-columns:1fr}.metric{padding:18px;border-radius:24px}.metric strong{font-size:25px}.panel{margin-top:12px;padding:18px;border-radius:24px}.panel-title{flex-direction:column;align-items:stretch}.panel-title button{width:100%}button,input,select{min-height:48px;font-size:16px}label{font-size:12px}.hint{font-size:13px}.table-wrap{margin-inline:-8px;padding-inline:8px;overflow-x:auto;-webkit-overflow-scrolling:touch}table{min-width:680px}th,td{padding:10px;font-size:12px}pre{max-height:260px;font-size:12px}.mini-card{border-radius:20px}.glass{backdrop-filter:blur(18px) saturate(1.2);-webkit-backdrop-filter:blur(18px) saturate(1.2)}
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
document.querySelectorAll('[data-section]').forEach(el=>{if(el.dataset.secret==='true')return;el.value=valueFor(el.dataset.section,el.name)});
function token(){return document.getElementById('token').value}
function coerce(el){const text=el.value.trim();if(el.dataset.secret==='true'&&!text)return undefined;if(el.dataset.type==='list')return text.split(',').map(v=>v.trim()).filter(Boolean);if(el.type==='number')return text===''?undefined:Number(text);if(el.dataset.type==='bool')return el.value==='true';if(el.tagName==='SELECT')return el.value;return text || null}
async function post(url,payload={}){const res=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-Scifinder-Route-Token':token()},body:JSON.stringify(payload)});const data=await res.json();if(!res.ok||data.error)throw new Error(data.error||res.statusText);return data}
async function saveConfig(event){event.preventDefault();const payload={};event.target.querySelectorAll('[data-section]').forEach(el=>{const value=coerce(el);if(value===undefined)return;payload[el.dataset.section] ||= {};payload[el.dataset.section][el.name]=value});try{await post('/api/config',payload);location.reload()}catch(err){alert(err.message)}}
async function scanInbox(){try{const data=await post('/api/scan',{});alert(`已登记 ${data.registered_count} 个，已跳过 ${data.skipped_count} 个`);location.reload()}catch(err){alert(err.message)}}
async function rebuildVector(){try{const data=await post('/api/vector/rebuild',{});alert(JSON.stringify(data,null,2));location.reload()}catch(err){alert(err.message)}}
async function backupDb(){try{const data=await post('/api/backup',{});alert(JSON.stringify(data,null,2));location.reload()}catch(err){alert(err.message)}}
async function cleanupDryRun(){try{const data=await post('/api/cleanup',{dry_run:true});alert(JSON.stringify(data,null,2))}catch(err){alert(err.message)}}
async function testEndpoint(kind){try{const data=await post('/api/integration/test',{kind});alert(JSON.stringify(data,null,2));location.reload()}catch(err){alert(err.message)}}
async function getJson(url){const res=await fetch(url,{headers:{'X-Scifinder-Route-Token':token()}});const data=await res.json();if(!res.ok||data.error)throw new Error(data.error||res.statusText);return data}
function esc(v){return String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function table(rows,cols){if(!rows.length)return '<p class="hint">暂无结果</p>';return '<table><thead><tr>'+cols.map(c=>`<th>${esc(c.label)}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>`<td>${c.render?c.render(r):esc(r[c.key])}</td>`).join('')+'</tr>').join('')+'</tbody></table>'}
function parseHeaders(){const text=document.getElementById('zoteroHeaders').value.trim();if(!text)return {};try{return JSON.parse(text)}catch(err){throw new Error('请求头 JSON 无效')}}
async function loadZoteroEndpoints(){try{const data=await getJson('/api/zotero/endpoints');document.getElementById('zoteroEndpoints').innerHTML=table(data,[{label:'别名',key:'alias'},{label:'分组',key:'group_name'},{label:'URL',key:'url'},{label:'启用',key:'enabled'},{label:'优先级',key:'priority'},{label:'写回笔记',key:'write_note_enabled'},{label:'状态',key:'last_status'},{label:'延迟',key:'last_latency_ms'},{label:'测试',render:r=>`<button onclick="testZoteroEndpoint('${esc(r.id)}')">测试</button>`},{label:'删除',render:r=>`<button onclick="deleteZoteroEndpoint('${esc(r.id)}')">删除</button>`}])}catch(err){alert(err.message)}}
async function saveZoteroEndpoint(){try{const payload={alias:document.getElementById('zoteroAlias').value.trim(),group_name:document.getElementById('zoteroGroup').value.trim(),url:document.getElementById('zoteroUrl').value.trim(),priority:Number(document.getElementById('zoteroPriority').value||100),timeout_seconds:Number(document.getElementById('zoteroTimeout').value||10),enabled:document.getElementById('zoteroEnabled').value==='true',write_note_enabled:document.getElementById('zoteroWriteNote').value==='true',headers:parseHeaders()};await post('/api/zotero/endpoints',payload);await loadZoteroEndpoints()}catch(err){alert(err.message)}}
async function testZoteroEndpoint(id){try{const data=await post('/api/zotero/endpoints/test',{id});alert(JSON.stringify(data,null,2));await loadZoteroEndpoints()}catch(err){alert(err.message)}}
async function deleteZoteroEndpoint(id){if(!confirm('要从 Web UI 配置中删除该 Zotero 端点吗？'))return;try{await post('/api/zotero/endpoints/delete',{id});await loadZoteroEndpoints()}catch(err){alert(err.message)}}
async function loadLiterature(){try{const doc=document.getElementById('literatureDocumentId').value.trim();const qs=doc?'?document_id='+encodeURIComponent(doc)+'&limit=50':'?status=candidate&limit=50';const links=await getJson('/api/literature/links'+qs);const jobs=await getJson('/api/literature/jobs?limit=20');document.getElementById('literatureJobs').innerHTML=table(jobs,[{label:'ID',key:'id'},{label:'文档',key:'document_id'},{label:'状态',key:'status'},{label:'阶段',key:'stage'},{label:'错误',key:'error'}]);document.getElementById('literatureLinks').innerHTML=table(links,[{label:'状态',key:'status'},{label:'反应',key:'reaction_step_id'},{label:'端点',key:'endpoint_alias'},{label:'DOI',key:'doi'},{label:'标题',key:'title'},{label:'评分',key:'confidence'},{label:'差异',render:r=>esc(Object.entries(r.field_diff||{}).map(([k,v])=>`${k}:${v.status}`).join(', '))},{label:'确认',render:r=>`<button onclick="confirmLiteratureLink('${esc(r.id)}')">确认</button>`},{label:'拒绝',render:r=>`<button onclick="rejectLiteratureLink('${esc(r.id)}')">拒绝</button>`},{label:'写笔记',render:r=>`<button onclick="writeZoteroNote('${esc(r.id)}')">写入</button>`}])}catch(err){alert(err.message)}}
async function startLiteratureLinking(){try{const document_id=document.getElementById('literatureDocumentId').value.trim();const data=await post('/api/literature/jobs/start',{document_id});alert(JSON.stringify(data,null,2));await loadLiterature()}catch(err){alert(err.message)}}
async function confirmLiteratureLink(id){try{await post('/api/literature/links/confirm',{id});await loadLiterature()}catch(err){alert(err.message)}}
async function rejectLiteratureLink(id){const reason=prompt('拒绝原因')||'';try{await post('/api/literature/links/reject',{id,reason});await loadLiterature()}catch(err){alert(err.message)}}
async function writeZoteroNote(id){try{const data=await post('/api/literature/links/write-note',{id});alert(JSON.stringify(data,null,2))}catch(err){alert(err.message)}}
async function installRdkit(){try{const data=await post('/api/chem/install-rdkit',{});alert('RDKit 安装任务已启动。成功后请重启服务或容器。\n'+JSON.stringify(data,null,2))}catch(err){alert(err.message)}}
async function runChemSearch(){try{const query=document.getElementById('chemQuery').value.trim();const mode=document.getElementById('chemMode').value;let data;if(mode==='text'){data=await getJson('/api/rdf/structures?q='+encodeURIComponent(query)+'&limit=50')}else if(mode==='similarity'){data=(await post('/api/chem/similarity-search',{query,query_type:'smiles',min_similarity:0.2,limit:50})).results}else{data=(await post('/api/chem/substructure-search',{query,query_type:'smarts',limit:50})).results}document.getElementById('chemResults').innerHTML=table(data,[{label:'名称',key:'name'},{label:'角色',key:'role'},{label:'CAS',key:'cas_rn'},{label:'版本',key:'molfile_version'},{label:'评分',render:r=>esc(r.similarity??'')},{label:'反应',render:r=>`<button onclick="showRdfReaction('${esc(r.rdf_reaction_id)}')">打开</button>`},{label:'删除',render:r=>`<button onclick="trashItem('rdf_structure','${esc(r.id)}')">移入回收站</button>`}])}catch(err){alert(err.message)}}
async function loadRdfReactions(){try{const doc=document.getElementById('rdfDocumentId').value.trim();const limit=document.getElementById('rdfLimit').value||25;const url='/api/rdf/reactions?limit='+encodeURIComponent(limit)+(doc?'&document_id='+encodeURIComponent(doc):'');const data=await getJson(url);document.getElementById('rdfReactions').innerHTML=table(data,[{label:'记录',key:'record_index'},{label:'方案',key:'scheme_id'},{label:'步骤',key:'step_id'},{label:'CAS 反应号',key:'cas_reaction_number'},{label:'收率',key:'yield_text'},{label:'结构数',key:'structure_count'},{label:'打开',render:r=>`<button onclick="showRdfReaction('${esc(r.id)}')">打开</button>`},{label:'删除',render:r=>`<button onclick="trashItem('rdf_reaction','${esc(r.id)}')">移入回收站</button>`}])}catch(err){alert(err.message)}}
async function showRdfReaction(id){try{const data=await getJson('/api/rdf/reactions/'+encodeURIComponent(id));const structures=data.structures||[];const summary={id:data.id,record_index:data.record_index,scheme_id:data.scheme_id,step_id:data.step_id,cas_reaction_number:data.cas_reaction_number,yield_text:data.yield_text,reagents:data.reagents,catalysts:data.catalysts,solvents:data.solvents,reference:data.reference,warnings:data.warnings,structures:structures.map(s=>({id:s.id,role:s.role,role_index:s.role_index,name:s.name,cas_rn:s.cas_rn,molfile_version:s.molfile_version,smiles:s.smiles,rdkit_status:s.rdkit_status,warnings:s.warnings,molfile:s.molfile}))};document.getElementById('rdfDetail').textContent=JSON.stringify(summary,null,2)}catch(err){alert(err.message)}}
async function trashItem(entity_type,entity_id){if(!confirm(`要将 ${entity_type} 移入回收站吗？`))return;try{await post('/api/trash/delete',{entity_type,entity_id});await loadRdfReactions();await runChemSearch()}catch(err){alert(err.message)}}
async function loadTrash(){try{const data=await getJson('/api/trash?limit=100');document.getElementById('trashList').innerHTML=table(data,[{label:'类型',key:'entity_type'},{label:'ID',key:'id'},{label:'标题',key:'title'},{label:'删除时间',key:'deleted_at'},{label:'还原',render:r=>`<button onclick="restoreTrash('${esc(r.entity_type)}','${esc(r.id)}')">还原</button>`}])}catch(err){alert(err.message)}}
async function restoreTrash(entity_type,entity_id){try{await post('/api/trash/restore',{entity_type,entity_id});await loadTrash()}catch(err){alert(err.message)}}
async function emptyTrash(){if(!confirm('要永久删除回收站中的全部项目吗？'))return;try{const data=await post('/api/trash/empty',{});alert(JSON.stringify(data,null,2));await loadTrash()}catch(err){alert(err.message)}}
"""
