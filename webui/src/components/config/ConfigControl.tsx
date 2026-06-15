import type { ConfigField, JsonObject } from '../../types';
import { Input } from '../../components';
import { useTranslation } from '../../i18n';

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
    const providerId = values[group.providerKey];
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
