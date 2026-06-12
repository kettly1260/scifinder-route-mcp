import type { ButtonHTMLAttributes, InputHTMLAttributes, PropsWithChildren, ReactNode } from 'react';
import type { Column } from './types';

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
type ButtonSize = 'md' | 'sm';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  fullWidth?: boolean;
  loading?: boolean;
}

// Adapted from router-for-me/Cli-Proxy-API-Management-Center (MIT).
export function Button({ children, variant = 'primary', size = 'md', fullWidth = false, loading = false, className = '', disabled, ...rest }: PropsWithChildren<ButtonProps>) {
  const classes = ['btn', `btn-${variant}`, size === 'sm' ? 'btn-sm' : '', fullWidth ? 'btn-full' : '', className].filter(Boolean).join(' ');
  return (
    <button className={classes} disabled={disabled || loading} {...rest}>
      {loading && <span className="loading-spinner" aria-hidden="true" />}
      {children !== undefined && <span>{children}</span>}
    </button>
  );
}

interface CardProps {
  title?: ReactNode;
  eyebrow?: string;
  extra?: ReactNode;
  className?: string;
}

// Adapted from router-for-me/Cli-Proxy-API-Management-Center (MIT).
export function Card({ title, eyebrow, extra, children, className }: PropsWithChildren<CardProps>) {
  return (
    <section className={className ? `card ${className}` : 'card'}>
      {(title || extra) && (
        <div className="card-header">
          <div>
            {eyebrow && <p className="eyebrow">{eyebrow}</p>}
            <div className="title">{title}</div>
          </div>
          {extra}
        </div>
      )}
      {children}
    </section>
  );
}

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
  rightElement?: ReactNode;
}

// Adapted from router-for-me/Cli-Proxy-API-Management-Center (MIT).
export function Input({ label, hint, error, rightElement, className = '', id, ...rest }: InputProps) {
  return (
    <div className="form-group">
      {label && <label htmlFor={id}>{label}</label>}
      <div className="input-shell">
        <input id={id} className={`input ${className}`.trim()} aria-invalid={Boolean(error) || rest['aria-invalid']} {...rest} />
        {rightElement && <div className="input-right">{rightElement}</div>}
      </div>
      {hint && <div className="hint">{hint}</div>}
      {error && <div className="error-box">{error}</div>}
    </div>
  );
}

export function EmptyState({ title = '暂无数据', description }: { title?: string; description?: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {description && <span>{description}</span>}
    </div>
  );
}

export function DataTable<T extends Record<string, unknown>>({ rows, columns, empty }: { rows: T[]; columns: Column<T>[]; empty?: string }) {
  if (!rows.length) {
    return <EmptyState description={empty} />;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={String(row.id || row.key || index)}>
              {columns.map((column) => <td key={column.key}>{column.render ? column.render(row) : String(row[column.key] ?? '')}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function JsonBlock({ value, maxHeight }: { value: unknown; maxHeight?: number }) {
  return <pre style={maxHeight ? { maxHeight } : undefined}>{JSON.stringify(value ?? {}, null, 2)}</pre>;
}

export function StatCard({ label, value, tone = 'default' }: { label: string; value: ReactNode; tone?: 'default' | 'good' | 'warn' | 'danger' }) {
  return (
    <article className={`stat-card stat-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}
