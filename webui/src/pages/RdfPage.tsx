import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import type { JsonObject } from '../types';
import type { PageProps } from '../constants';
import { getJson } from '../api';
import { Button, Card, DataTable, Input } from '../components';
import { shortName } from '../constants';
import { useTranslation } from '../i18n';

export type RdfPageProps = Pick<PageProps, 'token' | 'guarded'>;

export function RdfPage({ token, guarded }: RdfPageProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState('25');
  const [rows, setRows] = useState<JsonObject[]>([]);
  const navigate = useNavigate();

  async function load() {
    const url = `/api/rdf/reactions?limit=${encodeURIComponent(limit)}${query ? `&q=${encodeURIComponent(query)}` : ''}`;
    const data = await getJson<JsonObject[]>(url, token);
    setRows(data);
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  return (
    <div className="page-stack">
      <Card eyebrow="RDF Viewer" title={t("反应记录")} extra={<Button onClick={() => guarded(load, t('RDF 反应已加载'))}>{t('加载 RDF 反应')}</Button>}>
        <p className="muted">{t('RDF/RDfile 是结构化证据，但可能不含完整实验步骤；最终化学结论前请结合 PDF/RTF/HTML 可读或视觉证据核验。')}</p>
        <div className="form-grid compact-form">
          <Input label={t("CAS / 文件名 / 标题")} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("CAS 反应号、结构 CAS RN、PDF/RDF 文件名或标题")} />
          <Input label={t("数量限制")} type="number" min="1" value={limit} onChange={(event) => setLimit(event.target.value)} />
        </div>
        <DataTable
          rows={rows}
          columns={[
            { key: 'source_file_path', label: t('来源文件'), render: (row) => shortName(row.source_file_path || row.source_title || row.source_document_id) },
            { key: 'record_index', label: t('记录') },
            { key: 'scheme_id', label: t('方案') },
            { key: 'step_id', label: t('步骤') },
            { key: 'cas_reaction_number', label: t('CAS 反应号') },
            { key: 'yield_text', label: t('收率') },
            { key: 'structure_count', label: t('结构数') },
            { key: 'open', label: t('打开'), render: (row) => <Button size="sm" variant="ghost" onClick={() => navigate(`/rdf/${encodeURIComponent(String(row.id))}`)}>{t('打开')}</Button> }
          ]}
        />
      </Card>
    </div>
  );
}
