import { useState, useEffect, useRef } from 'react';
import type { AdminState, JsonObject } from '../types';
import type { PageProps } from '../constants';
import { buildConfigValues, configFieldByKey, integrationGroups, runtimeGroups, configFields } from '../constants';
import { postJson } from '../api';
import { Button, Card } from '../components';
import { AiProvidersPanel } from '../components/config/AiProvidersPanel';
import { ConfigIntegrationCard } from '../components/config/ConfigIntegrationCard';
import { ConfigFieldCard } from '../components/config/ConfigFieldCard';
import { ConfigControl } from '../components/config/ConfigControl';
import { ActionResult } from '../components/config/ActionResult';
import { RuntimePanel } from '../components/config/RuntimePanel';
import { useTranslation } from '../i18n';
import { X, Pencil, CircleCheck, CircleAlert } from 'lucide-react';

export interface ConfigPageProps extends PageProps {
  refresh: () => Promise<AdminState>;
}

type TabId = 'providers' | 'routes' | 'runtime';

export function ConfigPage({ token, state, guarded, refresh }: ConfigPageProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabId>('providers');
  const [values, setValues] = useState<Record<string, string>>(() => buildConfigValues(state.config));
  const [providers, setProviders] = useState<JsonObject[]>(() => {
    const p = (state.config.integrations as JsonObject | undefined)?.ai_providers;
    return Array.isArray(p) ? (p as JsonObject[]) : [];
  });
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
    await postJson('/api/config', token, payload);
    const next = await refresh();
    setValues(buildConfigValues(next.config));
    const p = (next.config.integrations as JsonObject | undefined)?.ai_providers;
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

  async function loadModels(kind: string) {
    const data = await postJson<{ models?: string[] } & JsonObject>('/api/integration/models', token, { kind, overrides: buildPayload(false) });
    setModels((current) => ({ ...current, [kind]: data.models || [] }));
    rememberResult(`${kind}:models`, data);
    if (data.models?.length) {
      const group = integrationGroups.find((item) => item.id === kind);
      const modelKey = group?.modelKey;
      if (modelKey && !values[modelKey]) {
        update(modelKey, data.models[0]);
      }
    }
    return data;
  }

  return (
    <div className="page-stack">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--line)', paddingBottom: '12px', marginBottom: '16px' }}>
        <div className="config-tabs-list" style={{ marginBottom: 0, borderBottom: 'none' }}>
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
        <Button onClick={() => guarded(save, t('配置已保存并重载'))}>{t('保存全局配置')}</Button>
      </div>

      {/* AI Providers Tab */}
      <div className={`config-tab-content ${activeTab === 'providers' ? 'active' : ''}`}>
        <AiProvidersPanel providers={providers} setProviders={setProviders} />
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
              models={models[group.id] || []}
              providers={providers}
              testResult={actionResults[group.id]}
              modelResult={actionResults[`${group.id}:models`]}
              onChange={update}
              onTest={() => guarded(() => runEndpointTest(group.id), `${group.title} ${t('测试完成')}`)}
              onLoadModels={() => guarded(() => loadModels(group.id), `${group.title} ${t('模型拉取完成')}`)}
              onSave={() => guarded(save, t('配置已保存并重载'))}
            />
          ))}
          <ZoteroRouteCard
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
          onSave={() => guarded(save, t('配置已保存并重载'))}
          onTestPostgres={() => guarded(() => runEndpointTest('postgres'), t('Postgres 测试完成'))}
          postgresResult={actionResults.postgres}
        />
      </div>
      <p className="muted" style={{ marginTop: '24px' }}>{t('端口、卷挂载、Docker 网络和重启策略仍应在 .env / Docker Compose 中修改。')}</p>
    </div>
  );
}

/* Zotero tile + modal (same pattern as ConfigIntegrationCard) */
function ZoteroRouteCard({ onTest, testResult }: { onTest: () => void; testResult?: JsonObject }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const backdropRef = useRef<HTMLDivElement>(null);

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
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

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
          <div className="route-modal" role="dialog" aria-label="Zotero MCP">
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
            <div className="route-modal-body">
              <p className="muted" style={{ marginBottom: '16px', fontSize: '13px' }}>{t('Zotero 端点地址在"文献 / Zotero"页面维护，这里只测试已保存的端点组。')}</p>
              <div className="route-modal-actions">
                <Button variant="secondary" size="sm" onClick={onTest}>{t('测试 Zotero MCP')}</Button>
              </div>
              <ActionResult result={testResult} />
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
