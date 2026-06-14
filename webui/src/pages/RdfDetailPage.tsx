import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import type { JsonObject } from '../types';
import type { PageProps } from '../constants';
import { getJson } from '../api';
import { Button, Card, DataTable, JsonBlock } from '../components';
import { StructureImage } from '../components/StructureImage';
import { Breadcrumb } from '../components/Breadcrumb';
import { asObject, asObjectArray, rdfRoleOrder } from '../constants';
import { useTranslation } from '../i18n';


export type RdfDetailPageProps = Pick<PageProps, 'token' | 'guarded'>;

function RdfDetailView({ detail, token }: { detail: JsonObject | null; token: string }) {
  const { t } = useTranslation();
  if (!detail) {
    return <JsonBlock value={{ hint: t('选择一条反应以查看中文解读、结构名称、CAS RN 与化学式。') }} maxHeight={560} />;
  }
  const readable = asObject(detail.readable);
  const zh = asObject(readable.zh);
  const structures = asObjectArray(readable.structures).sort(
    (a, b) => rdfRoleOrder(a.role) - rdfRoleOrder(b.role) || Number(a.role_index || 0) - Number(b.role_index || 0)
  );
  const equation = String(zh.equation || '');
  const reference = String(zh.reference || '');
  return (
    <div className="page-stack">
      <pre>{String(zh.text || detail.human_readable_text_zh || t('暂无中文解读'))}</pre>
      {equation && <p><strong>{t('反应式')}：</strong>{equation}</p>}
      <DataTable
        rows={structures}
        columns={[
          { key: 'role', label: t('角色'), render: (row) => `${String(row.role_label_zh || row.role || '')} ${String(row.role_index || '')}` },
          { key: 'name', label: t('名称'), render: (row) => String(row.name || row.label || '') },
          { key: 'formula', label: t('化学式') },
          { key: 'cas_rn', label: 'CAS RN' },
          { key: 'smiles', label: 'SMILES' },
          { key: 'molfile_version', label: t('结构信息') },
          { key: 'rdkit_status', label: 'RDKit' },
          { key: 'image', label: t('结构图'), render: (row) => (row.molfile || row.smiles) ? <StructureImage structureId={String(row.id)} token={token} /> : t('暂无') }
        ]}
        empty={t('该 RDF 反应没有可展示结构')}
      />
      {reference && <p><strong>{t('参考文献')}：</strong>{reference}</p>}
      <details>
        <summary>{t('原始 RDF 字段与调试 JSON')}</summary>
        <JsonBlock value={{ id: detail.id, fields: detail.fields, reference: detail.reference, structures: detail.structures, warnings: detail.warnings }} maxHeight={420} />
      </details>
    </div>
  );
}

export function RdfDetailPage({ token, guarded }: RdfDetailPageProps) {
  const { t } = useTranslation();
  const { reactionId } = useParams<{ reactionId: string }>();
  const [detail, setDetail] = useState<JsonObject | null>(null);

  async function open(id: string) {
    const data = await getJson<JsonObject>(`/api/rdf/reactions/${encodeURIComponent(id)}`, token);
    setDetail(data);
  }

  useEffect(() => {
    if (reactionId) {
      open(reactionId).catch(() => undefined);
    }
  }, [reactionId]);

  return (
    <div className="page-stack">
      <Breadcrumb
        items={[
          { label: t('RDF 反应'), path: '/rdf' },
          { label: detail?.cas_reaction_number ? String(detail.cas_reaction_number) : (detail?.id ? `${t('反应')} #${String(detail.id)}` : t('加载中...')) }
        ]}
      />
      <Card eyebrow="Detail" title={t('反应详情与结构')}>
        <RdfDetailView detail={detail} token={token} />
      </Card>
    </div>
  );
}
