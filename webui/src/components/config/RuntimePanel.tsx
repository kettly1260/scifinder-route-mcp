import { useState } from 'react';
import type { JsonObject } from '../../types';
import { runtimeGroups, configFieldByKey } from '../../constants';
import { Button, Card } from '../../components';
import { ConfigControl } from './ConfigControl';
import { ActionResult } from './ActionResult';
import { useTranslation } from '../../i18n';

export interface RuntimePanelProps {
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
  onSave: () => void;
  onTestPostgres: () => void;
  postgresResult?: JsonObject;
}

export function RuntimePanel({ values, onChange, onSave, onTestPostgres, postgresResult }: RuntimePanelProps) {
  const { t } = useTranslation();
  // Use the eyebrow of the first group as the default active tab
  const [activeId, setActiveId] = useState<string>(runtimeGroups[0].eyebrow);

  const activeGroup = runtimeGroups.find(g => g.eyebrow === activeId);

  return (
    <div className="runtime-layout">
      <div className="runtime-sidebar">
        {runtimeGroups.map(group => (
          <button
            key={group.eyebrow}
            className={`runtime-nav-item ${activeId === group.eyebrow ? 'active' : ''}`}
            onClick={() => setActiveId(group.eyebrow)}
            type="button"
          >
            <div className="runtime-nav-eyebrow">{group.eyebrow}</div>
            <div className="runtime-nav-title">{group.title}</div>
          </button>
        ))}
        <button
          className={`runtime-nav-item ${activeId === 'Postgres' ? 'active' : ''}`}
          onClick={() => setActiveId('Postgres')}
          type="button"
        >
          <div className="runtime-nav-eyebrow">Postgres</div>
          <div className="runtime-nav-title">PostgreSQL 存储</div>
        </button>
      </div>

      <div className="runtime-content">
        <Card className="runtime-card">
          {activeGroup && (
            <>
              <div className="card-header" style={{ marginBottom: '24px' }}>
                <div>
                  <p className="eyebrow">{activeGroup.eyebrow}</p>
                  <div className="title">{activeGroup.title}</div>
                </div>
              </div>
              <div className="form-grid single">
                {activeGroup.fields.map((key) => {
                  const field = configFieldByKey.get(key);
                  if (!field) return null;
                  return (
                    <ConfigControl
                      key={key}
                      field={field}
                      value={values[key] ?? ''}
                      onChange={onChange}
                    />
                  );
                })}
              </div>
            </>
          )}

          {activeId === 'Postgres' && (
            <>
              <div className="card-header" style={{ marginBottom: '24px' }}>
                <div>
                  <p className="eyebrow">Postgres</p>
                  <div className="title">PostgreSQL 存储</div>
                </div>
                <Button variant="secondary" onClick={onTestPostgres}>{t('测试 Postgres')}</Button>
              </div>
              <div className="form-grid single">
                <ConfigControl 
                  field={configFieldByKey.get('integrations.postgres_url')!} 
                  value={values['integrations.postgres_url'] ?? ''} 
                  onChange={onChange} 
                />
              </div>
              <ActionResult result={postgresResult} />
            </>
          )}

          <div style={{ marginTop: 'auto', paddingTop: '32px' }}>
            <div style={{ display: 'flex', justifyContent: 'flex-end', borderTop: '1px solid var(--line)', paddingTop: '16px' }}>
              <Button onClick={onSave}>{t('保存并重载')}</Button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
