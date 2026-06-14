import { useState, useEffect } from 'react';
import type { JsonObject } from '../types';
import type { PageProps, ZoteroEndpointForm } from '../constants';
import { defaultZoteroEndpoint } from '../constants';
import { getJson, postJson } from '../api';
import { Button, Card, Input, DataTable } from '../components';
import { useTranslation } from '../i18n';

export type LiteraturePageProps = PageProps;

export function LiteraturePage({ token, state, guarded }: LiteraturePageProps) {
  const { t } = useTranslation();
  const [endpoint, setEndpoint] = useState<ZoteroEndpointForm>(defaultZoteroEndpoint);
  const [endpoints, setEndpoints] = useState<JsonObject[]>([]);
  const [documentId, setDocumentId] = useState('');
  const [jobs, setJobs] = useState<JsonObject[]>([]);
  const [links, setLinks] = useState<JsonObject[]>([]);

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

  async function loadLiterature() {
    const qs = documentId ? `?document_id=${encodeURIComponent(documentId)}&limit=50` : '?status=candidate&limit=50';
    setLinks(await getJson<JsonObject[]>(`/api/literature/links${qs}`, token));
    setJobs(await getJson<JsonObject[]>('/api/literature/jobs?limit=20', token));
  }

  useEffect(() => {
    loadEndpoints().catch(() => undefined);
    loadLiterature().catch(() => undefined);
  }, []);

  return (
    <div className="page-stack">
      <Card
        eyebrow="Zotero MCP"
        title={endpoint.id ? `${t('编辑文献源地址')}：${endpoint.id}` : t('文献源地址')}
        extra={
          <div className="button-row">
            <Button onClick={() => guarded(saveEndpoint, t('Zotero 端点已保存'))}>
              {endpoint.id ? t('保存修改') : t('保存地址')}
            </Button>
            <Button variant="secondary" onClick={() => setEndpoint(defaultZoteroEndpoint)}>{t('新建')}</Button>
            <Button variant="secondary" onClick={() => guarded(loadEndpoints, t('端点已加载'))}>{t('加载端点')}</Button>
          </div>
        }
      >
        <p className="muted">{t('默认使用本机 Streamable HTTP 端点 http://127.0.0.1:23120/mcp；远程 Zotero 必须填写完整 URL，例如 http://192.168.99.3:23120/mcp。保存或删除端点会修改 Web UI 热配置，需要 admin 权限。')}</p>
        <div className="form-grid">
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
      </Card>
      <Card
        eyebrow="Literature"
        title={t("候选链接与任务")}
        extra={
          <div className="button-row">
            <Button onClick={() => guarded(() => postJson('/api/literature/jobs/start', token, { document_id: documentId }), t('Zotero 链接任务已启动'))}>
              {t('启动链接')}
            </Button>
            <Button variant="secondary" onClick={() => guarded(loadLiterature, t('文献链接已加载'))}>{t('加载链接')}</Button>
          </div>
        }
      >
        <Input label={t('文档 ID')} value={documentId} onChange={(e) => setDocumentId(e.target.value)} placeholder={t("可选；留空查看 candidate")} />
        <div className="summary-strip">
          <span>{t('OCR 积压')}: {String(state.health.ocr_backlog ?? 0)}</span>
          <span>{t('低置信 DOI')}: {String(((state.production.doi_low_confidence_queue as unknown[]) || []).length)}</span>
          <span>{t('文献候选')}: {String(((state.production.literature_candidates as unknown[]) || []).length)}</span>
        </div>
        <h3>{t('文献任务')}</h3>
        <DataTable rows={jobs} columns={[{ key: 'id', label: 'ID' }, { key: 'document_id', label: t('文档') }, { key: 'status', label: t('状态') }, { key: 'stage', label: t('阶段') }, { key: 'error', label: t('错误') }]} />
        <h3>{t('候选链接')}</h3>
        <DataTable
          rows={links}
          columns={[
            { key: 'status', label: t('状态') },
            { key: 'reaction_step_id', label: t('反应') },
            { key: 'endpoint_alias', label: t('端点') },
            { key: 'doi', label: 'DOI' },
            { key: 'title', label: t('标题') },
            { key: 'confidence', label: t('评分') },
            { key: 'confirm', label: t('确认'), render: (row) => <Button size="sm" variant="ghost" onClick={() => guarded(() => postJson('/api/literature/links/confirm', token, { id: row.id }), t('已确认链接'))}>{t('确认')}</Button> }
          ]}
        />
      </Card>
    </div>
  );
}
