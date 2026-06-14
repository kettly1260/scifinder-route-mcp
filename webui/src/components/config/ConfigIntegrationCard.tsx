import { useState, useEffect, useRef } from 'react';
import type { JsonObject } from '../../types';
import type { IntegrationGroup } from '../../constants';
import { configFieldByKey } from '../../constants';
import { Button } from '../../components';
import { ConfigControl } from './ConfigControl';
import { ModelSuggestions } from './ModelSuggestions';
import { ActionResult } from './ActionResult';
import { useTranslation } from '../../i18n';
import { X, Pencil, CircleCheck, CircleAlert } from 'lucide-react';

/* ── Icon mapping for each integration type ── */
const ROUTE_ICONS: Record<string, string> = {
  extraction: '🧠',
  embedding: '📐',
  ocr: '👁',
  document_parser: '📄',
  structure_recognition: '🔬',
  reranker: '🔀',
  zotero_mcp: '📚',
};

/* ── Small preview card (grid tile) ── */
export interface ConfigIntegrationCardProps {
  group: IntegrationGroup;
  values: Record<string, string>;
  models: string[];
  providers?: JsonObject[];
  testResult?: JsonObject;
  modelResult?: JsonObject;
  onChange: (key: string, value: string) => void;
  onTest: () => void;
  onLoadModels: () => void;
  onSave: () => void;
}

export function ConfigIntegrationCard(props: ConfigIntegrationCardProps) {
  const { group, values, testResult } = props;
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  const currentModel = values[group.modelKey] || '';
  const currentProvider = values[group.providerKey] || '';
  const icon = ROUTE_ICONS[group.id] || '⚙';

  // Determine connection status from latest test result
  let statusClass = '';
  let statusLabel = t('未测试');
  if (testResult) {
    const ok = testResult.ok || testResult.success || testResult.status === 'ok';
    statusClass = ok ? 'route-card--ok' : 'route-card--err';
    statusLabel = ok ? t('已连通') : t('失败');
  }

  return (
    <>
      <button
        type="button"
        className={`route-card ${statusClass}`}
        onClick={() => setOpen(true)}
        title={group.description}
      >
        <div className="route-card-head">
          <span className="route-card-icon">{icon}</span>
          <span className="route-card-title">{group.title}</span>
        </div>
        <div className="route-card-details">
          <div className="route-card-row">
            <span className="route-card-label">{t('供应商')}</span>
            <span className="route-card-val">{currentProvider || <span className="muted">—</span>}</span>
          </div>
          <div className="route-card-row">
            <span className="route-card-label">{t('模型')}</span>
            <span className="route-card-val">{currentModel || <span className="muted">—</span>}</span>
          </div>
        </div>
        <span className={`route-card-status ${statusClass}`}>
          {testResult
            ? (statusClass === 'route-card--ok'
                ? <CircleCheck size={13} />
                : <CircleAlert size={13} />)
            : null
          }
          {statusLabel}
        </span>
        <Pencil className="route-card-edit" size={14} />
      </button>

      {open && (
        <IntegrationModal {...props} onClose={() => setOpen(false)} />
      )}
    </>
  );
}

/* ── Modal dialog for editing ── */
function IntegrationModal({
  group,
  values,
  models,
  providers,
  testResult,
  modelResult,
  onChange,
  onTest,
  onLoadModels,
  onSave,
  onClose,
}: ConfigIntegrationCardProps & { onClose: () => void }) {
  const { t } = useTranslation();
  const backdropRef = useRef<HTMLDivElement>(null);
  const icon = ROUTE_ICONS[group.id] || '⚙';

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Close on backdrop click
  function handleBackdropClick(e: React.MouseEvent) {
    if (e.target === backdropRef.current) onClose();
  }

  return (
    <div className="route-modal-backdrop" ref={backdropRef} onClick={handleBackdropClick}>
      <div className="route-modal" role="dialog" aria-label={group.title}>
        {/* Header */}
        <div className="route-modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '24px' }}>{icon}</span>
            <div>
              <p className="eyebrow">{group.eyebrow}</p>
              <h3 style={{ margin: 0 }}>{group.title}</h3>
            </div>
          </div>
          <button className="route-modal-close" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="route-modal-body">
          <p className="muted" style={{ marginBottom: '16px', fontSize: '13px' }}>{group.description}</p>

          <div className="form-grid single">
            {group.fields.map((key) => {
              const field = configFieldByKey.get(key);
              if (!field) return null;
              return (
                <ConfigControl
                  key={key}
                  field={field}
                  value={values[key] ?? ''}
                  onChange={onChange}
                  suggestions={key === group.modelKey ? models : undefined}
                  providers={providers}
                />
              );
            })}
          </div>

          <ModelSuggestions models={models} modelKey={group.modelKey} onChange={onChange} />

          <div className="route-modal-actions">
            <Button variant="secondary" size="sm" onClick={onTest}>{t('测试端点')}</Button>
            <Button variant="ghost" size="sm" onClick={onLoadModels}>{t('拉取模型')}</Button>
          </div>

          <div className="result-grid" style={{ marginTop: '8px' }}>
            <ActionResult title={t("端点测试")} result={testResult} />
            <ActionResult title={t("模型拉取")} result={modelResult} />
          </div>
        </div>

        {/* Footer */}
        <div className="route-modal-footer">
          <Button variant="ghost" onClick={onClose}>{t('关闭')}</Button>
          <Button onClick={onSave}>{t('保存并重载')}</Button>
        </div>
      </div>
    </div>
  );
}
