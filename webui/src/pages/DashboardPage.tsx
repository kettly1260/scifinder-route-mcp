import type { AdminState, JsonObject } from '../types';
import { Card, DataTable, StatCard, Button } from '../components';
import { useTranslation } from '../i18n';
import { shortName, bytes } from '../constants';
import { useNavigate } from 'react-router-dom';
import { Upload, Settings, Search, RefreshCw, AlertTriangle, CheckCircle, XCircle } from 'lucide-react';

export interface DashboardPageProps {
  state: AdminState;
  token: string;
  guarded: <T>(action: () => Promise<T>, success?: string) => Promise<T | undefined>;
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="info-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function DashboardPage({ state, token, guarded }: DashboardPageProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const health = state.health;
  const production = state.production;
  const storage = (production.storage_usage as JsonObject | undefined) || {};

  const status = String(health.status || 'unknown').toLowerCase();
  
  const getStatusIndicator = () => {
    switch (status) {
      case 'ok':
      case 'healthy':
        return {
          color: 'var(--accent, #4ade80)',
          icon: <CheckCircle size={16} style={{ color: 'var(--accent, #4ade80)' }} />,
          label: t('系统运行正常')
        };
      case 'warning':
      case 'warn':
        return {
          color: 'var(--warning, #fbbf24)',
          icon: <AlertTriangle size={16} style={{ color: 'var(--warning, #fbbf24)' }} />,
          label: t('系统有警告提示')
        };
      case 'error':
      case 'failed':
      case 'unhealthy':
        return {
          color: 'var(--danger, #fb7185)',
          icon: <XCircle size={16} style={{ color: 'var(--danger, #fb7185)' }} />,
          label: t('系统故障或配置错误')
        };
      default:
        return {
          color: 'var(--subtle, #6f7f96)',
          icon: <AlertTriangle size={16} style={{ color: 'var(--subtle, #6f7f96)' }} />,
          label: t('健康状态未知')
        };
    }
  };

  const statusIndicator = getStatusIndicator();

  return (
    <div className="page-stack">
      {/* Health Indicator Banner */}
      <div 
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '12px',
          padding: '16px 20px',
          borderRadius: '12px',
          backgroundColor: 'var(--panel)',
          border: '1px solid var(--line)',
          borderLeft: `6px solid ${statusIndicator.color}`
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center' }}>
          {statusIndicator.icon}
        </span>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontWeight: 600, fontSize: '15px' }}>
            {t('系统状态')}：{t(String(health.status).toUpperCase())}
          </span>
          <span style={{ fontSize: '13px', color: 'var(--muted)' }}>
            {statusIndicator.label}
          </span>
        </div>
      </div>

      <section className="metrics-grid">
        <StatCard label={t('文档数')} value={String(health.documents ?? 0)} />
        <StatCard label={t('反应步骤')} value={String(health.reaction_steps ?? 0)} />
        <StatCard label={t('OCR 积压')} value={String(health.ocr_backlog ?? 0)} tone={Number(health.ocr_backlog || 0) ? 'warn' : 'good'} />
        <StatCard label={t('异步任务')} value={health.async_jobs ? t('启用') : t('停用')} tone={health.async_jobs ? 'good' : 'warn'} />
      </section>

      <div className="grid two">
        <Card eyebrow="Runtime" title={t('基础运行信息')}>
          <div className="info-list">
            <Info label={t('配置文件')} value={shortName(health.config_path)} />
            <Info label={t('存储后端')} value={String((state.config.server as JsonObject | undefined)?.storage_backend ?? '')} />
            <Info label={t('队列后端')} value={String((state.config.queue as JsonObject | undefined)?.backend ?? '')} />
            <Info label={t('化合物')} value={String(production.compound_count ?? 0)} />
          </div>
        </Card>

        {/* Quick Actions Card */}
        <Card eyebrow="Actions" title={t('快捷操作区')}>
          <div 
            style={{ 
              display: 'grid', 
              gridTemplateColumns: 'repeat(2, 1fr)', 
              gap: '12px',
              padding: '8px 0'
            }}
          >
            <Button 
              variant="secondary" 
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '16px' }}
              onClick={() => navigate('/ingest')}
            >
              <Upload size={16} />
              <span>{t('导入新文档')}</span>
            </Button>
            <Button 
              variant="secondary" 
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '16px' }}
              onClick={() => navigate('/config')}
            >
              <Settings size={16} />
              <span>{t('修改配置')}</span>
            </Button>
            <Button 
              variant="secondary" 
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '16px' }}
              onClick={() => navigate('/structures')}
            >
              <Search size={16} />
              <span>{t('结构检索')}</span>
            </Button>
            <Button 
              variant="ghost" 
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '16px' }}
              onClick={() => navigate('/documents')}
            >
              <RefreshCw size={16} />
              <span>{t('查看文档')}</span>
            </Button>
          </div>
        </Card>
      </div>

      <Card eyebrow="Storage" title={t('NAS 存储使用')}>
        <DataTable<JsonObject>
          rows={Object.entries(storage).map(([name, item]) => ({ name, ...(item as JsonObject) }))}
          columns={[
            { key: 'name', label: t('路径') },
            { key: 'files', label: t('文件数') },
            { key: 'bytes', label: t('大小'), render: (row) => bytes(row.bytes) }
          ]}
        />
      </Card>
    </div>
  );
}
