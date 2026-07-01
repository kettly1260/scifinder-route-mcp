import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import type { JsonObject } from '../types';
import type { PageProps } from '../constants';
import { getJson, postJson } from '../api';
import { Button, Card, DataTable, JsonBlock } from '../components';
import { CollapsibleText } from '../components/CollapsibleText';
import { Breadcrumb } from '../components/Breadcrumb';
import { shortName, mergeLineBreaks, asObject, asObjectArray } from '../constants';
import { useTranslation } from '../i18n';

export type DocumentDetailPageProps = Pick<PageProps, 'token' | 'guarded'>;

export function DocumentDetailPage({ token, guarded }: DocumentDetailPageProps) {
  const { t } = useTranslation();
  const { documentId } = useParams<{ documentId: string }>();
  const [detail, setDetail] = useState<JsonObject | null>(null);
  const [chunks, setChunks] = useState<JsonObject[]>([]);
  const [reactionLinks, setReactionLinks] = useState<JsonObject[]>([]);
  const [pdfEvidence, setPdfEvidence] = useState<JsonObject[]>([]);
  const [chunkTotal, setChunkTotal] = useState(0);
  const [chunkOffset, setChunkOffset] = useState(0);
  const chunkLimit = 50;

  async function loadReactionLinks(id: string) {
    const linksData = await getJson<unknown>(`/api/documents/${encodeURIComponent(id)}/reaction_links?limit=100`, token).catch(() => []);
    setReactionLinks(asObjectArray(linksData));
  }

  async function loadPdfEvidence(id: string) {
    const evidenceData = await getJson<unknown>(`/api/pdf_evidence?document_id=${encodeURIComponent(id)}&limit=200`, token).catch(() => []);
    setPdfEvidence(asObjectArray(evidenceData));
  }

  async function openDocument(id: string) {
    const data = await getJson<JsonObject>(`/api/documents/${encodeURIComponent(id)}?chunk_limit=${chunkLimit}&chunk_offset=0&reaction_limit=100`, token);
    const chunkPage = asObject(data.chunks);
    setDetail(data);
    setChunks(asObjectArray(chunkPage.chunks));
    setChunkTotal(Number(chunkPage.total || 0));
    setChunkOffset(Number(chunkPage.limit || chunkLimit));
    await loadReactionLinks(id);
    await loadPdfEvidence(id);
  }

  async function loadMoreChunks() {
    if (!documentId) return;
    const data = await getJson<JsonObject>(`/api/documents/${encodeURIComponent(documentId)}/chunks?limit=${chunkLimit}&offset=${chunkOffset}`, token);
    const nextChunks = asObjectArray(data.chunks);
    setChunks((current) => [...current, ...nextChunks]);
    setChunkTotal(Number(data.total || 0));
    setChunkOffset(chunkOffset + Number(data.limit || chunkLimit));
  }

  useEffect(() => {
    if (documentId) {
      openDocument(documentId).catch(() => undefined);
    }
  }, [documentId]);

  const document = asObject(detail?.document);
  const latestJob = asObject(detail?.latest_job);
  const reactions = asObjectArray(detail?.reaction_steps);
  const evidenceMeta = asObject(document.scifinder_metadata);
  const hasMoreChunks = chunks.length < chunkTotal;
  const status = String(document.ingest_status || '');

  return (
    <div className="page-stack">
      <Breadcrumb
        items={[
          { label: t('Documents'), path: '/documents' },
          { label: document.id ? shortName(document.file_path) : t('加载中...') }
        ]}
      />

      <Card
        eyebrow={t('解析结果')}
        title={document.id ? `解析结果：${shortName(document.file_path)}` : t('解析结果')}
        extra={Boolean(document.id) && (
          <div style={{ display: 'flex', gap: '8px' }}>
            <Button
              variant="secondary"
              onClick={() => {
                guarded(async () => {
                  const result = await postJson<JsonObject>(`/api/documents/${String(document.id)}/recognize_structure`, token, {});
                  await openDocument(String(document.id));
                  return result;
                }, t('已启动手动结构识别任务'));
              }}
            >
              {t('手动提取结构')}
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                if (window.confirm(t('重新解析将清除现有反应步骤并重新提取，确定继续？'))) {
                  guarded(async () => {
                    const result = await postJson<JsonObject>('/api/documents/reparse', token, { document_id: document.id });
                    await openDocument(String(document.id));
                    return result;
                  }, t('已启动重新解析任务'));
                }
              }}
            >
              {t('重新解析')}
            </Button>
          </div>
        )}
      >
        {!document.id && <JsonBlock value={{ hint: t('加载文档详情中...') }} maxHeight={240} />}
        {Boolean(document.id) && (
          <div className="page-stack">
            <div className="summary-strip">
              <span>{t('状态')}: {status}</span>
              <span>{t('类型')}: {String(document.file_type || '')}</span>
              {Boolean(evidenceMeta.evidence_kind) && <span>{t('证据')}: {String(evidenceMeta.evidence_kind)} / {String(evidenceMeta.evidence_priority ?? '')}</span>}
              <span>{t('文本块')}: {chunks.length}/{chunkTotal}</span>
              <span>{t('反应步骤')}: {reactions.length}</span>
              {Boolean(document.doi) && <span>DOI: {String(document.doi)}</span>}
            </div>
            {Boolean(evidenceMeta.provenance_warning) && <div className="notice warn">{String(evidenceMeta.provenance_warning)}</div>}
            {status === 'parsed_no_reactions' && <div className="notice warn">{t('该文档已解析，但未抽取到反应步骤。请检查解析文本是否包含实验步骤，或调整抽取规则/LLM 配置后重解析。')}</div>}
            {status === 'failed' && <div className="notice error">{t('该文档解析失败。若已有 partial chunks 会在下方展示；请查看任务错误并重试。')}</div>}
            {Boolean(latestJob.error) && <pre>{String(latestJob.error)}</pre>}
            <div className="grid two">
              <section className="chunk-panel">
                <div className="section-heading">
                  <p className="eyebrow">{t('解析文本块')}</p>
                  <h2>{t('完整解析文本')}</h2>
                </div>
                {chunks.length === 0 && (
                  <div className="empty-state">
                    <strong>{t('暂无解析文本')}</strong>
                    <span>{t('旧文档可能是在该功能上线前解析；点击上方「重新解析」后将保存文本块。')}</span>
                  </div>
                )}
                <div className="chunk-list">
                  {chunks.map((chunk) => (
                    <article className="parsed-chunk" key={String(chunk.id || chunk.chunk_index)}>
                      <div className="chunk-meta">
                        <span>{t('第')} {String(chunk.chunk_index)} {t('块')}</span>
                        <span>{chunk.page_number ? `${t('第')} ${chunk.page_number} ${t('页')}` : t('页码未知')}</span>
                        <span>{t('解析器')}: {String(chunk.parser_name || '')} {String(chunk.parser_version || '')}</span>
                      </div>
                      <div className="chunk-text">{mergeLineBreaks(String(chunk.text || ''))}</div>
                    </article>
                  ))}
                </div>
                {hasMoreChunks && <Button variant="secondary" onClick={() => guarded(loadMoreChunks, t('已加载更多文本块'))}>{t('加载更多文本块')}</Button>}
              </section>
              <section className="extraction-panel">
                <div className="section-heading">
                  <p className="eyebrow">{t('提取结果')}</p>
                  <h2>{t('抽取反应步骤')}</h2>
                </div>
                <DataTable
                  rows={reactions}
                  columns={[
                    { key: 'step_index', label: t('步骤') },
                    { key: 'reaction_name', label: t('名称') },
                    { key: 'reagent_text', label: t('试剂') },
                    { key: 'solvent_text', label: t('溶剂') },
                    { key: 'yield_text', label: t('收率') },
                    { key: 'confidence', label: t('置信度') },
                    { key: 'original_text', label: t('原文'), render: (row) => <CollapsibleText text={mergeLineBreaks(String(row.original_text || ''))} /> }
                  ]}
                  empty={t('该文档当前没有抽取出的反应步骤。')}
                />
              </section>
            </div>

            <div className="section-heading" style={{ marginTop: '2rem' }}>
              <p className="eyebrow">{t('RDF-PDF Linking')}</p>
              <h2>{t('反应级别关联证据')}</h2>
            </div>
            <DataTable
              rows={reactionLinks}
              columns={[
                { key: 'rdf_reaction_id', label: t('RDF反应ID') },
                { key: 'source_mode', label: t('模式') },
                { key: 'evidence_kind', label: t('证据类型') },
                { key: 'primary_pdf_page', label: t('PDF页码') },
                { key: 'pdf_evidence_count', label: t('PDF证据') },
                { key: 'link_confidence', label: t('关联置信度') },
                { key: 'link_method', label: t('关联方法') },
                { key: 'needs_review', label: t('需人工审核'), render: (r) => String(r.needs_review) === '1' ? t('待审核') : t('已确认') },
                { key: 'action', label: t('操作'), render: (row) => (
                  <div style={{ display: 'flex', gap: '8px' }}>
                    {String(row.needs_review) === '1' && (
                      <Button variant="secondary" onClick={() => guarded(async () => {
                        await postJson(`/api/reaction_links/${encodeURIComponent(String(row.id))}/confirm`, token, {});
                        await loadReactionLinks(String(document.id));
                      }, t('已确认'))}>{t('确认')}</Button>
                    )}
                    <Button variant="secondary" onClick={() => guarded(async () => {
                      if(window.confirm(t('确定删除此关联？'))) {
                        await postJson(`/api/reaction_links/${encodeURIComponent(String(row.id))}/unlink`, token, {});
                        await loadReactionLinks(String(document.id));
                      }
                    }, t('已删除'))}>{t('删除')}</Button>
                  </div>
                )}
              ]}
              empty={t('当前无关联的反应证据。')}
            />

            <div style={{ marginTop: '1rem', display: 'flex', gap: '8px', alignItems: 'center' }}>
              <Button variant="secondary" onClick={() => guarded(async () => {
                const rdfId = window.prompt(t('请输入 RDF 反应 ID：'));
                if (!rdfId) return;
                const page = window.prompt(t('请输入 PDF 页码：'));
                if (!page) return;
                await postJson(`/api/documents/${encodeURIComponent(String(document.id))}/force_link`, token, {
                  rdf_reaction_id: rdfId,
                  pdf_page: parseInt(page, 10)
                });
                await loadReactionLinks(String(document.id));
              }, t('已强制关联'))}>{t('手动关联 RDF 反应与页码')}</Button>
            </div>

            <div className="section-heading" style={{ marginTop: '2rem' }}>
              <p className="eyebrow">PDF Evidence</p>
              <h2>{t('PDF 反应证据页')}</h2>
            </div>
            <DataTable
              rows={pdfEvidence}
              columns={[
                { key: 'page_number', label: t('页码') },
                { key: 'cas_reaction_number', label: 'CAS' },
                { key: 'is_primary', label: t('主页'), render: (row) => Number(row.is_primary || 0) ? '✓' : '' },
                { key: 'yield_text', label: t('收率') },
                { key: 'procedure_text', label: t('步骤'), render: (row) => <CollapsibleText text={mergeLineBreaks(String(row.procedure_text || row.page_text || ''))} /> },
                { key: 'reaction_source_link_id', label: t('关联 ID') }
              ]}
              empty={t('当前文档没有 PDF 反应证据页。')}
            />
          </div>
        )}
      </Card>
    </div>
  );
}
