import { FormEvent, useEffect, useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Database,
  Eye,
  Globe,
  HardDrive,
  KeyRound,
  Lock,
  Mail,
  Shield,
  ShieldCheck,
  Trash2,
  UserPlus,
  Users,
  Wrench,
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
import { fetchEmailConfig, saveEmailConfig, testEmailConfig, type EmailConfig, type EmailConfigPayload } from '../api/settings';
import {
  clearMyStorage,
  clearMyToolStorage,
  clearToolStorage,
  clearUserStorage,
  clearUserToolStorage,
  fetchMyStorage,
  fetchStorageUsage,
  fetchToolAccess,
  saveToolAccess,
  type MyStorage,
  type StorageUsage,
  type ToolAccessItem,
} from '../api/tools';
import { LoginPanel } from '../components/LoginPanel';

type ManagedUser = AuthUser & { disabled: boolean };

type TabId = 'personal' | 'users' | 'data' | 'access' | 'email';

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
  const [clearingTool, setClearingTool] = useState<string | null>(null);
  const [clearingAll, setClearingAll] = useState(false);

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

  async function handleClearMyTool(toolId: string, toolName: string) {
    if (!window.confirm(`确认清除您在「${toolName}」中的数据？该操作不可恢复。`)) return;
    setClearingTool(toolId);
    try {
      const result = await clearMyToolStorage(toolId);
      onSuccess(`已清理 ${toolName}：删除 ${result.removedPaths.length} 个目录/文件`);
      await loadMyStorage();
    } catch (err) {
      onError(err, '清理数据失败');
    } finally {
      setClearingTool(null);
    }
  }

  async function handleClearAllMy() {
    if (!window.confirm('确认清除您在所有工具中的个人数据？该操作不可恢复。')) return;
    setClearingAll(true);
    try {
      const result = await clearMyStorage();
      onSuccess(`已清理所有个人数据：删除 ${result.removedPaths.length} 个目录/文件`);
      await loadMyStorage();
    } catch (err) {
      onError(err, '清理数据失败');
    } finally {
      setClearingAll(false);
    }
  }

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

      <section className="panel">
        <div className="result-header">
          <span><HardDrive size={17} />我的数据占用</span>
          {myStorage && myStorage.totalBytes > 0 && (
            <button
              className="chip"
              type="button"
              disabled={clearingAll}
              onClick={() => void handleClearAllMy()}
              style={{ color: 'var(--danger)' }}
            >
              <Trash2 size={14} />清除全部
            </button>
          )}
        </div>
        <div className="admin-notice" style={{ marginBottom: '12px' }}>
          <Shield size={14} />
          <span>
            此处仅显示您的个人数据（文件存储）。工具的共享数据库数据由管理员在「工具数据清理」中管理。
            总占用：<strong>{myStorage ? formatBytes(myStorage.totalBytes) : '加载中...'}</strong>
          </span>
        </div>
        <div className="compact-list">
          {myStorage?.tools.map((tool) => (
            <div className="user-row" key={tool.toolId}>
              <span>
                <strong>{tool.toolName}</strong>
                <small>{tool.toolId} · {formatBytes(tool.bytes)}</small>
              </span>
              <button
                className="chip"
                type="button"
                disabled={clearingTool === tool.toolId || tool.bytes === 0}
                onClick={() => void handleClearMyTool(tool.toolId, tool.toolName)}
                style={tool.bytes === 0 ? { opacity: 0.5 } : { color: 'var(--danger)' }}
              >
                <Trash2 size={14} />清除
              </button>
            </div>
          ))}
          {myStorage && myStorage.tools.length === 0 && (
            <div className="muted" style={{ padding: '12px 4px' }}>暂无工具数据</div>
          )}
        </div>
      </section>
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
  const [expandedTool, setExpandedTool] = useState<string | null>(null);
  const [expandedUser, setExpandedUser] = useState<string | null>(null);
  const [clearing, setClearing] = useState<string | null>(null);

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

  function matrixForTool(toolId: string) {
    return usage?.users
      .map((u) => ({
        user: u,
        bytes: usage.matrix.find((m) => m.userId === u.userId && m.toolId === toolId)?.bytes ?? 0,
      }))
      .filter((e) => e.bytes > 0)
      .sort((a, b) => b.bytes - a.bytes) ?? [];
  }

  function matrixForUser(userId: string) {
    return usage?.tools
      .map((t) => ({
        tool: t,
        bytes: usage.matrix.find((m) => m.userId === userId && m.toolId === t.toolId)?.bytes ?? 0,
      }))
      .filter((e) => e.bytes > 0)
      .sort((a, b) => b.bytes - a.bytes) ?? [];
  }

  async function handleClearTool(toolId: string, toolName: string) {
    if (!window.confirm(`确认清除「${toolName}」的所有数据（数据库表+所有用户文件+共享文件）？该操作不可恢复。`)) return;
    setClearing(`tool-${toolId}`);
    try {
      const result = await clearToolStorage(toolId);
      onSuccess(`已清理 ${toolName}：删除 ${result.droppedTables.length} 张表、${result.removedPaths.length} 个目录/文件`);
      await loadUsage();
    } catch (err) {
      onError(err, '清理工具数据失败');
    } finally {
      setClearing(null);
    }
  }

  async function handleClearUserTool(toolId: string, userId: string, toolName: string, userName: string) {
    if (!window.confirm(`确认清除用户「${userName}」在工具「${toolName}」中的数据？`)) return;
    setClearing(`ut-${toolId}-${userId}`);
    try {
      const result = await clearUserToolStorage(toolId, userId);
      onSuccess(`已清理：删除 ${result.removedPaths.length} 个目录/文件`);
      await loadUsage();
    } catch (err) {
      onError(err, '清理数据失败');
    } finally {
      setClearing(null);
    }
  }

  async function handleClearUser(userId: string, userName: string) {
    if (!window.confirm(`确认清除用户「${userName}」在所有工具中的个人数据？`)) return;
    setClearing(`user-${userId}`);
    try {
      const result = await clearUserStorage(userId);
      onSuccess(`已清理 ${userName} 的所有数据：删除 ${result.removedPaths.length} 个目录/文件`);
      await loadUsage();
    } catch (err) {
      onError(err, '清理用户数据失败');
    } finally {
      setClearing(null);
    }
  }

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

      {/* ── By Tool ── */}
      <section className="panel">
        <div className="result-header">
          <span><Wrench size={17} />按工具清理</span>
        </div>
        <div className="compact-list">
          {usage?.tools.map((tool) => {
            const userEntries = matrixForTool(tool.toolId);
            const isOpen = expandedTool === tool.toolId;
            return (
              <div key={tool.toolId} className="storage-row" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', width: '100%' }}>
                  <span style={{ minWidth: 0 }}>
                    <strong>{tool.toolName}</strong>
                    <small>
                      {tool.toolId} · 总计 {formatBytes(tool.totalBytes)}
                      {tool.sharedBytes > 0 && ` · 共享 ${formatBytes(tool.sharedBytes)}`}
                      {tool.userBytes > 0 && ` · 用户 ${formatBytes(tool.userBytes)}`}
                    </small>
                  </span>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
                    {userEntries.length > 0 && (
                      <button
                        className="chip"
                        type="button"
                        onClick={() => setExpandedTool(isOpen ? null : tool.toolId)}
                      >
                        {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        {userEntries.length} 位用户
                      </button>
                    )}
                    <button
                      className="chip"
                      type="button"
                      disabled={clearing === `tool-${tool.toolId}`}
                      onClick={() => void handleClearTool(tool.toolId, tool.toolName)}
                      style={{ color: 'var(--danger)' }}
                    >
                      <Trash2 size={14} />清除全部
                    </button>
                  </div>
                </div>
                {isOpen && userEntries.length > 0 && (
                  <div className="storage-sublist">
                    {userEntries.map((entry) => (
                      <div className="user-row" key={entry.user.userId} style={{ padding: '6px 10px' }}>
                        <span>
                          <strong>{entry.user.displayName}</strong>
                          <small>{entry.user.username} · {formatBytes(entry.bytes)}</small>
                        </span>
                        <button
                          className="chip"
                          type="button"
                          disabled={clearing === `ut-${tool.toolId}-${entry.user.userId}`}
                          onClick={() => void handleClearUserTool(tool.toolId, entry.user.userId, tool.toolName, entry.user.displayName)}
                          style={{ color: 'var(--danger)' }}
                        >
                          <Trash2 size={14} />清除
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {/* ── By User ── */}
      <section className="panel">
        <div className="result-header">
          <span><Users size={17} />按用户清理</span>
        </div>
        <div className="compact-list">
          {usage?.users.filter((u) => u.totalBytes > 0).map((user) => {
            const toolEntries = matrixForUser(user.userId);
            const isOpen = expandedUser === user.userId;
            return (
              <div key={user.userId} className="storage-row" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', width: '100%' }}>
                  <span style={{ minWidth: 0 }}>
                    <strong>{user.displayName}</strong>
                    <small>{user.username} · {formatBytes(user.totalBytes)}</small>
                  </span>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexShrink: 0 }}>
                    {toolEntries.length > 0 && (
                      <button
                        className="chip"
                        type="button"
                        onClick={() => setExpandedUser(isOpen ? null : user.userId)}
                      >
                        {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        {toolEntries.length} 个工具
                      </button>
                    )}
                    <button
                      className="chip"
                      type="button"
                      disabled={clearing === `user-${user.userId}`}
                      onClick={() => void handleClearUser(user.userId, user.displayName)}
                      style={{ color: 'var(--danger)' }}
                    >
                      <Trash2 size={14} />清除全部
                    </button>
                  </div>
                </div>
                {isOpen && toolEntries.length > 0 && (
                  <div className="storage-sublist">
                    {toolEntries.map((entry) => (
                      <div className="user-row" key={entry.tool.toolId} style={{ padding: '6px 10px' }}>
                        <span>
                          <strong>{entry.tool.toolName}</strong>
                          <small>{entry.tool.toolId} · {formatBytes(entry.bytes)}</small>
                        </span>
                        <button
                          className="chip"
                          type="button"
                          disabled={clearing === `ut-${entry.tool.toolId}-${user.userId}`}
                          onClick={() => void handleClearUserTool(entry.tool.toolId, user.userId, entry.tool.toolName, user.displayName)}
                          style={{ color: 'var(--danger)' }}
                        >
                          <Trash2 size={14} />清除
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          {usage && usage.users.filter((u) => u.totalBytes > 0).length === 0 && (
            <div className="muted" style={{ padding: '12px 4px' }}>暂无用户数据</div>
          )}
        </div>
      </section>
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
  );
}
