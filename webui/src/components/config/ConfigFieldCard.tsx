import type { ConfigGroup } from '../../constants';
import { configFieldByKey } from '../../constants';
import { Card } from '../../components';
import { ConfigControl } from './ConfigControl';

export interface ConfigFieldCardProps {
  group: ConfigGroup;
  values: Record<string, string>;
  onChange: (key: string, value: string) => void;
}

export function ConfigFieldCard({ group, values, onChange }: ConfigFieldCardProps) {
  return (
    <Card eyebrow={group.eyebrow} title={group.title}>
      <div className="form-grid single">
        {group.fields.map((key) => {
          const field = configFieldByKey.get(key);
          return field ? (
            <ConfigControl
              key={key}
              field={field}
              value={values[key] ?? ''}
              onChange={onChange}
            />
          ) : null;
        })}
      </div>
    </Card>
  );
}
