import { useEffect, useState, type FormEvent } from 'react';
import { clearToken, getJson, getStoredToken, hasTrustedToken, loadState, postJson, storeToken, uploadFile } from './api';
import { Button, Card, DataTable, Input, JsonBlock, StatCard } from './components';
import type { AdminState, ConfigField, JsonObject } from './types';

type PageId = 'dashboard' | 'ingest' | 'config' | 'rdf' | 'structures' | 'literature' | 'ops';
type ThemeId = 'aurora' | 'graphite' | 'emerald' | 'rose' | 'light';
type UploadResultRow = {
  file_name: string;
  status: string;
  tone: 'success' | 'deduped' | 'failed';
  detail: string;
  uploaded_path?: string;
  document_id?: string;
  job_id?: string;
  deduplicated?: boolean;
};

const THEME_KEY = 'scifinderRouteAdminTheme';

const themes: Array<{ id: ThemeId; label: string }> = [
  { id: 'aurora', label: 'Aurora' },
  { id: 'graphite', label: 'Graphite' },
  { id: 'emerald', label: 'Emerald' },
  { id: 'rose', label: 'Rose' },
  { id: 'light', label: 'Light' }
];

function initialTheme(): ThemeId {
  const saved = localStorage.getItem(THEME_KEY);
  return themes.some((theme) => theme.id === saved) ? (saved as ThemeId) : 'aurora';
}

const pages: Array<{ id: PageId; label: string; description: string }> = [
  { id: 'dashboard', label: 'Dashboard', description: '运行状态与关键指标' },
  { id: 'ingest', label: '导入与任务', description: '上传、扫描、解析队列' },
  { id: 'config', label: '配置', description: '集成、运行时、热配置' },
  { id: 'rdf', label: 'RDF 反应', description: 'CAS 反应记录与 molfile' },
  { id: 'structures', label: '结构检索', description: '相似度、子结构、文本过滤' },
  { id: 'literature', label: '文献 / Zotero', description: '端点、候选链接、写回' },
  { id: 'ops', label: '运维诊断', description: '索引、备份、回收站、配置警告' }
];

const configFields: ConfigField[] = [
  { section: 'integrations', name: 'llm_provider', label: 'LLM 提供商', type: 'select', options: ['openai_compatible', 'openai_chat', 'openai_responses', 'gemini', 'claude'] },
  { section: 'integrations', name: 'llm_enabled', label: '启用 LLM', type: 'bool' },
  { section: 'integrations', name: 'llm_api_key', label: 'LLM API Token', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'integrations', name: 'llm_endpoint', label: 'LLM 端点', placeholder: 'https://api.openai.com/v1' },
  { section: 'integrations', name: 'llm_model', label: 'LLM 模型', placeholder: 'gpt-4o-mini / gemini-2.5-pro' },
  { section: 'integrations', name: 'embedding_api_key', label: '嵌入 API Token', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'integrations', name: 'embedding_endpoint', label: '嵌入端点', placeholder: 'http://embedding:8000/v1' },
  { section: 'integrations', name: 'embedding_model', label: '嵌入模型', placeholder: 'bge-m3' },
  { section: 'integrations', name: 'ocr_provider', label: 'OCR 提供商', type: 'select', options: ['generic', 'mineru', 'paddleocr_vl'] },
  { section: 'integrations', name: 'ocr_api_key', label: 'OCR API Token', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'integrations', name: 'ocr_endpoint', label: 'OCR 端点' },
  { section: 'integrations', name: 'ocr_model', label: 'OCR 模型', placeholder: 'mineru-layout / PaddleOCR-VL-1.6' },
  { section: 'integrations', name: 'document_parser_api_key', label: '文档解析 API Token', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'integrations', name: 'document_parser_endpoint', label: '文档解析端点' },
  { section: 'integrations', name: 'document_parser_model', label: '文档解析模型', placeholder: 'pymupdf / mineru' },
  { section: 'integrations', name: 'document_parser_fallback', label: '解析失败回退', type: 'bool' },
  { section: 'integrations', name: 'structure_recognition_api_key', label: '结构识别 API Token', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'integrations', name: 'structure_recognition_endpoint', label: '结构识别端点' },
  { section: 'integrations', name: 'structure_recognition_model', label: '结构识别模型', placeholder: 'decimer / molscribe / osra' },
  { section: 'integrations', name: 'postgres_url', label: 'PostgreSQL URL', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'server', name: 'max_workers', label: '最大工作线程', type: 'number', min: '1' },
  { section: 'server', name: 'async_jobs', label: '异步任务', type: 'bool' },
  { section: 'server', name: 'storage_backend', label: '存储后端', type: 'select', options: ['sqlite', 'postgres'] },
  { section: 'queue', name: 'backend', label: '队列后端', type: 'select', options: ['sqlite', 'redis'] },
  { section: 'queue', name: 'redis_url', label: 'Redis URL', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'security', name: 'allow_external_paths', label: '允许外部路径', type: 'bool' },
  { section: 'security', name: 'token', label: '配置令牌', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'ingest', name: 'scan_extensions', label: '扫描扩展名', type: 'list', placeholder: '.pdf,.html,.mhtml,.rdf' },
  { section: 'thresholds', name: 'verification_confidence_threshold', label: '验证置信阈值', type: 'number', min: '0', max: '1', step: '0.01' },
  { section: 'extraction', name: 'llm_schema_version', label: 'LLM Schema 版本' },
  { section: 'extraction', name: 'llm_prompt_profile', label: 'LLM 提示词配置' },
  { section: 'extraction', name: 'llm_cost_limit_usd', label: 'LLM 成本上限 USD', type: 'number', min: '0', step: '0.01' },
  { section: 'retention', name: 'evidence_retention_days', label: '证据保留天数', type: 'number', min: '1' },
  { section: 'retention', name: 'cache_retention_days', label: '缓存保留天数', type: 'number', min: '1' },
  { section: 'integrations', name: 'zotero_linking_enabled', label: 'Zotero 自动链接', type: 'bool' }
];

const configFieldByKey = new Map(configFields.map((field) => [`${field.section}.${field.name}`, field]));

const integrationGroups = [
  { id: 'llm', eyebrow: 'LLM', title: 'LLM 结构化', description: '用于反应步骤结构化、证据整理和 Zotero 规则之外的补充判断。', fields: ['integrations.llm_provider', 'integrations.llm_enabled', 'integrations.llm_endpoint', 'integrations.llm_model', 'integrations.llm_api_key'], modelKey: 'integrations.llm_model' },
  { id: 'embedding', eyebrow: 'Embedding', title: '嵌入模型', description: '用于语义召回和向量索引重建，通常需要 OpenAI 兼容 /embeddings 与 /models。', fields: ['integrations.embedding_endpoint', 'integrations.embedding_model', 'integrations.embedding_api_key'], modelKey: 'integrations.embedding_model' },
  { id: 'ocr', eyebrow: 'OCR', title: 'OCR 识别', description: '用于扫描件和页面视觉证据抽取；不同提供商的端点形态可能不同。', fields: ['integrations.ocr_provider', 'integrations.ocr_endpoint', 'integrations.ocr_model', 'integrations.ocr_api_key'], modelKey: 'integrations.ocr_model' },
  { id: 'document_parser', eyebrow: 'Parser', title: '文档解析', description: '用于 PDF/RTF/HTML 正文解析和失败回退策略。', fields: ['integrations.document_parser_endpoint', 'integrations.document_parser_model', 'integrations.document_parser_api_key', 'integrations.document_parser_fallback'], modelKey: 'integrations.document_parser_model' },
  { id: 'structure_recognition', eyebrow: 'Structure', title: '结构识别', description: '用于图片结构识别和结构敏感证据补充。', fields: ['integrations.structure_recognition_endpoint', 'integrations.structure_recognition_model', 'integrations.structure_recognition_api_key'], modelKey: 'integrations.structure_recognition_model' }
] as const;

const runtimeGroups = [
  { eyebrow: 'Runtime', title: '服务运行时', fields: ['server.storage_backend', 'server.max_workers', 'server.async_jobs', 'security.allow_external_paths', 'security.token'] },
  { eyebrow: 'Queue', title: '队列与缓存', fields: ['queue.backend', 'queue.redis_url', 'retention.evidence_retention_days', 'retention.cache_retention_days'] },
  { eyebrow: 'Ingest', title: '导入与抽取', fields: ['ingest.scan_extensions', 'thresholds.verification_confidence_threshold', 'extraction.llm_schema_version', 'extraction.llm_prompt_profile', 'extraction.llm_cost_limit_usd'] },
  { eyebrow: 'Literature', title: 'Zotero 链接策略', fields: ['integrations.zotero_linking_enabled'] }
] as const;

function authError(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  return text.includes('Invalid or missing admin token') ? '需要管理令牌：请登录后重试' : text;
}

function valueAt(config: JsonObject, section: string, name: string): unknown {
  const group = config[section];
  return group && typeof group === 'object' ? (group as JsonObject)[name] : undefined;
}

function shortName(path: unknown): string {
  return String(path || '').replace(/\\/g, '/').split('/').filter(Boolean).pop() || String(path || '');
}

function bytes(value: unknown): string {
  const n = Number(value || 0);
  if (n > 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n > 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

export function App() {
  const [token, setToken] = useState(getStoredToken());
  const [trusted, setTrusted] = useState(hasTrustedToken());
  const [state, setState] = useState<AdminState | null>(null);
  const [page, setPage] = useState<PageId>('dashboard');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [authRequired, setAuthRequired] = useState(true);
  const [theme, setTheme] = useState<ThemeId>(initialTheme);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  async function refresh(nextToken = token, silent = false) {
    try {
      const data = await loadState(nextToken);
      setState(data);
      setAuthRequired(Boolean(data.auth_required));
      setError('');
      if (!silent) setMessage('状态已刷新');
      return data;
    } catch (err) {
      setError(authError(err));
      throw err;
    }
  }

  useEffect(() => {
    refresh(token, true).catch(() => undefined);
  }, []);

  useEffect(() => {
    setMessage('');
    setError('');
    setSidebarOpen(false);
  }, [page]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await refresh(token, true);
      storeToken(token, trusted);
      setMessage(trusted ? '已登录，并信任此设备' : '已登录，本次会话有效');
    } catch (err) {
      clearToken();
      setError(authError(err));
    } finally {
      setBusy(false);
    }
  }

  function logout() {
    clearToken();
    setToken('');
    setState(null);
    setMessage('已退出登录');
  }

  async function guarded<T>(action: () => Promise<T>, success?: string): Promise<T | undefined> {
    setBusy(true);
    try {
      const result = await action();
      if (success) setMessage(success);
      setError('');
      return result;
    } catch (err) {
      setError(authError(err));
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  if (!state) {
    return <LoginScreen token={token} setToken={setToken} trusted={trusted} setTrusted={setTrusted} login={login} busy={busy} error={error} />;
  }

  const active = pages.find((item) => item.id === page) || pages[0];

  return (
    <div className="app-shell">
      {sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭菜单" onClick={() => setSidebarOpen(false)} />}
      <aside className={sidebarOpen ? 'sidebar open' : 'sidebar'}>
        <div className="brand-block">
          <div className="brand-mark">SR</div>
          <div>
            <strong>SciFinder Route</strong>
            <span>Admin Console</span>
          </div>
        </div>
        <nav className="nav-list" aria-label="管理控制台分区导航">
          {pages.map((item) => (
            <button key={item.id} className={page === item.id ? 'nav-item active' : 'nav-item'} onClick={() => setPage(item.id)}>
              <span>{item.label}</span>
              <small>{item.description}</small>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="status-pill">{String(state.health.status || 'unknown').toUpperCase()}</span>
          <Button variant="ghost" size="sm" onClick={logout}>退出</Button>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <Button className="mobile-menu-button" variant="ghost" onClick={() => setSidebarOpen(true)}>菜单</Button>
          <div>
            <p className="eyebrow">{active.description}</p>
            <h1>{active.label}</h1>
          </div>
          <div className="topbar-actions">
            <span className="subtle">{authRequired ? 'Token protected' : 'Trusted local mode'}</span>
            <label className="theme-select" aria-label="主题颜色">
              <span>Theme</span>
              <select value={theme} onChange={(event) => setTheme(event.target.value as ThemeId)}>
                {themes.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
            <Button variant="secondary" onClick={() => guarded(() => refresh(), '状态已刷新')} loading={busy}>刷新</Button>
          </div>
        </header>
        {(message || error) && <div className={error ? 'notice error' : 'notice'}>{error || message}</div>}
        {page === 'dashboard' && <Dashboard state={state} />}
        {page === 'ingest' && <IngestPage token={token} state={state} guarded={guarded} refresh={refresh} />}
        {page === 'config' && <ConfigPage token={token} state={state} guarded={guarded} refresh={refresh} />}
        {page === 'rdf' && <RdfPage token={token} guarded={guarded} />}
        {page === 'structures' && <StructurePage token={token} state={state} guarded={guarded} />}
        {page === 'literature' && <LiteraturePage token={token} state={state} guarded={guarded} />}
        {page === 'ops' && <OpsPage token={token} state={state} guarded={guarded} refresh={refresh} />}
      </main>
    </div>
  );
}

function LoginScreen({ token, setToken, trusted, setTrusted, login, busy, error }: { token: string; setToken: (value: string) => void; trusted: boolean; setTrusted: (value: boolean) => void; login: (event: FormEvent) => void; busy: boolean; error: string }) {
  return (
    <main className="login-layout">
      <section className="login-card card">
        <div className="brand-block large">
          <div className="brand-mark">SR</div>
          <div>
            <p className="eyebrow">NAS 控制台</p>
            <h1>SciFinder Route MCP</h1>
          </div>
        </div>
        <p className="muted">输入管理令牌以访问配置、导入、RDF 反应和文献链接面板。未配置鉴权的本地可信部署会自动进入。</p>
        <form onSubmit={login} className="login-form">
          <Input label="管理令牌" type="password" value={token} onChange={(event) => setToken(event.target.value)} placeholder="X-Scifinder-Route-Token" autoComplete="current-password" />
          <label className="check-row">
            <input type="checkbox" checked={trusted} onChange={(event) => setTrusted(event.target.checked)} />
            <span>信任此设备，重启浏览器后仍保持登录</span>
          </label>
          {error && <div className="error-box">{error}</div>}
          <Button loading={busy} fullWidth>进入控制台</Button>
        </form>
      </section>
    </main>
  );
}

function Dashboard({ state }: { state: AdminState }) {
  const health = state.health;
  const production = state.production;
  const storage = (production.storage_usage as JsonObject | undefined) || {};
  return (
    <div className="page-stack">
      <section className="metrics-grid">
        <StatCard label="文档数" value={String(health.documents ?? 0)} />
        <StatCard label="反应步骤" value={String(health.reaction_steps ?? 0)} />
        <StatCard label="OCR 积压" value={String(health.ocr_backlog ?? 0)} tone={Number(health.ocr_backlog || 0) ? 'warn' : 'good'} />
        <StatCard label="异步任务" value={health.async_jobs ? '启用' : '停用'} tone={health.async_jobs ? 'good' : 'warn'} />
      </section>
      <div className="grid two">
        <Card eyebrow="Runtime" title="基础运行信息">
          <div className="info-list">
            <Info label="配置文件" value={shortName(health.config_path)} />
            <Info label="存储后端" value={String((state.config.server as JsonObject | undefined)?.storage_backend ?? '')} />
            <Info label="队列后端" value={String((state.config.queue as JsonObject | undefined)?.backend ?? '')} />
            <Info label="化合物" value={String(production.compound_count ?? 0)} />
          </div>
        </Card>
        <Card eyebrow="Storage" title="NAS 存储使用">
          <DataTable<JsonObject> rows={Object.entries(storage).map(([name, item]) => ({ name, ...(item as JsonObject) }))} columns={[{ key: 'name', label: '路径' }, { key: 'files', label: '文件数' }, { key: 'bytes', label: '大小', render: (row) => bytes(row.bytes) }]} />
        </Card>
      </div>
      <Card eyebrow="Production" title="诊断快照">
        <JsonBlock value={production} maxHeight={420} />
      </Card>
    </div>
  );
}

function IngestPage({ token, state, guarded, refresh }: PageProps & { refresh: () => Promise<AdminState> }) {
  const [files, setFiles] = useState<File[]>([]);
  const [uploadStatus, setUploadStatus] = useState('');
  const [uploadResults, setUploadResults] = useState<UploadResultRow[]>([]);

  async function uploadSelectedFiles() {
    if (!files.length) return;
    const results: UploadResultRow[] = [];
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      setUploadStatus(`正在上传 ${index + 1}/${files.length}: ${file.name}`);
      try {
        const result = await uploadFile(token, file);
        const job = result.job as JsonObject | undefined;
        const document = result.document as JsonObject | undefined;
        const deduplicated = Boolean(result.deduplicated);
        results.push({
          file_name: file.name,
          status: deduplicated ? '已去重' : String((job?.status as string | undefined) || '已导入'),
          tone: deduplicated ? 'deduped' : 'success',
          detail: deduplicated ? '服务器检测到重复文档，已跳过写入' : String((job?.stage as string | undefined) || result.uploaded_path || '已提交导入'),
          uploaded_path: String(result.uploaded_path || ''),
          document_id: String(document?.id || ''),
          job_id: String(job?.id || ''),
          deduplicated
        });
      } catch (error) {
        results.push({
          file_name: file.name,
          status: '失败',
          tone: 'failed',
          detail: error instanceof Error ? error.message : String(error)
        });
      }
    }
    setUploadResults(results);
    const successCount = results.filter((item) => item.status !== '失败').length;
    setUploadStatus(`完成：${successCount}/${results.length} 个文件已处理`);
    await refresh();
  }

  function clearSelection() {
    setFiles([]);
    setUploadResults([]);
    setUploadStatus('');
  }

  const uploadCounts = uploadResults.reduce(
    (counts, item) => ({ ...counts, [item.tone]: counts[item.tone] + 1 }),
    { success: 0, deduped: 0, failed: 0 }
  );

  return (
    <div className="page-stack">
      <div className="grid two">
        <Card
          eyebrow="Upload"
          title="上传并导入"
          extra={
            <div className="button-row">
              <Button disabled={!files.length} onClick={() => guarded(uploadSelectedFiles)}>批量上传并导入</Button>
              <Button variant="ghost" disabled={!files.length && !uploadResults.length} onClick={clearSelection}>清空</Button>
            </div>
          }
        >
          <input
            className="file-input"
            type="file"
            multiple
            accept=".pdf,.rtf,.rdf,.html,.htm,.mhtml,.mht,.md,.markdown,.txt"
            onChange={(event) => {
              setFiles(Array.from(event.target.files || []));
              setUploadResults([]);
              setUploadStatus('已选择文件，准备上传');
            }}
          />
          <p className="muted">支持批量选择 PDF/RTF/RDF/HTML/MHTML/Markdown/TXT，系统会逐个上传并导入。</p>
          <div className="upload-summary">
            <span>{files.length ? `已选择 ${files.length} 个文件` : '尚未选择文件'}</span>
            {uploadStatus && <strong>{uploadStatus}</strong>}
          </div>
          {files.length > 0 && (
            <ul className="upload-file-list">
              {files.map((file) => <li key={`${file.name}-${file.size}-${file.lastModified}`}>{file.name}</li>)}
            </ul>
          )}
          <p className="muted">支持 PDF/RTF/RDF/HTML/MHTML/Markdown/TXT。上传仍会经过后端扩展名、嗅探和安全校验。</p>
        </Card>
        <Card eyebrow="Inbox" title="扫描收件箱" extra={<Button onClick={() => guarded(async () => { const result = await postJson<JsonObject>('/api/scan', token); await refresh(); return result; }, '扫描完成')}>扫描</Button>}>
          <p className="muted">从服务端可见 inbox 中登记新增 SciFinder 导出文件。不会绕过导入规则。</p>
        </Card>
      </div>
      {uploadResults.length > 0 && (
        <Card eyebrow="Upload Results" title="批量上传结果">
          <div className="upload-result-summary" aria-label="批量上传结果统计">
            <span className="success">成功 {uploadCounts.success}</span>
            <span className="deduped">去重 {uploadCounts.deduped}</span>
            <span className="failed">失败 {uploadCounts.failed}</span>
          </div>
          <DataTable<UploadResultRow>
            rows={uploadResults}
            columns={[
              { key: 'file_name', label: '文件' },
              { key: 'status', label: '状态', render: (row) => <StatusBadge tone={row.tone}>{row.status}</StatusBadge> },
              { key: 'detail', label: '详情' },
              { key: 'document_id', label: '文档 ID' },
              { key: 'job_id', label: '任务 ID' },
              { key: 'uploaded_path', label: '写入路径' }
            ]}
          />
        </Card>
      )}
      <Card eyebrow="Jobs" title="最近解析任务" extra={<Button variant="secondary" onClick={() => guarded(() => postJson('/api/retry-failed', token), '已提交失败任务重试')}>重试失败任务</Button>}>
        <DataTable rows={state.jobs} columns={[{ key: 'id', label: 'ID' }, { key: 'status', label: '状态' }, { key: 'stage', label: '阶段' }, { key: 'error', label: '错误' }]} />
      </Card>
    </div>
  );
}

function StatusBadge({ tone, children }: { tone: 'success' | 'deduped' | 'failed'; children: string }) {
  return <span className={`status-badge ${tone}`}>{children}</span>;
}

function ConfigPage({ token, state, guarded, refresh }: PageProps & { refresh: () => Promise<AdminState> }) {
  const [values, setValues] = useState<Record<string, string>>(() => buildConfigValues(state.config));
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [actionResults, setActionResults] = useState<Record<string, JsonObject>>({});

  function update(key: string, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function buildPayload(includeEmptySecrets = false) {
    const payload: JsonObject = {};
    for (const field of configFields) {
      const key = `${field.section}.${field.name}`;
      const raw = values[key]?.trim() ?? '';
      if (field.secret && !raw && !includeEmptySecrets) continue;
      const section = (payload[field.section] ||= {}) as JsonObject;
      if (field.type === 'list') section[field.name] = raw.split(',').map((item) => item.trim()).filter(Boolean);
      else if (field.type === 'number') section[field.name] = raw === '' ? undefined : Number(raw);
      else if (field.type === 'bool') section[field.name] = raw === 'true';
      else section[field.name] = raw || null;
    }
    return payload;
  }

  async function save() {
    const payload = buildPayload(false);
    await postJson('/api/config', token, payload);
    const next = await refresh();
    setValues(buildConfigValues(next.config));
  }

  function rememberResult(key: string, data: JsonObject) {
    setActionResults((current) => ({ ...current, [key]: data }));
  }

  async function runEndpointTest(kind: string) {
    const data = await postJson<JsonObject>('/api/integration/test', token, { kind, overrides: buildPayload(false) });
    rememberResult(kind, data);
    return data;
  }

  async function loadModels(kind: string) {
    const data = await postJson<{ models?: string[] } & JsonObject>('/api/integration/models', token, { kind, overrides: buildPayload(false) });
    setModels((current) => ({ ...current, [kind]: data.models || [] }));
    rememberResult(`${kind}:models`, data);
    if (data.models?.length) {
      const group = integrationGroups.find((item) => item.id === kind);
      const modelKey = group?.modelKey;
      if (modelKey && !values[modelKey]) update(modelKey, data.models[0]);
    }
    return data;
  }

  return (
    <div className="page-stack">
      <section className="config-hero card">
        <div>
          <p className="eyebrow">Hot Config</p>
          <div className="title">热配置工作区</div>
          <p className="muted">每个集成单独测试和拉取模型。按钮会使用当前表单内容，不需要先保存；保存后才会写入 `webui-config.yaml`。</p>
        </div>
        <Button onClick={() => guarded(save, '配置已保存并重载')}>保存并重载</Button>
      </section>

      <section className="config-section">
        <div className="section-heading"><p className="eyebrow">Model Providers</p><h2>模型与外部能力</h2></div>
        <div className="config-grid">
          {integrationGroups.map((group) => (
            <ConfigIntegrationCard
              key={group.id}
              group={group}
              values={values}
              models={models[group.id] || []}
              testResult={actionResults[group.id]}
              modelResult={actionResults[`${group.id}:models`]}
              onChange={update}
              onTest={() => guarded(() => runEndpointTest(group.id), `${group.title} 测试完成`)}
              onLoadModels={() => guarded(() => loadModels(group.id), `${group.title} 模型拉取完成`)}
            />
          ))}
        </div>
      </section>

      <section className="config-section">
        <div className="section-heading"><p className="eyebrow">Service Controls</p><h2>服务、队列与抽取策略</h2></div>
        <div className="config-grid two-column">
          {runtimeGroups.map((group) => <ConfigFieldCard key={group.title} group={group} values={values} onChange={update} />)}
          <Card eyebrow="Postgres" title="PostgreSQL 存储" extra={<Button variant="secondary" onClick={() => guarded(() => runEndpointTest('postgres'), 'Postgres 测试完成')}>测试 Postgres</Button>}>
            <div className="form-grid single">
              <ConfigControl field={configFieldByKey.get('integrations.postgres_url')!} value={values['integrations.postgres_url'] ?? ''} onChange={update} />
            </div>
            <ActionResult result={actionResults.postgres} />
          </Card>
          <Card eyebrow="Zotero MCP" title="文献源连通性" extra={<Button variant="secondary" onClick={() => guarded(() => runEndpointTest('zotero_mcp'), 'Zotero MCP 测试完成')}>测试 Zotero MCP</Button>}>
            <p className="muted">Zotero 端点地址在“文献 / Zotero”页面维护，这里只测试已保存的端点组。</p>
            <ActionResult result={actionResults.zotero_mcp} />
          </Card>
        </div>
      </section>
      <p className="muted">端口、卷挂载、Docker 网络和重启策略仍应在 `.env` / Docker Compose 中修改。</p>
    </div>
  );
}

type ConfigGroup = { eyebrow: string; title: string; fields: readonly string[] };
type IntegrationGroup = ConfigGroup & { id: string; description: string; modelKey: string };

function ConfigIntegrationCard({
  group,
  values,
  models,
  testResult,
  modelResult,
  onChange,
  onTest,
  onLoadModels
}: {
  group: IntegrationGroup;
  values: Record<string, string>;
  models: string[];
  testResult?: JsonObject;
  modelResult?: JsonObject;
  onChange: (key: string, value: string) => void;
  onTest: () => void;
  onLoadModels: () => void;
}) {
  return (
    <Card eyebrow={group.eyebrow} title={group.title} extra={<div className="button-row"><Button variant="secondary" onClick={onTest}>测试端点</Button><Button variant="ghost" onClick={onLoadModels}>拉取模型</Button></div>}>
      <p className="muted config-description">{group.description}</p>
      <div className="form-grid single">
        {group.fields.map((key) => {
          const field = configFieldByKey.get(key);
          if (!field) return null;
          return <ConfigControl key={key} field={field} value={values[key] ?? ''} onChange={onChange} suggestions={key === group.modelKey ? models : undefined} />;
        })}
      </div>
      <ModelSuggestions models={models} modelKey={group.modelKey} onChange={onChange} />
      <div className="result-grid">
        <ActionResult title="端点测试" result={testResult} />
        <ActionResult title="模型拉取" result={modelResult} />
      </div>
    </Card>
  );
}

function ConfigFieldCard({ group, values, onChange }: { group: ConfigGroup; values: Record<string, string>; onChange: (key: string, value: string) => void }) {
  return (
    <Card eyebrow={group.eyebrow} title={group.title}>
      <div className="form-grid single">
        {group.fields.map((key) => {
          const field = configFieldByKey.get(key);
          return field ? <ConfigControl key={key} field={field} value={values[key] ?? ''} onChange={onChange} /> : null;
        })}
      </div>
    </Card>
  );
}

function ModelSuggestions({ models, modelKey, onChange }: { models: string[]; modelKey: string; onChange: (key: string, value: string) => void }) {
  if (!models.length) return null;
  return (
    <div className="model-list" aria-label="已拉取模型列表">
      {models.slice(0, 12).map((model) => <button key={model} type="button" onClick={() => onChange(modelKey, model)}>{model}</button>)}
      {models.length > 12 && <span>+{models.length - 12} more</span>}
    </div>
  );
}

function ActionResult({ title, result }: { title?: string; result?: JsonObject }) {
  if (!result) return <div className="action-result empty">{title && <strong>{title}</strong>}<span>尚未执行</span></div>;
  const status = String(result.status || (result.error ? 'error' : 'ok'));
  const detail = String(result.detail || result.error || JSON.stringify(result));
  const tone = status === 'ok' ? 'ok' : status === 'unknown' ? 'unknown' : 'error';
  return (
    <div className={`action-result ${tone}`}>
      {title && <strong>{title}</strong>}
      <span>{status}</span>
      <p>{detail}</p>
    </div>
  );
}

function RdfPage({ token, guarded }: Pick<PageProps, 'token' | 'guarded'>) {
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState('25');
  const [rows, setRows] = useState<JsonObject[]>([]);
  const [detail, setDetail] = useState<JsonObject | null>(null);
  async function load() {
    const url = `/api/rdf/reactions?limit=${encodeURIComponent(limit)}${query ? `&q=${encodeURIComponent(query)}` : ''}`;
    const data = await getJson<JsonObject[]>(url, token);
    setRows(data);
  }
  async function open(id: unknown) {
    const data = await getJson<JsonObject>(`/api/rdf/reactions/${encodeURIComponent(String(id))}`, token);
    setDetail(data);
  }
  return (
    <div className="page-stack">
      <Card eyebrow="RDF Viewer" title="反应记录" extra={<Button onClick={() => guarded(load, 'RDF 反应已加载')}>加载 RDF 反应</Button>}>
        <div className="form-grid compact-form">
          <Input label="CAS / 文件名 / 标题" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="CAS 反应号、结构 CAS RN、PDF/RDF 文件名或标题" />
          <Input label="数量限制" type="number" min="1" value={limit} onChange={(event) => setLimit(event.target.value)} />
        </div>
        <DataTable rows={rows} columns={[{ key: 'source_file_path', label: '来源文件', render: (row) => shortName(row.source_file_path || row.source_title || row.source_document_id) }, { key: 'record_index', label: '记录' }, { key: 'scheme_id', label: '方案' }, { key: 'step_id', label: '步骤' }, { key: 'cas_reaction_number', label: 'CAS 反应号' }, { key: 'yield_text', label: '收率' }, { key: 'structure_count', label: '结构数' }, { key: 'open', label: '打开', render: (row) => <Button size="sm" variant="ghost" onClick={() => guarded(() => open(row.id), '反应详情已加载')}>打开</Button> }]} />
      </Card>
      <Card eyebrow="Detail" title="反应详情与结构">
        <JsonBlock value={detail || { hint: '选择一条反应以查看 molfile、试剂、催化剂、溶剂和引用。' }} maxHeight={560} />
      </Card>
    </div>
  );
}

function StructurePage({ token, state, guarded }: PageProps) {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('similarity');
  const [rows, setRows] = useState<JsonObject[]>([]);
  async function search() {
    if (mode === 'text') {
      setRows(await getJson<JsonObject[]>(`/api/rdf/structures?q=${encodeURIComponent(query)}&limit=50`, token));
      return;
    }
    const endpoint = mode === 'similarity' ? '/api/chem/similarity-search' : '/api/chem/substructure-search';
    const data = await postJson<{ results?: JsonObject[] }>(endpoint, token, { query, query_type: mode === 'similarity' ? 'smiles' : 'smarts', min_similarity: 0.2, limit: 50 });
    setRows(data.results || []);
  }
  return (
    <div className="page-stack">
      <div className="grid two">
        <Card eyebrow="RDKit" title="结构检索" extra={<Button onClick={() => guarded(search, '结构检索完成')}>检索</Button>}>
          <div className="form-grid compact-form">
            <Input label="查询" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="SMILES、SMARTS、CAS 或名称" />
            <label className="form-group">模式<select value={mode} onChange={(event) => setMode(event.target.value)}><option value="similarity">相似度</option><option value="substructure">子结构</option><option value="text">文本过滤</option></select></label>
          </div>
        </Card>
        <Card eyebrow="Chem Status" title="化学索引状态" extra={<Button variant="secondary" onClick={() => guarded(() => postJson('/api/chem/install-rdkit', token), 'RDKit 安装任务已启动')}>安装 RDKit</Button>}>
          <JsonBlock value={(state.production.chem as JsonObject | undefined) || {}} maxHeight={260} />
        </Card>
      </div>
      <Card eyebrow="Results" title="结构结果">
        <DataTable rows={rows} columns={[{ key: 'name', label: '名称' }, { key: 'role', label: '角色' }, { key: 'cas_rn', label: 'CAS' }, { key: 'molfile_version', label: '版本' }, { key: 'similarity', label: '评分' }, { key: 'rdf_reaction_id', label: '反应 ID' }]} />
      </Card>
    </div>
  );
}

function LiteraturePage({ token, state, guarded }: PageProps) {
  const [endpoint, setEndpoint] = useState({ alias: '', group_name: '', url: '', priority: '100', timeout_seconds: '10', enabled: 'true', write_note_enabled: 'false', headers: '' });
  const [endpoints, setEndpoints] = useState<JsonObject[]>([]);
  const [documentId, setDocumentId] = useState('');
  const [jobs, setJobs] = useState<JsonObject[]>([]);
  const [links, setLinks] = useState<JsonObject[]>([]);
  async function loadEndpoints() { setEndpoints(await getJson<JsonObject[]>('/api/zotero/endpoints', token)); }
  async function saveEndpoint() {
    await postJson('/api/zotero/endpoints', token, { ...endpoint, priority: Number(endpoint.priority || 100), timeout_seconds: Number(endpoint.timeout_seconds || 10), enabled: endpoint.enabled === 'true', write_note_enabled: endpoint.write_note_enabled === 'true', headers: endpoint.headers ? JSON.parse(endpoint.headers) : {} });
    await loadEndpoints();
  }
  async function loadLiterature() {
    const qs = documentId ? `?document_id=${encodeURIComponent(documentId)}&limit=50` : '?status=candidate&limit=50';
    setLinks(await getJson<JsonObject[]>(`/api/literature/links${qs}`, token));
    setJobs(await getJson<JsonObject[]>('/api/literature/jobs?limit=20', token));
  }
  return (
    <div className="page-stack">
      <Card eyebrow="Zotero MCP" title="文献源地址" extra={<div className="button-row"><Button onClick={() => guarded(saveEndpoint, 'Zotero 端点已保存')}>保存地址</Button><Button variant="secondary" onClick={() => guarded(loadEndpoints, '端点已加载')}>加载端点</Button></div>}>
        <div className="form-grid">
          <Input label="地址别名" value={endpoint.alias} onChange={(e) => setEndpoint({ ...endpoint, alias: e.target.value })} placeholder="lab-zotero-lan" />
          <Input label="文献源组名" value={endpoint.group_name} onChange={(e) => setEndpoint({ ...endpoint, group_name: e.target.value })} placeholder="primary-library" />
          <Input label="地址 URL" value={endpoint.url} onChange={(e) => setEndpoint({ ...endpoint, url: e.target.value })} placeholder="http://host:23120/mcp" />
          <Input label="优先级" type="number" value={endpoint.priority} onChange={(e) => setEndpoint({ ...endpoint, priority: e.target.value })} />
          <Input label="超时秒数" type="number" step="0.5" value={endpoint.timeout_seconds} onChange={(e) => setEndpoint({ ...endpoint, timeout_seconds: e.target.value })} />
          <Input label="请求头 JSON" value={endpoint.headers} onChange={(e) => setEndpoint({ ...endpoint, headers: e.target.value })} placeholder='{"Authorization":"Bearer ..."}' />
          <label className="form-group">启用<select value={endpoint.enabled} onChange={(e) => setEndpoint({ ...endpoint, enabled: e.target.value })}><option value="true">启用</option><option value="false">停用</option></select></label>
          <label className="form-group">允许写回笔记<select value={endpoint.write_note_enabled} onChange={(e) => setEndpoint({ ...endpoint, write_note_enabled: e.target.value })}><option value="false">禁止</option><option value="true">允许</option></select></label>
        </div>
        <DataTable rows={endpoints} columns={[{ key: 'alias', label: '别名' }, { key: 'group_name', label: '组名' }, { key: 'url', label: 'URL' }, { key: 'enabled', label: '启用' }, { key: 'priority', label: '优先级' }, { key: 'last_status', label: '状态' }, { key: 'test', label: '测试', render: (row) => <Button size="sm" variant="ghost" onClick={() => guarded(() => postJson('/api/zotero/endpoints/test', token, { id: row.id }), '端点测试完成')}>测试</Button> }]} />
      </Card>
      <Card eyebrow="Literature" title="候选链接与任务" extra={<div className="button-row"><Button onClick={() => guarded(() => postJson('/api/literature/jobs/start', token, { document_id: documentId }), 'Zotero 链接任务已启动')}>启动链接</Button><Button variant="secondary" onClick={() => guarded(loadLiterature, '文献链接已加载')}>加载链接</Button></div>}>
        <Input label="文档 ID" value={documentId} onChange={(e) => setDocumentId(e.target.value)} placeholder="可选；留空查看 candidate" />
        <div className="summary-strip"><span>OCR 积压: {String(state.health.ocr_backlog ?? 0)}</span><span>低置信 DOI: {String(((state.production.doi_low_confidence_queue as unknown[]) || []).length)}</span><span>文献候选: {String(((state.production.literature_candidates as unknown[]) || []).length)}</span></div>
        <h3>文献任务</h3>
        <DataTable rows={jobs} columns={[{ key: 'id', label: 'ID' }, { key: 'document_id', label: '文档' }, { key: 'status', label: '状态' }, { key: 'stage', label: '阶段' }, { key: 'error', label: '错误' }]} />
        <h3>候选链接</h3>
        <DataTable rows={links} columns={[{ key: 'status', label: '状态' }, { key: 'reaction_step_id', label: '反应' }, { key: 'endpoint_alias', label: '端点' }, { key: 'doi', label: 'DOI' }, { key: 'title', label: '标题' }, { key: 'confidence', label: '评分' }, { key: 'confirm', label: '确认', render: (row) => <Button size="sm" variant="ghost" onClick={() => guarded(() => postJson('/api/literature/links/confirm', token, { id: row.id }), '已确认链接')}>确认</Button> }]} />
      </Card>
    </div>
  );
}

function OpsPage({ token, state, guarded, refresh }: PageProps & { refresh: () => Promise<AdminState> }) {
  const [trash, setTrash] = useState<JsonObject[]>([]);
  return (
    <div className="page-stack">
      <div className="grid two">
        <Card eyebrow="Vector" title="向量索引" extra={<Button onClick={() => guarded(async () => { const result = await postJson('/api/vector/rebuild', token); await refresh(); return result; }, '向量索引已提交重建')}>重建</Button>}><JsonBlock value={state.production.vector_index} /></Card>
        <Card eyebrow="Backup" title="备份与清理" extra={<div className="button-row"><Button onClick={() => guarded(() => postJson('/api/backup', token), '数据库已备份')}>备份</Button><Button variant="secondary" onClick={() => guarded(() => postJson('/api/cleanup', token, { dry_run: true }), '清理试运行完成')}>清理试运行</Button></div>}><p className="muted">备份和保留清理只操作服务管理的数据目录，不修改 Docker-owned 配置。</p></Card>
      </div>
      <Card eyebrow="Trash" title="回收站" extra={<div className="button-row"><Button variant="secondary" onClick={() => guarded(async () => setTrash(await getJson<JsonObject[]>('/api/trash?limit=100', token)), '回收站已加载')}>加载</Button><Button variant="danger" onClick={() => guarded(() => postJson('/api/trash/empty', token), '回收站已清空')}>清空</Button></div>}>
        <DataTable rows={trash} columns={[{ key: 'entity_type', label: '类型' }, { key: 'id', label: 'ID' }, { key: 'title', label: '标题' }, { key: 'deleted_at', label: '删除时间' }, { key: 'restore', label: '还原', render: (row) => <Button size="sm" variant="ghost" onClick={() => guarded(() => postJson('/api/trash/restore', token, { entity_type: row.entity_type, entity_id: row.id }), '项目已还原')}>还原</Button> }]} />
      </Card>
      <div className="grid two">
        <Card eyebrow="Validation" title="配置警告"><ul className="warning-list">{(state.validation.warnings || ['暂无配置警告']).map((item) => <li key={item}>{item}</li>)}</ul></Card>
        <Card eyebrow="Jobs" title="最近解析任务"><DataTable rows={state.jobs} columns={[{ key: 'id', label: 'ID' }, { key: 'status', label: '状态' }, { key: 'stage', label: '阶段' }, { key: 'error', label: '错误' }]} /></Card>
      </div>
    </div>
  );
}

interface PageProps {
  token: string;
  state: AdminState;
  guarded: <T>(action: () => Promise<T>, success?: string) => Promise<T | undefined>;
}

function Info({ label, value }: { label: string; value: string }) {
  return <div className="info-item"><span>{label}</span><strong>{value}</strong></div>;
}

function buildConfigValues(config: JsonObject): Record<string, string> {
  const values: Record<string, string> = {};
  for (const field of configFields) {
    if (field.secret) continue;
    const value = valueAt(config, field.section, field.name);
    values[`${field.section}.${field.name}`] = Array.isArray(value) ? value.join(',') : value === undefined || value === null ? '' : String(value);
  }
  return values;
}

function ConfigControl({ field, value, onChange, suggestions }: { field: ConfigField; value: string; onChange: (key: string, value: string) => void; suggestions?: string[] }) {
  const key = `${field.section}.${field.name}`;
  if (field.type === 'select') {
    return <label className="form-group">{field.label}<select value={value} onChange={(event) => onChange(key, event.target.value)}>{(field.options || []).map((option) => <option key={option} value={option}>{option}</option>)}</select></label>;
  }
  if (field.type === 'bool') {
    return <label className="form-group">{field.label}<select value={value || 'false'} onChange={(event) => onChange(key, event.target.value)}><option value="true">启用</option><option value="false">停用</option></select></label>;
  }
  const listId = suggestions?.length ? `list-${field.section}-${field.name}` : undefined;
  return (
    <>
      <Input label={field.label} type={field.type === 'password' ? 'password' : field.type === 'number' ? 'number' : 'text'} value={value} onChange={(event) => onChange(key, event.target.value)} placeholder={field.placeholder} min={field.min} max={field.max} step={field.step} list={listId} />
      {listId && <datalist id={listId}>{suggestions?.map((item) => <option key={item} value={item} />)}</datalist>}
    </>
  );
}
