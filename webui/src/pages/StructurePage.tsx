import { useState } from 'react';
import type { JsonObject } from '../types';
import type { PageProps } from '../constants';
import { getJson, postJson } from '../api';
import { Button, Card, DataTable, Input, JsonBlock } from '../components';
import { StructureImage } from '../components/StructureImage';
import { useTranslation } from '../i18n';

export type StructurePageProps = PageProps;

export function StructurePage({ token, state, guarded, isBusy }: StructurePageProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('similarity');
  const [rows, setRows] = useState<JsonObject[]>([]);

  const chem = (state.production.chem || {}) as any;
  const installJob = chem.install_job as any;
  const restartRequired =
    chem.restart_required === true ||
    installJob?.status === 'installed_restart_required' ||
    (installJob?.result as JsonObject | undefined)?.restart_required === true;
  const persistence = chem.runtime_install_persistence as JsonObject | undefined;
  const persistenceMessage = typeof persistence?.message === 'string' ? persistence.message : '';

  async function search() {
    if (mode === 'text') {
      setRows(await getJson<JsonObject[]>(`/api/rdf/structures?q=${encodeURIComponent(query)}&limit=50`, token));
      return;
    }
    const endpoint = mode === 'similarity' ? '/api/chem/similarity-search' : '/api/chem/substructure-search';
    const data = await postJson<{ results?: JsonObject[] }>(endpoint, token, {
      query,
      query_type: mode === 'similarity' ? 'smiles' : 'smarts',
      min_similarity: 0.2,
      limit: 50
    });
    setRows(data.results || []);
  }

  return (
    <div className="page-stack">
      <div className="grid two wide-first">
        <Card eyebrow="RDKit" title={t('结构检索')} extra={<Button loading={isBusy('structure-search')} onClick={() => guarded(search, t('结构检索完成'), 'structure-search')}>{t('检索')}</Button>}>
          <div className="form-grid compact-form">
            <Input label={t('查询')} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('SMILES、SMARTS、CAS 或名称')} />
            <label className="form-group">
              {t('模式')}
              <select value={mode} onChange={(event) => setMode(event.target.value)}>
                <option value="similarity">{t('相似度')}</option>
                <option value="substructure">{t('子结构')}</option>
                <option value="text">{t('文本过滤')}</option>
              </select>
            </label>
          </div>
        </Card>
        {(!chem?.rdkit?.available || restartRequired) && (
          <Card
            eyebrow="Chem Status"
            title={t('化学索引状态')}
            extra={
              <Button
                variant="secondary"
                loading={isBusy('install-rdkit')}
                onClick={() =>
                  guarded(
                    () => postJson('/api/chem/install-rdkit', token),
                    t('临时 RDKit 安装任务已启动；完成后若提示需要重启，请重启容器'),
                    'install-rdkit'
                  )
                }
              >
                {t('临时安装 RDKit')}
              </Button>
            }
          >
            {restartRequired && (
              <div className="notice warn">
                <strong>{t('需要重启容器。')}</strong> {t('RDKit 安装已完成或部分完成，但长驻 worker 需要通过容器重启获得干净导入环境。')}
              </div>
            )}
            {persistenceMessage && (
              <div className="notice">
                <strong>{t('持久化提醒。')}</strong> {persistenceMessage}
              </div>
            )}
            <div className="notice">
              <strong>{t('推荐方式。')}</strong> {t('当前镜像构建默认包含 RDKit；如果此处显示 RDKit 缺失，请优先重新拉取/重建镜像。按钮只用于旧镜像或异常环境的临时修复。')}
            </div>
            <JsonBlock value={chem} maxHeight={260} />
          </Card>
        )}
      </div>
      <Card eyebrow="Results" title={t('结构结果')}>
        <DataTable
          rows={rows}
          columns={[
            { key: 'name', label: t('名称') },
            { key: 'role', label: t('角色') },
            { key: 'cas_rn', label: 'CAS' },
            { key: 'molfile_version', label: t('版本') },
            { key: 'similarity', label: t('评分') },
            { key: 'rdf_reaction_id', label: t('反应 ID') },
            { key: 'image', label: t('结构图'), render: (row) => ((row.molfile || row.smiles) ? <StructureImage structureId={String(row.id)} token={token} /> : t('暂无')) }
          ]}
        />
      </Card>
    </div>
  );
}
