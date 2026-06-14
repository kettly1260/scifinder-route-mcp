import { useState } from 'react';
import type { JsonObject } from '../../types';
import { Card, Input, Button, DataTable } from '../../components';
import { useTranslation } from '../../i18n';

export interface AiProvidersPanelProps {
  providers: JsonObject[];
  setProviders: (p: JsonObject[]) => void;
}

export function AiProvidersPanel({ providers, setProviders }: AiProvidersPanelProps) {
  const { t } = useTranslation();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<JsonObject>({ id: '', name: '', format: 'openai_chat', endpoint: '', api_key: '' });

  function handleSave() {
    if (!form.id) return;
    const newProviders = [...providers];
    const index = newProviders.findIndex((p) => p.id === form.id);
    if (index >= 0 && editingId) {
      newProviders[index] = { ...form };
    } else {
      newProviders.push({ ...form });
    }
    setProviders(newProviders);
    setEditingId(null);
    setForm({ id: '', name: '', format: 'openai_chat', endpoint: '', api_key: '' });
  }

  function handleEdit(p: JsonObject) {
    setEditingId(String(p.id));
    setForm({ ...p });
  }

  function handleDelete(id: string) {
    if (!window.confirm(`删除供应商 ${id}？这只会影响尚未保存的配置。`)) return;
    setProviders(providers.filter((p) => p.id !== id));
    if (editingId === id) {
      setEditingId(null);
      setForm({ id: '', name: '', format: 'openai_chat', endpoint: '', api_key: '' });
    }
  }

  return (
    <Card eyebrow="Providers" title={t("AI 供应商管理")}>
      <p className="muted">{t("维护所有大模型、OCR、嵌入等底层服务的供应商。添加后可供下方各功能选择绑定。")}</p>
      <div className="form-grid">
        <Input label={t("唯一标识 ID")} value={String(form.id || '')} onChange={(e) => setForm({ ...form, id: e.target.value })} placeholder="如 my-openai" disabled={!!editingId} />
        <Input label={t("显示名称")} value={String(form.name || '')} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如 OpenAI GPT-4" />
        <label className="form-group">
          {t("API 格式")}
          <select value={String(form.format || 'openai_chat')} onChange={(e) => setForm({ ...form, format: e.target.value })}>
            <option value="openai_chat">OpenAI Chat</option>
            <option value="openai_responses">OpenAI Structured</option>
            <option value="openai_compatible">OpenAI Compatible</option>
            <option value="gemini">Gemini</option>
            <option value="claude">Claude</option>
            <option value="generic">Generic (Base URL only)</option>
            <option value="mineru">MinerU</option>
            <option value="paddleocr_vl">PaddleOCR-VL</option>
          </select>
        </label>
        <Input label={t("API 端点 (Base URL)")} value={String(form.endpoint || '')} onChange={(e) => setForm({ ...form, endpoint: e.target.value })} placeholder="如 https://api.openai.com/v1" />
        <Input label={t("API Token")} type="password" value={String(form.api_key || '')} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="（可选）" />
        <div style={{ alignSelf: 'end', display: 'flex', gap: '8px' }}>
          <Button onClick={handleSave}>{editingId ? t('更新') : t('添加')}</Button>
          {editingId && (
            <Button variant="ghost" onClick={() => { setEditingId(null); setForm({ id: '', name: '', format: 'openai_chat', endpoint: '', api_key: '' }); }}>
              {t('取消')}
            </Button>
          )}
        </div>
      </div>
      {providers.length > 0 && (
        <div style={{ marginTop: '16px' }}>
          <DataTable
            rows={providers}
            columns={[
              { key: 'id', label: 'ID' },
              { key: 'name', label: t('名称') },
              { key: 'format', label: t('格式') },
              { key: 'endpoint', label: t('端点') },
              { key: 'actions', label: t('操作'), render: (row) => (
                <div className="button-row">
                  <Button size="sm" variant="ghost" onClick={() => handleEdit(row)}>{t('编辑')}</Button>
                  <Button size="sm" variant="danger" onClick={() => handleDelete(String(row.id))}>{t('删除')}</Button>
                </div>
              )}
            ]}
          />
        </div>
      )}
    </Card>
  );
}
