// ============================================================
// Admin Panel — Docker Manager
// (AdminServersPanel + AdminTemplatesPanel)
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import {
  Box,
  CheckCircle,
  ClipboardList,
  Cpu,
  Database,
  FileText,
  HardDrive,
  Image,
  Info,
  Pencil,
  Plus,
  RefreshCw,
  ScanLine,
  Server,
  Settings,
  Shield,
  Trash2,
  Users,
} from 'lucide-react';
import { apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { Alert, Field, Modal, ResourceLoadingWrapper, SkeletonRows, Spin, TruncText } from './components';
import { API, containerStateClass, renderMarkdown, useErrorMsg } from './utils';
import {
  DEFAULT_PERMS,
  type DmServer,
  type ResourceRoles,
  type ServerPermEntry,
  type ServerResources,
  type Template,
  type TemplateDetail,
  type UserPerms,
} from './types';

// ============================================================
// PermCheck — 权限勾选框 + Tooltip 气泡提示
// ============================================================

/** 内联 CSS Tooltip：鼠标悬停在 Info 图标上时显示气泡说明 */
function PermCheck({
  checked,
  onChange,
  label,
  tooltip,
  disabled = false,
}: {
  checked: boolean;
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
  label: React.ReactNode;
  tooltip: string;
  disabled?: boolean;
}) {
  return (
    <label
      className={`dm-form-check${disabled ? ' disabled' : ''}`}
    >
      <input type="checkbox" checked={checked} onChange={onChange} disabled={disabled} />
      <span style={{ color: disabled ? '#94a3b8' : undefined, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        {label}
        <span
          style={{
            position: 'relative',
            display: 'inline-flex',
            cursor: 'help',
          }}
          className="dm-tooltip-trigger"
        >
          <Info size={13} style={{ color: '#94a3b8', flexShrink: 0 }} />
          <span
            className="dm-tooltip-content"
            style={{
              display: 'none',
              position: 'absolute',
              bottom: 'calc(100% + 6px)',
              left: '50%',
              transform: 'translateX(-50%)',
              background: '#1e293b',
              color: '#f1f5f9',
              padding: '6px 10px',
              borderRadius: 6,
              fontSize: 12,
              lineHeight: 1.5,
              whiteSpace: 'normal',
              width: 260,
              textAlign: 'left',
              zIndex: 9999,
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
              pointerEvents: 'none',
            }}
          >
            {tooltip}
            <span
              style={{
                position: 'absolute',
                top: '100%',
                left: '50%',
                transform: 'translateX(-50%)',
                border: '5px solid transparent',
                borderTopColor: '#1e293b',
              }}
            />
          </span>
        </span>
      </span>
    </label>
  );
}

// ============================================================
// CapsuleSwitch — 胶囊开关（权限区块主开关）
// ============================================================

function CapsuleSwitch({
  checked,
  onChange,
  label,
  tooltip,
  disabled = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: React.ReactNode;
  tooltip?: string;
  disabled?: boolean;
}) {
  return (
    <label className={`dm-capsule-switch${disabled ? ' disabled' : ''}`}>
      <span className="dm-capsule-switch-label">
        {label}
        {tooltip && (
          <span
            className="dm-tooltip-trigger"
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
            style={{ position: 'relative', display: 'inline-flex', cursor: 'help', marginLeft: 4 }}
          >
            <Info size={13} style={{ color: '#94a3b8', flexShrink: 0 }} />
            <span
              className="dm-tooltip-content"
              style={{
                display: 'none',
                position: 'absolute',
                bottom: 'calc(100% + 6px)',
                right: 0,
                background: '#1e293b',
                color: '#f1f5f9',
                padding: '6px 10px',
                borderRadius: 6,
                fontSize: 12,
                lineHeight: 1.5,
                whiteSpace: 'normal',
                width: 260,
                textAlign: 'left',
                zIndex: 9999,
                boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                pointerEvents: 'none',
              }}
            >
              {tooltip}
              <span
                style={{
                  position: 'absolute',
                  top: '100%',
                  right: 12,
                  border: '5px solid transparent',
                  borderTopColor: '#1e293b',
                }}
              />
            </span>
          </span>
        )}
      </span>
      <input
        type="checkbox"
        className="dm-capsule-switch-input"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className={`dm-capsule-switch-track${checked ? ' on' : ''}`}>
        <span className="dm-capsule-switch-knob" />
      </span>
    </label>
  );
}

// ============================================================
// AdminServersPanel
// ============================================================

export function AdminServersPanel({ onRefresh }: { onRefresh: () => void }) {
  const [servers, setServers] = useState<DmServer[]>([]);
  const [users, setUsers] = useState<ServerPermEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState({ name: '', host: '', port: '22', sshUsername: '', sshPassword: '' });
  const [adding, setAdding] = useState(false);
  const [addMsg, setAddMsg] = useState('');

  // ─── 服务器连接状态 ──────────────────────────────────
  const [serverStatuses, setServerStatuses] = useState<Record<string, 'online' | 'offline'>>({});
  const [statusLoading, setStatusLoading] = useState(false);

  // ─── 合并管理面板 ────────────────────────────────────────
  // panelServer: 当前打开了合并管理面板的服务器
  // panelTab: 当前面板显示哪个视图 'perms'=权限配置  'resources'=资源角色管理
  const [panelServer, setPanelServer] = useState<DmServer | null>(null);
  const [panelTab, setPanelTab] = useState<'perms' | 'resources'>('perms');
  const [panelLoading, setPanelLoading] = useState(false);

  // ─── 权限列表 ─────────────────────────────────────────────
  const [perms, setPerms] = useState<ServerPermEntry[]>([]);
  // 细粒度权限编辑弹窗
  const [permsEditTarget, setPermsEditTarget] = useState<ServerPermEntry | null>(null);
  const [permsForm, setPermsForm] = useState<UserPerms>(DEFAULT_PERMS);
  const [permsPathStr, setPermsPathStr] = useState('');
  const [savingPerms, setSavingPerms] = useState(false);

  // ─── 资源角色管理 ──────────────────────────────────────────
  const [resources, setResources] = useState<ServerResources | null>(null);
  const [resourcesLoading, setResourcesLoading] = useState(false);
  const [resourceTab, setResourceTab] = useState<'containers' | 'images' | 'volumes'>('containers');
  const [assignError, setAssignError, clearAssignError] = useErrorMsg();
  const [assignSuccess, setAssignSuccess] = useState<string | null>(null);

  // 角色分配弹窗状态
  type AssignRolesTarget = { resourceType: string; resourceRef: string; label: string; currentRoles: ResourceRoles };
  const [assignRolesTarget, setAssignRolesTarget] = useState<AssignRolesTarget | null>(null);
  const [assignOwnerIds, setAssignOwnerIds] = useState<string[]>([]);
  const [assignViewerIds, setAssignViewerIds] = useState<string[]>([]);
  const [assignQuotaHolderIds, setAssignQuotaHolderIds] = useState<string[]>([]);
  const [savingRoles, setSavingRoles] = useState(false);

  // CUDA 重新扫描
  const [scanningCuda, setScanningCuda] = useState<string | null>(null);

  // ─── 加载服务器资源 ───────────────────────────────────────
  const loadResources = useCallback(async (serverId: string) => {
    setResourcesLoading(true);
    clearAssignError();
    try {
      const r = await apiGet<ServerResources>(`${API}/servers/${serverId}/resources`);
      setResources(r);
    } catch (e) {
      setAssignError(e);
    } finally {
      setResourcesLoading(false);
    }
  }, [clearAssignError, setAssignError]);

  // ─── 加载权限列表 ─────────────────────────────────────────
  const loadPerms = useCallback(async (serverId: string) => {
    try {
      const r = await apiGet<{ permissions: ServerPermEntry[] }>(`${API}/servers/${serverId}/permissions`);
      setPerms(r.permissions);
      setUsers(r.permissions);
    } catch (e) {
      setError(e);
    }
  }, [setError]);

  // ─── 打开合并管理面板 ─────────────────────────────────────
  async function openPanel(s: DmServer, defaultTab: 'perms' | 'resources' = 'perms') {
    setPanelServer(s);
    setPanelTab(defaultTab);
    setResources(null);
    setAssignSuccess(null);
    clearAssignError();
    clearError();
    setResourceTab('containers');
    setPanelLoading(true);

    // 同时加载权限列表和资源列表
    await Promise.allSettled([
      loadPerms(s.id),
      loadResources(s.id),
    ]);
    setPanelLoading(false);
  }

  // ─── 切换面板视图 ─────────────────────────────────────────
  async function switchPanelTab(tab: 'perms' | 'resources') {
    setPanelTab(tab);
    if (!panelServer) return;
    if (tab === 'perms' && perms.length === 0) {
      await loadPerms(panelServer.id);
    } else if (tab === 'resources' && !resources) {
      await loadResources(panelServer.id);
    }
  }

  // ─── 关闭合并面板 ─────────────────────────────────────────
  function closePanel() {
    setPanelServer(null);
    setResources(null);
    setPerms([]);
    setPermsEditTarget(null);
    clearError();
    clearAssignError();
    setAssignSuccess(null);
  }

  // ─── 资源角色分配 ─────────────────────────────────────────
  function openAssignRoles(resourceType: string, resourceRef: string, label: string, currentRoles: ResourceRoles) {
    setAssignRolesTarget({ resourceType, resourceRef, label, currentRoles });
    setAssignOwnerIds(currentRoles.ownerUserIds ?? []);
    setAssignViewerIds(currentRoles.viewerUserIds ?? []);
    setAssignQuotaHolderIds(currentRoles.quotaHolderUserIds ?? []);
    clearAssignError();
  }

  async function doAssignRoles() {
    if (!panelServer || !assignRolesTarget) return;
    setSavingRoles(true);
    clearAssignError();
    setAssignSuccess(null);
    try {
      await apiPut(`${API}/servers/${panelServer.id}/resource-roles`, {
        resourceType: assignRolesTarget.resourceType,
        resourceRef: assignRolesTarget.resourceRef,
        ownerUserIds: assignOwnerIds,
        viewerUserIds: assignViewerIds,
        quotaHolderUserIds: assignQuotaHolderIds,
      });
      setAssignSuccess(`「${assignRolesTarget.label}」角色分配已更新`);
      setAssignRolesTarget(null);
      void loadResources(panelServer.id);
    } catch (e) {
      setAssignError(e);
    } finally {
      setSavingRoles(false);
    }
  }

  // ─── 服务器列表加载 ───────────────────────────────────────
  const load = useCallback(async () => {
    setLoading(true);
    clearError();
    try {
      const r = await apiGet<{ servers: DmServer[] }>(`${API}/servers`);
      setServers(r.servers);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { void load(); }, [load]);

  // 加载服务器连接状态
  const loadStatuses = useCallback(async () => {
    setStatusLoading(true);
    try {
      const r = await apiGet<{ statuses: Record<string, 'online' | 'offline'> }>(`${API}/servers/status`);
      setServerStatuses(r.statuses);
    } catch {
      // 静默失败
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => { void loadStatuses(); }, [loadStatuses]);

  const af = (k: keyof typeof addForm) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setAddForm((p) => ({ ...p, [k]: e.target.value }));

  async function doAdd() {
    setAdding(true);
    setAddMsg('');
    clearError();
    try {
      await apiPost(`${API}/servers`, { ...addForm, port: parseInt(addForm.port) || 22 });
      setAddMsg('服务器添加成功！');
      setAddForm({ name: '', host: '', port: '22', sshUsername: '', sshPassword: '' });
      void load();
      onRefresh();
      setTimeout(() => { setShowAdd(false); setAddMsg(''); }, 1200);
    } catch (e) {
      setError(e);
    } finally {
      setAdding(false);
    }
  }

  async function doDelete(id: string, name: string) {
    if (!confirm(`确定删除服务器 ${name}？此操作将同时删除该服务器的权限和配额记录。`)) return;
    clearError();
    try {
      await apiDelete(`${API}/servers/${id}`);
      void load();
      onRefresh();
    } catch (e) {
      setError(e);
    }
  }

  async function doRescanCuda(id: string) {
    setScanningCuda(id);
    clearError();
    try {
      const r = await apiPost<{ server: DmServer }>(`${API}/servers/${id}/rescan-cuda`, {});
      setServers((prev) => prev.map((s) => s.id === id ? { ...s, ...r.server } : s));
      onRefresh();
    } catch (e) {
      setError(e);
    } finally {
      setScanningCuda(null);
    }
  }

  // ─── 权限编辑 ─────────────────────────────────────────────
  function openPermsEdit(entry: ServerPermEntry) {
    setPermsEditTarget(entry);
    const p = entry.perms ?? DEFAULT_PERMS;
    setPermsForm({ ...DEFAULT_PERMS, ...p });
    setPermsPathStr((p.ctr_path_whitelist ?? []).join('\n'));
    clearError();
  }

  function pf(k: keyof UserPerms) {
    return (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.type === 'checkbox' ? e.target.checked : parseFloat(e.target.value) || 0;
      setPermsForm((prev) => ({ ...prev, [k]: val }));
    };
  }

  async function savePerms() {
    if (!permsEditTarget || !panelServer) return;
    setSavingPerms(true);
    clearError();
    try {
      const pathList = permsPathStr.split('\n').map((s) => s.trim()).filter(Boolean);
      await apiPut(`${API}/servers/${panelServer.id}/user-perms`, {
        userId: permsEditTarget.userId,
        ...permsForm,
        ctr_path_whitelist: pathList,
      });
      setPermsEditTarget(null);
      await loadPerms(panelServer.id);
    } catch (e) {
      setError(e);
    } finally {
      setSavingPerms(false);
    }
  }

  // ─── 权限预设 ─────────────────────────────────────────────
  // 新权限模型：
  //   none  = 无任何权限
  //   view  = 能看到服务器、查看资源（使用 img_use/ctr_use/vol_use）
  //   use   = 能查看 + 创建资源、使用模板
  //   manage= 全部权限
  function applyPreset(preset: 'none' | 'view' | 'use' | 'manage') {
    const none = { ...DEFAULT_PERMS };
    if (preset === 'none') { setPermsForm(none); return; }
    if (preset === 'view') {
      setPermsForm({
        ...none,
        server_visible: true,
        img_use: true,
        ctr_use: true,
        vol_use: true,
      });
      return;
    }
    if (preset === 'use') {
      setPermsForm({
        ...none,
        server_visible: true,
        // 镜像：可查看和拉取
        img_use: true, img_pull: true,
        // 容器：可查看、创建
        ctr_use: true,
        ctr_create: true, ctr_create_template: true,
        // 卷：可查看、创建、复制
        vol_use: true, vol_create: true, vol_copy: true,
        // 模板：可使用
        tpl_use: true,
      });
      return;
    }
    if (preset === 'manage') {
      setPermsForm({
        server_visible: true,
        // 镜像：全权限
        img_use: true, img_pull: true, img_view_all: true, img_manage_all: true, img_copy: true, img_quota_gb: 0,
        // 容器：全权限
        ctr_use: true, ctr_view_all: true,
        ctr_manage_all: true,
        ctr_create: true, ctr_create_template: true,
        ctr_path_whitelist: [], ctr_quota_num: 0,
        // 卷：全权限
        vol_use: true, vol_create: true, vol_delete_all: true, vol_copy: true, vol_quota_gb: 0,
        // 模板：全权限
        tpl_use: true, tpl_create: true, tpl_edit: true,
        // CUDA：不在预设中设置（保留当前值）
        cuda_gpu_indices: permsForm.cuda_gpu_indices,
      });
    }
  }

  // ─── 辅助：根据细粒度权限生成摘要显示 ────────────────────
  function getPermSummary(entry: ServerPermEntry): { color: string; bg: string; label: string } {
    if (entry.role === 'admin') {
      return { color: '#166534', bg: '#dcfce7', label: '管理员' };
    }
    const p = entry.perms;
    if (!p.server_visible) {
      return { color: '#6b7280', bg: '#f3f4f6', label: '无权限' };
    }
    const manageFlags = [p.img_manage_all, p.ctr_manage_all, p.vol_delete_all, p.tpl_edit];
    const useFlags = [p.img_pull, p.img_copy, p.ctr_create, p.ctr_create_template, p.vol_create, p.vol_copy, p.tpl_create];
    const viewFlags = [p.img_use, p.img_view_all, p.ctr_use, p.ctr_view_all, p.vol_use, p.tpl_use];
    if (manageFlags.some(Boolean)) {
      return { color: '#166534', bg: '#dcfce7', label: '管理' };
    }
    if (useFlags.some(Boolean)) {
      return { color: '#1e40af', bg: '#dbeafe', label: '使用' };
    }
    if (viewFlags.some(Boolean)) {
      return { color: '#92400e', bg: '#fef3c7', label: '可见' };
    }
    return { color: '#6b7280', bg: '#f3f4f6', label: '仅可见' };
  }

  return (
    <div style={{ display: 'grid', gap: 20 }}>
      {error && <Alert type="error">{error}</Alert>}

      {/* 顶部工具栏 */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <button className="btn btn-primary" onClick={() => { setShowAdd(true); setAddMsg(''); clearError(); }}>
          <Plus size={14} /> 添加服务器
        </button>
        <button className="btn" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} /> 刷新
        </button>
        <button className="btn" onClick={() => void loadStatuses()} disabled={statusLoading} style={{ marginLeft: 'auto' }}>
          {statusLoading ? <Spin /> : <RefreshCw size={14} />} 刷新连接状态
        </button>
      </div>

      {/* Server List */}
      {loading ? <div className="dm-empty"><Spin /> 加载中…</div> :
       servers.length === 0 ? <div className="dm-empty"><Server size={32} /> 暂无服务器</div> : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '1.5fr 1.5fr 0.8fr 1fr 0.8fr auto' }}>
            <span>服务器</span><span>地址</span><span>状态</span><span>GPU</span><span>添加时间</span><span>操作</span>
          </div>
          {servers.map((s) => (
            <div key={s.id} className="dm-table-row" style={{ gridTemplateColumns: '1.5fr 1.5fr 0.8fr 1fr 0.8fr auto' }}>
              <span style={{ fontWeight: 600 }}>{s.name}</span>
              <span style={{ color: '#526071', fontFamily: 'monospace', fontSize: 13 }}>{s.host}:{s.port}</span>
              <span>
                {statusLoading && !serverStatuses[s.id] ? (
                  <span className="dm-status-badge checking"><span className="dm-status-dot" />检测中</span>
                ) : serverStatuses[s.id] === 'online' ? (
                  <span className="dm-status-badge online"><span className="dm-status-dot" />在线</span>
                ) : (
                  <span className="dm-status-badge offline"><span className="dm-status-dot" />离线</span>
                )}
              </span>
              <span>
                {s.cudaAvailable
                  ? <span className="dm-cuda-badge"><Cpu size={11} /> {s.gpuCount ?? 0} GPU</span>
                  : <span style={{ color: '#94a3b8', fontSize: 12 }}>无</span>
                }
              </span>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>{s.createdAt.slice(0, 10)}</span>
              <span style={{ display: 'flex', gap: 4 }}>
                {/* 合并管理按钮：默认打开权限配置视图 */}
                <button className="dm-btn-icon" title="权限 & 资源管理" onClick={() => void openPanel(s, 'perms')}>
                  <Settings size={13} />
                </button>
                <button className="dm-btn-icon" title="重新扫描 CUDA" onClick={() => void doRescanCuda(s.id)} disabled={scanningCuda === s.id}>
                  {scanningCuda === s.id ? <Spin /> : <ScanLine size={13} />}
                </button>
                <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(s.id, s.name)}><Trash2 size={13} /></button>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ============================================================
          合并管理面板 Modal（权限配置 + 资源角色管理）
      ============================================================ */}
      {panelServer && !permsEditTarget && !assignRolesTarget && (
        <Modal
          title={`服务器管理 — ${panelServer.name}`}
          onClose={closePanel}
          wide
          foot={<button className="btn" onClick={closePanel}>关闭</button>}
        >
          {/* 切换按钮 */}
          <div style={{
            display: 'flex',
            gap: 0,
            marginBottom: 18,
            border: '1px solid #e2e8f0',
            borderRadius: 8,
            overflow: 'hidden',
            width: 'fit-content',
          }}>
            <button
              onClick={() => void switchPanelTab('perms')}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '7px 18px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer',
                background: panelTab === 'perms' ? '#2563eb' : '#f8fafc',
                color: panelTab === 'perms' ? '#fff' : '#526071',
                transition: 'all 0.15s',
              }}
            >
              <Shield size={13} /> 权限配置
            </button>
            <button
              onClick={() => void switchPanelTab('resources')}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '7px 18px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer',
                borderLeft: '1px solid #e2e8f0',
                background: panelTab === 'resources' ? '#2563eb' : '#f8fafc',
                color: panelTab === 'resources' ? '#fff' : '#526071',
                transition: 'all 0.15s',
              }}
            >
              <Database size={13} /> 资源角色管理
            </button>
          </div>

          {/* ── 权限配置视图 ── */}
          {panelTab === 'perms' && (
            <div>
              {error && <Alert type="error">{error}</Alert>}
              {panelLoading ? (
                <div className="dm-empty"><Spin /></div>
              ) : (
                <div className="dm-perm-table">
                  {perms.length === 0 && (
                    <div className="dm-empty" style={{ padding: '24px 0' }}>
                      <Users size={24} /> 暂无用户权限记录
                    </div>
                  )}
                  {perms.map((p) => {
                    const { color, bg, label } = getPermSummary(p);
                    return (
                      <div key={p.userId} className="dm-perm-row" style={{ alignItems: 'center' }}>
                        <span style={{ flex: 1, minWidth: 0 }}>
                          <strong>{p.displayName}</strong>
                          <small style={{ color: '#94a3b8', marginLeft: 6 }}>@{p.username}</small>
                          {p.role === 'admin' && (
                            <span style={{ marginLeft: 6, fontSize: 11, background: '#fef3c7', color: '#92400e', padding: '1px 5px', borderRadius: 4 }}>系统管理员</span>
                          )}
                        </span>
                        <span style={{
                          fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600,
                          color, background: bg,
                        }}>
                          {label}
                        </span>
                        <button
                          className="btn"
                          style={{ fontSize: 12 }}
                          disabled={p.role === 'admin'}
                          onClick={() => openPermsEdit(p)}
                        >
                          <Shield size={12} /> 编辑权限
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* ── 资源角色管理视图 ── */}
          {panelTab === 'resources' && (
            <div>
              {assignError && <Alert type="error">{assignError}</Alert>}
              {assignSuccess && <Alert type="success">{assignSuccess}</Alert>}

              {/* 资源类型标签页 */}
              <div style={{ display: 'flex', gap: 4, marginBottom: 14 }}>
                {(['containers', 'images', 'volumes'] as const).map((tab) => (
                  <button
                    key={tab}
                    className={`btn${resourceTab === tab ? ' btn-primary' : ''}`}
                    style={{ fontSize: 12 }}
                    onClick={() => setResourceTab(tab)}
                  >
                    {tab === 'containers' ? <><Box size={12} /> 容器</> : tab === 'images' ? <><Image size={12} /> 镜像</> : <><HardDrive size={12} /> 卷</>}
                  </button>
                ))}
                <button
                  className="btn"
                  style={{ fontSize: 12, marginLeft: 'auto' }}
                  onClick={() => panelServer && loadResources(panelServer.id)}
                  disabled={resourcesLoading}
                >
                  <RefreshCw size={12} /> 刷新
                </button>
              </div>

              {resourcesLoading && !resources ? (
                <div style={{ display: 'grid', gap: 12 }}>
                  <div style={{ display: 'flex', gap: 8 }}>
                    {[70, 60, 50].map((w, i) => (
                      <div key={i} className="dm-skeleton-cell" style={{ width: w, height: 30, borderRadius: 6 }} />
                    ))}
                  </div>
                  <SkeletonRows cols={['1.5fr', '2fr', '1fr', '1fr', '80px']} rows={6} />
                </div>
              ) : !resources ? (
                <div className="dm-empty"><Server size={24} /> 暂无数据</div>
              ) : resourceTab === 'containers' ? (
                <ResourceLoadingWrapper loading={resourcesLoading}>
                  {resources.containers.length === 0 ? (
                    <div className="dm-empty"><Box size={24} /> 暂无容器</div>
                  ) : (
                    <div className="dm-table">
                      <div className="dm-table-header" style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr 1.5fr 1.5fr auto' }}>
                        <span>名称</span><span>镜像</span><span>状态</span><span>创建者</span><span>所有者</span><span>操作</span>
                      </div>
                      {resources.containers.map((ctr) => {
                        const cname = (ctr.Names ?? '').replace(/^\//, '');
                        const ref = (cname || ctr.ID) ?? '';
                        const creator = users.find((u) => u.userId === ctr.creatorUserId);
                        const ownerList = (ctr.ownerUserIds ?? []).map((id) => users.find((u) => u.userId === id)).filter(Boolean) as ServerPermEntry[];
                        return (
                          <div key={ref} className="dm-table-row" style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr 1.5fr 1.5fr auto' }}>
                            <span style={{ fontFamily: 'monospace', fontSize: 13, minWidth: 0 }}><TruncText text={ref} /></span>
                            <span style={{ fontSize: 12, color: '#526071', minWidth: 0 }}><TruncText text={ctr.Image ?? ''} /></span>
                            <span>
                              <span className={`dm-status ${containerStateClass(ctr.State ?? ctr.Status)}`}>
                                <span className="dm-status-dot" />
                                {ctr.State ?? ctr.Status}
                              </span>
                            </span>
                            <span>
                              {creator
                                ? <span className="dm-role-tag creator">{creator.displayName}</span>
                                : <span style={{ fontSize: 12, color: '#94a3b8' }}>平台外</span>}
                            </span>
                            <span style={{ display: 'flex', flexWrap: 'wrap', gap: 3, alignItems: 'center' }}>
                              {ownerList.length > 0
                                ? ownerList.map((u) => <span key={u.userId} className="dm-role-tag owner">{u.displayName}</span>)
                                : <span style={{ fontSize: 12, color: '#94a3b8' }}>未分配</span>}
                            </span>
                            <span>
                              <button
                                className="btn"
                                style={{ fontSize: 11, padding: '3px 8px' }}
                                onClick={() => openAssignRoles('container', ref, ref, ctr)}
                              >
                                <Users size={11} /> 管理
                              </button>
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </ResourceLoadingWrapper>
              ) : resourceTab === 'images' ? (
                <ResourceLoadingWrapper loading={resourcesLoading}>
                  {resources.images.length === 0 ? (
                    <div className="dm-empty"><Image size={24} /> 暂无镜像</div>
                  ) : (
                    <div className="dm-table">
                      <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 1fr 1.5fr 1.5fr auto' }}>
                        <span>仓库</span><span>标签</span><span>大小</span><span>创建者</span><span>所有者</span><span>操作</span>
                      </div>
                      {resources.images.map((img) => {
                        const ref = `${img.repo}:${img.tag}`;
                        const creator = users.find((u) => u.userId === img.creatorUserId);
                        const ownerList = (img.ownerUserIds ?? []).map((id) => users.find((u) => u.userId === id)).filter(Boolean) as ServerPermEntry[];
                        return (
                          <div key={img.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1fr 1fr 1.5fr 1.5fr auto' }}>
                            <span style={{ fontFamily: 'monospace', fontSize: 12, minWidth: 0 }}><TruncText text={img.repo} /></span>
                            <span><code style={{ background: '#f1f5f9', padding: '2px 5px', borderRadius: 3, fontSize: 12 }}>{img.tag}</code></span>
                            <span style={{ fontSize: 12, color: '#526071' }}>{img.size}</span>
                            <span>
                              {creator
                                ? <span className="dm-role-tag creator">{creator.displayName}</span>
                                : <span style={{ fontSize: 12, color: '#94a3b8' }}>平台外</span>}
                            </span>
                            <span style={{ display: 'flex', flexWrap: 'wrap', gap: 3, alignItems: 'center' }}>
                              {ownerList.length > 0
                                ? ownerList.map((u) => <span key={u.userId} className="dm-role-tag owner">{u.displayName}</span>)
                                : <span style={{ fontSize: 12, color: '#94a3b8' }}>未分配</span>}
                            </span>
                            <span>
                              <button
                                className="btn"
                                style={{ fontSize: 11, padding: '3px 8px' }}
                                onClick={() => openAssignRoles('image', ref, ref, img)}
                              >
                                <Users size={11} /> 管理
                              </button>
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </ResourceLoadingWrapper>
              ) : (
                <ResourceLoadingWrapper loading={resourcesLoading}>
                  {resources.volumes.length === 0 ? (
                    <div className="dm-empty"><HardDrive size={24} /> 暂无卷</div>
                  ) : (
                    <div className="dm-table">
                      <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1.5fr 1.5fr auto' }}>
                        <span>卷名称</span><span>创建者</span><span>所有者</span><span>操作</span>
                      </div>
                      {resources.volumes.map((vol) => {
                        const creator = users.find((u) => u.userId === vol.creatorUserId);
                        const ownerList = (vol.ownerUserIds ?? []).map((id) => users.find((u) => u.userId === id)).filter(Boolean) as ServerPermEntry[];
                        return (
                          <div key={vol.name} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1.5fr 1.5fr auto' }}>
                            <span style={{ fontFamily: 'monospace', fontSize: 13, minWidth: 0 }}><TruncText text={vol.name} /></span>
                            <span>
                              {creator
                                ? <span className="dm-role-tag creator">{creator.displayName}</span>
                                : <span style={{ fontSize: 12, color: '#94a3b8' }}>平台外</span>}
                            </span>
                            <span style={{ display: 'flex', flexWrap: 'wrap', gap: 3, alignItems: 'center' }}>
                              {ownerList.length > 0
                                ? ownerList.map((u) => <span key={u.userId} className="dm-role-tag owner">{u.displayName}</span>)
                                : <span style={{ fontSize: 12, color: '#94a3b8' }}>未分配</span>}
                            </span>
                            <span>
                              <button
                                className="btn"
                                style={{ fontSize: 11, padding: '3px 8px' }}
                                onClick={() => openAssignRoles('volume', vol.name, vol.name, vol)}
                              >
                                <Users size={11} /> 管理
                              </button>
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </ResourceLoadingWrapper>
              )}
            </div>
          )}
        </Modal>
      )}

      {/* ============================================================
          角色分配弹窗（二级弹窗，在合并面板的资源角色视图中打开）
      ============================================================ */}
      {assignRolesTarget && (
        <Modal
          title={`角色分配 — ${assignRolesTarget.label}`}
          onClose={() => setAssignRolesTarget(null)}
          foot={
            <>
              <button className="btn" onClick={() => setAssignRolesTarget(null)}>取消</button>
              <button className="btn btn-primary" onClick={doAssignRoles} disabled={savingRoles}>
                {savingRoles ? <Spin /> : <CheckCircle size={14} />} 保存
              </button>
            </>
          }
        >
          {assignError && <Alert type="error">{assignError}</Alert>}

          {/* 角色说明提示 */}
          <div style={{
            background: '#f0f9ff', border: '1px solid #bae6fd', borderRadius: 8,
            padding: '10px 14px', marginBottom: 16, fontSize: 12, color: '#0369a1',
            display: 'grid', gap: 4,
          }}>
            <div><strong>角色说明：</strong></div>
            <div>• <strong>创建者</strong>：平台自动记录，不可修改，默认同时是所有者</div>
            <div>• <strong>所有者</strong>：拥有该资源的全部权限（查看、启停、分配查看者等）</div>
            <div>• <strong>配额占用者</strong>：必须同时是所有者，可独占或分摊资源配额</div>
            <div>• <strong>查看者</strong>：只读权限；拥有容器查看权后自动继承其挂载卷和镜像的查看权</div>
          </div>

          {/* 创建者（只读） */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Users size={13} /> 创建者（平台自动记录，不可修改）</div>
            {assignRolesTarget.currentRoles.creatorUserId ? (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}>
                {(() => {
                  const u = users.find((x) => x.userId === assignRolesTarget.currentRoles.creatorUserId);
                  return u
                    ? <span className="dm-role-tag creator">{u.displayName} <small style={{ color: '#64748b' }}>@{u.username}</small></span>
                    : <span className="dm-role-tag creator">{assignRolesTarget.currentRoles.creatorUserId}</span>;
                })()}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 6 }}>无（平台外资源）</div>
            )}
          </div>

          {/* 所有者（多选） */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <Shield size={13} /> 所有者
              <span style={{ fontSize: 11, color: '#64748b', fontWeight: 400, marginLeft: 6 }}>
                拥有该资源全部权限，可管理查看者
              </span>
            </div>
            <div className="dm-roles-checklist">
              {users.filter((u) => u.role !== 'admin').map((u) => {
                // 用户必须拥有 ctr_use/img_use/vol_use 权限才能成为对应资源的所有者
                const hasUsePerms = (() => {
                  const rtype = assignRolesTarget.resourceType;
                  const p = u.perms;
                  if (rtype === 'container') return p?.ctr_use ?? false;
                  if (rtype === 'image') return p?.img_use ?? false;
                  if (rtype === 'volume') return p?.vol_use ?? false;
                  return true;
                })();
                return (
                  <label
                    key={u.userId}
                    className={`dm-form-check${!hasUsePerms ? ' disabled' : ''}`}
                    title={!hasUsePerms ? `${u.displayName} 没有该资源类型的使用权限，无法成为所有者` : ''}
                  >
                    <input
                      type="checkbox"
                      checked={assignOwnerIds.includes(u.userId)}
                      disabled={!hasUsePerms}
                      onChange={(e) => {
                        setAssignOwnerIds((prev) =>
                          e.target.checked ? [...prev, u.userId] : prev.filter((id) => id !== u.userId)
                        );
                        if (e.target.checked) {
                          // 成为所有者时从查看者移除（互斥）
                          setAssignViewerIds((prev) => prev.filter((id) => id !== u.userId));
                        }
                      }}
                    />
                    <span>{u.displayName}</span>
                    <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                    {!hasUsePerms && (
                      <span style={{ fontSize: 11, color: '#ef4444', marginLeft: 4 }}>(无使用权)</span>
                    )}
                  </label>
                );
              })}
              {users.filter((u) => u.role !== 'admin').length === 0 && (
                <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无普通用户</div>
              )}
            </div>
          </div>

          {/* 配额占用者（多选，无需是所有者） */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <Database size={13} /> 配额占用者
              <span style={{ fontSize: 11, color: '#64748b', fontWeight: 400, marginLeft: 6 }}>
                资源大小在所有配额占用者间均分
              </span>
            </div>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8 }}>
              配额占用者无需是所有者。资源大小将被所有配额占用者平均分配占用。例如 100MB 镜像、5 个配额占用者，每人占用 20MB。
            </div>
            <div className="dm-roles-checklist">
              {users.filter((u) => u.role !== 'admin').map((u) => {
                const hasUsePerms = (() => {
                  const rtype = assignRolesTarget.resourceType;
                  const p = u.perms;
                  if (rtype === 'container') return p?.ctr_use ?? false;
                  if (rtype === 'image') return p?.img_use ?? false;
                  if (rtype === 'volume') return p?.vol_use ?? false;
                  return true;
                })();
                return (
                  <label
                    key={u.userId}
                    className={`dm-form-check${!hasUsePerms ? ' disabled' : ''}`}
                    title={!hasUsePerms ? `${u.displayName} 没有该资源类型的使用权限` : ''}
                  >
                    <input
                      type="checkbox"
                      checked={assignQuotaHolderIds.includes(u.userId)}
                      disabled={!hasUsePerms}
                      onChange={(e) => {
                        setAssignQuotaHolderIds((prev) =>
                          e.target.checked ? [...prev, u.userId] : prev.filter((id) => id !== u.userId)
                        );
                      }}
                    />
                    <span>{u.displayName}</span>
                    <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                    {!hasUsePerms && (
                      <span style={{ fontSize: 11, color: '#ef4444', marginLeft: 4 }}>(无使用权)</span>
                    )}
                  </label>
                );
              })}
              {users.filter((u) => u.role !== 'admin').length === 0 && (
                <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无普通用户</div>
              )}
            </div>
          </div>

          {/* 查看者（多选） */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <FileText size={13} /> 查看者
              <span style={{ fontSize: 11, color: '#64748b', fontWeight: 400, marginLeft: 6 }}>
                只读权限；容器查看者自动继承其挂载卷和镜像的查看权
              </span>
            </div>
            <div className="dm-roles-checklist">
              {users.filter((u) => u.role !== 'admin').map((u) => {
                // 用户必须拥有对应资源类型的 use 权限才能成为查看者
                const hasUsePerms = (() => {
                  const rtype = assignRolesTarget.resourceType;
                  const p = u.perms;
                  if (rtype === 'container') return p?.ctr_use ?? false;
                  if (rtype === 'image') return p?.img_use ?? false;
                  if (rtype === 'volume') return p?.vol_use ?? false;
                  return true;
                })();
                const isOwner = assignOwnerIds.includes(u.userId);
                return (
                  <label
                    key={u.userId}
                    className={`dm-form-check${(isOwner || !hasUsePerms) ? ' disabled' : ''}`}
                    title={
                      isOwner ? '已是所有者，无需另设查看者'
                      : !hasUsePerms ? `${u.displayName} 没有该资源类型的使用权限，无法成为查看者`
                      : ''
                    }
                  >
                    <input
                      type="checkbox"
                      checked={assignViewerIds.includes(u.userId)}
                      disabled={isOwner || !hasUsePerms}
                      onChange={(e) => {
                        setAssignViewerIds((prev) =>
                          e.target.checked ? [...prev, u.userId] : prev.filter((id) => id !== u.userId)
                        );
                      }}
                    />
                    <span>{u.displayName}</span>
                    <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                    {isOwner && (
                      <span style={{ fontSize: 11, color: '#64748b', marginLeft: 4 }}>(已是所有者)</span>
                    )}
                    {!isOwner && !hasUsePerms && (
                      <span style={{ fontSize: 11, color: '#ef4444', marginLeft: 4 }}>(无使用权)</span>
                    )}
                  </label>
                );
              })}
              {users.filter((u) => u.role !== 'admin').length === 0 && (
                <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无普通用户</div>
              )}
            </div>
          </div>
        </Modal>
      )}

      {/* ============================================================
          细粒度权限编辑弹窗（从合并面板的权限配置视图中打开）
      ============================================================ */}
      {permsEditTarget && panelServer && (
        <Modal
          title={`权限配置 — ${permsEditTarget.displayName} @ ${panelServer.name}`}
          onClose={() => setPermsEditTarget(null)}
          wide
          foot={
            <>
              <button className="btn" onClick={() => setPermsEditTarget(null)}>取消</button>
              <button className="btn btn-primary" onClick={savePerms} disabled={savingPerms}>
                {savingPerms ? <Spin /> : <CheckCircle size={14} />} 保存权限
              </button>
            </>
          }
        >
          {error && <Alert type="error">{error}</Alert>}

          {/* 权限模型说明 */}
          <div style={{
            background: '#f0f9ff', border: '1px solid #bae6fd', borderRadius: 8,
            padding: '10px 14px', marginBottom: 16, fontSize: 12, color: '#0369a1',
          }}>
            <strong>注意：</strong>以下权限控制用户在此服务器上的<strong>操作能力</strong>。资源的具体访问权由「资源角色管理」中的所有者/查看者角色控制。
            用户权限优先级高于资源角色权限——若用户拥有「管理所有容器」权限，则自动对所有容器拥有所有者级别的操作权。
          </div>

          {/* 快速预设 */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>快速预设：</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {([
                { key: 'none', label: '无权限', desc: '无任何权限' },
                { key: 'view', label: '查看', desc: '可查看资源，不可创建/操作' },
                { key: 'use', label: '使用', desc: '可查看 + 创建资源' },
                { key: 'manage', label: '管理', desc: '全部权限' },
              ] as const).map(({ key, label, desc }) => (
                <button
                  key={key}
                  className="btn"
                  style={{ fontSize: 12 }}
                  title={desc}
                  onClick={() => applyPreset(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* 服务器可见性 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <Server size={13} /> 服务器访问
              <CapsuleSwitch
                checked={permsForm.server_visible}
                onChange={(v) => setPermsForm((p) => ({ ...p, server_visible: v }))}
                label="可见该服务器"
                tooltip="用户能在服务器列表中看到此服务器，是所有功能的前提"
              />
            </div>
          </div>

          {/* 镜像权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <Image size={13} /> 镜像权限
              <CapsuleSwitch
                checked={permsForm.img_use}
                onChange={(v) => setPermsForm((p) => ({ ...p, img_use: v }))}
                label="使用镜像"
                tooltip="可查看和访问镜像列表；是资源角色「查看者」的前提条件"
              />
            </div>
            <div className="dm-perm-checks">
              <PermCheck
                checked={permsForm.img_pull}
                onChange={pf('img_pull')}
                disabled={!permsForm.img_use}
                label="拉取镜像"
                tooltip="执行 docker pull 从远程仓库拉取新镜像到此服务器"
              />
              <PermCheck
                checked={permsForm.img_manage_all}
                onChange={(e) => {
                  const checked = e.target.checked;
                  setPermsForm((prev) => ({
                    ...prev,
                    img_manage_all: checked,
                    // 勾选「管理所有」时自动勾选「查看所有」
                    img_view_all: checked ? true : prev.img_view_all,
                  }));
                }}
                disabled={!permsForm.img_use}
                label={<strong>管理所有用户的镜像</strong>}
                tooltip="可删除任意用户的镜像（执行 docker rmi）；勾选后「查看所有用户的镜像」将自动开启"
              />
              <PermCheck
                checked={permsForm.img_view_all || permsForm.img_manage_all}
                onChange={(e) => {
                  if (permsForm.img_manage_all) return; // 管理所有时不可单独关闭
                  setPermsForm((prev) => ({ ...prev, img_view_all: e.target.checked }));
                }}
                disabled={!permsForm.img_use || permsForm.img_manage_all}
                label="查看所有用户的镜像"
                tooltip="可看到所有用户的镜像（不受资源角色分配限制）；开启「管理所有用户的镜像」时此项自动开启且不可单独关闭"
              />
              <PermCheck
                checked={permsForm.img_copy}
                onChange={pf('img_copy')}
                disabled={!permsForm.img_use}
                label="跨服务器复制镜像"
                tooltip="将镜像从当前服务器复制到其他服务器（通过 docker save | SSH | docker load 流式传输）"
              />
            </div>
            <Field label="镜像空间配额 (GB，0 = 不限制)" full={false}>
              <input
                type="number" min="0" step="10"
                value={permsForm.img_quota_gb}
                onChange={pf('img_quota_gb')}
                style={{ width: 120 }}
              />
            </Field>
          </div>

          {/* 容器权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <Box size={13} /> 容器权限
              <CapsuleSwitch
                checked={permsForm.ctr_use}
                onChange={(v) => setPermsForm((p) => ({ ...p, ctr_use: v }))}
                label="使用容器"
                tooltip="可查看和访问容器列表；是资源角色「查看者」的前提条件"
              />
            </div>
            <div className="dm-perm-checks">
              <PermCheck
                checked={permsForm.ctr_manage_all}
                onChange={(e) => {
                  const checked = e.target.checked;
                  setPermsForm((prev) => ({
                    ...prev,
                    ctr_manage_all: checked,
                    // 勾选「管理所有」时自动勾选「查看所有」
                    ctr_view_all: checked ? true : prev.ctr_view_all,
                  }));
                }}
                disabled={!permsForm.ctr_use}
                label={<strong>管理所有用户的容器</strong>}
                tooltip="自动成为所有容器的所有者角色，可对任意容器进行启停、删除、配置等全部操作；勾选后「查看所有用户的容器」将自动开启"
              />
              <PermCheck
                checked={permsForm.ctr_view_all || permsForm.ctr_manage_all}
                onChange={(e) => {
                  if (permsForm.ctr_manage_all) return; // 管理所有时不可单独关闭
                  setPermsForm((prev) => ({ ...prev, ctr_view_all: e.target.checked }));
                }}
                disabled={!permsForm.ctr_use || permsForm.ctr_manage_all}
                label="查看所有用户的容器"
                tooltip="可看到所有用户的容器（不受资源角色分配限制）；开启「管理所有用户的容器」时此项自动开启且不可单独关闭"
              />
              <PermCheck
                checked={permsForm.ctr_create}
                onChange={pf('ctr_create')}
                disabled={!permsForm.ctr_use}
                label="创建容器"
                tooltip="可通过 docker run 命令行模式或 docker compose 文件模式创建新容器"
              />
              <PermCheck
                checked={permsForm.ctr_create_template}
                onChange={pf('ctr_create_template')}
                disabled={!permsForm.ctr_use}
                label="从模板创建容器"
                tooltip="可使用平台预置的模板快速部署容器"
              />
            </div>
            <Field label="容器数量配额（0 = 不限制）" full={false}>
              <input
                type="number" min="0" step="1"
                value={permsForm.ctr_quota_num}
                onChange={pf('ctr_quota_num')}
                style={{ width: 120 }}
              />
            </Field>
            <Field label="宿主机路径挂载白名单（每行一个前缀，留空则禁止 -v 本地路径挂载）" full>
              <textarea
                className="mono"
                value={permsPathStr}
                onChange={(e) => setPermsPathStr(e.target.value)}
                placeholder={'/data/shared\n/mnt/datasets'}
                style={{ minHeight: 80 }}
              />
            </Field>
          </div>

          {/* 卷权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <HardDrive size={13} /> 卷权限
              <CapsuleSwitch
                checked={permsForm.vol_use}
                onChange={(v) => setPermsForm((p) => ({ ...p, vol_use: v }))}
                label="使用卷"
                tooltip="可查看和访问卷列表；是资源角色「查看者」的前提条件"
              />
            </div>
            <div className="dm-perm-checks">
              <PermCheck
                checked={permsForm.vol_create}
                onChange={pf('vol_create')}
                disabled={!permsForm.vol_use}
                label="创建卷"
                tooltip="可执行 docker volume create 新建数据卷"
              />
              <PermCheck
                checked={permsForm.vol_delete_all}
                onChange={pf('vol_delete_all')}
                disabled={!permsForm.vol_use}
                label={<strong>删除他人的卷</strong>}
                tooltip="可删除非自己创建的卷；自身创建的卷及拥有所有者角色的卷默认可删"
              />
              <PermCheck
                checked={permsForm.vol_copy}
                onChange={pf('vol_copy')}
                disabled={!permsForm.vol_use}
                label="复制卷到其他服务器"
                tooltip="将卷数据通过 tar + SSH 流式传输方式复制到其他服务器"
              />
            </div>
            <Field label="卷空间配额 (GB，0 = 不限制)" full={false}>
              <input
                type="number" min="0" step="10"
                value={permsForm.vol_quota_gb}
                onChange={pf('vol_quota_gb')}
                style={{ width: 120 }}
              />
            </Field>
          </div>

          {/* 模板权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><ClipboardList size={13} /> 模板权限</div>
            <div className="dm-perm-checks">
              <PermCheck
                checked={permsForm.tpl_use}
                onChange={pf('tpl_use')}
                label="使用模板"
                tooltip="可浏览模板列表并从模板部署容器"
              />
              <PermCheck
                checked={permsForm.tpl_create}
                onChange={pf('tpl_create')}
                label="创建/上传模板"
                tooltip="可新建或上传容器部署模板，供其他用户使用"
              />
              <PermCheck
                checked={permsForm.tpl_edit}
                onChange={pf('tpl_edit')}
                label="编辑/删除模板"
                tooltip="可修改或删除已有模板"
              />
            </div>
          </div>

          {/* CUDA / GPU 权限 */}
          {panelServer?.cudaAvailable && (panelServer.gpuInfo ?? []).length > 0 ? (
            <div className="dm-perm-section">
              <div className="dm-perm-section-title">
                <Cpu size={13} /> CUDA / GPU 权限
                <CapsuleSwitch
                  checked={(permsForm.cuda_gpu_indices ?? []).length > 0}
                  onChange={(v) => setPermsForm((p) => ({ ...p, cuda_gpu_indices: v ? (panelServer.gpuInfo ?? []).map((g) => g.index) : [] }))}
                  label="允许使用 CUDA"
                  tooltip="开启后可选择该用户可挂载的显卡；关闭则不允许使用任何 CUDA 资源"
                />
              </div>
              <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8 }}>
                选择该用户可挂载的显卡序号（未选中则不允许使用 CUDA）：
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <button
                  className="btn"
                  style={{ fontSize: 11 }}
                  onClick={() => setPermsForm((p) => ({ ...p, cuda_gpu_indices: (panelServer.gpuInfo ?? []).map((g) => g.index) }))}
                >
                  全选
                </button>
                <button
                  className="btn"
                  style={{ fontSize: 11 }}
                  onClick={() => setPermsForm((p) => ({ ...p, cuda_gpu_indices: [] }))}
                >
                  全不选
                </button>
              </div>
              <div className="dm-roles-checklist" style={{ marginTop: 8 }}>
                {(panelServer.gpuInfo ?? []).map((gpu) => {
                  const checked = (permsForm.cuda_gpu_indices ?? []).includes(gpu.index);
                  return (
                    <label key={gpu.index} className="dm-form-check">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          setPermsForm((p) => ({
                            ...p,
                            cuda_gpu_indices: e.target.checked
                              ? [...(p.cuda_gpu_indices ?? []), gpu.index].sort((a, b) => a - b)
                              : (p.cuda_gpu_indices ?? []).filter((i) => i !== gpu.index),
                          }));
                        }}
                      />
                      <span className="dm-cuda-gpu-label">
                        <strong>GPU {gpu.index}</strong>
                        <span style={{ color: '#64748b', marginLeft: 4 }}>{gpu.name}</span>
                        <span style={{ color: '#94a3b8', fontSize: 11, marginLeft: 4 }}>{gpu.memoryTotal}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>
          ) : panelServer && !panelServer.cudaAvailable ? (
            <div className="dm-perm-section">
              <div className="dm-perm-section-title"><Cpu size={13} /> CUDA / GPU 权限</div>
              <div style={{ fontSize: 12, color: '#94a3b8' }}>该服务器未检测到 CUDA / GPU，无需配置。</div>
            </div>
          ) : null}
        </Modal>
      )}

      {/* Add Server Modal */}
      {showAdd && (
        <Modal
          title="添加服务器"
          onClose={() => { setShowAdd(false); setAddMsg(''); clearError(); }}
          foot={
            <>
              <button className="btn" onClick={() => { setShowAdd(false); setAddMsg(''); clearError(); }}>取消</button>
              <button
                className="btn btn-primary"
                onClick={doAdd}
                disabled={adding || !addForm.host || !addForm.sshUsername || !addForm.sshPassword || !addForm.name}
              >
                {adding ? <Spin /> : <Plus size={14} />} 连接并添加
              </button>
            </>
          }
        >
          {error && <Alert type="error">{error}</Alert>}
          {addMsg && <Alert type="success">{addMsg}</Alert>}
          <div className="dm-form-grid">
            <Field label="显示名称"><input value={addForm.name} onChange={af('name')} placeholder="实验室服务器A" /></Field>
            <Field label="主机地址"><input value={addForm.host} onChange={af('host')} placeholder="192.168.1.100" /></Field>
            <Field label="SSH 端口"><input type="number" value={addForm.port} onChange={af('port')} /></Field>
            <Field label="SSH 用户名"><input value={addForm.sshUsername} onChange={af('sshUsername')} placeholder="labuser" /></Field>
            <Field label="SSH 密码" full><input type="password" value={addForm.sshPassword} onChange={af('sshPassword')} /></Field>
          </div>
          <div style={{ marginTop: 8, fontSize: 12, color: '#94a3b8' }}>
            提交后将通过 SSH 自动验证 Docker 权限，请确保目标服务器已安装 Docker。
          </div>
        </Modal>
      )}
    </div>
  );
}

// ============================================================
// AdminTemplatesPanel
// ============================================================

export function AdminTemplatesPanel() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<TemplateDetail | null>(null);
  const [form, setForm] = useState({
    name: '', description: '', category: 'general',
    docContent: '', configStr: '{}', isPublic: true,
  });
  const [saving, setSaving] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    clearError();
    try {
      const r = await apiGet<{ templates: Template[] }>(`${API}/templates`);
      setTemplates(r.templates);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { void load(); }, [load]);

  function openCreate() {
    setForm({ name: '', description: '', category: 'general', docContent: '', configStr: '{}', isPublic: true });
    setEditing(null);
    setShowCreate(true);
    setSuccessMsg('');
  }

  async function openEdit(id: string) {
    clearError();
    try {
      const r = await apiGet<{ template: TemplateDetail }>(`${API}/templates/${id}`);
      const t = r.template;
      setForm({
        name: t.name, description: t.description, category: t.category,
        docContent: t.docContent, configStr: JSON.stringify(t.config, null, 2), isPublic: t.isPublic,
      });
      setEditing(t);
      setShowCreate(true);
      setSuccessMsg('');
    } catch (e) {
      setError(e);
    }
  }

  async function doSave() {
    setSaving(true);
    clearError();
    let config: Record<string, unknown> = {};
    try {
      config = JSON.parse(form.configStr || '{}');
    } catch {
      setError(new Error('配置 JSON 格式错误'));
      setSaving(false);
      return;
    }
    try {
      if (editing) {
        await apiPut(`${API}/templates/${editing.id}`, {
          name: form.name, description: form.description, category: form.category,
          docContent: form.docContent, config, isPublic: form.isPublic,
        });
        setSuccessMsg('模板更新成功！');
      } else {
        await apiPost(`${API}/templates`, {
          name: form.name, description: form.description, category: form.category,
          docContent: form.docContent, config, isPublic: form.isPublic,
        });
        setSuccessMsg('模板创建成功！');
      }
      void load();
      setShowCreate(false);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  async function doDelete(id: string, name: string) {
    if (!confirm(`确定删除模板 ${name}？`)) return;
    clearError();
    try {
      await apiDelete(`${API}/templates/${id}`);
      void load();
    } catch (e) {
      setError(e);
    }
  }

  const f = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.type === 'checkbox' ? (e.target as HTMLInputElement).checked : e.target.value }));

  return (
    <div style={{ display: 'grid', gap: 20 }}>
      {error && <Alert type="error">{error}</Alert>}
      {successMsg && <Alert type="success">{successMsg}</Alert>}

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" onClick={openCreate}><Plus size={14} /> 创建模板</button>
        <button className="btn" onClick={load} disabled={loading}><RefreshCw size={14} /> 刷新</button>
      </div>

      {loading ? <div className="dm-empty"><Spin /> 加载中…</div> :
       templates.length === 0 ? <div className="dm-empty"><ClipboardList size={32} /> 暂无模板</div> : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1.5fr 1fr 1fr auto' }}>
            <span>模板名称</span><span>分类</span><span>可见性</span><span>更新时间</span><span>操作</span>
          </div>
          {templates.map((t) => (
            <div key={t.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1.5fr 1fr 1fr auto' }}>
              <span style={{ fontWeight: 600 }}>{t.name}</span>
              <span><span className="dm-category-tag">{t.category}</span></span>
              <span style={{ color: t.isPublic ? '#065f46' : '#91400e', fontSize: 12 }}>
                {t.isPublic ? '公开' : '私有'}
              </span>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>{t.updatedAt.slice(0, 10)}</span>
              <span style={{ display: 'flex', gap: 4 }}>
                <button className="dm-btn-icon" title="编辑" onClick={() => openEdit(t.id)}><Pencil size={13} /></button>
                <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(t.id, t.name)}><Trash2 size={13} /></button>
              </span>
            </div>
          ))}
        </div>
      )}

      {showCreate && (
        <Modal title={editing ? `编辑模板 — ${editing.name}` : '创建模板'} onClose={() => setShowCreate(false)} wide
          foot={
            <>
              <button className="btn" onClick={() => setShowCreate(false)}>取消</button>
              <button className="btn btn-primary" onClick={doSave} disabled={saving || !form.name.trim()}>
                {saving ? <Spin /> : <CheckCircle size={14} />} {editing ? '保存更改' : '创建'}
              </button>
            </>
          }>
          {error && <Alert type="error">{error}</Alert>}
          <div className="dm-form-grid">
            <Field label="模板名称 *"><input value={form.name} onChange={f('name')} placeholder="Jupyter Notebook" required /></Field>
            <Field label="分类"><input value={form.category} onChange={f('category')} placeholder="ml / web / database / general" /></Field>
            <Field label="描述" full><input value={form.description} onChange={f('description')} placeholder="简短说明" /></Field>
          </div>
          <div>
            <label className="dm-form-check">
              <input type="checkbox" checked={form.isPublic} onChange={f('isPublic')} />
              公开（所有用户可见）
            </label>
          </div>
          <Field label="说明文档（Markdown）" full>
            <textarea className="mono" value={form.docContent} onChange={f('docContent')}
              placeholder={"## Jupyter Notebook\n\n使用说明...\n\n### 端口\n\n- `8888` — Jupyter Web 界面"} style={{ minHeight: 200 }} />
          </Field>
          {form.docContent && (
            <details style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: '8px 12px' }}>
              <summary style={{ cursor: 'pointer', fontSize: 13, color: '#526071' }}>预览 Markdown</summary>
              <div className="dm-md-preview" style={{ marginTop: 10 }}
                dangerouslySetInnerHTML={{ __html: renderMarkdown(form.docContent) }} />
            </details>
          )}
          <Field label="容器配置（JSON）" full>
            <textarea className="mono" value={form.configStr} onChange={f('configStr')}
              placeholder={'{\n  "type": "run",\n  "image": "jupyter/base-notebook",\n  "ports": ["8888:8888"]\n}'}
              style={{ minHeight: 160 }} />
          </Field>
          <Alert type="info">
            配置支持 <code>type: "run"</code>（docker run）或 <code>type: "compose"</code>（docker compose，需 <code>composeYaml</code> 字段）。
            字符串和数值类型的配置项会在用户创建容器时作为可覆盖参数显示。
          </Alert>
        </Modal>
      )}
    </div>
  );
}