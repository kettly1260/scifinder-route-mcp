import { useEffect, useState, type FormEvent } from 'react';
import { Routes, Route, Navigate, useNavigate, useLocation, Link } from 'react-router-dom';
import { clearToken, getStoredToken, hasTrustedToken, loadState, loadStatus, storeToken, postJson } from './api';
import { Button } from './components';
import type { AdminState, JsonObject } from './types';
import { useTranslation } from "./i18n";
import { useToast } from './components/Toast';
import {
  themes,
  initialTheme,
  pages,
  THEME_KEY,
  authError,
  type ThemeId
} from './constants';

// Page components
import { LoginScreen } from './pages/LoginScreen';
import { DashboardPage } from './pages/DashboardPage';
import { IngestPage } from './pages/IngestPage';
import { DocumentsListPage } from './pages/DocumentsListPage';
import { DocumentDetailPage } from './pages/DocumentDetailPage';
import { ConfigPage } from './pages/ConfigPage';
import { RdfPage } from './pages/RdfPage';
import { RdfDetailPage } from './pages/RdfDetailPage';
import { StructurePage } from './pages/StructurePage';
import { LiteraturePage } from './pages/LiteraturePage';
import { OpsPage } from './pages/OpsPage';

// Lucide Icons
import {
  LayoutDashboard,
  Upload,
  FileText,
  Settings,
  FlaskConical,
  Search,
  BookOpen,
  Wrench,
  Menu
} from 'lucide-react';

const IconComponents: Record<string, React.ComponentType<any>> = {
  LayoutDashboard,
  Upload,
  FileText,
  Settings,
  FlaskConical,
  Search,
  BookOpen,
  Wrench
};

export function App() {
  const { language, setLanguage, t } = useTranslation();
  const toast = useToast();
  const [token, setToken] = useState(getStoredToken());
  const [trusted, setTrusted] = useState(() => hasTrustedToken() || !getStoredToken());
  const [state, setState] = useState<AdminState | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [busyActions, setBusyActions] = useState<Record<string, boolean>>({});
  const [authRequired, setAuthRequired] = useState(true);
  const [theme, setTheme] = useState<ThemeId>(initialTheme);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const navigate = useNavigate();
  const location = useLocation();

  async function refresh(nextToken = token, silent = false) {
    try {
      const data = await loadState(nextToken);
      setState(data);
      setAuthRequired(Boolean(data.auth_required));
      setError('');
      return data;
    } catch (err) {
      const errMsg = authError(err);
      setError(silent && !nextToken ? '' : errMsg);
      if (!silent) toast.error(errMsg);
      throw err;
    }
  }

  async function refreshStatus(nextToken = token, silent = false) {
    try {
      const data = await loadStatus(nextToken);
      setState((current) => current ? { ...current, ...data } : current);
      setAuthRequired(Boolean(data.auth_required));
      setError('');
      return data;
    } catch (err) {
      const errMsg = authError(err);
      setError(silent && !nextToken ? '' : errMsg);
      if (!silent) toast.error(errMsg);
      throw err;
    }
  }

  useEffect(() => {
    refresh(token, true).catch(() => undefined);
  }, []);

  useEffect(() => {
    setError('');
    setSidebarOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await refresh(token, true);
      storeToken(token, trusted);
      toast.success(trusted ? t('已登录，并信任此设备') : t('已登录，本次会话有效'));
    } catch (err) {
      clearToken();
      const errMsg = authError(err);
      setError(errMsg);
      toast.error(errMsg);
    } finally {
      setBusy(false);
    }
  }

  function logout() {
    clearToken();
    setToken('');
    setState(null);
    toast.success(t('已退出登录'));
  }

  async function guarded<T>(action: () => Promise<T>, success?: string, busyKey?: string): Promise<T | undefined> {
    if (busyKey) {
      setBusyActions((current) => ({ ...current, [busyKey]: true }));
    } else {
      setBusy(true);
    }
    try {
      const result = await action();
      if (success) toast.success(success);
      setError('');
      return result;
    } catch (err) {
      const errMsg = authError(err);
      setError(errMsg);
      toast.error(errMsg);
      return undefined;
    } finally {
      if (busyKey) {
        setBusyActions((current) => ({ ...current, [busyKey]: false }));
      } else {
        setBusy(false);
      }
    }
  }

  function isBusy(busyKey: string): boolean {
    return Boolean(busyActions[busyKey]);
  }

  function applyConfig(config: JsonObject) {
    setState((current) => current ? { ...current, config } : current);
  }


  if (!state) {
    return (
      <LoginScreen
        token={token}
        setToken={setToken}
        trusted={trusted}
        setTrusted={setTrusted}
        login={login}
        busy={busy}
        error={error}
      />
    );
  }

  const active = pages.find((item) =>
    item.path === '/'
      ? location.pathname === '/'
      : location.pathname.startsWith(item.path)
  ) || pages[0];

  return (
    <div className="app-shell">
      {sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭菜单" onClick={() => setSidebarOpen(false)} />}
      <aside className={sidebarOpen ? 'sidebar open' : 'sidebar'}>
        <div className="brand-block">
          <div className="brand-mark">SR</div>
          <div>
            <strong>SciFinder Route</strong>
            <span>{t('Admin Console')}</span>
          </div>
        </div>
        <nav className="nav-list" aria-label="管理控制台分区导航">
          {pages.map((item) => {
            const IconComp = IconComponents[item.iconName];
            const isActive = location.pathname === item.path || (item.path !== '/' && location.pathname.startsWith(item.path));
            return (
              <Link
                key={item.id}
                to={item.path}
                className={isActive ? 'nav-item active' : 'nav-item'}
                style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}
              >
                {IconComp && (
                  <IconComp
                    className="nav-icon"
                    style={{
                      width: '18px',
                      height: '18px',
                      marginTop: '2px',
                      flexShrink: 0,
                      color: isActive ? 'var(--primary-2, #00d2ff)' : 'var(--muted)'
                    }}
                  />
                )}
                <div style={{ display: 'flex', flexDirection: 'column' }}>
                  <span>{t(item.label)}</span>
                  <small style={{ marginTop: '2px' }}>{t(item.description)}</small>
                </div>
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <span className="status-pill">{String(state.health.status || 'unknown').toUpperCase()}</span>
          <Button variant="ghost" size="sm" onClick={logout}>{t('退出')}</Button>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <button className="mobile-menu-btn" onClick={() => setSidebarOpen(true)} aria-label="打开菜单">
              <Menu size={24} />
            </button>
            <div>
              <p className="eyebrow">{t(active.description)}</p>
              <h1>{t(active.label)}</h1>
            </div>
          </div>
          <div className="topbar-actions">
            <Button variant="ghost" className="lang-toggle" onClick={() => setLanguage(language.startsWith('zh') ? 'en' : 'zh')} aria-label="切换语言">
              {language.startsWith('zh') ? 'En' : '中'}
            </Button>
            <span className="subtle">{authRequired ? t('Token protected') : t('Trusted local mode')}</span>
            <label className="theme-select" aria-label="主题颜色">
              <span>{t('Theme')}</span>
              <select value={theme} onChange={(event) => setTheme(event.target.value as ThemeId)}>
                {themes.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
            <Button variant="secondary" onClick={() => guarded(() => refreshStatus(), t('状态已刷新'), 'refresh-status')} loading={isBusy('refresh-status')}>{t('刷新')}</Button>
          </div>
        </header>
        <Routes>
          <Route path="/" element={<DashboardPage state={state} token={token} guarded={guarded} />} />
          <Route
            path="/ingest"
            element={
              <IngestPage
                token={token}
                state={state}
                guarded={guarded}
                refresh={refreshStatus}
                isBusy={isBusy}
                openDocument={(documentId) => navigate(`/documents/${documentId}`)}
              />
            }
          />
          <Route path="/documents" element={<DocumentsListPage token={token} guarded={guarded} />} />
          <Route path="/documents/:documentId" element={<DocumentDetailPage token={token} guarded={guarded} />} />
          <Route path="/config" element={<ConfigPage token={token} state={state} guarded={guarded} isBusy={isBusy} onConfigSaved={applyConfig} />} />
          <Route path="/rdf" element={<RdfPage token={token} guarded={guarded} />} />
          <Route path="/rdf/:reactionId" element={<RdfDetailPage token={token} guarded={guarded} />} />
          <Route path="/structures" element={<StructurePage token={token} state={state} guarded={guarded} isBusy={isBusy} />} />
          <Route path="/literature" element={<LiteraturePage token={token} state={state} guarded={guarded} isBusy={isBusy} />} />
          <Route path="/ops" element={<OpsPage token={token} state={state} guarded={guarded} isBusy={isBusy} refresh={refreshStatus} />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
