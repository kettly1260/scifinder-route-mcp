import type { ReactNode } from 'react';

export type JsonObject = Record<string, unknown>;

export interface AdminState {
  auth_required: boolean;
  health: JsonObject;
  config: JsonObject;
  validation: { valid?: boolean; warnings?: string[] };
  jobs: JsonObject[];
  production: JsonObject;
}

export interface Column<T> {
  key: string;
  label: string;
  render?: (row: T) => ReactNode;
}

export interface ConfigField {
  section: string;
  name: string;
  label: string;
  type?: 'text' | 'password' | 'number' | 'select' | 'bool' | 'list';
  options?: string[];
  placeholder?: string;
  step?: string;
  min?: string;
  max?: string;
  secret?: boolean;
}
