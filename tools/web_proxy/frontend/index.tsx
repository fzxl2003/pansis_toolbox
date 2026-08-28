import './style.css';

import { FormEvent, useEffect, useState } from 'react';
import { ArrowRight, FlaskConical, Globe2, LoaderCircle, Plus, Settings, Trash2, X } from 'lucide-react';

import { fetchMe } from '../../../frontend/src/api/auth';
import { apiDelete, apiGet, apiPost } from '../../../frontend/src/api/client';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

type AuthStatus = 'checking' | 'authenticated' | 'anonymous';

type ExitServer = {
  id: string;
  name: string;
  host: string;
};

type TestSite = {
  id: string;
  url: string;
};

type TestResult = {
  url: string;
  reachable: boolean;
  statusCode: number | null;
  latencyMs: number;
  finalUrl: string | null;
  error: string | null;
};

export default function WebProxyTool() {
  const [url, setUrl] = useState('');
  const [authStatus, setAuthStatus] = useState<AuthStatus>('checking');
  const [error, setError] = useState<string | null>(null);
  const [servers, setServers] = useState<ExitServer[]>([]);
  const [selectedExit, setSelectedExit] = useState('direct');
  const [testSites, setTestSites] = useState<TestSite[]>([]);
  const [showTestSettings, setShowTestSettings] = useState(false);
  const [newTestSite, setNewTestSite] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResults, setTestResults] = useState<TestResult[]>([]);

  useEffect(() => {
    fetchMe()
      .then(async (state) => {
        if (!state.authenticated) {
          setAuthStatus('anonymous');
          return;
        }
        setAuthStatus('authenticated');
        try {
          const [serverResult, sessionResult, sitesResult] = await Promise.all([
            apiGet<{ servers: ExitServer[] }>('/web-proxy/servers'),
            apiGet<{ exitServerId: string | null }>('/web-proxy/session'),
            apiGet<{ sites: TestSite[] }>('/web-proxy/test-sites'),
          ]);
          setServers(serverResult.servers);
          setSelectedExit(sessionResult.exitServerId ?? 'direct');
          setTestSites(sitesResult.sites);
        } catch (exc) {
          setError(exc instanceof Error ? exc.message : '无法加载 SSH 出口服务器');
        }
      })
      .catch(() => setAuthStatus('anonymous'));
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    const target = url.trim();
    if (!target) {
      setError('请输入网址');
      return;
    }
    setError(null);
    // Keep the navigation on the browser-visible origin.  In development the
    // Vite server on :5173 forwards this route internally; sending the browser
    // to :8000 bypasses that single-port entry and breaks external access.
    window.location.href = `/web-proxy?url=${encodeURIComponent(target)}&serverId=${encodeURIComponent(selectedExit)}`;
  }

  async function addTestSite(event: FormEvent) {
    event.preventDefault();
    const target = newTestSite.trim();
    if (!target) return;
    try {
      const result = await apiPost<{ site: TestSite }>('/web-proxy/test-sites', { url: target });
      setTestSites((current) => current.some((site) => site.id === result.site.id) ? current : [...current, result.site]);
      setNewTestSite('');
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '添加测试网站失败');
    }
  }

  async function removeTestSite(siteId: string) {
    try {
      await apiDelete(`/web-proxy/test-sites/${encodeURIComponent(siteId)}`);
      setTestSites((current) => current.filter((site) => site.id !== siteId));
      setTestResults((current) => current.filter((result) => !testSites.some((site) => site.id === siteId && site.url === result.url)));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '删除测试网站失败');
    }
  }

  async function testSelectedExit() {
    if (selectedExit === 'direct' || testing) return;
    setTesting(true);
    setError(null);
    try {
      const result = await apiPost<{ results: TestResult[] }>(`/web-proxy/servers/${encodeURIComponent(selectedExit)}/test`, {});
      setTestResults(result.results);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'SSH 出口测试失败');
    } finally {
      setTesting(false);
    }
  }

  if (authStatus === 'checking') {
    return <div className="web-proxy-shell"><div className="web-proxy-status">正在检查登录状态...</div></div>;
  }

  if (authStatus === 'anonymous') {
    return <div className="web-proxy-shell"><LoginPanel onSuccess={() => setAuthStatus('authenticated')} /></div>;
  }

  return (
    <main className="web-proxy-shell">
      <form className="web-proxy-form" onSubmit={submit}>
        <label className="web-proxy-label" htmlFor="web-proxy-url"><Globe2 size={18} /> 网页代理</label>
        <div className="web-proxy-row">
          <input id="web-proxy-url" className="web-proxy-input" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com" autoFocus />
          <button className="web-proxy-button" type="submit" aria-label="打开"><ArrowRight size={20} /></button>
        </div>
        <label className="web-proxy-exit-label" htmlFor="web-proxy-exit">
          出口网络
          <select id="web-proxy-exit" className="web-proxy-exit" value={selectedExit} onChange={(event) => { setSelectedExit(event.target.value); setTestResults([]); }}>
            <option value="direct">本机直连</option>
            {servers.map((server) => <option key={server.id} value={server.id}>{server.name} · {server.host}</option>)}
          </select>
        </label>
        <div className="web-proxy-actions">
          <button className="web-proxy-secondary" type="button" onClick={() => setShowTestSettings(true)}><Settings size={16} /> 测试网站设置</button>
          {selectedExit !== 'direct' && <button className="web-proxy-test" type="button" onClick={testSelectedExit} disabled={testing || testSites.length === 0}>
            {testing ? <LoaderCircle className="web-proxy-spin" size={16} /> : <FlaskConical size={16} />}
            {testing ? '测试中...' : '测试出口连通性'}
          </button>}
        </div>
        {selectedExit !== 'direct' && testSites.length === 0 && <p className="web-proxy-hint">请先在“测试网站设置”中添加至少一个网址。</p>}
        {selectedExit === 'direct' && <p className="web-proxy-hint">选择全局 SSH 服务器后，可测试该服务器出口是否能访问设置中的网站。</p>}
        {selectedExit !== 'direct' && testResults.length > 0 && <div className="web-proxy-results" aria-live="polite">
          {testResults.map((result) => <div className="web-proxy-result" key={result.url}>
            <span className={result.reachable ? 'web-proxy-result-ok' : 'web-proxy-result-failed'}>{result.reachable ? '可达' : '失败'}</span>
            <span className="web-proxy-result-url">{result.url}</span>
            <span>{result.statusCode ?? '—'}</span><span>{result.latencyMs} ms</span>
            {result.error && <span className="web-proxy-result-error">{result.error}</span>}
          </div>)}
        </div>}
        <p className="web-proxy-hint">SSH 出口服务器由全局“设置 → SSH 服务器”统一管理；网页代理仅选择其中一个服务器作为出口。</p>
        {error && <div className="web-proxy-error">{error}</div>}
      </form>

      {showTestSettings && <div className="web-proxy-modal-backdrop" role="presentation">
        <section className="web-proxy-modal" role="dialog" aria-modal="true" aria-labelledby="test-sites-title">
          <div className="web-proxy-modal-title"><h2 id="test-sites-title">测试网站设置</h2><button type="button" className="web-proxy-icon-button" aria-label="关闭" onClick={() => setShowTestSettings(false)}><X size={18} /></button></div>
          <p>测试会通过所选 SSH 服务器的出口访问以下 HTTP/HTTPS 网站。</p>
          <form className="web-proxy-site-add" onSubmit={addTestSite}>
            <input value={newTestSite} onChange={(event) => setNewTestSite(event.target.value)} placeholder="https://www.example.com/" aria-label="测试网站地址" />
            <button type="submit"><Plus size={16} /> 添加</button>
          </form>
          <div className="web-proxy-site-list">
            {testSites.length === 0 && <div className="web-proxy-empty-sites">尚未添加测试网站。</div>}
            {testSites.map((site) => <div className="web-proxy-site" key={site.id}><code>{site.url}</code><button type="button" aria-label={`删除 ${site.url}`} onClick={() => removeTestSite(site.id)}><Trash2 size={16} /></button></div>)}
          </div>
        </section>
      </div>}
    </main>
  );
}
