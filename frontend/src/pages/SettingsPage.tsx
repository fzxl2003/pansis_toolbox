import { FormEvent, useEffect, useState } from 'react';
import { UserPlus, Users } from 'lucide-react';

import { apiGet, apiPost } from '../api/client';
import { fetchMe, type AuthUser } from '../api/auth';
import { LoginPanel } from '../components/LoginPanel';

type ManagedUser = AuthUser & { disabled: boolean };

export function SettingsPage() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [form, setForm] = useState({ username: '', displayName: '', password: '', role: 'user' });
  const [error, setError] = useState<string | null>(null);

  const isAdmin = me?.role === 'admin';

  useEffect(() => {
    fetchMe().then((state) => setMe(state.user)).catch(() => setMe(null));
  }, []);

  useEffect(() => {
    if (isAdmin) void loadUsers();
  }, [isAdmin]);

  async function loadUsers() {
    try {
      const payload = await apiGet<{ users: ManagedUser[] }>('/api/auth/users');
      setUsers(payload.users);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载用户失败');
    }
  }

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await apiPost('/api/auth/users', form);
      setForm({ username: '', displayName: '', password: '', role: 'user' });
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建用户失败');
    }
  }

  async function setDisabled(userId: string, disabled: boolean) {
    setError(null);
    try {
      await apiPost(`/api/auth/users/${userId}/disabled`, { disabled });
      await loadUsers();
    } catch (err) {
      setError(err instanceof Error ? err.message : '更新用户失败');
    }
  }

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Platform</p>
          <h1>设置</h1>
        </div>
      </header>

      {error && <div className="error-box">{error}</div>}

      {!me && <LoginPanel />}

      {me && !isAdmin && (
        <section className="content-band">
          <h2>平台设置</h2>
          <p className="muted">当前账号不是管理员，无法管理用户。</p>
        </section>
      )}

      {isAdmin && (
        <section className="panel">
          <div className="result-header">
            <span>用户管理</span>
            <Users size={17} />
          </div>
          <form className="monitor-form user-form" onSubmit={createUser}>
            <input className="text-input" placeholder="用户名" value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} />
            <input className="text-input" placeholder="显示名" value={form.displayName} onChange={(event) => setForm({ ...form, displayName: event.target.value })} />
            <input className="text-input" type="password" placeholder="密码" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} />
            <select className="text-input" value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>
              <option value="user">普通用户</option>
              <option value="admin">管理员</option>
            </select>
            <button className="primary-button" type="submit"><UserPlus size={16} />创建</button>
          </form>
          <div className="compact-list">
            {users.map((user) => (
              <div className="user-row" key={user.id}>
                <span><strong>{user.displayName}</strong><small>{user.username} · {user.role}</small></span>
                {user.role !== 'admin' && (
                  <button className="chip" type="button" onClick={() => void setDisabled(user.id, !user.disabled)}>
                    {user.disabled ? '启用' : '禁用'}
                  </button>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
