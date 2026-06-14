import { useTranslation } from '../../i18n';

export interface ModelSuggestionsProps {
  models: string[];
  modelKey: string;
  onChange: (key: string, value: string) => void;
}

export function ModelSuggestions({ models, modelKey, onChange }: ModelSuggestionsProps) {
  const { t } = useTranslation();
  if (!models.length) return null;
  return (
    <div className="model-list" aria-label="已拉取模型列表">
      {models.slice(0, 12).map((model) => (
        <button key={model} type="button" onClick={() => onChange(modelKey, model)}>
          {model}
        </button>
      ))}
      {models.length > 12 && <span>+{models.length - 12} more</span>}
    </div>
  );
}
