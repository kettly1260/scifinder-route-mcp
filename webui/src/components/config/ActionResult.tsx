import type { JsonObject } from '../../types';

export interface ActionResultProps {
  title?: string;
  result?: JsonObject;
}

export function ActionResult({ title, result }: ActionResultProps) {
  if (!result) {
    return (
      <div className="action-result empty">
        {title && <strong>{title}</strong>}
        <span>尚未执行</span>
      </div>
    );
  }
  const status = String(result.status || (result.error ? 'error' : 'ok'));
  const detail = String(result.detail || result.error || JSON.stringify(result));
  const tone = status === 'ok' ? 'ok' : status === 'unknown' ? 'unknown' : 'error';
  return (
    <div className={`action-result ${tone}`}>
      {title && <strong>{title}</strong>}
      <span>{status}</span>
      <p>{detail}</p>
    </div>
  );
}
