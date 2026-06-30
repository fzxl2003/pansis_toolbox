import './style.css';

import { FormEvent, useEffect, useState } from 'react';
import { ArrowRight, Globe2 } from 'lucide-react';

import { fetchMe } from '../../../frontend/src/api/auth';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

type AuthStatus = 'checking' | 'authenticated' | 'anonymous';

export default function WebProxyTool() {
  const [url, setUrl] = useState('');
  const [authStatus, setAuthStatus] = useState<AuthStatus>('checking');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchMe()
      .then((state) => setAuthStatus(state.authenticated ? 'authenticated' : 'anonymous'))
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
    window.location.href = `${backendOrigin()}/web-proxy?url=${encodeURIComponent(target)}`;
  }

  if (authStatus === 'checking') {
    return <div className="web-proxy-shell"><div className="web-proxy-status">正在检查登录状态...</div></div>;
  }

  if (authStatus === 'anonymous') {
    return (
      <div className="web-proxy-shell">
        <LoginPanel onSuccess={() => setAuthStatus('authenticated')} />
      </div>
    );
  }

  return (
    <main className="web-proxy-shell">
      <form className="web-proxy-form" onSubmit={submit}>
        <label className="web-proxy-label" htmlFor="web-proxy-url">
          <Globe2 size={18} />
          网页代理
        </label>
        <div className="web-proxy-row">
          <input
            id="web-proxy-url"
            className="web-proxy-input"
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://example.com"
            autoFocus
          />
          <button className="web-proxy-button" type="submit" aria-label="打开">
            <ArrowRight size={20} />
          </button>
        </div>
        {error && <div className="web-proxy-error">{error}</div>}
      </form>
    </main>
  );
}

function backendOrigin() {
  if (window.location.port === '5173') {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return window.location.origin;
}
