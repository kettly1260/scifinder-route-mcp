import { useState, useEffect, useMemo } from 'react';
import type { JsonObject } from '../types';
import type { PageProps } from '../constants';
import { getJson, postJson } from '../api';
import { Button, Card, DataTable } from '../components';
import { StatusBadge } from '../components/StatusBadge';
import { useTranslation } from '../i18n';
import { useToast } from '../components/Toast';
import { AlertTriangle, BrainCircuit, Check, Edit2, FileText, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { Link } from 'react-router-dom';

type ReviewFilter = 'pending' | 'confirmed' | 'all';
type ConflictFilter = 'all' | 'conflict' | 'clean';

function isReviewPending(row: JsonObject): boolean {
  return Number(row.needs_review || 0) === 1;
}

function conflictText(row: JsonObject): string {
  const flags = row.conflict_flags;
  if (flags && typeof flags === 'object' && !Array.isArray(flags)) {
    return Object.keys(flags as Record<string, unknown>).join(', ');
  }
  return row.has_conflicts ? 'conflict' : '';
}

function aiReview(row: JsonObject): JsonObject {
  const flags = row.conflict_flags;
  if (flags && typeof flags === 'object' && !Array.isArray(flags)) {
    const review = (flags as Record<string, unknown>).ai_review;
    return review && typeof review === 'object' && !Array.isArray(review) ? review as JsonObject : {};
  }
  return {};
}

export function ReactionLinksPage({ token, guarded, isBusy }: PageProps) {
  const { t } = useTranslation();
  const toast = useToast();
  const [links, setLinks] = useState<JsonObject[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>('pending');
  const [sourceMode, setSourceMode] = useState('');
  const [evidenceKind, setEvidenceKind] = useState('');
  const [conflictFilter, setConflictFilter] = useState<ConflictFilter>('all');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [backfillPreview, setBackfillPreview] = useState<JsonObject | null>(null);

  const selectedIds = useMemo(() => Array.from(selected), [selected]);

  async function loadLinks() {
    setLoading(true);
    const params = new URLSearchParams({ limit: '300' });
    if (reviewFilter !== 'all') params.set('needs_review', reviewFilter === 'pending' ? '1' : '0');
    if (sourceMode) params.set('source_mode', sourceMode);
    if (evidenceKind) params.set('evidence_kind', evidenceKind);
    if (conflictFilter !== 'all') params.set('has_conflicts', conflictFilter === 'conflict' ? '1' : '0');
    const data = await guarded(() => getJson<{ items?: JsonObject[]; total?: number }>(`/api/reaction_links?${params.toString()}`, token));
    setLinks(Array.isArray(data?.items) ? data.items : []);
    setTotal(Number(data?.total || 0));
    setSelected(new Set());
    setLoading(false);
  }

  useEffect(() => {
    loadLinks().catch(() => undefined);
  }, [reviewFilter, sourceMode, evidenceKind, conflictFilter]);

  async function confirmLink(id: string) {
    await guarded(() => postJson(`/api/reaction_links/${encodeURIComponent(id)}/confirm`, token), t('已确认关联'));
    loadLinks();
  }

  async function unlinkLink(id: string) {
    await guarded(() => postJson(`/api/reaction_links/${encodeURIComponent(id)}/unlink`, token), t('已解除关联'));
    loadLinks();
  }

  async function reviewLinkWithAi(id: string) {
    await guarded(
      () => postJson(`/api/reaction_links/${encodeURIComponent(id)}/ai_review`, token),
      t('AI 复核完成'),
      `reaction-links-ai-${id}`
    );
    loadLinks();
  }

  async function bulkAction(action: 'confirm' | 'reject') {
    if (!selectedIds.length) return;
    const data = await guarded(
      () => postJson<JsonObject>('/api/reaction_links/bulk', token, { action, link_ids: selectedIds }),
      action === 'confirm' ? t('已批量确认') : t('已批量拒绝'),
      `reaction-links-${action}`
    );
    if (data?.error_count) toast.warning(`${t('部分操作失败')}: ${String(data.error_count)}`);
    loadLinks();
  }

  async function setPrimaryPage(id: string, currentPage: string) {
    const page = prompt(t('请输入新的主页码:'), currentPage);
    if (!page) return;
    const pageNum = parseInt(page, 10);
    if (Number.isNaN(pageNum)) {
      toast.error(t('请输入有效的数字'));
      return;
    }
    await guarded(() => postJson(`/api/reaction_links/${encodeURIComponent(id)}/set_primary_page`, token, { pdf_page: pageNum }), t('已更新主页码'));
    loadLinks();
  }

  async function runBackfill(dryRun: boolean) {
    if (!dryRun && !window.confirm(t('这会把历史 PDF-only/冲突链接标记为待审核，确定继续？'))) return;
    const data = await guarded(
      () => postJson<JsonObject>('/api/reaction_links/backfill_review', token, { dry_run: dryRun }),
      dryRun ? t('已生成回填预览') : t('已应用审核回填'),
      dryRun ? 'reaction-links-backfill-preview' : 'reaction-links-backfill-apply'
    );
    if (data) {
      setBackfillPreview(data);
      if (!dryRun) loadLinks();
    }
  }

  function toggleSelected(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="page-stack">
      <Card
        eyebrow="Reaction Links"
        title={t('RDF-PDF 反应级证据审核队列')}
        extra={
          <div className="button-row">
            <Button variant="secondary" onClick={() => runBackfill(true)} loading={isBusy('reaction-links-backfill-preview')}>
              <ShieldCheck size={16} />
              {t('回填预览')}
            </Button>
            <Button variant="secondary" onClick={loadLinks} loading={loading}>
              <RefreshCw size={16} />
              {t('刷新')}
            </Button>
          </div>
        }
      >
        <div className="form-grid compact-form">
          <label className="form-group">
            {t('审核状态')}
            <select value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value as ReviewFilter)}>
              <option value="pending">{t('待审核')}</option>
              <option value="confirmed">{t('已确认')}</option>
              <option value="all">{t('全部')}</option>
            </select>
          </label>
          <label className="form-group">
            {t('来源模式')}
            <select value={sourceMode} onChange={(event) => setSourceMode(event.target.value)}>
              <option value="">{t('全部')}</option>
              <option value="rdf_pdf_linked">RDF + PDF</option>
              <option value="rdf_only">RDF only</option>
              <option value="pdf_only">PDF only</option>
              <option value="pdf_only_low_confidence">PDF low confidence</option>
            </select>
          </label>
          <label className="form-group">
            {t('证据类型')}
            <select value={evidenceKind} onChange={(event) => setEvidenceKind(event.target.value)}>
              <option value="">{t('全部')}</option>
              <option value="paper_si">{t('论文 SI')}</option>
              <option value="scifinder_pdf">SciFinder PDF</option>
              <option value="scifinder_readable">SciFinder Readable</option>
              <option value="patent">{t('专利反应过程')}</option>
              <option value="scifinder_rdf">SciFinder RDF</option>
            </select>
          </label>
          <label className="form-group">
            {t('冲突')}
            <select value={conflictFilter} onChange={(event) => setConflictFilter(event.target.value as ConflictFilter)}>
              <option value="all">{t('全部')}</option>
              <option value="conflict">{t('有冲突')}</option>
              <option value="clean">{t('无冲突')}</option>
            </select>
          </label>
        </div>

        <div className="summary-strip" style={{ marginBottom: '12px' }}>
          <span>{t('当前结果')}: {links.length}/{total}</span>
          <span>{t('已选择')}: {selectedIds.length}</span>
          <span>{t('待审核')}: {links.filter(isReviewPending).length}</span>
          <span>{t('冲突')}: {links.filter((row) => row.has_conflicts).length}</span>
        </div>

        <div className="button-row" style={{ marginBottom: '12px' }}>
          <Button size="sm" disabled={!selectedIds.length} loading={isBusy('reaction-links-confirm')} onClick={() => bulkAction('confirm')}>
            <Check size={14} />
            {t('批量确认')}
          </Button>
          <Button size="sm" variant="danger" disabled={!selectedIds.length} loading={isBusy('reaction-links-reject')} onClick={() => bulkAction('reject')}>
            <X size={14} />
            {t('批量拒绝')}
          </Button>
          {backfillPreview && (
            <Button size="sm" variant="secondary" loading={isBusy('reaction-links-backfill-apply')} onClick={() => runBackfill(false)}>
              <AlertTriangle size={14} />
              {t('应用回填')} ({String(backfillPreview.candidate_count || 0)})
            </Button>
          )}
        </div>

        <DataTable
          rows={links}
          defaultPageSize={20}
          columns={[
            {
              key: 'select',
              label: '',
              render: (row) => (
                <input
                  type="checkbox"
                  checked={selected.has(String(row.id))}
                  onChange={() => toggleSelected(String(row.id))}
                  aria-label={t('选择关联')}
                />
              )
            },
            { key: 'cas_reaction_number', label: 'CAS' },
            {
              key: 'status',
              label: t('状态'),
              render: (row) => isReviewPending(row) ? <StatusBadge tone="failed">{t('待审核')}</StatusBadge> : <StatusBadge tone="success">{t('已确认')}</StatusBadge>
            },
            { key: 'source_mode', label: t('模式') },
            { key: 'evidence_kind', label: t('证据类型') },
            {
              key: 'pdf_document_id',
              label: t('PDF 文档'),
              render: (row) => row.pdf_document_id ? (
                <Link to={`/documents/${String(row.pdf_document_id)}`} style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <FileText size={14} />
                  {String(row.pdf_file_name || row.pdf_document_id)}
                </Link>
              ) : ''
            },
            {
              key: 'rdf_document_id',
              label: t('RDF 文档'),
              render: (row) => row.rdf_document_id ? String(row.rdf_file_name || row.rdf_document_id) : ''
            },
            {
              key: 'primary_pdf_page',
              label: t('主页码'),
              render: (row) => (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span>{String(row.primary_pdf_page || '')}</span>
                  <Button size="sm" variant="ghost" onClick={() => setPrimaryPage(String(row.id), String(row.primary_pdf_page || ''))} title={t('修改主页码')}>
                    <Edit2 size={12} />
                  </Button>
                </div>
              )
            },
            { key: 'pdf_evidence_count', label: t('PDF证据') },
            {
              key: 'link_confidence',
              label: t('置信度'),
              render: (row) => row.link_confidence != null ? Number(row.link_confidence).toFixed(2) : ''
            },
            {
              key: 'conflicts',
              label: t('冲突'),
              render: (row) => conflictText(row)
            },
            {
              key: 'ai_review',
              label: t('AI建议'),
              render: (row) => {
                const review = aiReview(row);
                if (!review.recommendation) return '';
                return (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    <strong>{String(review.recommendation)} {review.confidence != null ? `(${Number(review.confidence).toFixed(2)})` : ''}</strong>
                    <span className="muted">{String(review.rationale || '')}</span>
                  </div>
                );
              }
            },
            {
              key: 'actions',
              label: t('操作'),
              render: (row) => (
                <div className="button-row">
                  <Button size="sm" variant="secondary" loading={isBusy(`reaction-links-ai-${String(row.id)}`)} onClick={() => reviewLinkWithAi(String(row.id))} title={t('AI 复核此证据')}>
                    <BrainCircuit size={14} />
                  </Button>
                  {isReviewPending(row) ? (
                    <Button size="sm" onClick={() => confirmLink(String(row.id))} title={t('确认此推荐')}>
                      <Check size={14} />
                    </Button>
                  ) : null}
                  <Button size="sm" variant="ghost" onClick={() => unlinkLink(String(row.id))} title={t('删除/取消此关联')}>
                    <X size={14} />
                  </Button>
                </div>
              )
            }
          ]}
          empty={t('当前筛选条件下没有反应关联。')}
        />
      </Card>
    </div>
  );
}
