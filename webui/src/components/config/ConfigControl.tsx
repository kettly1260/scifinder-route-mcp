import type { ConfigField, JsonObject } from '../../types';
import { Button, Input } from '../../components';
import { useTranslation } from '../../i18n';
import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react';
import { useState } from 'react';

export interface ConfigControlProps {
  field: ConfigField;
  value: string;
  onChange: (key: string, value: string) => void;
  suggestions?: string[];
  providers?: JsonObject[];
  values?: Record<string, string>;
  group?: { providerKey: string; modelKey: string };
}

export function ConfigControl({ field, value, onChange, suggestions, providers, values, group }: ConfigControlProps) {
  const { t } = useTranslation();
  const key = `${field.section}.${field.name}`;

  if (field.name.endsWith('provider_ids') && providers) {
    return <ProviderChainControl field={field} value={value} onChange={(next) => onChange(key, next)} providers={providers} />;
  }

  if (field.name.endsWith('provider_id') && providers) {
    return (
      <label className="form-group">
        {field.label}
        <select value={value} onChange={(event) => onChange(key, event.target.value)}>
          <option value="">(空 - 不使用)</option>
          {providers.map((p) => (
            <option key={String(p.id)} value={String(p.id)}>{String(p.name)} ({String(p.id)})</option>
          ))}
        </select>
      </label>
    );
  }

  if (field.type === 'select') {
    return (
      <label className="form-group">
        {field.label}
        <select value={value} onChange={(event) => onChange(key, event.target.value)}>
          {(field.options || []).map((option) => (
            <option key={option} value={option}>{option}</option>
          ))}
        </select>
      </label>
    );
  }

  if (field.type === 'bool') {
    return (
      <label className="form-group">
        {field.label}
        <select value={value || 'false'} onChange={(event) => onChange(key, event.target.value)}>
          <option value="true">{t('启用')}</option>
          <option value="false">{t('停用')}</option>
        </select>
      </label>
    );
  }

  // If this is a model field and the selected provider has enabled_models, use a select
  if (group && key === group.modelKey && providers && values) {
    const fallbackKey = group.providerKey ? group.providerKey.replace(/provider_id$/, 'provider_ids') : '';
    const fallbackProviderId = fallbackKey ? parseProviderChain(values[fallbackKey] || '')[0] : '';
    const providerId = values[group.providerKey] || fallbackProviderId;
    const provider = providers.find(p => p.id === providerId);
    const enabledModels = (provider?.enabled_models as string[]) || [];
    if (enabledModels.length > 0) {
      return (
        <label className="form-group">
          {field.label}
          <select value={value} onChange={(event) => onChange(key, event.target.value)}>
            <option value="">{t('(空 - 不使用)')}</option>
            {enabledModels.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </label>
      );
    }
  }

  const listId = suggestions?.length ? `list-${field.section}-${field.name}` : undefined;
  return (
    <>
      <Input
        label={field.label}
        type={field.type === 'password' ? 'password' : field.type === 'number' ? 'number' : 'text'}
        value={value}
        onChange={(event) => onChange(key, event.target.value)}
        placeholder={field.placeholder}
        min={field.min}
        max={field.max}
        step={field.step}
        list={listId}
      />
      {listId && (
        <datalist id={listId}>
          {suggestions?.map((item) => <option key={item} value={item} />)}
        </datalist>
      )}
    </>
  );
}

function ProviderChainControl({ field, value, onChange, providers }: { field: ConfigField; value: string; onChange: (value: string) => void; providers: JsonObject[] }) {
  const { t } = useTranslation();
  const chain = parseProviderChain(value);
  const [selectedProvider, setSelectedProvider] = useState('');
  const providerById = new Map(providers.map((provider) => [String(provider.id), provider]));
  const availableProviders = providers.filter((provider) => !chain.includes(String(provider.id)));

  function commit(next: string[]) {
    onChange(next.join(','));
  }

  function move(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= chain.length) return;
    const next = [...chain];
    [next[index], next[target]] = [next[target], next[index]];
    commit(next);
  }

  function remove(index: number) {
    commit(chain.filter((_, itemIndex) => itemIndex !== index));
  }

  function addSelected() {
    if (!selectedProvider || chain.includes(selectedProvider)) return;
    commit([...chain, selectedProvider]);
    setSelectedProvider('');
  }

  return (
    <div className="form-group">
      <label>{field.label}</label>
      <div style={{ display: 'grid', gap: '8px' }}>
        {chain.length === 0 ? (
          <div className="muted" style={{ fontSize: '13px' }}>{t('未配置 fallback；将只使用上方单一供应商。')}</div>
        ) : (
          chain.map((providerId, index) => {
            const provider = providerById.get(providerId);
            const label = provider ? `${String(provider.name)} (${providerId})` : providerId;
            return (
              <div
                key={`${providerId}-${index}`}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '28px minmax(0, 1fr) auto auto auto',
                  alignItems: 'center',
                  gap: '8px',
                }}
              >
                <span className="muted" style={{ fontSize: '12px' }}>{index + 1}</span>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
                <Button variant="ghost" size="sm" onClick={() => move(index, -1)} disabled={index === 0} title={t('上移')} aria-label={t('上移')}>
                  <ArrowUp size={14} />
                </Button>
                <Button variant="ghost" size="sm" onClick={() => move(index, 1)} disabled={index === chain.length - 1} title={t('下移')} aria-label={t('下移')}>
                  <ArrowDown size={14} />
                </Button>
                <Button variant="ghost" size="sm" onClick={() => remove(index)} title={t('移除')} aria-label={t('移除')}>
                  <X size={14} />
                </Button>
              </div>
            );
          })
        )}

        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', gap: '8px', alignItems: 'end' }}>
          <select value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value)} disabled={availableProviders.length === 0}>
            <option value="">{availableProviders.length ? t('添加供应商到 fallback') : t('没有可添加的供应商')}</option>
            {availableProviders.map((provider) => (
              <option key={String(provider.id)} value={String(provider.id)}>
                {String(provider.name)} ({String(provider.id)})
              </option>
            ))}
          </select>
          <Button variant="secondary" size="sm" onClick={addSelected} disabled={!selectedProvider} title={t('添加')} aria-label={t('添加')}>
            <Plus size={14} />
          </Button>
        </div>
      </div>
    </div>
  );
}

function parseProviderChain(value: string): string[] {
  return value.split(',').map((item) => item.trim()).filter(Boolean);
}
