import { useState } from 'react';
import type { AdminStatusState, JsonObject } from '../types';
import type { PageProps, UploadResultRow } from '../constants';
import { postJson } from '../api';
import { Button, Card, DataTable } from '../components';
import { StatusBadge } from '../components/StatusBadge';
import { useTranslation } from '../i18n';
import { useToast } from '../components/Toast';
import { Upload } from 'lucide-react';

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

export function IngestPage({ token, state, guarded, refresh, openDocument, isBusy }: IngestPageProps) {
  const { t } = useTranslation();
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [uploadStatus, setUploadStatus] = useState('');
  const [uploadResults, setUploadResults] = useState<UploadResultRow[]>([]);
  const [isDragOver, setIsDragOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<Record<string, { percent: number; status: string; error?: string }>>({});

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
      setUploadStatus(t('已添加拖拽文件，准备上传'));
    } else {
      toast.warning(t('未检测到支持的 SciFinder 格式文件'));
    }
  };

  async function uploadSelectedFiles() {
    if (!files.length) return;
    const results: UploadResultRow[] = [];
    
    // Initialize progress
    const initProgress: Record<string, { percent: number; status: string }> = {};
    files.forEach(f => {
      initProgress[f.name] = { percent: 0, status: 'pending' };
    });
    setUploadProgress(initProgress);

    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      setUploadStatus(`正在上传 ${index + 1}/${files.length}: ${file.name}`);
      
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
              <Button variant="ghost" disabled={!files.length && !uploadResults.length} onClick={() => {
                setFiles([]);
                setUploadResults([]);
                setUploadStatus('');
                setUploadProgress({});
              }}>{t('清空')}</Button>
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
                setUploadStatus(t('已选择文件，准备上传'));
              }}
            />
          </div>

          <div className="upload-summary" style={{ marginTop: '12px' }}>
            <span>{files.length ? `已选择 ${files.length} 个文件` : t('尚未选择文件')}</span>
            {uploadStatus && <strong style={{ marginLeft: '12px' }}>{uploadStatus}</strong>}
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
        <Card eyebrow="Inbox" title={t('扫描收件箱')} extra={<Button loading={isBusy('scan-inbox')} onClick={() => guarded(async () => { const result = await postJson<JsonObject>('/api/scan', token); await refresh(); return result; }, t('扫描完成'), 'scan-inbox')}>{t('扫描')}</Button>}>
          <p className="muted">{t('从服务端可见 inbox 中登记新增 SciFinder 导出文件。不会绕过导入规则。')}</p>
        </Card>
      </div>
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
