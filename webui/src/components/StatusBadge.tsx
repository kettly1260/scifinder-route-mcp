import { useTranslation } from '../i18n';

export interface StatusBadgeProps {
  tone: 'success' | 'deduped' | 'failed';
  children: string;
}

export function StatusBadge({ tone, children }: StatusBadgeProps) {
  const { t } = useTranslation();
  return <span className={`status-badge ${tone}`}>{t(children)}</span>;
}
