import type { FormEvent } from 'react';
import { Button, Input } from '../components';
import { useTranslation } from '../i18n';

export interface LoginScreenProps {
  token: string;
  setToken: (value: string) => void;
  trusted: boolean;
  setTrusted: (value: boolean) => void;
  login: (event: FormEvent) => void;
  busy: boolean;
  error: string;
}

export function LoginScreen({ token, setToken, trusted, setTrusted, login, busy, error }: LoginScreenProps) {
  const { t } = useTranslation();
  return (
    <main className="login-layout">
      <section className="login-card card">
        <div className="brand-block large">
          <div className="brand-mark">SR</div>
          <div>
            <p className="eyebrow">{t('NAS 控制台')}</p>
            <h1>SciFinder Route MCP</h1>
          </div>
        </div>
        <p className="muted">{t('输入管理令牌以访问配置、导入、RDF 反应和文献链接面板。未配置鉴权的本地可信部署会自动进入。')}</p>
        <form onSubmit={login} className="login-form">
          <Input
            label={t('管理令牌')}
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="X-Scifinder-Route-Token"
            autoComplete="current-password"
          />
          <label className="check-row">
            <input type="checkbox" checked={trusted} onChange={(event) => setTrusted(event.target.checked)} />
            <span>{t('信任此设备，重启浏览器后仍保持登录')}</span>
          </label>
          {error && <div className="error-box">{error}</div>}
          <Button loading={busy} fullWidth>{t('进入控制台')}</Button>
        </form>
      </section>
    </main>
  );
}
