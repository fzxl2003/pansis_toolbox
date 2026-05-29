import { FormEvent, useState } from 'react';
import { LogIn } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { login } from '../api/auth';

export function LoginPanel({ onSuccess, redirectTo }: { onSuccess?: () => void; redirectTo?: string }) {
  const navigate = useNavigate();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('admin123');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    try {
      await login(username, password);
      onSuccess?.();
      if (redirectTo) {
        navigate(redirectTo, { replace: true });
        return;
      }
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败');
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section className="panel login-panel">
      <div>
        <p className="eyebrow">Login Required</p>
        <h2>需要登录后使用个人数据</h2>
        <p className="muted">默认开发账号：admin / admin123</p>
      </div>
      {error && <div className="error-box">{error}</div>}
      <form className="login-form" onSubmit={submit}>
        <label className="field-label" htmlFor="login-username">用户名</label>
        <input
          id="login-username"
          className="text-input"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
        />
        <label className="field-label" htmlFor="login-password">密码</label>
        <input
          id="login-password"
          className="text-input"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
        />
        <button className="primary-button" type="submit" disabled={isLoading}>
          <LogIn size={16} />
          {isLoading ? '登录中' : '登录'}
        </button>
      </form>
    </section>
  );
}
