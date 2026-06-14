import { useState, useMemo, type ButtonHTMLAttributes, type InputHTMLAttributes, type PropsWithChildren, type ReactNode } from 'react';
import type { Column } from './types';
import { useTranslation } from './i18n';
import { ChevronUp, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-react';

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
  style?: React.CSSProperties;
}

// Adapted from router-for-me/Cli-Proxy-API-Management-Center (MIT).
export function Card({ title, eyebrow, extra, children, className, style }: PropsWithChildren<CardProps>) {
  return (
    <section className={className ? `card ${className}` : 'card'} style={style}>
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

export function DataTable<T extends Record<string, any>>({ 
  rows, 
  columns, 
  empty,
  defaultPageSize = 10 
}: { 
  rows: T[]; 
  columns: Column<T>[]; 
  empty?: string;
  defaultPageSize?: number;
}) {
  const { t } = useTranslation();
  
  // Sort State
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');
  
  // Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(defaultPageSize);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortOrder('asc');
    }
    setCurrentPage(1); // Reset to first page on sort
  };

  // Sort rows
  const sortedRows = useMemo(() => {
    if (!sortKey) return rows;
    
    return [...rows].sort((a, b) => {
      const aVal = a[sortKey];
      const bVal = b[sortKey];
      
      if (aVal === undefined || aVal === null) return 1;
      if (bVal === undefined || bVal === null) return -1;
      
      if (typeof aVal === 'number' && typeof bVal === 'number') {
        return sortOrder === 'asc' ? aVal - bVal : bVal - aVal;
      }
      
      const aStr = String(aVal).toLowerCase();
      const bStr = String(bVal).toLowerCase();
      
      if (aStr < bStr) return sortOrder === 'asc' ? -1 : 1;
      if (aStr > bStr) return sortOrder === 'asc' ? 1 : -1;
      return 0;
    });
  }, [rows, sortKey, sortOrder]);

  // Paginate rows
  const paginatedRows = useMemo(() => {
    const startIndex = (currentPage - 1) * pageSize;
    return sortedRows.slice(startIndex, startIndex + pageSize);
  }, [sortedRows, currentPage, pageSize]);

  const totalPages = Math.ceil(rows.length / pageSize);

  if (!rows.length) {
    return <EmptyState description={empty} />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {columns.map((column) => {
                const isSortable = column.key && !column.render;
                return (
                  <th 
                    key={column.key}
                    onClick={() => isSortable && handleSort(column.key)}
                    style={isSortable ? { cursor: 'pointer', userSelect: 'none' } : undefined}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <span>{column.label}</span>
                      {sortKey === column.key && (
                        sortOrder === 'asc' ? <ChevronUp size={14} /> : <ChevronDown size={14} />
                      )}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {paginatedRows.map((row, index) => (
              <tr key={String(row.id || row.key || index)}>
                {columns.map((column) => (
                  <td key={column.key}>
                    {column.render ? column.render(row) : String(row[column.key] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination Controls */}
      {rows.length > pageSize && (
        <div 
          style={{ 
            display: 'flex', 
            justifyContent: 'space-between', 
            alignItems: 'center',
            padding: '8px 12px',
            backgroundColor: 'var(--panel-soft)',
            borderRadius: '6px',
            fontSize: '13px'
          }}
        >
          <div style={{ color: 'var(--muted)' }}>
            {t('显示')} {Math.min(rows.length, (currentPage - 1) * pageSize + 1)} - {Math.min(rows.length, currentPage * pageSize)} {t('条，共')} {rows.length} {t('条')}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <Button 
              size="sm" 
              variant="ghost" 
              disabled={currentPage === 1}
              onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
              style={{ padding: '4px 8px' }}
            >
              <ChevronLeft size={16} />
            </Button>
            <span style={{ fontWeight: 500 }}>
              {currentPage} / {totalPages}
            </span>
            <Button 
              size="sm" 
              variant="ghost" 
              disabled={currentPage === totalPages}
              onClick={() => setCurrentPage(prev => Math.min(totalPages, prev + 1))}
              style={{ padding: '4px 8px' }}
            >
              <ChevronRight size={16} />
            </Button>
          </div>
        </div>
      )}
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
