import { useState, useRef, useEffect } from 'react';
import type { JsonObject } from '../../types';
import { Card, Input, Button, DataTable } from '../../components';
import { useTranslation } from '../../i18n';
import { postJson } from '../../api';
import { ActionResult } from './ActionResult';
import { X, Wrench } from 'lucide-react';

export interface AiProvidersPanelProps {
  providers: JsonObject[];
  setProviders: (p: JsonObject[]) => void;
  token: string;
  guarded: <T>(action: () => Promise<T>, success?: string) => Promise<T | undefined>;
}

export function AiProvidersPanel({ providers, setProviders, token, guarded }: AiProvidersPanelProps) {
  const { t } = useTranslation();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<JsonObject>({ id: '', name: '', format: 'openai_chat', endpoint: '', api_key: '', models_endpoint: '' });

  // Modal State
  const [modalProvider, setModalProvider] = useState<JsonObject | null>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const [testResult, setTestResult] = useState<JsonObject | undefined>();
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [enabledModels, setEnabledModels] = useState<string[]>([]);
  const [customModel, setCustomModel] = useState('');

  useEffect(() => {
    if (!modalProvider) return;
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') setModalProvider(null); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [modalProvider]);

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
    setForm({ id: '', name: '', format: 'openai_chat', endpoint: '', api_key: '', models_endpoint: '' });
  }

  function handleEdit(p: JsonObject) {
    setEditingId(String(p.id));
    setForm({ ...p });
  }

  function handleDelete(id: string) {
    if (!window.confirm(t(`删除供应商 ${id}？这只会影响尚未保存的配置。`))) return;
    setProviders(providers.filter((p) => p.id !== id));
    if (editingId === id) {
      setEditingId(null);
      setForm({ id: '', name: '', format: 'openai_chat', endpoint: '', api_key: '', models_endpoint: '' });
    }
  }

  function openModal(p: JsonObject) {
    setModalProvider(p);
    setTestResult(undefined);
    setAvailableModels((p.available_models as string[]) || []);
    setEnabledModels((p.enabled_models as string[]) || []);
    setCustomModel('');
  }

  async function testProvider() {
    if (!modalProvider) return;
    const res = await postJson<JsonObject>('/api/provider/test', token, { id: modalProvider.id });
    setTestResult(res);
  }

  async function pullModels() {
    if (!modalProvider) return;
    const res = await postJson<{ models?: string[] } & JsonObject>('/api/provider/models', token, { id: modalProvider.id });
    setTestResult(res);
    if (res.models && res.models.length > 0) {
      const newAvail = Array.from(new Set([...availableModels, ...res.models]));
      setAvailableModels(newAvail);
    }
  }

  function toggleModelEnabled(model: string) {
    setEnabledModels((current) => {
      if (current.includes(model)) return current.filter((m) => m !== model);
      return [...current, model];
    });
  }

  function addCustomModel() {
    const val = customModel.trim();
    if (!val) return;
    setAvailableModels((current) => Array.from(new Set([...current, val])));
    setEnabledModels((current) => Array.from(new Set([...current, val])));
    setCustomModel('');
  }

  async function saveModelsConfig() {
    if (!modalProvider) return;
    await postJson('/api/provider/models/update', token, {
      id: modalProvider.id,
      available_models: availableModels,
      enabled_models: enabledModels
    });
    const updatedProvider = {
      ...modalProvider,
      available_models: availableModels,
      enabled_models: enabledModels
    };
    setProviders(providers.map((p) => (p.id === modalProvider.id ? updatedProvider : p)));
    setModalProvider(updatedProvider);
  }

  return (
    <>
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
          <Input label={t("模型拉取端点 (Models Endpoint)")} value={String(form.models_endpoint || '')} onChange={(e) => setForm({ ...form, models_endpoint: e.target.value })} placeholder="（可选）如 https://api.openai.com/v1/models" />
          <Input label={t("API Token")} type="password" value={String(form.api_key || '')} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="（可选）" />
          <div style={{ alignSelf: 'end', display: 'flex', gap: '8px' }}>
            <Button onClick={handleSave}>{editingId ? t('更新列表项 (需保存全局)') : t('添加至列表')}</Button>
            {editingId && (
              <Button variant="ghost" onClick={() => { setEditingId(null); setForm({ id: '', name: '', format: 'openai_chat', endpoint: '', api_key: '', models_endpoint: '' }); }}>
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
                    <Button size="sm" variant="secondary" onClick={() => openModal(row)}><Wrench size={14} style={{ marginRight: 4 }} /> {t('配置模型')}</Button>
                    <Button size="sm" variant="ghost" onClick={() => handleEdit(row)}>{t('编辑')}</Button>
                    <Button size="sm" variant="danger" onClick={() => handleDelete(String(row.id))}>{t('删除')}</Button>
                  </div>
                )}
              ]}
            />
          </div>
        )}
      </Card>

      {modalProvider && (
        <div className="route-modal-backdrop" ref={backdropRef} onClick={(e) => { if (e.target === backdropRef.current) setModalProvider(null); }}>
          <div className="route-modal" role="dialog" aria-label={String(modalProvider.name || modalProvider.id)} style={{ maxWidth: '600px', width: '90%' }}>
            <div className="route-modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                <span style={{ fontSize: '24px' }}>⚙️</span>
                <div>
                  <p className="eyebrow">{t('供应商模型管理')}</p>
                  <h3 style={{ margin: 0 }}>{String(modalProvider.name || modalProvider.id)}</h3>
                </div>
              </div>
              <button className="route-modal-close" onClick={() => setModalProvider(null)} aria-label="Close"><X size={18} /></button>
            </div>
            
            <div className="route-modal-body" style={{ maxHeight: 'calc(100vh - 200px)', overflowY: 'auto' }}>
              <p className="muted" style={{ marginBottom: '16px', fontSize: '13px' }}>
                {t('注意：必须先在主界面点击【保存全局配置】后，后端的端点测试与模型拉取才能生效。')}
              </p>
              <div className="button-row" style={{ marginBottom: '16px' }}>
                <Button variant="secondary" size="sm" onClick={() => guarded(testProvider, t('测试完成'))}>{t('端点连通性测试')}</Button>
                <Button variant="secondary" size="sm" onClick={() => guarded(pullModels, t('拉取完成'))}>{t('拉取云端模型')}</Button>
              </div>
              <ActionResult result={testResult} />

              <h4 style={{ marginTop: '24px', marginBottom: '12px' }}>{t('已勾选的可用模型 (Enabled Models)')}</h4>
              <p className="muted" style={{ fontSize: '13px', marginBottom: '12px' }}>{t('只有勾选的模型才会出现在功能路由的下拉列表中。')}</p>
              
              <div style={{ background: 'var(--panel)', padding: '16px', borderRadius: '8px', marginBottom: '16px', border: '1px solid var(--border)' }}>
                {availableModels.length === 0 && <p className="muted" style={{ margin: 0, fontSize: '13px' }}>{t('暂无可用模型。请先拉取云端模型或手动添加。')}</p>}
                {availableModels.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px' }}>
                    {availableModels.map(m => (
                      <label key={m} style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', fontSize: '14px' }}>
                        <input
                          type="checkbox"
                          checked={enabledModels.includes(m)}
                          onChange={() => toggleModelEnabled(m)}
                        />
                        {m}
                      </label>
                    ))}
                  </div>
                )}
              </div>

              <div style={{ display: 'flex', gap: '8px', alignItems: 'end', marginBottom: '24px' }}>
                <Input label={t('自定义模型 ID')} value={customModel} onChange={(e) => setCustomModel(e.target.value)} placeholder="如 gpt-4o" />
                <Button variant="secondary" onClick={addCustomModel}>{t('添加')}</Button>
              </div>

              <div className="button-row">
                <Button onClick={() => guarded(saveModelsConfig, t('模型列表已保存'))}>{t('保存选中模型')}</Button>
              </div>
            </div>
            
            <div className="route-modal-footer">
              <Button variant="ghost" onClick={() => setModalProvider(null)}>{t('关闭')}</Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
