import React, { createContext, useContext, useState, useEffect } from 'react';

export type Language = 'zh' | 'en';

const translations: Record<Language, Record<string, string>> = {
  zh: {
    // We will use the key directly if zh translation is not found
  },
  en: {
    // Sidebar & Pages
    'Dashboard': 'Dashboard',
    '运行状态与关键指标': 'Status & Metrics',
    '导入与任务': 'Ingest & Jobs',
    '上传、扫描、解析队列': 'Upload, Scan, Parse Queue',
    'Documents': 'Documents',
    '查看 PDF/RTF/HTML 解析结果': 'View PDF/RTF/HTML parsed results',
    '配置': 'Config',
    '集成、运行时、热配置': 'Integrations, Runtime, Hot Config',
    'RDF 反应': 'RDF Reactions',
    'CAS 反应记录与 molfile': 'CAS reaction records and molfile',
    '结构检索': 'Structure Search',
    '相似度、子结构、文本过滤': 'Similarity, Substructure, Text filter',
    '文献 / Zotero': 'Literature / Zotero',
    '端点、候选链接、写回': 'Endpoints, Candidate Links, Write-back',
    '运维诊断': 'Ops & Diagnostics',
    '索引、备份、回收站、配置警告': 'Index, Backup, Trash, Config Warnings',
    'Admin Console': 'Admin Console',
    '退出': 'Logout',
    'Token protected': 'Token protected',
    'Trusted local mode': 'Trusted local mode',
    'Theme': 'Theme',
    '刷新': 'Refresh',

    // Dashboard
    '状态已刷新': 'Status refreshed',
    '需要管理令牌：请登录后重试': 'Admin token required: please login and try again',
    '文档数': 'Documents',
    '反应步骤': 'Reaction Steps',
    'OCR 积压': 'OCR Backlog',
    '异步任务': 'Async Jobs',
    '启用': 'Enabled',
    '停用': 'Disabled',
    '基础运行信息': 'Basic Runtime Info',
    '配置文件': 'Config File',
    '存储后端': 'Storage Backend',
    '队列后端': 'Queue Backend',
    '化合物': 'Compounds',
    'NAS 存储使用': 'NAS Storage Usage',
    '路径': 'Path',
    '文件数': 'Files',
    '大小': 'Size',
    '诊断快照': 'Diagnostic Snapshot',

    // Ingest
    '上传并导入': 'Upload & Ingest',
    '批量上传并导入': 'Batch Upload & Ingest',
    '清空': 'Clear',
    '点击选择文件': 'Click to select files',
    '尚未选择文件': 'No file selected',
    '已选择文件，准备上传': 'Files selected, ready to upload',
    '支持 PDF/RTF/RDF/HTML/MHTML/Markdown/TXT。上传仍会经过后端扩展名、嗅探和安全校验。': 'Supports PDF/RTF/RDF/HTML/MHTML/Markdown/TXT. Uploads still pass backend extension, sniffing and security checks.',
    '扫描收件箱': 'Scan Inbox',
    '扫描': 'Scan',
    '扫描完成': 'Scan completed',
    '从服务端可见 inbox 中登记新增 SciFinder 导出文件。不会绕过导入规则。': 'Register new SciFinder exports from server-visible inbox. Will not bypass import rules.',
    '批量上传结果': 'Batch Upload Results',
    '成功': 'Success',
    '去重': 'Deduped',
    '失败': 'Failed',
    '文件': 'File',
    '状态': 'Status',
    '详情': 'Detail',
    '文档 ID': 'Document ID',
    '查看解析': 'View Parse',
    '任务 ID': 'Job ID',
    '写入路径': 'Write Path',
    '最近解析任务': 'Recent Parse Jobs',
    '重试失败任务': 'Retry Failed Jobs',
    '已提交失败任务重试': 'Failed jobs retry submitted',
    '阶段': 'Stage',
    '错误': 'Error',
    '状态: ': 'Status: ',
    '类型: ': 'Type: ',
    '文本块: ': 'Chunks: ',
    '反应步骤: ': 'Reaction Steps: ',

    // Documents
    '上传/注册文档': 'Uploaded/Registered Documents',
    '加载文档': 'Load Documents',
    '文档列表已加载': 'Document list loaded',
    '查看非 RDF 文件的完整解析文本块、页码/解析器来源，以及同一文档抽取出的反应步骤。': 'View complete parsed chunks, page/parser sources, and extracted reaction steps from non-RDF files.',
    '搜索': 'Search',
    '标题、文件名、DOI 或文档 ID': 'Title, filename, DOI or document ID',
    '文件类型': 'File Type',
    '全部': 'All',
    '类型': 'Type',
    '标题': 'Title',
    '文本块': 'Chunks',
    '反应': 'Reactions',
    '最近错误': 'Recent Error',
    '打开': 'Open',
    '暂无文档；请先上传或扫描收件箱。': 'No documents; please upload or scan inbox first.',

    // Document Detail
    '← 返回文档列表': '← Back to Document List',
    '解析结果': 'Parse Results',
    '重新解析': 'Reparse',
    '重新解析将清除现有反应步骤并重新提取，确定继续？': 'Reparsing will clear existing reaction steps and re-extract. Continue?',
    '已启动重新解析任务': 'Reparse job started',
    '加载文档详情中...': 'Loading document details...',
    '该文档已解析，但未抽取到反应步骤。请检查解析文本是否包含实验步骤，或调整抽取规则/LLM 配置后重解析。': 'Document parsed, but no reaction steps extracted. Please check if parsed text contains experimental procedures, or adjust extraction rules/LLM config and reparse.',
    '该文档解析失败。若已有 partial chunks 会在下方展示；请查看任务错误并重试。': 'Document parse failed. Any partial chunks will be shown below; please check job error and retry.',
    '完整解析文本': 'Complete Parsed Text',
    '暂无解析文本': 'No Parsed Text',
    '旧文档可能是在该功能上线前解析；点击上方「重新解析」后将保存文本块。': 'Older documents might be parsed before this feature; click "Reparse" above to save chunks.',
    '页码未知': 'Unknown Page',
    '解析器: ': 'Parser: ',
    '加载更多文本块': 'Load More Chunks',
    '已加载更多文本块': 'More chunks loaded',
    '提取结果': 'Extraction Results',
    '抽取反应步骤': 'Extracted Reaction Steps',
    '步骤': 'Step',
    '名称': 'Name',
    '试剂': 'Reagents',
    '溶剂': 'Solvent',
    '收率': 'Yield',
    '置信度': 'Confidence',
    '原文': 'Original Text',
    '该文档当前没有抽取出的反应步骤。': 'No extracted reaction steps for this document currently.',

    // Config
    '热配置工作区': 'Hot Config Workspace',
    '每个集成单独测试和拉取模型。按钮会使用当前表单内容，不需要先保存；保存后才会写入 `webui-config.yaml`。': 'Test each integration and pull models separately. Buttons use current form content without saving; only saves write to `webui-config.yaml`.',
    '保存并重载': 'Save & Reload',
    '配置已保存并重载': 'Config saved and reloaded',
    '功能路由配置': 'Function Routing Config',
    '服务、队列与抽取策略': 'Services, Queues & Extraction Policies',
    '测试 Postgres': 'Test Postgres',
    'Postgres 测试完成': 'Postgres test completed',
    '文献源连通性': 'Literature Source Connectivity',
    '测试 Zotero MCP': 'Test Zotero MCP',
    'Zotero MCP 测试完成': 'Zotero MCP test completed',
    'Zotero 端点地址在“文献 / Zotero”页面维护，这里只测试已保存的端点组。': 'Zotero endpoints are maintained in "Literature / Zotero" page, only testing saved endpoint groups here.',
    '测试': 'Test',
    '拉取模型': 'Pull Models',

    // RDF
    'RDF 记录管理': 'RDF Records Management',
    '检索记录': 'Search Records',
    '记录列表已加载': 'Records list loaded',
    '只支持搜索 CAS Reaction Number 和内部 Reaction ID。如需结构检索请前往「结构检索」。': 'Only supports searching CAS Reaction Number and internal Reaction ID. For structure search, go to "Structure Search".',
    'CAS / Reaction ID': 'CAS / Reaction ID',
    '有结构图': 'Has Image',
    '是': 'Yes',
    '否': 'No',
    '来源文档': 'Source Doc',
    '创建时间': 'Created At',
    '详情加载中...': 'Loading details...',
    '未提供相关文献引用': 'No related literature references provided',
    '未提供分子结构图': 'No molecular structure image provided',
    '记录已删除': 'Record deleted',
    '确定删除该记录吗？': 'Are you sure you want to delete this record?',
    '删除': 'Delete',
    '此面板展示 RDF 中的原始字段。如果反应被多个文档提及，可能会有合并记录。': 'This panel shows raw fields from RDF. If reaction is mentioned in multiple docs, there might be merged records.',
    '包含': 'Contains',
    '相关反应': 'Related Reactions',
    '共用此文档': 'Sharing this document',

    // Structure
    '← 返回反应记录': '← Back to Reaction Record',
    '反应详情与结构': 'Reaction Details & Structure',
    '化学索引状态': 'Chem Index Status',
    '临时安装 RDKit': 'Temporary Install RDKit',
    '需要重启容器。': 'Container Restart Required.',
    'RDKit 安装已完成或部分完成，但长驻 worker 需要通过容器重启获得干净导入环境。': 'RDKit installation complete or partially complete, but resident worker requires a container restart for a clean import environment.',
    '持久化提醒。': 'Persistence Warning.',
    '推荐方式。': 'Recommended Method.',
    '当前镜像构建默认包含 RDKit；如果此处显示 RDKit 缺失，请优先重新拉取/重建镜像。按钮只用于旧镜像或异常环境的临时修复。': 'Current image build includes RDKit by default; if RDKit is missing here, prioritize re-pulling/rebuilding the image. This button is only for temporary fixes of older images or broken environments.',
    '临时 RDKit 安装任务已启动；完成后若提示需要重启，请重启容器': 'Temporary RDKit install job started; if it prompts for restart upon completion, please restart container',
    '结构结果': 'Structure Results',
    '角色': 'Role',
    '版本': 'Version',
    '评分': 'Score',
    '反应 ID': 'Reaction ID',
    '结构图': 'Structure Image',
    '暂无': 'None',
    '查询': 'Query',
    'SMILES、SMARTS、CAS 或名称': 'SMILES, SMARTS, CAS or Name',
    '模式': 'Mode',
    '相似度': 'Similarity',
    '子结构': 'Substructure',
    '文本过滤': 'Text Filter',
    '结构检索完成': 'Structure search completed',

    // Literature
    'Zotero 端点配置': 'Zotero Endpoint Config',
    '文献索引服务': 'Literature Index Service',
    '添加端点': 'Add Endpoint',
    '刷新端点列表完成': 'Endpoint list refreshed',
    '在此注册您的 zotero-mcp 服务地址，用于自动或手动将证据关联回文献库。': 'Register your zotero-mcp service addresses here, used for auto or manual evidence linking back to literature library.',
    '暂无配置端点，请添加。': 'No configured endpoints, please add one.',
    '别名': 'Alias',
    '组名': 'Group Name',
    '优先级': 'Priority',
    '超时': 'Timeout',
    '写笔记': 'Write Note',
    '操作': 'Actions',
    '禁用': 'Disable',
    '删除已提交': 'Deletion submitted',
    '保存已提交': 'Save submitted',
    '保存端点': 'Save Endpoint',
    '取消': 'Cancel',
    '未链接文献匹配': 'Unlinked Literature Matching',
    '搜索候选文献': 'Search Candidate Literature',
    '暂无候选文献。': 'No candidate literature.',

    // Ops
    '缓存与状态管理': 'Cache & State Management',
    '系统维护': 'System Maintenance',
    '清理孤立文档': 'Clean Orphaned Docs',
    '重建结构索引': 'Rebuild Structure Index',
    '执行已启动': 'Execution started',
    '谨慎操作：会遍历存储并修复数据库不一致。': 'Use with caution: traverses storage to fix DB inconsistencies.',
    '仅在查询不到新写入的化合物时需要手动触发。': 'Manually trigger only when newly written compounds cannot be queried.',

    // Login
    'NAS 控制台': 'NAS Console',
    '输入管理令牌以访问配置、导入、RDF 反应和文献链接面板。未配置鉴权的本地可信部署会自动进入。': 'Enter admin token to access config, ingest, RDF reactions and literature linking panels. Trusted local deployments without auth configured will enter automatically.',
    '管理令牌': 'Admin Token',
    '信任此设备，重启浏览器后仍保持登录': 'Trust this device, stay logged in after browser restart',
    '进入控制台': 'Enter Console',
    '已登录，并信任此设备': 'Logged in, and trusted this device',
    '已登录，本次会话有效': 'Logged in, valid for this session',
    '已退出登录': 'Logged out',

    // Misc
    '暂无数据': 'No data available',
    '展开全文': 'Expand Full Text',
    '收起': 'Collapse',
    '第 ': 'No. ',
    ' 块': ' Chunk',
    ' 页': ' Page',
    '支持批量选择 PDF/RTF/RDF/HTML/MHTML/Markdown/TXT，系统会逐个上传并导入。': 'Supports batch selection of PDF/RTF/RDF/HTML/MHTML/Markdown/TXT, system will upload and ingest them one by one.',
    '已选择 ': 'Selected ',
    ' 个文件': ' file(s)'
  }
};

interface I18nContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: (key: string) => string;
}

const I18nContext = createContext<I18nContextType>({
  language: 'zh',
  setLanguage: () => {},
  t: (key) => key,
});

export function useTranslation() {
  return useContext(I18nContext);
}

const LANG_KEY = 'scifinderRouteAdminLang';

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>('zh');

  useEffect(() => {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved === 'zh' || saved === 'en') {
      setLanguageState(saved);
    }
  }, []);

  const setLanguage = (lang: Language) => {
    setLanguageState(lang);
    localStorage.setItem(LANG_KEY, lang);
  };

  const t = (key: string) => {
    const translation = translations[language]?.[key];
    return translation || key;
  };

  return (
    <I18nContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </I18nContext.Provider>
  );
}
