import { useState, useEffect, useRef } from 'react';
import type { JsonObject } from '../types';
import type { PageProps, ZoteroEndpointForm } from '../constants';
import { defaultZoteroEndpoint, buildConfigValues, configFieldByKey, integrationGroups, runtimeGroups, configFields } from '../constants';
import { getJson, postJson } from '../api';
import { Button, Card, Input, DataTable } from '../components';
import { AiProvidersPanel } from '../components/config/AiProvidersPanel';
import { ConfigIntegrationCard } from '../components/config/ConfigIntegrationCard';
import { ConfigFieldCard } from '../components/config/ConfigFieldCard';
import { ConfigControl } from '../components/config/ConfigControl';
import { ActionResult } from '../components/config/ActionResult';
import { RuntimePanel } from '../components/config/RuntimePanel';
import { useTranslation } from '../i18n';
import { X, Pencil, CircleCheck, CircleAlert } from 'lucide-react';

export interface ConfigPageProps extends PageProps {
  onConfigSaved: (config: JsonObject) => void;
}

type TabId = 'providers' | 'routes' | 'runtime';

export function ConfigPage({ token, state, guarded, onConfigSaved }: ConfigPageProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabId>('providers');
  const [values, setValues] = useState<Record<string, string>>(() => buildConfigValues(state.config));
  const [providers, setProviders] = useState<JsonObject[]>(() => {
    const p = (state.config.integrations as JsonObject | undefined)?.ai_providers;
    return Array.isArray(p) ? (p as JsonObject[]) : [];
  });

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
      if (field.type === 'list') {
        section[field.name] = raw.split(',').map((item) => item.trim()).filter(Boolean);
      } else if (field.type === 'number') {
        section[field.name] = raw === '' ? undefined : Number(raw);
      } else if (field.type === 'bool') {
        section[field.name] = raw === 'true';
      } else {
        section[field.name] = raw || null;
      }
    }
    const integrations = (payload.integrations ||= {}) as JsonObject;
    integrations.ai_providers = providers;
    return payload;
  }

  async function save() {
    const payload = buildPayload(false);
    const nextConfig = await postJson<JsonObject>('/api/config', token, payload);
    onConfigSaved(nextConfig);
    setValues(buildConfigValues(nextConfig));
    const p = (nextConfig.integrations as JsonObject | undefined)?.ai_providers;
    setProviders(Array.isArray(p) ? (p as JsonObject[]) : []);
  }

  function rememberResult(key: string, data: JsonObject) {
    setActionResults((current) => ({ ...current, [key]: data }));
  }

  async function runEndpointTest(kind: string) {
    const data = await postJson<JsonObject>('/api/integration/test', token, { kind, overrides: buildPayload(false) });
    rememberResult(kind, data);
    return data;
  }


  return (
    <div className="page-stack">
      <div className="config-tabs-bar">
        <div className="config-tabs-list">
          <button className={`config-tab-trigger ${activeTab === 'providers' ? 'active' : ''}`} onClick={() => setActiveTab('providers')}>
            {t('AI 供应商')}
          </button>
          <button className={`config-tab-trigger ${activeTab === 'routes' ? 'active' : ''}`} onClick={() => setActiveTab('routes')}>
            {t('功能路由')}
          </button>
          <button className={`config-tab-trigger ${activeTab === 'runtime' ? 'active' : ''}`} onClick={() => setActiveTab('runtime')}>
            {t('运行时与存储')}
          </button>
        </div>
        <Button onClick={() => guarded(save, t('配置已保存'))}>{t('保存全局配置')}</Button>
      </div>

      {/* AI Providers Tab */}
      <div className={`config-tab-content ${activeTab === 'providers' ? 'active' : ''}`}>
        <AiProvidersPanel providers={providers} setProviders={setProviders} token={token} guarded={guarded} />
      </div>

      {/* Integration Routes Tab */}
      <div className={`config-tab-content ${activeTab === 'routes' ? 'active' : ''}`}>
        <div className="section-heading">
          <p className="eyebrow">Integrations</p>
          <h2>{t('功能路由配置')}</h2>
        </div>
        <div className="route-grid">
          {integrationGroups.map((group) => (
            <ConfigIntegrationCard
              key={group.id}
              group={group}
              values={values}
              providers={providers}
              testResult={actionResults[group.id]}
              onChange={update}
              onTest={() => guarded(() => runEndpointTest(group.id), `${group.title} ${t('测试完成')}`)}
              onSave={() => guarded(save, t('配置已保存'))}
            />
          ))}
          <ZoteroRouteCard
            token={token}
            guarded={guarded}
            onTest={() => guarded(() => runEndpointTest('zotero_mcp'), t('Zotero MCP 测试完成'))}
            testResult={actionResults.zotero_mcp}
          />
        </div>
      </div>

      {/* Runtime & Storage Tab */}
      <div className={`config-tab-content ${activeTab === 'runtime' ? 'active' : ''}`}>
        <RuntimePanel
          values={values}
          onChange={update}
          onSave={() => guarded(save, t('配置已保存'))}
          onTestPostgres={() => guarded(() => runEndpointTest('postgres'), t('Postgres 测试完成'))}
          postgresResult={actionResults.postgres}
        />
      </div>
      <p className="muted" style={{ marginTop: '24px' }}>{t('端口、卷挂载、Docker 网络和重启策略仍应在 .env / Docker Compose 中修改。')}</p>
    </div>
  );
}

/* Zotero tile + modal (same pattern as ConfigIntegrationCard) */
function ZoteroRouteCard({ token, guarded, onTest, testResult }: { token: string; guarded: <T>(action: () => Promise<T>, success?: string) => Promise<T | undefined>; onTest: () => void; testResult?: JsonObject }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const backdropRef = useRef<HTMLDivElement>(null);
  
  const [endpoint, setEndpoint] = useState<ZoteroEndpointForm>(defaultZoteroEndpoint);
  const [endpoints, setEndpoints] = useState<JsonObject[]>([]);

  let statusClass = '';
  let statusLabel = t('未测试');
  if (testResult) {
    const ok = testResult.ok || testResult.success || testResult.status === 'ok';
    statusClass = ok ? 'route-card--ok' : 'route-card--err';
    statusLabel = ok ? t('已连通') : t('失败');
  }

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') setOpen(false); }
    document.addEventListener('keydown', onKey);
    loadEndpoints().catch(() => undefined);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  async function loadEndpoints() {
    setEndpoints(await getJson<JsonObject[]>('/api/zotero/endpoints', token));
  }

  async function saveEndpoint() {
    const url = endpoint.url.trim();
    if (endpoint.enabled === 'true' && !url) {
      throw new Error(t('启用的 Zotero MCP 端点必须填写 URL'));
    }
    if (url && !/^https?:\/\//.test(url)) {
      throw new Error(t('Zotero MCP URL 必须以 http:// 或 https:// 开头，例如 http://127.0.0.1:23120/mcp'));
    }
    const payload: JsonObject = {
      ...endpoint,
      url,
      priority: Number(endpoint.priority || 100),
      timeout_seconds: Number(endpoint.timeout_seconds || 10),
      enabled: endpoint.enabled === 'true',
      write_note_enabled: endpoint.write_note_enabled === 'true'
    };
    if (endpoint.headers.trim()) {
      payload.headers = JSON.parse(endpoint.headers);
    }
    await postJson('/api/zotero/endpoints', token, payload);
    await loadEndpoints();
  }

  function editEndpoint(row: JsonObject) {
    const headers = row.headers && typeof row.headers === 'object' ? JSON.stringify(row.headers, null, 2) : '';
    setEndpoint({
      id: String(row.id || ''),
      alias: String(row.alias || ''),
      group_name: String(row.group_name || ''),
      url: String(row.url || ''),
      priority: String(row.priority ?? 100),
      timeout_seconds: String(row.timeout_seconds ?? 10),
      enabled: String(row.enabled !== false),
      write_note_enabled: String(row.write_note_enabled === true),
      headers
    });
  }

  async function deleteEndpoint(row: JsonObject) {
    const id = String(row.id || '');
    if (!id || !window.confirm(t('删除 Zotero 端点') + ` ${String(row.alias || id)}？`)) return;
    await postJson('/api/zotero/endpoints/delete', token, { id });
    if (endpoint.id === id) {
      setEndpoint(defaultZoteroEndpoint);
    }
    await loadEndpoints();
  }

  return (
    <>
      <button type="button" className={`route-card ${statusClass}`} onClick={() => setOpen(true)} title={t('Zotero 文献源连通性测试')}>
        <div className="route-card-head">
          <span className="route-card-icon">📚</span>
          <span className="route-card-title">{t('Zotero MCP')}</span>
        </div>
        <div className="route-card-details">
          <div className="route-card-row">
            <span className="route-card-label">{t('测试')}</span>
            <span className="route-card-val"><span className="muted">{t('文献源连通性')}</span></span>
          </div>
        </div>
        <span className={`route-card-status ${statusClass}`}>
          {testResult ? (statusClass === 'route-card--ok' ? <CircleCheck size={13} /> : <CircleAlert size={13} />) : null}
          {statusLabel}
        </span>
        <Pencil className="route-card-edit" size={14} />
      </button>

      {open && (
        <div className="route-modal-backdrop" ref={backdropRef} onClick={(e) => { if (e.target === backdropRef.current) setOpen(false); }}>
          <div className="route-modal" role="dialog" aria-label="Zotero MCP" style={{ maxWidth: '800px', width: '90%' }}>
            <div className="route-modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <span style={{ fontSize: '24px' }}>📚</span>
                <div>
                  <p className="eyebrow">Zotero MCP</p>
                  <h3 style={{ margin: 0 }}>{t('文献源连通性')}</h3>
                </div>
              </div>
              <button className="route-modal-close" onClick={() => setOpen(false)} aria-label="Close"><X size={18} /></button>
            </div>
            <div className="route-modal-body" style={{ maxHeight: 'calc(100vh - 200px)', overflowY: 'auto' }}>
              <p className="muted" style={{ marginBottom: '16px', fontSize: '13px' }}>
                {t('默认使用本机 Streamable HTTP 端点 http://127.0.0.1:23120/mcp；远程 Zotero 必须填写完整 URL，例如 http://192.168.99.3:23120/mcp。保存或删除端点会修改 Web UI 热配置，需要 admin 权限。')}
              </p>
              
              <div style={{ background: 'var(--panel)', padding: '16px', borderRadius: '8px', marginBottom: '16px', border: '1px solid var(--border)' }}>
                <h4 style={{ margin: '0 0 12px 0' }}>{endpoint.id ? `${t('编辑文献源地址')}：${endpoint.id}` : t('新建文献源地址')}</h4>
                <div className="form-grid" style={{ marginBottom: '16px' }}>
                  <Input label={t("地址别名")} value={endpoint.alias} onChange={(e) => setEndpoint({ ...endpoint, alias: e.target.value })} placeholder="local-zotero" />
                  <Input label={t("文献源组名")} value={endpoint.group_name} onChange={(e) => setEndpoint({ ...endpoint, group_name: e.target.value })} placeholder="local-zotero" />
                  <Input label={t("地址 URL")} value={endpoint.url} onChange={(e) => setEndpoint({ ...endpoint, url: e.target.value })} placeholder="http://127.0.0.1:23120/mcp" />
                  <Input label={t('优先级')} type="number" value={endpoint.priority} onChange={(e) => setEndpoint({ ...endpoint, priority: e.target.value })} />
                  <Input label={t("超时秒数")} type="number" step="0.5" value={endpoint.timeout_seconds} onChange={(e) => setEndpoint({ ...endpoint, timeout_seconds: e.target.value })} />
                  <Input label={t("请求头 JSON")} value={endpoint.headers} onChange={(e) => setEndpoint({ ...endpoint, headers: e.target.value })} placeholder='{"Authorization":"Bearer ..."}' />
                  <label className="form-group">
                    {t('启用')}
                    <select value={endpoint.enabled} onChange={(e) => setEndpoint({ ...endpoint, enabled: e.target.value })}>
                      <option value="true">{t('启用')}</option>
                      <option value="false">{t('停用')}</option>
                    </select>
                  </label>
                  <label className="form-group">
                    {t('允许写回笔记')}
                    <select value={endpoint.write_note_enabled} onChange={(e) => setEndpoint({ ...endpoint, write_note_enabled: e.target.value })}>
                      <option value="false">{t('禁止')}</option>
                      <option value="true">{t('允许')}</option>
                    </select>
                  </label>
                </div>
                <div className="button-row">
                  <Button onClick={() => guarded(saveEndpoint, t('Zotero 端点已保存'))}>
                    {endpoint.id ? t('保存修改') : t('保存地址')}
                  </Button>
                  <Button variant="secondary" onClick={() => setEndpoint(defaultZoteroEndpoint)}>{t('新建')}</Button>
                  <Button variant="secondary" onClick={() => guarded(loadEndpoints, t('端点已加载'))}>{t('加载端点')}</Button>
                </div>
              </div>

              <DataTable
                rows={endpoints}
                columns={[
                  { key: 'alias', label: t('别名') },
                  { key: 'group_name', label: t('组名') },
                  { key: 'url', label: 'URL' },
                  { key: 'enabled', label: t('启用') },
                  { key: 'priority', label: t('优先级') },
                  { key: 'last_status', label: t('状态') },
                  { key: 'edit', label: t('编辑'), render: (row) => <Button size="sm" variant="ghost" onClick={() => editEndpoint(row)}>{t('编辑')}</Button> },
                  { key: 'test', label: t('测试'), render: (row) => <Button size="sm" variant="ghost" onClick={() => guarded(() => postJson('/api/zotero/endpoints/test', token, { id: row.id }), t('端点测试完成'))}>{t('测试')}</Button> },
                  { key: 'delete', label: t('删除'), render: (row) => <Button size="sm" variant="danger" onClick={() => guarded(() => deleteEndpoint(row), t('端点已删除'))}>{t('删除')}</Button> }
                ]}
              />
              
              <div style={{ marginTop: '24px', borderTop: '1px solid var(--border)', paddingTop: '16px' }}>
                <h4 style={{ margin: '0 0 12px 0' }}>{t('功能级连通性测试')}</h4>
                <div className="route-modal-actions">
                  <Button variant="secondary" size="sm" onClick={onTest}>{t('测试已启用的所有端点')}</Button>
                </div>
                <ActionResult result={testResult} />
              </div>
            </div>
            <div className="route-modal-footer">
              <Button variant="ghost" onClick={() => setOpen(false)}>{t('关闭')}</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
