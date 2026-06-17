import { FormEvent, useEffect, useState } from 'react';
import { Globe, Mail, Shield, UserPlus, Users } from 'lucide-react';

import { apiGet, apiPost } from '../api/client';
import { fetchMe, type AuthUser } from '../api/auth';
import { fetchEmailConfig, saveEmailConfig, testEmailConfig, type EmailConfig, type EmailConfigPayload } from '../api/settings';
import { LoginPanel } from '../components/LoginPanel';

type ManagedUser = AuthUser & { disabled: boolean };

const emptyEmailConfigForm: EmailConfigPayload = {
  smtpHost: '',
  smtpPort: 465,
  smtpUsername: '',
  smtpPassword: '',
  smtpFromAddress: '',
  smtpFromName: '实验监控系统',
};

export function SettingsPage() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [form, setForm] = useState({ username: '', displayName: '', password: '', role: 'user' });
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Email config state
  const [emailConfig, setEmailConfig] = useState<EmailConfig | null>(null);
  const [emailForm, setEmailForm] = useState<EmailConfigPayload>(emptyEmailConfigForm);
  const [emailLoading, setEmailLoading] = useState(false);
  const [emailConfigLoaded, setEmailConfigLoaded] = useState(false);

  const isAdmin = me?.role === 'admin';

  useEffect(() => {
    fetchMe().then((state) => setMe(state.user)).catch(() => setMe(null));
  }, []);

  useEffect(() => {
    if (isAdmin) {
      void loadUsers();
      void loadEmailConfig();
    }
  }, [isAdmin]);

  async function loadUsers() {
    try {
      const payload = await apiGet<{ users: ManagedUser[] }>('/api/auth/users');
      setUsers(payload.users);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载用户失败');
    }
  }

  async function loadEmailConfig() {
    try {
      const config = await fetchEmailConfig();
      setEmailConfig(config);
      setEmailForm({
        smtpHost: config.smtpHost,
        smtpPort: config.smtpPort,
        smtpUsername: config.smtpUsername,
        smtpPassword: '',
        smtpFromAddress: config.smtpFromAddress,
        smtpFromName: config.smtpFromName,
      });
      setEmailConfigLoaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载邮件配置失败');
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

  async function handleSaveEmailConfig(event: FormEvent) {
    event.preventDefault();
    setEmailLoading(true);
    setError(null);
    try {
      const updated = await saveEmailConfig(emailForm);
      setEmailConfig(updated);
      // Clear password field after save
      setEmailForm((prev) => ({ ...prev, smtpPassword: '' }));
      showSuccess('邮件配置保存成功');
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存邮件配置失败');
    } finally {
      setEmailLoading(false);
    }
  }

  async function handleTestEmailConfig() {
    if (!emailForm.smtpFromAddress && !emailForm.smtpUsername) {
      setError('请先填写发件人地址');
      return;
    }
    setEmailLoading(true);
    setError(null);
    try {
      const result = await testEmailConfig(emailForm);
      showSuccess(`测试邮件已发送至 ${result.testTo}，请查收`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '发送测试邮件失败');
    } finally {
      setEmailLoading(false);
    }
  }

  function showSuccess(msg: string) {
    setSuccessMsg(msg);
    setTimeout(() => setSuccessMsg(null), 4000);
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
      {successMsg && <div className="success-box">{successMsg}</div>}

      {!me && <LoginPanel />}

      {me && !isAdmin && (
        <section className="content-band">
          <h2>平台设置</h2>
          <p className="muted">当前账号不是管理员，无法管理平台设置。</p>
        </section>
      )}

      {isAdmin && (
        <>
          {/* Email Configuration */}
          <section className="panel">
            <div className="result-header">
              <span>邮件配置</span>
              <Mail size={17} />
            </div>
            <div className="admin-notice" style={{ marginBottom: '12px' }}>
              <Shield size={14} />
              <span>
                邮件配置仅管理员可见和可编辑。所有工具共享同一套平台邮件配置。
                {emailConfig?.configured ? (
                  <strong style={{ color: 'var(--color-success, #4caf50)', marginLeft: '6px' }}>✓ 已配置</strong>
                ) : (
                  <strong style={{ color: 'var(--color-warning, #ff9800)', marginLeft: '6px' }}>未配置</strong>
                )}
              </span>
            </div>
            {emailConfigLoaded && (
              <form className="monitor-form" onSubmit={(e) => void handleSaveEmailConfig(e)}>
                <div className="form-row" style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: '8px', alignItems: 'end' }}>
                  <div className="form-group" style={{ margin: 0 }}>
                    <label style={{ fontSize: '12px', color: 'var(--color-muted)', display: 'block', marginBottom: '4px' }}>
                      SMTP 服务器地址 *
                    </label>
                    <input
                      className="text-input"
                      placeholder="smtp.example.com"
                      value={emailForm.smtpHost}
                      onChange={(e) => setEmailForm({ ...emailForm, smtpHost: e.target.value })}
                    />
                  </div>
                  <div className="form-group" style={{ margin: 0 }}>
                    <label style={{ fontSize: '12px', color: 'var(--color-muted)', display: 'block', marginBottom: '4px' }}>
                      端口（SSL 465）
                    </label>
                    <input
                      className="text-input"
                      type="number"
                      style={{ width: '90px' }}
                      value={emailForm.smtpPort}
                      onChange={(e) => setEmailForm({ ...emailForm, smtpPort: Number(e.target.value) })}
                    />
                  </div>
                </div>
                <div className="form-row" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                  <div className="form-group" style={{ margin: 0 }}>
                    <label style={{ fontSize: '12px', color: 'var(--color-muted)', display: 'block', marginBottom: '4px' }}>
                      SMTP 用户名
                    </label>
                    <input
                      className="text-input"
                      placeholder="用户名"
                      value={emailForm.smtpUsername}
                      onChange={(e) => setEmailForm({ ...emailForm, smtpUsername: e.target.value })}
                    />
                  </div>
                  <div className="form-group" style={{ margin: 0 }}>
                    <label style={{ fontSize: '12px', color: 'var(--color-muted)', display: 'block', marginBottom: '4px' }}>
                      SMTP 密码
                    </label>
                    <input
                      className="text-input"
                      type="password"
                      placeholder={emailConfig?.configured ? '留空则不修改' : '请输入密码'}
                      value={emailForm.smtpPassword}
                      onChange={(e) => setEmailForm({ ...emailForm, smtpPassword: e.target.value })}
                    />
                  </div>
                </div>
                <div className="form-row" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
                  <div className="form-group" style={{ margin: 0 }}>
                    <label style={{ fontSize: '12px', color: 'var(--color-muted)', display: 'block', marginBottom: '4px' }}>
                      发件人地址
                    </label>
                    <input
                      className="text-input"
                      placeholder="noreply@example.com"
                      value={emailForm.smtpFromAddress}
                      onChange={(e) => setEmailForm({ ...emailForm, smtpFromAddress: e.target.value })}
                    />
                  </div>
                  <div className="form-group" style={{ margin: 0 }}>
                    <label style={{ fontSize: '12px', color: 'var(--color-muted)', display: 'block', marginBottom: '4px' }}>
                      发件人名称
                    </label>
                    <input
                      className="text-input"
                      placeholder="实验监控系统"
                      value={emailForm.smtpFromName}
                      onChange={(e) => setEmailForm({ ...emailForm, smtpFromName: e.target.value })}
                    />
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginTop: '4px' }}>
                  <button className="primary-button" type="submit" disabled={emailLoading}>
                    <Globe size={16} />保存邮件配置
                  </button>
                  <button
                    className="chip"
                    type="button"
                    disabled={emailLoading || (!emailForm.smtpFromAddress && !emailForm.smtpUsername)}
                    onClick={() => void handleTestEmailConfig()}
                  >
                    <Mail size={15} />发送测试邮件
                  </button>
                </div>
              </form>
            )}
          </section>

          {/* User Management */}
          <section className="panel">
            <div className="result-header">
              <span>用户管理</span>
              <Users size={17} />
            </div>
            <form className="monitor-form user-form" onSubmit={(e) => void createUser(e)}>
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
        </>
      )}
    </div>
  );
}
