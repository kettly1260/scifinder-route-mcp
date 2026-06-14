import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import type { JsonObject } from '../types';
import type { PageProps } from '../constants';
import { getJson } from '../api';
import { Button, Card, DataTable, Input } from '../components';
import { useTranslation } from '../i18n';

export type DocumentsListPageProps = Pick<PageProps, 'token' | 'guarded'>;

export function DocumentsListPage({ token, guarded }: DocumentsListPageProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [fileType, setFileType] = useState('');
  const [documents, setDocuments] = useState<JsonObject[]>([]);
  const navigate = useNavigate();

  async function loadDocuments() {
    const params = new URLSearchParams({ limit: '100' });
    if (query.trim()) params.set('q', query.trim());
    if (fileType) params.set('file_type', fileType);
    const rows = await getJson<JsonObject[]>(`/api/documents?${params.toString()}`, token);
    setDocuments(rows);
  }

  useEffect(() => {
    loadDocuments().catch(() => undefined);
  }, []);

  return (
    <div className="page-stack">
      <Card eyebrow={t('Documents')} title={t('上传/注册文档')} extra={<Button onClick={() => guarded(loadDocuments, t('文档列表已加载'))}>{t('加载文档')}</Button>}>
        <p className="muted">{t('查看非 RDF 文件的完整解析文本块、页码/解析器来源，以及同一文档抽取出的反应步骤。')}</p>
        <div className="form-grid compact-form">
          <Input label={t('搜索')} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('标题、文件名、DOI 或文档 ID')} />
          <label className="form-group">
            {t('文件类型')}
            <select value={fileType} onChange={(event) => setFileType(event.target.value)}>
              <option value="">{t('全部')}</option>
              <option value="pdf">PDF</option>
              <option value="rtf">RTF</option>
              <option value="html">HTML</option>
              <option value="mhtml">MHTML</option>
              <option value="md">Markdown</option>
              <option value="txt">TXT</option>
              <option value="rdf">RDF</option>
            </select>
          </label>
        </div>
        <DataTable
          rows={documents}
          columns={[
            { key: 'file_name', label: t('文件') },
            { key: 'file_type', label: t('类型') },
            { key: 'ingest_status', label: t('状态') },
            { key: 'title', label: t('标题') },
            { key: 'parsed_chunk_count', label: t('文本块') },
            { key: 'reaction_step_count', label: t('反应') },
            { key: 'last_job_error', label: t('最近错误') },
            { key: 'open', label: t('打开'), render: (row) => <Button size="sm" variant="ghost" onClick={() => navigate(`/documents/${encodeURIComponent(String(row.id))}`)}>{t('查看解析')}</Button> }
          ]}
          empty={t('暂无文档；请先上传或扫描收件箱。')}
        />
      </Card>
    </div>
  );
}
