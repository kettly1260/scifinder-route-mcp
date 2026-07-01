import { useState } from 'react';
import type { AdminStatusState, JsonObject } from '../types';
import { bytes, type PageProps, type UploadResultRow } from '../constants';
import { postJson } from '../api';
import { Button, Card, DataTable } from '../components';
import { StatusBadge } from '../components/StatusBadge';
import { useTranslation } from '../i18n';
import { useToast } from '../components/Toast';
import { Search, ShieldCheck, Upload } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

export interface IngestPageProps extends PageProps {
  refresh: () => Promise<AdminStatusState>;
  openDocument: (documentId: string) => void;
}

function uploadFileWithProgress(
  token: string,
  file: File,
  onProgress: (percent: number) => void
): Promise<JsonObject> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload');
    xhr.setRequestHeader('X-Scifinder-Route-Token', token);
    
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const percent = Math.round((event.loaded / event.total) * 100);
        onProgress(percent);
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const res = JSON.parse(xhr.responseText);
          if (res.error) {
            reject(new Error(res.error));
          } else {
            resolve(res);
          }
        } catch (e) {
          reject(new Error('解析响应失败'));
        }
      } else {
        reject(new Error(xhr.statusText || '上传失败'));
      }
    };

    xhr.onerror = () => reject(new Error('网络错误'));
    xhr.onabort = () => reject(new Error('上传已中止'));

    const formData = new FormData();
    formData.append('file', file);
    xhr.send(formData);
  });
}

async function previewUploadFile(token: string, file: File): Promise<JsonObject[]> {
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch('/api/upload/preview', {
    method: 'POST',
    headers: { 'X-Scifinder-Route-Token': token },
    body: formData
  });
  const data = await response.json().catch(() => ({ error: response.statusText }));
  if (!response.ok || data.error) {
    throw new Error(String(data.error || response.statusText));
  }
  return Array.isArray(data.items) ? data.items as JsonObject[] : [];
}

export function IngestPage({ token, state, guarded, refresh, openDocument, isBusy }: IngestPageProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [uploadStatus, setUploadStatus] = useState('');
  const [uploadResults, setUploadResults] = useState<UploadResultRow[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<Record<string, { percent: number; status: string; error?: string }>>({});
  const [previewRows, setPreviewRows] = useState<JsonObject[]>([]);
  const [previewStatus, setPreviewStatus] = useState('');
  const [inboxPreviewRows, setInboxPreviewRows] = useState<JsonObject[]>([]);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const droppedFiles = Array.from(e.dataTransfer.files);
    // Filter by accepted extensions
    const acceptedExtensions = ['.pdf', '.rtf', '.rdf', '.html', '.htm', '.mhtml', '.mht', '.md', '.markdown', '.txt'];
    const filteredFiles = droppedFiles.filter(file => {
      const ext = '.' + file.name.split('.').pop()?.toLowerCase();
      return acceptedExtensions.includes(ext);
    });
    if (filteredFiles.length > 0) {
      setFiles(prev => [...prev, ...filteredFiles]);
      setUploadResults([]);
      setPreviewRows([]);
      setPreviewStatus('');
      setUploadStatus(t('已添加拖拽文件，准备上传'));
    } else {
      toast.warning(t('未检测到支持的 SciFinder 格式文件'));
    }
  };

  async function uploadSelectedFiles() {
    if (!files.length) return;
    const results: UploadResultRow[] = [];
    const previewByName = new Map(previewRows.map((row) => [String(row.client_file_name || row.file_name || row.original_file_name || ''), row]));
    const excludedNames = new Set(
      previewRows
        .filter((row) => row.include === false)
        .map((row) => String(row.client_file_name || row.file_name || row.original_file_name || ''))
    );
    for (const row of previewRows.filter((item) => item.include === false)) {
      results.push({
        file_name: String(row.client_file_name || row.file_name || row.original_file_name || ''),
        status: '已排除',
        tone: 'failed',
        detail: String(row.exclude_reason || row.reason || '预检未通过')
      });
    }
    const filesToUpload = previewRows.length
      ? files.filter((file) => !excludedNames.has(file.name) && previewByName.has(file.name))
      : files;
    if (!filesToUpload.length) {
      setUploadResults(results);
      setUploadStatus(t('预检没有可导入文件'));
      toast.warning(t('预检没有可导入文件'));
      return;
    }
    
    // Initialize progress
    const initProgress: Record<string, { percent: number; status: string }> = {};
    filesToUpload.forEach(f => {
      initProgress[f.name] = { percent: 0, status: 'pending' };
    });
    setUploadProgress(initProgress);

    for (let index = 0; index < filesToUpload.length; index += 1) {
      const file = filesToUpload[index];
      setUploadStatus(`正在上传 ${index + 1}/${filesToUpload.length}: ${file.name}`);
      
      setUploadProgress(prev => ({
        ...prev,
        [file.name]: { percent: 0, status: 'running' }
      }));

      try {
        const result = await uploadFileWithProgress(token, file, (percent) => {
          setUploadProgress(prev => ({
            ...prev,
            [file.name]: { percent, status: 'running' }
          }));
        });

        const job = result.job as JsonObject | undefined;
        const document = result.document as JsonObject | undefined;
        const deduplicated = Boolean(result.deduplicated);
        
        setUploadProgress(prev => ({
          ...prev,
          [file.name]: { percent: 100, status: deduplicated ? 'deduped' : 'success' }
        }));

        results.push({
          file_name: file.name,
          status: deduplicated ? '已去重' : String((job?.status as string | undefined) || '已导入'),
          tone: deduplicated ? 'deduped' : 'success',
          detail: deduplicated ? '服务器检测到重复文档，已跳过写入' : String((job?.stage as string | undefined) || result.uploaded_path || '已提交导入'),
          uploaded_path: String(result.uploaded_path || ''),
          document_id: String(document?.id || ''),
          job_id: String(job?.id || ''),
          deduplicated
        });
      } catch (error) {
        const errMsg = error instanceof Error ? error.message : String(error);
        setUploadProgress(prev => ({
          ...prev,
          [file.name]: { percent: 100, status: 'failed', error: errMsg }
        }));
        results.push({
          file_name: file.name,
          status: '失败',
          tone: 'failed',
          detail: errMsg
        });
      }
    }
    setUploadResults(results);
    const successCount = results.filter((item) => item.status !== '失败').length;
    setUploadStatus(`完成：${successCount}/${results.length} 个文件已处理`);
    toast.success(`完成：${successCount}/${results.length} 个文件已处理`);
    await refresh();
  }

  async function previewSelectedFiles() {
    if (!files.length) return;
    const rows: JsonObject[] = [];
    setPreviewStatus(t('正在预检证据文件...'));
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      try {
        const resultRows = await previewUploadFile(token, file);
        rows.push(...resultRows.map((row) => ({ ...row, client_file_name: file.name, client_size: file.size })));
      } catch (error) {
        rows.push({
          id: `${file.name}-${file.size}`,
          file_name: file.name,
          size_bytes: file.size,
          include: false,
          import_action: 'exclude',
          evidence_kind: 'preview_error',
          evidence_priority: 0,
          reason: error instanceof Error ? error.message : String(error),
          exclude_reason: error instanceof Error ? error.message : String(error)
        });
      }
    }
    setPreviewRows(rows);
    const includeCount = rows.filter((row) => row.include !== false).length;
    setPreviewStatus(`${t('预检完成')}：${includeCount}/${rows.length} ${t('个文件可导入')}`);
  }

  async function previewInbox() {
    const data = await postJson<{ items?: JsonObject[] }>('/api/import/preview', token, { inbox: true, limit: 500 });
    setInboxPreviewRows(Array.isArray(data.items) ? data.items : []);
    return data as JsonObject;
  }

  const uploadCounts = uploadResults.reduce(
    (counts, item) => ({ ...counts, [item.tone]: counts[item.tone] + 1 }),
    { success: 0, deduped: 0, failed: 0 }
  );

  return (
    <div className="page-stack">
      <div className="grid two wide-first">
        <Card
          eyebrow="Upload"
          title={t('上传并导入')}
          extra={
            <div className="button-row">
              <Button disabled={!files.length} loading={isBusy('upload-files')} onClick={() => guarded(uploadSelectedFiles, undefined, 'upload-files')}>{t('批量上传并导入')}</Button>
              <Button variant="secondary" disabled={!files.length} loading={isBusy('preview-files')} onClick={() => guarded(previewSelectedFiles, undefined, 'preview-files')}>
                <Search size={16} />
                {t('证据预检')}
              </Button>
              <Button variant="ghost" disabled={!files.length && !uploadResults.length} onClick={() => {
                setFiles([]);
                setUploadResults([]);
                setUploadStatus('');
                setUploadProgress({});
                setPreviewRows([]);
                setPreviewStatus('');
              }}>{t('清空')}</Button>
              <Button variant="secondary" onClick={() => navigate('/reaction_links')}>{t('查看反应关联')}</Button>
            </div>
          }
        >
          <div
            className={`upload-dragzone ${isDragOver ? 'dragover' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => document.getElementById('file-input-id')?.click()}
          >
            <div className="upload-icon">
              <Upload size={32} />
            </div>
            <h3>{t('拖拽文件到此处，或点击选择文件')}</h3>
            <p>{t('支持 PDF, RTF, RDF, HTML, MHTML, Markdown, TXT 格式')}</p>
            <input
              id="file-input-id"
              type="file"
              multiple
              style={{ display: 'none' }}
              accept=".pdf,.rtf,.rdf,.html,.htm,.mhtml,.mht,.md,.markdown,.txt"
              onChange={(event) => {
                const selectedFiles = Array.from(event.target.files || []);
                setFiles(prev => [...prev, ...selectedFiles]);
                setUploadResults([]);
                setPreviewRows([]);
                setPreviewStatus('');
                setUploadStatus(t('已选择文件，准备上传'));
              }}
            />
          </div>

          <div className="upload-summary" style={{ marginTop: '12px' }}>
            <span>{files.length ? `已选择 ${files.length} 个文件` : t('尚未选择文件')}</span>
            {uploadStatus && <strong style={{ marginLeft: '12px' }}>{uploadStatus}</strong>}
            {previewStatus && <strong style={{ marginLeft: '12px' }}>{previewStatus}</strong>}
          </div>

          {files.length > 0 && (
            <div className="upload-progress-list">
              {files.map((file) => {
                const state = uploadProgress[file.name] || { percent: 0, status: 'pending' };
                return (
                  <div key={`${file.name}-${file.size}`} className="upload-progress-item">
                    <div className="upload-progress-info">
                      <span className="upload-progress-name">{file.name}</span>
                      <span className={`upload-progress-status ${state.status}`}>
                        {state.status === 'pending' && t('等待中')}
                        {state.status === 'running' && `${state.percent}%`}
                        {state.status === 'success' && t('已完成')}
                        {state.status === 'deduped' && t('已去重')}
                        {state.status === 'failed' && `${t('失败')}: ${state.error || ''}`}
                      </span>
                    </div>
                    <div className="progress-bar-bg">
                      <div
                        className="progress-bar-fill"
                        style={{ width: `${state.percent}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <p className="muted" style={{ marginTop: '12px' }}>{t('支持 PDF/RTF/RDF/HTML/MHTML/Markdown/TXT。上传仍会经过后端扩展名、嗅探和安全校验。')}</p>
        </Card>
        <Card
          eyebrow="Inbox"
          title={t('扫描收件箱')}
          extra={
            <div className="button-row">
              <Button variant="secondary" loading={isBusy('preview-inbox')} onClick={() => guarded(previewInbox, t('收件箱预检完成'), 'preview-inbox')}>
                <ShieldCheck size={16} />
                {t('预检')}
              </Button>
              <Button loading={isBusy('scan-inbox')} onClick={() => guarded(async () => { const result = await postJson<JsonObject>('/api/scan', token); await refresh(); return result; }, t('扫描完成'), 'scan-inbox')}>{t('扫描')}</Button>
            </div>
          }
        >
          <p className="muted">{t('从服务端可见 inbox 中登记新增 SciFinder 导出文件。不会绕过导入规则。')}</p>
        </Card>
      </div>
      {previewRows.length > 0 && (
        <Card eyebrow="Evidence Manifest" title={t('上传前证据预检')}>
          <DataTable
            rows={previewRows}
            columns={[
              { key: 'client_file_name', label: t('文件'), render: (row) => String(row.client_file_name || row.file_name || '') },
              { key: 'include', label: t('导入'), render: (row) => row.include === false ? <StatusBadge tone="failed">{t('排除')}</StatusBadge> : <StatusBadge tone="success">{t('导入')}</StatusBadge> },
              { key: 'evidence_kind', label: t('证据类型') },
              { key: 'evidence_priority', label: t('优先级') },
              { key: 'cas_count', label: 'CAS' },
              { key: 'page_count', label: t('页数') },
              { key: 'size_bytes', label: t('大小'), render: (row) => bytes(row.client_size || row.size_bytes) },
              { key: 'paired_rdf_name', label: t('精确配对'), render: (row) => String(row.paired_rdf_name || row.paired_pdf_name || '') },
              { key: 'reason', label: t('原因'), render: (row) => String(row.exclude_reason || row.reason || row.label || '') }
            ]}
            empty={t('暂无预检结果')}
          />
        </Card>
      )}
      {inboxPreviewRows.length > 0 && (
        <Card eyebrow="Inbox Manifest" title={t('收件箱预检结果')}>
          <DataTable
            rows={inboxPreviewRows}
            columns={[
              { key: 'file_name', label: t('文件') },
              { key: 'include', label: t('导入'), render: (row) => row.include === false ? <StatusBadge tone="failed">{t('排除')}</StatusBadge> : <StatusBadge tone="success">{t('导入')}</StatusBadge> },
              { key: 'evidence_kind', label: t('证据类型') },
              { key: 'evidence_priority', label: t('优先级') },
              { key: 'cas_count', label: 'CAS' },
              { key: 'paired_rdf_name', label: t('配对'), render: (row) => String(row.paired_rdf_name || row.paired_pdf_name || '') },
              { key: 'reason', label: t('原因'), render: (row) => String(row.exclude_reason || row.reason || row.label || '') }
            ]}
          />
        </Card>
      )}
      {uploadResults.length > 0 && (
        <Card eyebrow="Upload Results" title={t('批量上传结果')}>
          <div className="upload-result-summary" aria-label="批量上传结果统计">
            <span className="success">{t('成功')}{uploadCounts.success}</span>
            <span className="deduped">{t('去重')}{uploadCounts.deduped}</span>
            <span className="failed">{t('失败')}{uploadCounts.failed}</span>
          </div>
          <DataTable<UploadResultRow>
            rows={uploadResults}
            columns={[
              { key: 'file_name', label: t('文件') },
              { key: 'status', label: t('状态'), render: (row) => <StatusBadge tone={row.tone}>{row.status}</StatusBadge> },
              { key: 'detail', label: t('详情') },
              { key: 'document_id', label: t('文档 ID'), render: (row) => row.document_id ? <Button size="sm" variant="ghost" onClick={() => openDocument(String(row.document_id))}>{t('查看解析')}</Button> : '' },
              { key: 'job_id', label: t('任务 ID') },
              { key: 'uploaded_path', label: t('写入路径') }
            ]}
          />
        </Card>
      )}
      <Card eyebrow="Jobs" title={t('最近解析任务')} extra={<Button variant="secondary" loading={isBusy('retry-failed-jobs')} onClick={() => guarded(() => postJson('/api/retry-failed', token), t('已提交失败任务重试'), 'retry-failed-jobs')}>{t('重试失败任务')}</Button>}>
        <DataTable rows={state.jobs} columns={[{ key: 'id', label: 'ID' }, { key: 'status', label: t('状态') }, { key: 'stage', label: t('阶段') }, { key: 'error', label: t('错误') }]} />
      </Card>
    </div>
  );
}
