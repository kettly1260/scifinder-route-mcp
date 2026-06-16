import type { JsonObject, ConfigField, AdminState } from './types';

export type ThemeId = 'aurora' | 'graphite' | 'emerald' | 'rose' | 'light';

export type UploadResultRow = {
  file_name: string;
  status: string;
  tone: 'success' | 'deduped' | 'failed';
  detail: string;
  uploaded_path?: string;
  document_id?: string;
  job_id?: string;
  deduplicated?: boolean;
};

export type ZoteroEndpointForm = {
  id?: string;
  alias: string;
  group_name: string;
  url: string;
  priority: string;
  timeout_seconds: string;
  enabled: string;
  write_note_enabled: string;
  headers: string;
};

export const defaultZoteroEndpoint: ZoteroEndpointForm = {
  alias: 'local-zotero',
  group_name: 'local-zotero',
  url: 'http://127.0.0.1:23120/mcp',
  priority: '100',
  timeout_seconds: '10',
  enabled: 'true',
  write_note_enabled: 'false',
  headers: ''
};

export const THEME_KEY = 'scifinderRouteAdminTheme';

export const themes: Array<{ id: ThemeId; label: string }> = [
  { id: 'aurora', label: 'Aurora' },
  { id: 'graphite', label: 'Graphite' },
  { id: 'emerald', label: 'Emerald' },
  { id: 'rose', label: 'Rose' },
  { id: 'light', label: 'Light' }
];

export function initialTheme(): ThemeId {
  const saved = localStorage.getItem(THEME_KEY);
  return themes.some((theme) => theme.id === saved) ? (saved as ThemeId) : 'aurora';
}

export interface PageConfig {
  id: string;
  path: string;
  label: string;
  description: string;
  iconName: string;
}

export const pages: PageConfig[] = [
  { id: 'dashboard', path: '/',          label: 'Dashboard', description: '运行状态与关键指标', iconName: 'LayoutDashboard' },
  { id: 'ingest',    path: '/ingest',    label: '导入与任务', description: '上传、扫描、解析队列', iconName: 'Upload' },
  { id: 'documents', path: '/documents', label: 'Documents', description: '查看 PDF/RTF/HTML 解析结果', iconName: 'FileText' },
  { id: 'config',    path: '/config',    label: '配置',      description: '集成、运行时、热配置', iconName: 'Settings' },
  { id: 'rdf',       path: '/rdf',       label: 'RDF 反应',  description: 'CAS 反应记录与 molfile', iconName: 'FlaskConical' },
  { id: 'structures',path: '/structures',label: '结构检索',   description: '相似度、子结构、文本过滤', iconName: 'Search' },
  { id: 'literature',path: '/literature',label: '文献 / Zotero',description: '端点、候选链接、写回', iconName: 'BookOpen' },
  { id: 'ops',       path: '/ops',       label: '运维诊断',   description: '索引、备份、回收站、配置警告', iconName: 'Wrench' }
];

export const configFields: ConfigField[] = [
  { section: 'integrations', name: 'extraction_provider_id', label: 'LLM 供应商 ID' },
  { section: 'integrations', name: 'extraction_model', label: 'LLM 模型', placeholder: 'gpt-4o-mini / gemini-2.5-pro' },

  { section: 'integrations', name: 'embedding_provider_id', label: '嵌入供应商 ID' },
  { section: 'integrations', name: 'embedding_model', label: '嵌入模型', placeholder: 'bge-m3' },
  { section: 'integrations', name: 'ocr_provider_id', label: 'OCR 供应商 ID' },
  { section: 'integrations', name: 'ocr_model', label: 'OCR 模型', placeholder: 'mineru-layout / PaddleOCR-VL-1.6' },
  { section: 'integrations', name: 'document_parser_provider_id', label: '文档解析供应商 ID' },
  { section: 'integrations', name: 'document_parser_model', label: '文档解析模型', placeholder: 'pymupdf / mineru' },
  { section: 'integrations', name: 'document_parser_fallback', label: '解析失败回退', type: 'bool' },
  { section: 'integrations', name: 'structure_recognition_provider_id', label: '结构识别供应商 ID' },
  { section: 'integrations', name: 'structure_recognition_model', label: '结构识别模型', placeholder: 'decimer / molscribe / osra' },
  { section: 'integrations', name: 'reranker_provider_id', label: '重排供应商 ID' },
  { section: 'integrations', name: 'reranker_model', label: '重排模型', placeholder: 'bge-reranker-v2-m3' },
  { section: 'integrations', name: 'postgres_url', label: 'PostgreSQL URL', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'server', name: 'max_workers', label: '最大工作线程', type: 'number', min: '1' },
  { section: 'server', name: 'async_jobs', label: '异步任务', type: 'bool' },
  { section: 'server', name: 'storage_backend', label: '存储后端', type: 'select', options: ['sqlite', 'postgres'] },
  { section: 'queue', name: 'backend', label: '队列后端', type: 'select', options: ['sqlite', 'redis'] },
  { section: 'queue', name: 'redis_url', label: 'Redis URL', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'security', name: 'allow_external_paths', label: '允许外部路径', type: 'bool' },
  { section: 'security', name: 'token', label: '配置令牌', type: 'password', secret: true, placeholder: '留空则不变' },
  { section: 'ingest', name: 'scan_extensions', label: '扫描扩展名', type: 'list', placeholder: '.pdf,.rtf,.rdf,.html,.htm,.mhtml,.mht,.md,.markdown,.txt' },
  { section: 'thresholds', name: 'verification_confidence_threshold', label: '验证置信阈值', type: 'number', min: '0', max: '1', step: '0.01' },
  { section: 'extraction', name: 'llm_schema_version', label: 'LLM Schema 版本' },
  { section: 'extraction', name: 'llm_prompt_profile', label: 'LLM 提示词配置' },
  { section: 'extraction', name: 'llm_cost_limit_usd', label: 'LLM 成本上限 USD', type: 'number', min: '0', step: '0.01' },
  { section: 'retention', name: 'evidence_retention_days', label: '证据保留天数', type: 'number', min: '1' },
  { section: 'retention', name: 'cache_retention_days', label: '缓存保留天数', type: 'number', min: '1' },
  { section: 'integrations', name: 'zotero_linking_enabled', label: 'Zotero 自动链接', type: 'bool' }
];

export const configFieldByKey = new Map(configFields.map((field) => [`${field.section}.${field.name}`, field]));

export type ConfigGroup = { eyebrow: string; title: string; fields: readonly string[] };
export type IntegrationGroup = ConfigGroup & { id: string; description: string; modelKey: string; providerKey: string };

export const integrationGroups = [
  { id: 'extraction', eyebrow: 'LLM', title: 'LLM 结构化', description: '用于反应步骤结构化、证据整理等。', fields: ['integrations.extraction_provider_id', 'integrations.extraction_model'], modelKey: 'integrations.extraction_model', providerKey: 'integrations.extraction_provider_id' },
  { id: 'embedding', eyebrow: 'Embedding', title: '嵌入模型', description: '用于语义召回和向量索引重建。', fields: ['integrations.embedding_provider_id', 'integrations.embedding_model'], modelKey: 'integrations.embedding_model', providerKey: 'integrations.embedding_provider_id' },
  { id: 'ocr', eyebrow: 'OCR', title: 'OCR 识别', description: '用于扫描件和页面视觉证据抽取。', fields: ['integrations.ocr_provider_id', 'integrations.ocr_model'], modelKey: 'integrations.ocr_model', providerKey: 'integrations.ocr_provider_id' },
  { id: 'document_parser', eyebrow: 'Parser', title: '文档解析', description: '用于 PDF/RTF/HTML 正文解析 and 失败回退策略。', fields: ['integrations.document_parser_provider_id', 'integrations.document_parser_model', 'integrations.document_parser_fallback'], modelKey: 'integrations.document_parser_model', providerKey: 'integrations.document_parser_provider_id' },
  { id: 'structure_recognition', eyebrow: 'Structure', title: '结构识别', description: '用于图片结构识别和结构敏感证据补充。', fields: ['integrations.structure_recognition_provider_id', 'integrations.structure_recognition_model'], modelKey: 'integrations.structure_recognition_model', providerKey: 'integrations.structure_recognition_provider_id' },
  { id: 'reranker', eyebrow: 'Reranker', title: '重排模型', description: '用于提升搜索召回结果的排序质量。', fields: ['integrations.reranker_provider_id', 'integrations.reranker_model'], modelKey: 'integrations.reranker_model', providerKey: 'integrations.reranker_provider_id' }
] as const;

export const runtimeGroups = [
  { eyebrow: 'Runtime', title: '服务运行时', fields: ['server.storage_backend', 'server.max_workers', 'server.async_jobs', 'security.allow_external_paths', 'security.token'] },
  { eyebrow: 'Queue', title: '队列与缓存', fields: ['queue.backend', 'queue.redis_url', 'retention.evidence_retention_days', 'retention.cache_retention_days'] },
  { eyebrow: 'Ingest', title: '导入与抽取', fields: ['ingest.scan_extensions', 'thresholds.verification_confidence_threshold', 'extraction.llm_schema_version', 'extraction.llm_prompt_profile', 'extraction.llm_cost_limit_usd'] },
  { eyebrow: 'Literature', title: 'Zotero 链接策略', fields: ['integrations.zotero_linking_enabled'] }
] as const;

export function authError(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  return text.includes('Invalid or missing admin token') ? '需要管理令牌：请登录后重试' : text;
}

export function valueAt(config: JsonObject, section: string, name: string): unknown {
  const group = config[section];
  return group && typeof group === 'object' ? (group as JsonObject)[name] : undefined;
}

export function shortName(path: unknown): string {
  return String(path || '').replace(/\\/g, '/').split('/').filter(Boolean).pop() || String(path || '');
}

export function bytes(value: unknown): string {
  const n = Number(value || 0);
  if (n > 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n > 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

export function mergeLineBreaks(text: string): string {
  if (!text) return '';
  return text
    .replace(/([a-zA-Z0-9,;:\-])\n\s*([a-zA-Z0-9])/g, '$1 $2')
    .replace(/[ \t]+/g, ' ')
    .trim();
}

export function buildConfigValues(config: JsonObject): Record<string, string> {
  const values: Record<string, string> = {};
  for (const field of configFields) {
    if (field.secret) continue;
    const value = valueAt(config, field.section, field.name);
    values[`${field.section}.${field.name}`] = Array.isArray(value) ? value.join(',') : value === undefined || value === null ? '' : String(value);
  }
  return values;
}

export function asObject(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {};
}

export function asObjectArray(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter((item): item is JsonObject => Boolean(item) && typeof item === 'object' && !Array.isArray(item)) : [];
}

export function rdfRoleOrder(role: unknown): number {
  const order = ['reactant', 'product', 'reagent', 'catalyst', 'solvent', 'unknown'];
  const index = order.indexOf(String(role || 'unknown'));
  return index === -1 ? order.length : index;
}

export interface PageProps {
  token: string;
  state: AdminState;
  guarded: <T>(action: () => Promise<T>, success?: string, busyKey?: string) => Promise<T | undefined>;
  isBusy: (busyKey: string) => boolean;
}
