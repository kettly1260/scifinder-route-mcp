import { Link } from 'react-router-dom';
import { ChevronRight, Home } from 'lucide-react';

export interface BreadcrumbItem {
  label: string;
  path?: string;
}

interface BreadcrumbProps {
  items: BreadcrumbItem[];
}

export function Breadcrumb({ items }: BreadcrumbProps) {
  return (
    <nav className="breadcrumb-container" aria-label="Breadcrumb">
      <div className="breadcrumb-item">
        <Link to="/" className="breadcrumb-link">
          <Home size={14} />
        </Link>
      </div>
      {items.map((item, index) => (
        <div key={index} className="breadcrumb-item">
          <span className="breadcrumb-separator">
            <ChevronRight size={14} />
          </span>
          {item.path ? (
            <Link to={item.path} className="breadcrumb-link">
              {item.label}
            </Link>
          ) : (
            <span className="breadcrumb-current" title={item.label}>
              {item.label}
            </span>
          )}
        </div>
      ))}
    </nav>
  );
}
