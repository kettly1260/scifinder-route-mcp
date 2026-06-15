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
  const [documentId, setDocumentId] = useState('');
  const [jobs, setJobs] = useState<JsonObject[]>([]);
  const [links, setLinks] = useState<JsonObject[]>([]);



  async function loadLiterature() {
    const qs = documentId ? `?document_id=${encodeURIComponent(documentId)}&limit=50` : '?status=candidate&limit=50';
    setLinks(await getJson<JsonObject[]>(`/api/literature/links${qs}`, token));
    setJobs(await getJson<JsonObject[]>('/api/literature/jobs?limit=20', token));
  }

  useEffect(() => {

    loadLiterature().catch(() => undefined);
  }, []);

  return (
    <div className="page-stack">

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
