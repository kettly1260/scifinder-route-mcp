import { useState } from 'react';
import { useTranslation } from '../i18n';

export interface CollapsibleTextProps {
  text: string;
}

export function CollapsibleText({ text }: CollapsibleTextProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > 200;

  if (!isLong) {
    return <div className="collapsible-text-content">{text}</div>;
  }

  return (
    <div className="collapsible-text-content">
      <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {expanded ? text : text.slice(0, 200) + '...'}
      </div>
      <button
        className="expand-btn"
        onClick={(e) => {
          e.preventDefault();
          setExpanded(!expanded);
        }}
        style={{
          background: 'none',
          border: 'none',
          color: 'var(--primary-2, #00d2ff)',
          cursor: 'pointer',
          fontSize: '12px',
          padding: '4px 0 0 0',
          display: 'block'
        }}
      >
        {expanded ? t('收起') : t('展开全文')}
      </button>
    </div>
  );
}
