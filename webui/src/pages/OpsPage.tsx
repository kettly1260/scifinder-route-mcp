import { useState } from 'react';
import type { AdminState, JsonObject } from '../types';
import type { PageProps } from '../constants';
import { getJson, postJson } from '../api';
import { Button, Card, DataTable, JsonBlock } from '../components';
import { useTranslation } from '../i18n';

export interface OpsPageProps extends PageProps {
  refresh: () => Promise<AdminState>;
}

export function OpsPage({ token, state, guarded, refresh }: OpsPageProps) {
  const { t } = useTranslation();
  const [trash, setTrash] = useState<JsonObject[]>([]);

  return (
    <div className="page-stack">
      <div className="grid two">
        <Card
          eyebrow="Vector"
          title={t("向量索引")}
          extra={
            <Button
              onClick={() =>
                guarded(async () => {
                  const result = await postJson('/api/vector/rebuild', token);
                  await refresh();
                  return result;
                }, t('向量索引已提交重建'))
              }
            >
              {t('重建')}
            </Button>
          }
        >
          <JsonBlock value={state.production.vector_index} />
        </Card>
        <Card
          eyebrow="Backup"
          title={t("备份与清理")}
          extra={
            <div className="button-row">
              <Button onClick={() => guarded(() => postJson('/api/backup', token), t('数据库已备份'))}>{t('备份')}</Button>
              <Button variant="secondary" onClick={() => guarded(() => postJson('/api/cleanup', token, { dry_run: true }), t('清理试运行完成'))}>
                {t('清理试运行')}
              </Button>
            </div>
          }
        >
          <p className="muted">{t('备份和保留清理只操作服务管理的数据目录，不修改 Docker-owned 配置。')}</p>
        </Card>
      </div>
      <Card
        eyebrow="Trash"
        title={t("回收站")}
        extra={
          <div className="button-row">
            <Button variant="secondary" onClick={() => guarded(async () => setTrash(await getJson<JsonObject[]>('/api/trash?limit=100', token)), t('回收站已加载'))}>
              {t('加载')}
            </Button>
            <Button variant="danger" onClick={() => {
              if (window.confirm(t('确定要永久清空回收站吗？此操作无法撤销。'))) {
                guarded(() => postJson('/api/trash/empty', token), t('回收站已清空'));
              }
            }}>
              {t('清空')}
            </Button>
          </div>
        }
      >
        <DataTable
          rows={trash}
          columns={[
            { key: 'entity_type', label: t('类型') },
            { key: 'id', label: 'ID' },
            { key: 'title', label: t('标题') },
            { key: 'deleted_at', label: t('删除时间') },
            { key: 'restore', label: t('还原'), render: (row) => <Button size="sm" variant="ghost" onClick={() => guarded(() => postJson('/api/trash/restore', token, { entity_type: row.entity_type, entity_id: row.id }), t('项目已还原'))}>{t('还原')}</Button> }
          ]}
        />
      </Card>
      <div className="grid two">
        <Card eyebrow="Validation" title={t("配置警告")}>
          <ul className="warning-list">
            {(state.validation.warnings || [t('暂无配置警告')]).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Card>
        <Card eyebrow="Jobs" title={t('最近解析任务')}>
          <DataTable
            rows={state.jobs}
            columns={[
              { key: 'id', label: 'ID' },
              { key: 'status', label: t('状态') },
              { key: 'stage', label: t('阶段') },
              { key: 'error', label: t('错误') }
            ]}
          />
        </Card>
      </div>
      <Card eyebrow="Production" title={t('诊断快照')} style={{ marginTop: '16px' }}>
        <JsonBlock value={state.production} maxHeight={420} />
      </Card>
    </div>
  );
}
