import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  Calendar,
  Clock,
  Database,
  Eye,
  Globe,
  Info,
  KeyRound,
  Lock,
  Mail,
  Shield,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
} from 'lucide-react';

import { apiGet, apiPost } from '../api/client';
import {
  changePassword,
  deleteUser,
  fetchMe,
  resetUserPassword,
  setUserDisabled,
  updateUserRole,
  type AuthUser,
} from '../api/auth';
import { fetchEmailConfig, saveEmailConfig, testEmailConfig, type EmailConfig, type EmailConfigPayload, fetchAbout, type AboutInfo } from '../api/settings';
import {
  fetchMyStorage,
  fetchStorageUsage,
  fetchToolAccess,
  saveToolAccess,
  type MyStorage,
  type StorageUsage,
  type StorageUsageUser,
  type ToolAccessItem,
} from '../api/tools';
import {
  fetchDataCategories,
  fetchDataCount,
  fetchDataUsage,
  deleteData,
  type DataCategoryUsage,
  type DataDeletionResult,
  type ToolDataCategories,
} from '../api/tools';
import { LoginPanel } from '../components/LoginPanel';

type ManagedUser = AuthUser & { disabled: boolean };

type TabId = 'personal' | 'users' | 'data' | 'access' | 'email' | 'about';

const emptyEmailConfigForm: EmailConfigPayload = {
  smtpHost: '',
  smtpPort: 465,
  smtpUsername: '',
  smtpPassword: '',
  smtpFromAddress: '',
  smtpFromName: '实验监控系统',
};

/** Format a byte count into a human-readable string. */
function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function SettingsPage() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>('personal');
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const isAdmin = me?.role === 'admin';

  useEffect(() => {
    fetchMe().then((state) => setMe(state.user)).catch(() => setMe(null));
  }, []);

  // If non-admin tries to access an admin tab, redirect to personal.
  useEffect(() => {
    if (me && !isAdmin && activeTab !== 'personal') {
      setActiveTab('personal');
    }
  }, [me, isAdmin, activeTab]);

  function showSuccess(msg: string) {
    setSuccessMsg(msg);
    setTimeout(() => setSuccessMsg(null), 4000);
  }

  function handleError(err: unknown, fallback: string) {
    setError(err instanceof Error ? err.message : fallback);
  }

  if (!me) {
    return (
      <div className="page-stack">
        <header className="page-header">
          <div>
            <p className="eyebrow">Platform</p>
            <h1>设置</h1>
          </div>
        </header>
        <LoginPanel />
      </div>
    );
  }

  const tabs: { id: TabId; label: string; icon: typeof Users; adminOnly: boolean }[] = [
    { id: 'personal', label: '个人', icon: KeyRound, adminOnly: false },
    { id: 'users', label: '用户管理', icon: Users, adminOnly: true },
    { id: 'data', label: '工具数据清理', icon: Database, adminOnly: true },
    { id: 'access', label: '工具可见性', icon: Eye, adminOnly: true },
    { id: 'email', label: '邮件配置', icon: Mail, adminOnly: true },
    { id: 'about', label: '关于', icon: Info, adminOnly: false },
  ];

  const visibleTabs = tabs.filter((tab) => !tab.adminOnly || isAdmin);

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Platform</p>
          <h1>设置</h1>
        </div>
      </header>

      {error && <div className="error-box" onClick={() => setError(null)} style={{ cursor: 'pointer' }}>{error}</div>}
      {successMsg && <div className="success-box">{successMsg}</div>}

      <nav className="settings-tabs">
        {visibleTabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              className={`settings-tab ${activeTab === tab.id ? 'active' : ''}`}
              type="button"
              onClick={() => { setActiveTab(tab.id); setError(null); }}
            >
              <Icon size={16} />
              {tab.label}
            </button>
          );
        })}
      </nav>

      <div className="settings-tab-content">
        {activeTab === 'personal' && (
          <PersonalTab me={me} onError={handleError} onSuccess={showSuccess} />
        )}
        {activeTab === 'users' && isAdmin && (
          <UserManagementTab me={me} onError={handleError} onSuccess={showSuccess} />
        )}
        {activeTab === 'data' && isAdmin && (
          <ToolDataTab onError={handleError} onSuccess={showSuccess} />
        )}
        {activeTab === 'access' && isAdmin && (
          <ToolAccessTab onError={handleError} onSuccess={showSuccess} />
        )}
        {activeTab === 'email' && isAdmin && (
          <EmailTab onError={handleError} onSuccess={showSuccess} />
        )}
        {activeTab === 'about' && (
          <AboutTab onError={handleError} />
        )}
      </div>
    </div>
  );
}

// ── Personal Tab ──────────────────────────────────────────────────────────

function PersonalTab({
  me,
  onError,
  onSuccess,
}: {
  me: AuthUser;
  onError: (err: unknown, fallback: string) => void;
  onSuccess: (msg: string) => void;
}) {
  const [passwordForm, setPasswordForm] = useState({ currentPassword: '', newPassword: '' });
  const [myStorage, setMyStorage] = useState<MyStorage | null>(null);

  useEffect(() => {
    void loadMyStorage();
  }, []);

  async function loadMyStorage() {
    try {
      const data = await fetchMyStorage();
      setMyStorage(data);
    } catch (err) {
      onError(err, '加载个人数据失败');
    }
  }

  async function handleChangePassword(event: FormEvent) {
    event.preventDefault();
    try {
      await changePassword(passwordForm.currentPassword, passwordForm.newPassword);
      setPasswordForm({ currentPassword: '', newPassword: '' });
      onSuccess('密码已更新，请重新登录');
    } catch (err) {
      onError(err, '修改密码失败');
    }
  }

  const storageMap = useMemo(() => {
    const m: Record<string, number> = {};
    myStorage?.tools.forEach((t) => { m[t.toolId] = t.bytes; });
    return m;
  }, [myStorage]);

  return (
    <>
      <section className="panel">
        <div className="result-header">
          <span><KeyRound size={17} />修改密码</span>
        </div>
        <form className="monitor-form user-form" onSubmit={(e) => void handleChangePassword(e)}>
          <input
            className="text-input"
            type="password"
            placeholder="当前密码"
            value={passwordForm.currentPassword}
            onChange={(e) => setPasswordForm({ ...passwordForm, currentPassword: e.target.value })}
          />
          <input
            className="text-input"
            type="password"
            placeholder="新密码"
            value={passwordForm.newPassword}
            onChange={(e) => setPasswordForm({ ...passwordForm, newPassword: e.target.value })}
          />
          <button className="primary-button" type="submit"><Lock size={16} />更新密码</button>
        </form>
      </section>

      <DataCleanupPanel
        isAdminMode={false}
        onError={onError}
        onSuccess={onSuccess}
        storageMap={storageMap}
        totalBytes={myStorage?.totalBytes ?? 0}
        onAfterDelete={() => void loadMyStorage()}
      />
    </>
  );
}

// ── User Management Tab ───────────────────────────────────────────────────

function UserManagementTab({
  me,
  onError,
  onSuccess,
}: {
  me: AuthUser;
  onError: (err: unknown, fallback: string) => void;
  onSuccess: (msg: string) => void;
}) {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [form, setForm] = useState({ username: '', displayName: '', password: '', role: 'user' });
  const [resetPasswords, setResetPasswords] = useState<Record<string, string>>({});

  const isSuperAdmin = me.isSuperAdmin;

  useEffect(() => {
    void loadUsers();
  }, []);

  async function loadUsers() {
    try {
      const payload = await apiGet<{ users: ManagedUser[] }>('/api/auth/users');
      setUsers(payload.users);
    } catch (err) {
      onError(err, '加载用户失败');
    }
  }

  async function createUser(event: FormEvent) {
    event.preventDefault();
    try {
      await apiPost('/api/auth/users', form);
      setForm({ username: '', displayName: '', password: '', role: 'user' });
      await loadUsers();
      onSuccess('用户创建成功');
    } catch (err) {
      onError(err, '创建用户失败');
    }
  }

  async function handleResetPassword(userId: string) {
    const password = resetPasswords[userId] ?? '';
    if (!password) return;
    try {
      await resetUserPassword(userId, password);
      setResetPasswords((prev) => ({ ...prev, [userId]: '' }));
      onSuccess('用户密码已重置');
    } catch (err) {
      onError(err, '重置密码失败');
    }
  }

  async function handleToggleDisabled(user: ManagedUser) {
    try {
      await setUserDisabled(user.id, !user.disabled);
      await loadUsers();
    } catch (err) {
      onError(err, '更新用户失败');
    }
  }

  async function handleRoleChange(user: ManagedUser, role: 'admin' | 'user') {
    try {
      await updateUserRole(user.id, role);
      await loadUsers();
      onSuccess('用户角色已更新');
    } catch (err) {
      onError(err, '更新角色失败');
    }
  }

  async function handleDeleteUser(user: ManagedUser) {
    const roleLabel = user.role === 'admin' ? '管理员' : '用户';
    if (!window.confirm(`确认删除${roleLabel}「${user.displayName}」(${user.username})？该操作不可恢复。`)) return;
    try {
      await deleteUser(user.id);
      await loadUsers();
      onSuccess('用户已删除');
    } catch (err) {
      onError(err, '删除用户失败');
    }
  }

  return (
    <>
      <section className="panel">
        <div className="result-header">
          <span><UserPlus size={17} />添加用户</span>
        </div>
        {isSuperAdmin && (
          <div className="admin-notice" style={{ marginBottom: '12px' }}>
            <ShieldCheck size={14} />
            <span>您是超级管理员，可以创建管理员账号并调整角色。</span>
          </div>
        )}
        <form className="monitor-form user-form" onSubmit={(e) => void createUser(e)}>
          <input className="text-input" placeholder="用户名" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <input className="text-input" placeholder="显示名" value={form.displayName} onChange={(e) => setForm({ ...form, displayName: e.target.value })} />
          <input className="text-input" type="password" placeholder="密码" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <select className="text-input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} disabled={!isSuperAdmin && form.role === 'admin'}>
            <option value="user">普通用户</option>
            {isSuperAdmin && <option value="admin">管理员</option>}
          </select>
          <button className="primary-button" type="submit"><UserPlus size={16} />创建</button>
        </form>
      </section>

      <section className="panel">
        <div className="result-header">
          <span><Users size={17} />用户列表</span>
        </div>
        <div className="compact-list">
          {users.map((user) => {
            const canManage = !user.isSuperAdmin && (isSuperAdmin || user.role !== 'admin');
            return (
              <div className="user-row storage-row" key={user.id} style={{ alignItems: 'flex-start', flexWrap: 'wrap' }}>
                <div style={{ minWidth: '160px', flex: '1 1 160px' }}>
                  <strong>
                    {user.displayName}
                    {user.isSuperAdmin && <span className="badge info" style={{ marginLeft: '6px' }}>超级管理员</span>}
                    {!user.isSuperAdmin && user.role === 'admin' && <span className="badge success" style={{ marginLeft: '6px' }}>管理员</span>}
                    {user.disabled && <span className="badge danger" style={{ marginLeft: '6px' }}>已禁用</span>}
                  </strong>
                  <small>{user.username}</small>
                </div>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                  {/* Role selector - only super admin can change */}
                  {isSuperAdmin && !user.isSuperAdmin && (
                    <select
                      className="text-input"
                      style={{ width: '110px', minHeight: '34px' }}
                      value={user.role}
                      onChange={(e) => void handleRoleChange(user, e.target.value as 'admin' | 'user')}
                    >
                      <option value="user">普通用户</option>
                      <option value="admin">管理员</option>
                    </select>
                  )}
                  {/* Reset password - admin can reset non-admin, super admin can reset all */}
                  {canManage && (
                    <>
                      <input
                        className="text-input"
                        type="password"
                        placeholder="新密码"
                        style={{ width: '120px' }}
                        value={resetPasswords[user.id] ?? ''}
                        onChange={(e) => setResetPasswords({ ...resetPasswords, [user.id]: e.target.value })}
                      />
                      <button className="chip" type="button" onClick={() => void handleResetPassword(user.id)}>
                        <KeyRound size={14} />重置
                      </button>
                      <button className="chip" type="button" onClick={() => void handleToggleDisabled(user)}>
                        {user.disabled ? '启用' : '禁用'}
                      </button>
                      <button
                        className="chip"
                        type="button"
                        onClick={() => void handleDeleteUser(user)}
                        style={{ color: 'var(--danger)' }}
                      >
                        <Trash2 size={14} />删除
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </>
  );
}

// ── Tool Data Cleanup Tab ─────────────────────────────────────────────────

function ToolDataTab({
  onError,
  onSuccess,
}: {
  onError: (err: unknown, fallback: string) => void;
  onSuccess: (msg: string) => void;
}) {
  const [usage, setUsage] = useState<StorageUsage | null>(null);

  useEffect(() => {
    void loadUsage();
  }, []);

  async function loadUsage() {
    try {
      const data = await fetchStorageUsage();
      setUsage(data);
    } catch (err) {
      onError(err, '加载存储用量失败');
    }
  }

  const storageMap = useMemo(() => {
    const m: Record<string, number> = {};
    usage?.tools.forEach((t) => { m[t.toolId] = t.totalBytes; });
    return m;
  }, [usage]);

  const users = useMemo(() => (usage?.users ?? []).slice().sort((a, b) => b.totalBytes - a.totalBytes), [usage]);

  return (
    <>
      <section className="panel">
        <div className="result-header">
          <span><Database size={17} />存储总览</span>
        </div>
        <div className="storage-summary">
          <div className="storage-summary-item">
            <small>总占用</small>
            <strong>{usage ? formatBytes(usage.grandTotal) : '...'}</strong>
          </div>
        </div>
      </section>

      <DataCleanupPanel
        isAdminMode={true}
        onError={onError}
        onSuccess={onSuccess}
        storageMap={storageMap}
        totalBytes={usage?.grandTotal ?? 0}
        users={users}
        onAfterDelete={() => void loadUsage()}
      />
    </>
  );
}

// ── Tool Access Tab ───────────────────────────────────────────────────────

function ToolAccessTab({
  onError,
  onSuccess,
}: {
  onError: (err: unknown, fallback: string) => void;
  onSuccess: (msg: string) => void;
}) {
  const [toolAccess, setToolAccess] = useState<ToolAccessItem[]>([]);
  const [users, setUsers] = useState<ManagedUser[]>([]);

  useEffect(() => {
    void loadToolAccess();
    void loadUsers();
  }, []);

  async function loadToolAccess() {
    try {
      const payload = await fetchToolAccess();
      setToolAccess(payload.items);
    } catch (err) {
      onError(err, '加载工具权限失败');
    }
  }

  async function loadUsers() {
    try {
      const payload = await apiGet<{ users: ManagedUser[] }>('/api/auth/users');
      setUsers(payload.users);
    } catch (err) {
      onError(err, '加载用户失败');
    }
  }

  async function toggleToolGlobalPublic(item: ToolAccessItem, globalPublic: boolean) {
    try {
      await saveToolAccess(item.tool.id, {
        globalPublic,
        allowedUserIds: item.allowedUsers.map((user) => user.id),
      });
      await loadToolAccess();
    } catch (err) {
      onError(err, '保存工具权限失败');
    }
  }

  async function toggleToolUser(item: ToolAccessItem, userId: string, allowed: boolean) {
    const current = new Set(item.allowedUsers.map((user) => user.id));
    if (allowed) current.add(userId);
    else current.delete(userId);
    try {
      await saveToolAccess(item.tool.id, {
        globalPublic: item.globalPublic,
        allowedUserIds: Array.from(current),
      });
      await loadToolAccess();
    } catch (err) {
      onError(err, '保存工具权限失败');
    }
  }

  return (
    <section className="panel">
      <div className="result-header">
        <span><Eye size={17} />工具可见性</span>
      </div>
      <div className="compact-list">
        {toolAccess.map((item) => {
          const allowedIds = new Set(item.allowedUsers.map((user) => user.id));
          return (
            <div className="user-row" key={item.tool.id} style={{ alignItems: 'flex-start' }}>
              <span>
                <strong>{item.tool.name}</strong>
                <small>{item.tool.id} · {item.globalPublic ? '全局公开' : '按用户授权'}</small>
              </span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', alignItems: 'flex-end' }}>
                <label className="chip" style={{ cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={item.globalPublic}
                    onChange={(e) => void toggleToolGlobalPublic(item, e.target.checked)}
                  />
                  全局公开
                </label>
                {!item.globalPublic && (
                  <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                    {users.filter((user) => !user.disabled).map((user) => (
                      <label className="chip" key={user.id} style={{ cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={allowedIds.has(user.id)}
                          onChange={(e) => void toggleToolUser(item, user.id, e.target.checked)}
                        />
                        {user.displayName}
                      </label>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ── Email Config Tab ──────────────────────────────────────────────────────

function EmailTab({
  onError,
  onSuccess,
}: {
  onError: (err: unknown, fallback: string) => void;
  onSuccess: (msg: string) => void;
}) {
  const [emailConfig, setEmailConfig] = useState<EmailConfig | null>(null);
  const [emailForm, setEmailForm] = useState<EmailConfigPayload>(emptyEmailConfigForm);
  const [emailLoading, setEmailLoading] = useState(false);
  const [emailConfigLoaded, setEmailConfigLoaded] = useState(false);

  useEffect(() => {
    void loadEmailConfig();
  }, []);

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
      onError(err, '加载邮件配置失败');
    }
  }

  async function handleSaveEmailConfig(event: FormEvent) {
    event.preventDefault();
    setEmailLoading(true);
    try {
      const updated = await saveEmailConfig(emailForm);
      setEmailConfig(updated);
      setEmailForm((prev) => ({ ...prev, smtpPassword: '' }));
      onSuccess('邮件配置保存成功');
    } catch (err) {
      onError(err, '保存邮件配置失败');
    } finally {
      setEmailLoading(false);
    }
  }

  async function handleTestEmailConfig() {
    if (!emailForm.smtpFromAddress && !emailForm.smtpUsername) {
      onError(new Error('请先填写发件人地址'), '请先填写发件人地址');
      return;
    }
    setEmailLoading(true);
    try {
      const result = await testEmailConfig(emailForm);
      onSuccess(`测试邮件已发送至 ${result.testTo}，请查收`);
    } catch (err) {
      onError(err, '发送测试邮件失败');
    } finally {
      setEmailLoading(false);
    }
  }

  return (
    <section className="panel">
      <div className="result-header">
        <span><Mail size={17} />邮件配置</span>
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
          <div className="responsive-grid host-port">
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
          <div className="responsive-grid two">
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
          <div className="responsive-grid two">
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
          <div className="responsive-actions" style={{ marginTop: '4px' }}>
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
  );
}

// ── Unified Data Cleanup Panel ─────────────────────────────────────────────

const ALL_TOOLS = '__all__';

/** Format a date range (YYYY-MM-DD) into a compact label. */
function rangeLabel(start: string, end: string): string {
  if (!start && !end) return '全部时间';
  return `${start || '…'} ~ ${end || '…'}`;
}

/** A compact button that opens a popover with two date inputs (day granularity). */
function DateRangePicker({
  start,
  end,
  onChange,
  disabled,
}: {
  start: string;
  end: string;
  onChange: (start: string, end: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button
        className="chip small"
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        style={disabled ? { opacity: 0.5 } : undefined}
      >
        <Calendar size={13} />{rangeLabel(start, end)}
      </button>
      {open && !disabled && (
        <>
          <div
            style={{ position: 'fixed', inset: 0, zIndex: 20 }}
            onClick={() => setOpen(false)}
          />
          <div
            className="date-range-popover"
            style={{
              position: 'absolute',
              zIndex: 21,
              top: 'calc(100% + 6px)',
              right: 0,
              display: 'grid',
              gap: 8,
              padding: 12,
              minWidth: 230,
            }}
          >
            <label className="date-range-field">
              <span>起始日期（含）</span>
              <input
                className="text-input"
                type="date"
                value={start}
                max={end || undefined}
                onChange={(e) => onChange(e.target.value, end)}
              />
            </label>
            <label className="date-range-field">
              <span>结束日期（含）</span>
              <input
                className="text-input"
                type="date"
                value={end}
                min={start || undefined}
                onChange={(e) => onChange(start, e.target.value)}
              />
            </label>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button
                className="chip tiny"
                type="button"
                onClick={() => { onChange('', ''); setOpen(false); }}
              >
                清除
              </button>
              <button className="chip tiny" type="button" onClick={() => setOpen(false)}>
                确定
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── Donut Chart ───────────────────────────────────────────────────────────

const DONUT_COLORS = [
  '#1a73e8', '#0f766e', '#d97706', '#dc2626', '#7c3aed',
  '#0891b2', '#c026d3', '#65a30d', '#ea580c', '#4f46e5',
  '#0d9488', '#be185d', '#4338ca', '#b45309', '#0369a1',
];

type DonutSegment = {
  id: string;
  label: string;
  value: number;
  color: string;
};

function DonutChart({
  segments,
  size = 200,
  thickness = 34,
  centerValue,
  centerTitle,
  selectedId,
  onSelect,
  formatValue,
  emptyText = '暂无数据',
}: {
  segments: DonutSegment[];
  size?: number;
  thickness?: number;
  centerValue?: string;
  centerTitle?: string;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  formatValue?: (v: number) => string;
  emptyText?: string;
}) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const radius = size / 2 - thickness / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * radius;
  const fmt = formatValue ?? ((v: number) => String(v));

  if (total === 0 || segments.length === 0) {
    return (
      <div className="donut-card">
        <div className="donut-chart-wrapper" style={{ width: size, height: size }}>
          <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
            <circle cx={cx} cy={cy} r={radius} fill="none" stroke="#eef2f7" strokeWidth={thickness} />
          </svg>
          <div className="donut-center">
            <small className="muted">{emptyText}</small>
          </div>
        </div>
      </div>
    );
  }

  let cumulativeOffset = 0;
  return (
    <div className="donut-card">
      <div className="donut-chart-wrapper" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="donut-svg">
          <circle cx={cx} cy={cy} r={radius} fill="none" stroke="#f1f5f9" strokeWidth={thickness} />
          {segments.map((seg) => {
            const fraction = seg.value / total;
            const segLength = fraction * circumference;
            const dashOffset = -cumulativeOffset;
            cumulativeOffset += segLength;
            const isSelected = selectedId === seg.id;
            const isDimmed = selectedId != null && !isSelected;
            return (
              <circle
                key={seg.id}
                cx={cx}
                cy={cy}
                r={radius}
                fill="none"
                stroke={seg.color}
                strokeWidth={thickness}
                strokeDasharray={`${Math.max(segLength - 1.5, 0.5)} ${circumference}`}
                strokeDashoffset={dashOffset}
                transform={`rotate(-90 ${cx} ${cy})`}
                style={{
                  cursor: onSelect ? 'pointer' : 'default',
                  opacity: isDimmed ? 0.3 : 1,
                  transition: 'opacity 150ms ease',
                }}
                onClick={onSelect ? () => onSelect(seg.id) : undefined}
              />
            );
          })}
        </svg>
        <div className="donut-center">
          {centerValue && <strong>{centerValue}</strong>}
          {centerTitle && <small className="muted">{centerTitle}</small>}
        </div>
      </div>
      <ul className="donut-legend">
        {segments.map((seg) => {
          const pct = total > 0 ? (seg.value / total) * 100 : 0;
          const isSelected = selectedId === seg.id;
          return (
            <li
              key={seg.id}
              className={isSelected ? 'active' : ''}
              style={{ cursor: onSelect ? 'pointer' : 'default' }}
              onClick={onSelect ? () => onSelect(seg.id) : undefined}
            >
              <span className="donut-legend-dot" style={{ background: seg.color }} />
              <span className="donut-legend-label">{seg.label}</span>
              <span className="donut-legend-value">{fmt(seg.value)} · {pct.toFixed(1)}%</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

type CategoryState = { selected: boolean; start: string; end: string };

function DataCleanupPanel({
  onError,
  onSuccess,
  isAdminMode,
  storageMap,
  totalBytes,
  users,
  onAfterDelete,
}: {
  onError: (err: unknown, fallback: string) => void;
  onSuccess: (msg: string) => void;
  isAdminMode: boolean;
  storageMap: Record<string, number>;
  totalBytes: number;
  users?: StorageUsageUser[];
  onAfterDelete?: () => void | Promise<void>;
}) {
  const ALL_USERS = '__all__';
  const [tools, setTools] = useState<ToolDataCategories[]>([]);
  const [selectedTool, setSelectedTool] = useState<string>(ALL_TOOLS);
  const [selectedUser, setSelectedUser] = useState<string>(ALL_USERS);
  const [usage, setUsage] = useState<DataCategoryUsage[]>([]);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [catState, setCatState] = useState<Record<string, CategoryState>>({});
  const [globalRange, setGlobalRange] = useState<{ start: string; end: string }>({ start: '', end: '' });
  const [donutFocusTool, setDonutFocusTool] = useState<string | null>(null);
  const [donutCatUsage, setDonutCatUsage] = useState<DataCategoryUsage[]>([]);
  const [donutLoading, setDonutLoading] = useState(false);
  // Selected (date-range-filtered) row counts per category, keyed by category name.
  const [selectedCounts, setSelectedCounts] = useState<Record<string, number>>({});
  const [countLoading, setCountLoading] = useState(false);

  // In admin mode, null = all users; otherwise the current user (null is fine
  // because the backend defaults to self for non-admins).
  const targetUserId = isAdminMode
    ? (selectedUser === ALL_USERS ? null : selectedUser)
    : null;

  useEffect(() => {
    void loadCategories();
  }, []);

  async function loadCategories() {
    setLoading(true);
    try {
      const data = await fetchDataCategories();
      setTools(data.tools);
    } catch (err) {
      onError(err, '加载数据分类失败');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (selectedTool === ALL_TOOLS) {
      setUsage([]);
      setCatState({});
      return;
    }
    void loadUsage(selectedTool, targetUserId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTool, selectedUser]);

  async function loadUsage(toolId: string, userId: string | null) {
    try {
      const data = await fetchDataUsage(toolId, isAdminMode ? userId : undefined);
      setUsage(data.categories);
      const tool = tools.find((t) => t.toolId === toolId);
      const next: Record<string, CategoryState> = {};
      tool?.categories.forEach((c) => {
        next[c.name] = { selected: false, start: '', end: '' };
      });
      setCatState(next);
    } catch (err) {
      onError(err, '加载数据用量失败');
    }
  }

  // When the donut focus tool changes, fetch its category usage for the second donut.
  useEffect(() => {
    if (!donutFocusTool) {
      setDonutCatUsage([]);
      return;
    }
    // If the focused tool is the same as the selected tool, reuse `usage`.
    if (donutFocusTool === selectedTool && usage.length > 0) {
      setDonutCatUsage(usage);
      return;
    }
    let cancelled = false;
    setDonutLoading(true);
    fetchDataUsage(donutFocusTool, isAdminMode ? targetUserId : undefined)
      .then((data) => {
        if (!cancelled) setDonutCatUsage(data.categories);
      })
      .catch((err) => {
        if (!cancelled) onError(err, '加载数据用量失败');
      })
      .finally(() => {
        if (!cancelled) setDonutLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [donutFocusTool, targetUserId]);

  function patchCat(name: string, patch: Partial<CategoryState>) {
    setCatState((prev) => ({
      ...prev,
      [name]: { ...(prev[name] ?? { selected: false, start: '', end: '' }), ...patch },
    }));
  }

  // Fetch date-range-filtered ("selected") row counts so the UI can show
  // "selected X / total Y" when a date range is chosen.
  useEffect(() => {
    if (selectedTool === ALL_TOOLS) {
      setSelectedCounts({});
      return;
    }
    const tool = tools.find((t) => t.toolId === selectedTool);
    if (!tool) {
      setSelectedCounts({});
      return;
    }
    // Only time-based categories with a non-empty date range need preview counts.
    const items = tool.categories
      .filter((c) => c.timeColumn !== null)
      .map((c) => {
        const st = catState[c.name];
        return { category: c.name, startDate: st?.start || null, endDate: st?.end || null };
      })
      .filter((it) => it.startDate || it.endDate);
    if (items.length === 0) {
      setSelectedCounts({});
      return;
    }
    let cancelled = false;
    setCountLoading(true);
    fetchDataCount({ toolId: selectedTool, items, userId: targetUserId })
      .then((data) => {
        if (!cancelled) setSelectedCounts(data.counts);
      })
      .catch((err) => {
        if (!cancelled) onError(err, '获取选中计数失败');
      })
      .finally(() => {
        if (!cancelled) setCountLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catState, selectedTool, tools, selectedUser]);

  // For all-tools mode, fetch the global date-range-filtered count per tool.
  const allToolsSelectedTotal = useMemo(() => {
    if (selectedTool !== ALL_TOOLS) return null;
    if (!globalRange.start && !globalRange.end) return null;
    return Object.values(selectedCounts).reduce((s, n) => s + n, 0);
  }, [selectedTool, globalRange, selectedCounts]);

  useEffect(() => {
    if (selectedTool !== ALL_TOOLS) {
      setSelectedCounts({});
      return;
    }
    if (!globalRange.start && !globalRange.end) {
      setSelectedCounts({});
      return;
    }
    // Backend counts per category; for "all tools" we query each tool with
    // category=null (all time-based categories) in the chosen date range.
    let cancelled = false;
    setCountLoading(true);
    Promise.all(
      tools.map((t) =>
        fetchDataCount({
          toolId: t.toolId,
          items: [{ category: null, startDate: globalRange.start || null, endDate: globalRange.end || null }],
          userId: targetUserId,
        }).then((data) => ({ toolId: t.toolId, count: data.counts['__all__'] ?? 0 })),
      ),
    )
      .then((results) => {
        if (cancelled) return;
        const map: Record<string, number> = {};
        results.forEach((r) => { map[r.toolId] = r.count; });
        setSelectedCounts(map);
      })
      .catch((err) => {
        if (!cancelled) onError(err, '获取选中计数失败');
      })
      .finally(() => {
        if (!cancelled) setCountLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTool, globalRange, tools, selectedUser]);

  function countDeleted(result: DataDeletionResult): number {
    let total = 0;
    for (const cat of Object.keys(result.deleted)) {
      for (const table of Object.keys(result.deleted[cat])) {
        total += result.deleted[cat][table];
      }
    }
    return total;
  }

  async function handleDelete() {
    const scopeLabel = isAdminMode
      ? (targetUserId ? '用户' : '所有用户')
      : '您';
    const userClause = isAdminMode && targetUserId
      ? `「${users?.find((u) => u.userId === targetUserId)?.displayName ?? targetUserId}」的`
      : '';

    if (selectedTool === ALL_TOOLS) {
      const timeLabel = rangeLabel(globalRange.start, globalRange.end);
      if (!window.confirm(`确认删除${userClause || scopeLabel}在「全部工具」中「${timeLabel}」的数据？该操作不可恢复。`)) return;
      setDeleting(true);
      try {
        let total = 0;
        let message: string | null = null;
        for (const t of tools) {
          const result = await deleteData({
            toolId: t.toolId,
            category: null,
            startDate: globalRange.start || null,
            endDate: globalRange.end || null,
            userId: targetUserId,
          });
          if (result.message) message = result.message;
          total += countDeleted(result);
        }
        if (message) onSuccess(message);
        else onSuccess(`已删除 ${total} 条记录`);
        await onAfterDelete?.();
      } catch (err) {
        onError(err, '删除数据失败');
      } finally {
        setDeleting(false);
      }
      return;
    }

    const tool = tools.find((t) => t.toolId === selectedTool);
    if (!tool) return;

    const selectedCats = tool.categories.filter((c) => catState[c.name]?.selected);
    const catsToDelete = selectedCats.length > 0 ? selectedCats : tool.categories;
    const descList = catsToDelete.map((c) => c.description).join('、');
    if (!window.confirm(`确认删除${userClause || scopeLabel}在「${tool.toolName}」中的「${descList}」？该操作不可恢复。`)) return;

    setDeleting(true);
    try {
      let total = 0;
      let message: string | null = null;
      for (const c of catsToDelete) {
        const st = catState[c.name];
        // Non-time-based categories ignore the date range (delete all of their rows).
        const start = c.timeColumn ? (st?.start || null) : null;
        const end = c.timeColumn ? (st?.end || null) : null;
        const result = await deleteData({
          toolId: selectedTool,
          category: c.name,
          startDate: start,
          endDate: end,
          userId: targetUserId,
        });
        if (result.message) message = result.message;
        total += countDeleted(result);
      }
      if (message) onSuccess(message);
      else onSuccess(`已删除 ${total} 条记录`);
      await onAfterDelete?.();
      await loadUsage(selectedTool, targetUserId);
    } catch (err) {
      onError(err, '删除数据失败');
    } finally {
      setDeleting(false);
    }
  }

  const selectedToolData = tools.find((t) => t.toolId === selectedTool);
  const selectedCount = selectedToolData
    ? selectedToolData.categories.filter((c) => catState[c.name]?.selected).length
    : 0;

  const deleteLabel = deleting
    ? '删除中...'
    : selectedTool === ALL_TOOLS
      ? '删除全部工具数据'
      : selectedCount > 0
        ? `删除选中 ${selectedCount} 项`
        : '删除全部数据';

  return (
    <section className="panel">
      <div className="result-header">
        <span><Clock size={17} />数据清理</span>
        <small className="muted">总占用 {formatBytes(totalBytes)}</small>
      </div>
      <div className="admin-notice" style={{ marginBottom: '12px' }}>
        <Shield size={14} />
        <span>
          选择工具与数据分类，可按时间范围精确删除。
          {isAdminMode
            ? (targetUserId
                ? `管理员模式：将影响用户「${users?.find((u) => u.userId === targetUserId)?.displayName ?? targetUserId}」的数据。`
                : '管理员模式：将影响所有用户的数据。')
            : '仅删除您自己的数据。'}
        </span>
      </div>

      {tools.length === 0 && !loading && (
        <div className="muted" style={{ padding: '12px 4px' }}>暂无已注册数据分类的工具</div>
      )}

      {tools.length > 0 && (
        <>
          {/* Tool selector + (admin) user selector */}
          <div className="responsive-row" style={{ gap: '12px', marginBottom: '12px' }}>
            <div className="form-group" style={{ flex: '1 1 200px', marginBottom: 0 }}>
              <label style={{ fontSize: '12px', color: 'var(--color-muted)', display: 'block', marginBottom: '4px' }}>
                选择工具
              </label>
              <select
                className="text-input"
                value={selectedTool}
                onChange={(e) => setSelectedTool(e.target.value)}
                disabled={loading}
              >
                <option value={ALL_TOOLS}>全部工具</option>
                {tools.map((t) => (
                  <option key={t.toolId} value={t.toolId}>{t.toolName}</option>
                ))}
              </select>
            </div>
            {isAdminMode && users && users.length > 0 && (
              <div className="form-group" style={{ flex: '1 1 200px', marginBottom: 0 }}>
                <label style={{ fontSize: '12px', color: 'var(--color-muted)', display: 'block', marginBottom: '4px' }}>
                  选择用户
                </label>
                <select
                  className="text-input"
                  value={selectedUser}
                  onChange={(e) => setSelectedUser(e.target.value)}
                  disabled={loading}
                >
                  <option value={ALL_USERS}>全部用户</option>
                  {users.map((u) => (
                    <option key={u.userId} value={u.userId}>
                      {u.displayName}（{u.username}）{u.totalBytes > 0 ? ` · ${formatBytes(u.totalBytes)}` : ''}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {selectedTool === ALL_TOOLS ? (
            <>
              {/* All-tools mode: donut visualization + global time range */}
              <div
                className="storage-donuts"
                style={{ gridTemplateColumns: donutFocusTool ? '1fr 1fr' : '1fr' }}
              >
                <DonutChart
                  segments={tools
                    .filter((t) => (storageMap[t.toolId] ?? 0) > 0)
                    .map((t, i) => ({
                      id: t.toolId,
                      label: t.toolName,
                      value: storageMap[t.toolId] ?? 0,
                      color: DONUT_COLORS[i % DONUT_COLORS.length],
                    }))}
                  size={200}
                  thickness={34}
                  centerValue={formatBytes(totalBytes)}
                  centerTitle="总占用"
                  selectedId={donutFocusTool}
                  onSelect={(id) => setDonutFocusTool((prev) => (prev === id ? null : id))}
                  formatValue={(v) => formatBytes(v)}
                  emptyText="暂无存储数据"
                />
                {donutFocusTool && (
                  <DonutChart
                    segments={donutCatUsage
                      .filter((u) => u.totalRows > 0)
                      .map((u, i) => ({
                        id: u.category,
                        label: u.description,
                        value: u.totalRows,
                        color: DONUT_COLORS[i % DONUT_COLORS.length],
                      }))}
                    size={200}
                    thickness={34}
                    centerValue={
                      donutLoading
                        ? '...'
                        : `${donutCatUsage.reduce((s, u) => s + u.totalRows, 0)} 条`
                    }
                    centerTitle={
                      tools.find((t) => t.toolId === donutFocusTool)?.toolName ?? '记录数占比'
                    }
                    formatValue={(v) => `${v} 条`}
                    emptyText={donutLoading ? '加载中...' : '暂无记录'}
                  />
                )}
              </div>
              {donutFocusTool ? (
                <small className="muted" style={{ textAlign: 'center', display: 'block', marginBottom: '12px' }}>
                  左图为各工具存储占用占比，右图为「{tools.find((t) => t.toolId === donutFocusTool)?.toolName}」各数据分类的记录数占比。点击左侧扇区可切换工具，再次点击可取消。
                </small>
              ) : (
                <small className="muted" style={{ textAlign: 'center', display: 'block', marginBottom: '12px' }}>
                  各工具存储占用占比。点击环状图任一扇区可查看该工具的数据分类分布。
                </small>
              )}

              {/* Time range */}
              <div className="form-group" style={{ marginBottom: '12px' }}>
                <label style={{ fontSize: '12px', color: 'var(--color-muted)', display: 'block', marginBottom: '6px' }}>
                  时间范围
                </label>
                <DateRangePicker
                  start={globalRange.start}
                  end={globalRange.end}
                  onChange={(s, e) => setGlobalRange({ start: s, end: e })}
                />
              </div>
              {allToolsSelectedTotal !== null && (
                <div className="admin-notice" style={{ marginBottom: '12px' }}>
                  <Shield size={14} />
                  <span>
                    所选时间范围内共 <strong>{countLoading ? '...' : `${allToolsSelectedTotal} 条`}</strong> 记录将被删除（总占用 {formatBytes(totalBytes)}）。
                  </span>
                </div>
              )}
              <div className="admin-notice" style={{ marginBottom: '12px' }}>
                <Shield size={14} />
                <span>将删除全部工具在所选时间范围内的全部数据（仅含支持时间维度的数据分类）。</span>
              </div>
            </>
          ) : (
            <>
              {/* Single-tool mode: donut + per-category selection + per-category date range */}
              {selectedToolData && (
                <>
                  <div className="responsive-row between" style={{ marginBottom: '8px' }}>
                    <small className="muted">{selectedToolData.toolId}</small>
                    <small className="muted">占用 {formatBytes(storageMap[selectedToolData.toolId] ?? 0)}</small>
                  </div>

                  {/* Category proportion donut */}
                  <div className="storage-donuts" style={{ gridTemplateColumns: '1fr', marginBottom: '12px' }}>
                    <DonutChart
                      segments={usage
                        .filter((u) => u.totalRows > 0)
                        .map((u, i) => ({
                          id: u.category,
                          label: u.description,
                          value: u.totalRows,
                          color: DONUT_COLORS[i % DONUT_COLORS.length],
                        }))}
                      size={180}
                      thickness={30}
                      centerValue={
                        Object.keys(selectedCounts).length > 0
                          ? `${Object.values(selectedCounts).reduce((s, n) => s + n, 0)} / ${usage.reduce((s, u) => s + u.totalRows, 0)} 条`
                          : `${usage.reduce((s, u) => s + u.totalRows, 0)} 条`
                      }
                      centerTitle={Object.keys(selectedCounts).length > 0 ? '选中 / 全部记录' : '记录数占比'}
                      formatValue={(v) => `${v} 条`}
                      emptyText="暂无记录"
                    />
                  </div>

                  {/* Category checkboxes */}
                  <div className="compact-list" style={{ marginBottom: '12px' }}>
                    {selectedToolData.categories.map((cat) => {
                      const usageItem = usage.find((u) => u.category === cat.name);
                      const rowCount = usageItem?.totalRows ?? 0;
                      const isTimeBased = cat.timeColumn !== null;
                      const st = catState[cat.name] ?? { selected: false, start: '', end: '' };
                      const hasRange = !!(st.start || st.end);
                      const selCount = hasRange ? (selectedCounts[cat.name] ?? null) : null;
                      return (
                        <div className="user-row" key={cat.name} style={{ alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
                          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', flex: '1 1 220px', minWidth: 0 }}>
                            <input
                              type="checkbox"
                              checked={st.selected}
                              onChange={() => patchCat(cat.name, { selected: !st.selected })}
                            />
                            <span>
                              <strong>{cat.description}</strong>
                              <small>
                                {cat.name} · {rowCount} 条记录
                                {selCount !== null && ` · 选中 ${selCount} 条`}
                                {cat.storage === 'platform_db' ? ' · 共享数据库' : ' · 用户数据库'}
                                {!isTimeBased && ' · 配置数据（不支持按时间删除）'}
                              </small>
                            </span>
                          </label>
                          {isTimeBased && (
                            <DateRangePicker
                              start={st.start}
                              end={st.end}
                              onChange={(s, e) => patchCat(cat.name, { start: s, end: e })}
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </>
          )}

          <div className="responsive-actions">
            <button
              className="chip"
              type="button"
              disabled={deleting || tools.length === 0}
              onClick={() => void handleDelete()}
              style={{ color: 'var(--danger)' }}
            >
              <Trash2 size={14} />{deleteLabel}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

// ── About Tab ─────────────────────────────────────────────────────────────

function AboutTab({
  onError,
}: {
  onError: (err: unknown, fallback: string) => void;
}) {
  const [about, setAbout] = useState<AboutInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void loadAbout();
  }, []);

  async function loadAbout() {
    setLoading(true);
    try {
      const data = await fetchAbout();
      setAbout(data);
    } catch (err) {
      onError(err, '加载关于信息失败');
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel">
      <div className="result-header">
        <span><Info size={17} />关于</span>
      </div>

      {loading ? (
        <div style={{ padding: '24px', color: 'var(--color-muted)', textAlign: 'center' }}>
          加载中…
        </div>
      ) : about ? (
        <div style={{ padding: '20px', maxWidth: '640px' }}>
          {about.title && (
            <h2 style={{ margin: '0 0 8px', fontSize: '20px' }}>{about.title}</h2>
          )}
          {about.description && (
            <p style={{ margin: '0 0 20px', color: 'var(--color-muted)', lineHeight: 1.6 }}>
              {about.description}
            </p>
          )}
          {about.items && about.items.length > 0 && (
            <dl style={{ margin: 0, display: 'grid', gap: '12px' }}>
              {about.items.map((item, idx) => (
                <div
                  key={idx}
                  className="user-row"
                  style={{ alignItems: 'center', justifyContent: 'space-between', padding: '10px 12px' }}
                >
                  <dt style={{ color: 'var(--color-muted)', fontSize: '13px' }}>{item.label}</dt>
                  <dd style={{ margin: 0, fontWeight: 500 }}>
                    {item.type === 'email' && item.value ? (
                      <a href={`mailto:${item.value}`} style={{ color: 'var(--color-primary)' }}>{item.value}</a>
                    ) : item.type === 'url' && item.value ? (
                      <a href={item.value} target="_blank" rel="noreferrer" style={{ color: 'var(--color-primary)' }}>{item.value}</a>
                    ) : (
                      item.value
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {!about.title && !about.description && (!about.items || about.items.length === 0) && (
            <p style={{ color: 'var(--color-muted)' }}>暂无关于信息。</p>
          )}
        </div>
      ) : (
        <div style={{ padding: '24px', color: 'var(--color-muted)' }}>暂无关于信息。</div>
      )}
    </section>
  );
}
