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
  Layers,
  Pencil,
  Plus,
  RefreshCw,
  ScanLine,
  Server,
  Shield,
  Trash2,
  Users,
} from 'lucide-react';
import { apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import type { GpuInfo } from './types';
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
  // 权限面板
  const [permServer, setPermServer] = useState<DmServer | null>(null);
  const [perms, setPerms] = useState<ServerPermEntry[]>([]);
  const [permsLoading, setPermsLoading] = useState(false);
  // 细粒度权限编辑弹窗
  const [permsEditTarget, setPermsEditTarget] = useState<ServerPermEntry | null>(null);
  const [permsForm, setPermsForm] = useState<UserPerms>(DEFAULT_PERMS);
  const [permsPathStr, setPermsPathStr] = useState('');
  const [savingPerms, setSavingPerms] = useState(false);
  // CUDA 重新扫描
  const [scanningCuda, setScanningCuda] = useState<string | null>(null); // 当前正在扫描的 server_id
  // 资源多角色分配面板
  const [resourceServer, setResourceServer] = useState<DmServer | null>(null);
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
  const [assignQuotaMode, setAssignQuotaMode] = useState<'shared' | 'exclusive'>('shared');
  const [savingRoles, setSavingRoles] = useState(false);

  // 加载服务器资源列表
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

  async function openResourcePanel(s: DmServer) {
    setResourceServer(s);
    setResources(null);
    setAssignSuccess(null);
    clearAssignError();
    setResourceTab('containers');
    setResourcesLoading(true);
    await Promise.allSettled([
      apiGet<{ permissions: ServerPermEntry[] }>(`${API}/servers/${s.id}/permissions`)
        .then((r) => setUsers(r.permissions))
        .catch(() => { /* 用户列表加载失败不影响主功能 */ }),
      apiGet<ServerResources>(`${API}/servers/${s.id}/resources`)
        .then((r) => setResources(r))
        .catch((e) => setAssignError(e)),
    ]);
    setResourcesLoading(false);
  }

  function openAssignRoles(resourceType: string, resourceRef: string, label: string, currentRoles: ResourceRoles) {
    setAssignRolesTarget({ resourceType, resourceRef, label, currentRoles });
    setAssignOwnerIds(currentRoles.ownerUserIds ?? []);
    setAssignViewerIds(currentRoles.viewerUserIds ?? []);
    setAssignQuotaHolderIds(currentRoles.quotaHolderUserIds ?? []);
    setAssignQuotaMode(currentRoles.quotaMode ?? 'shared');
    clearAssignError();
  }

  async function doAssignRoles() {
    if (!resourceServer || !assignRolesTarget) return;
    setSavingRoles(true);
    clearAssignError();
    setAssignSuccess(null);
    try {
      await apiPut(`${API}/servers/${resourceServer.id}/resource-roles`, {
        resourceType: assignRolesTarget.resourceType,
        resourceRef: assignRolesTarget.resourceRef,
        ownerUserIds: assignOwnerIds,
        viewerUserIds: assignViewerIds,
        quotaHolderUserIds: assignQuotaHolderIds,
        quotaMode: assignQuotaMode,
      });
      setAssignSuccess(`「${assignRolesTarget.label}」角色分配已更新`);
      setAssignRolesTarget(null);
      void loadResources(resourceServer.id);
    } catch (e) {
      setAssignError(e);
    } finally {
      setSavingRoles(false);
    }
  }

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

  async function openPerms(s: DmServer) {
    setPermServer(s);
    setPermsLoading(true);
    clearError();
    try {
      const r = await apiGet<{ permissions: ServerPermEntry[] }>(`${API}/servers/${s.id}/permissions`);
      setPerms(r.permissions);
    } catch (e) {
      setError(e);
    } finally {
      setPermsLoading(false);
    }
  }

  async function reloadPerms() {
    if (!permServer) return;
    try {
      const r = await apiGet<{ permissions: ServerPermEntry[] }>(`${API}/servers/${permServer.id}/permissions`);
      setPerms(r.permissions);
    } catch (e) {
      setError(e);
    }
  }

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
    if (!permsEditTarget || !permServer) return;
    setSavingPerms(true);
    clearError();
    try {
      const pathList = permsPathStr.split('\n').map((s) => s.trim()).filter(Boolean);
      await apiPut(`${API}/servers/${permServer.id}/user-perms`, {
        userId: permsEditTarget.userId,
        ...permsForm,
        ctr_path_whitelist: pathList,
      });
      setPermsEditTarget(null);
      await reloadPerms();
    } catch (e) {
      setError(e);
    } finally {
      setSavingPerms(false);
    }
  }

  function applyPreset(preset: 'none' | 'view' | 'use' | 'manage') {
    const none = { ...DEFAULT_PERMS };
    if (preset === 'none') { setPermsForm(none); return; }
    if (preset === 'view') {
      setPermsForm({ ...none, server_visible: true, ctr_view_own: true });
      return;
    }
    if (preset === 'use') {
      setPermsForm({
        ...none,
        server_visible: true,
        img_pull: true,
        ctr_view_own: true,
        ctr_create_run: true, ctr_create_compose: true, ctr_create_template: true,
        ctr_manage_own: true,
        vol_create: true, vol_delete_own: true, vol_copy: true,
        tpl_use: true,
      });
      return;
    }
    if (preset === 'manage') {
      setPermsForm({
        server_visible: true,
        img_pull: true, img_delete: true, img_copy: true,
        ctr_view_own: true, ctr_view_all: true,
        ctr_create_run: true, ctr_create_compose: true, ctr_create_template: true,
        ctr_manage_own: true, ctr_manage_all: true, ctr_path_whitelist: [],
        vol_create: true, vol_delete_own: true, vol_delete_all: true, vol_copy: true, vol_quota_gb: 0,
        tpl_use: true, tpl_create: true, tpl_edit: true,
      });
    }
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
      </div>

      {/* Server List */}
      {loading ? <div className="dm-empty"><Spin /> 加载中…</div> :
       servers.length === 0 ? <div className="dm-empty"><Server size={32} /> 暂无服务器</div> : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr 1fr auto' }}>
            <span>服务器</span><span>地址</span><span>GPU</span><span>添加时间</span><span>操作</span>
          </div>
          {servers.map((s) => (
            <div key={s.id} className="dm-table-row" style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr 1fr auto' }}>
              <span style={{ fontWeight: 600 }}>{s.name}</span>
              <span style={{ color: '#526071', fontFamily: 'monospace', fontSize: 13 }}>{s.host}:{s.port}</span>
              <span>
                {s.cudaAvailable
                  ? <span className="dm-cuda-badge"><Cpu size={11} /> {s.gpuCount ?? 0} GPU</span>
                  : <span style={{ color: '#94a3b8', fontSize: 12 }}>无</span>
                }
              </span>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>{s.createdAt.slice(0, 10)}</span>
              <span style={{ display: 'flex', gap: 4 }}>
                <button className="dm-btn-icon" title="权限管理" onClick={() => openPerms(s)}><Users size={13} /></button>
                <button className="dm-btn-icon" title="资源分配" onClick={() => openResourcePanel(s)}><Database size={13} /></button>
                <button className="dm-btn-icon" title="重新扫描 CUDA" onClick={() => void doRescanCuda(s.id)} disabled={scanningCuda === s.id}>
                  {scanningCuda === s.id ? <Spin /> : <ScanLine size={13} />}
                </button>
                <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(s.id, s.name)}><Trash2 size={13} /></button>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Permissions List Modal */}
      {permServer && !permsEditTarget && (
        <Modal title={`权限管理 — ${permServer.name}`} onClose={() => setPermServer(null)} wide
          foot={<button className="btn" onClick={() => setPermServer(null)}>关闭</button>}>
          {error && <Alert type="error">{error}</Alert>}
          {permsLoading ? <div className="dm-empty"><Spin /></div> : (
            <div className="dm-perm-table">
              {perms.map((p) => {
                const lvlColor: Record<string, string> = {
                  manage: '#166534', use: '#1e40af', view: '#92400e', none: '#6b7280'
                };
                const lvlBg: Record<string, string> = {
                  manage: '#dcfce7', use: '#dbeafe', view: '#fef3c7', none: '#f3f4f6'
                };
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
                      color: lvlColor[p.level] ?? '#6b7280',
                      background: lvlBg[p.level] ?? '#f3f4f6',
                    }}>
                      {p.role === 'admin' ? '全满权限' : { manage: '管理', use: '使用', view: '查看', none: '无权限' }[p.level] ?? p.level}
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
        </Modal>
      )}

      {/* Resource Roles Assignment Modal */}
      {resourceServer && !permServer && (
        <Modal
          title={`资源角色管理 — ${resourceServer.name}`}
          onClose={() => { setResourceServer(null); setResources(null); }}
          wide
          foot={
            <button className="btn" onClick={() => { setResourceServer(null); setResources(null); }}>关闭</button>
          }
        >
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
              onClick={() => resourceServer && loadResources(resourceServer.id)}
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
        </Modal>
      )}

      {/* 角色分配弹窗（二级弹窗） */}
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
            <div className="dm-perm-section-title"><Shield size={13} /> 所有者（可管理该资源）</div>
            <div className="dm-roles-checklist">
              {users.filter((u) => u.role !== 'admin').map((u) => (
                <label key={u.userId} className="dm-form-check">
                  <input
                    type="checkbox"
                    checked={assignOwnerIds.includes(u.userId)}
                    onChange={(e) => {
                      setAssignOwnerIds((prev) =>
                        e.target.checked ? [...prev, u.userId] : prev.filter((id) => id !== u.userId)
                      );
                      if (e.target.checked) {
                        setAssignViewerIds((prev) => prev.filter((id) => id !== u.userId));
                      } else {
                        // 取消所有者时同时取消配额占用者
                        setAssignQuotaHolderIds((prev) => prev.filter((id) => id !== u.userId));
                      }
                    }}
                  />
                  <span>{u.displayName}</span>
                  <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                </label>
              ))}
              {users.filter((u) => u.role !== 'admin').length === 0 && (
                <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无普通用户</div>
              )}
            </div>
          </div>

          {/* 配额占用者（多选，只能从所有者中选） */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <Database size={13} /> 配额占用者（独占配额，必须同时是所有者）
            </div>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8 }}>
              配额占用者拥有所有者全部权限，在「配额占用者独占」模式下，其卷配额独立计算，不参与共享均分。
            </div>
            {assignOwnerIds.length === 0 ? (
              <div style={{ fontSize: 12, color: '#94a3b8' }}>请先选择所有者</div>
            ) : (
              <div className="dm-roles-checklist">
                {users.filter((u) => assignOwnerIds.includes(u.userId)).map((u) => (
                  <label key={u.userId} className="dm-form-check">
                    <input
                      type="checkbox"
                      checked={assignQuotaHolderIds.includes(u.userId)}
                      onChange={(e) => {
                        setAssignQuotaHolderIds((prev) =>
                          e.target.checked ? [...prev, u.userId] : prev.filter((id) => id !== u.userId)
                        );
                      }}
                    />
                    <span>{u.displayName}</span>
                    <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                    <span className="dm-role-tag owner" style={{ marginLeft: 4, fontSize: 10, padding: '1px 5px' }}>所有者</span>
                  </label>
                ))}
              </div>
            )}

            {/* 配额模式选择 */}
            <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid #f1f5f9' }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#374151', marginBottom: 8 }}>配额分配模式</div>
              <div style={{ display: 'flex', gap: 16 }}>
                <label className="dm-form-check">
                  <input
                    type="radio"
                    name="quotaMode"
                    checked={assignQuotaMode === 'shared'}
                    onChange={() => setAssignQuotaMode('shared')}
                  />
                  <span>
                    <strong>所有者均分</strong>
                    <span style={{ color: '#64748b', fontSize: 11, marginLeft: 4 }}>配额在所有所有者间平均分配</span>
                  </span>
                </label>
                <label className="dm-form-check">
                  <input
                    type="radio"
                    name="quotaMode"
                    checked={assignQuotaMode === 'exclusive'}
                    onChange={() => setAssignQuotaMode('exclusive')}
                  />
                  <span>
                    <strong>配额占用者独占</strong>
                    <span style={{ color: '#64748b', fontSize: 11, marginLeft: 4 }}>配额占用者独享其配额，不参与均分</span>
                  </span>
                </label>
              </div>
            </div>
          </div>

          {/* 查看者（多选） */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><FileText size={13} /> 查看者（只可查看该资源）</div>
            <div className="dm-roles-checklist">
              {users.filter((u) => u.role !== 'admin').map((u) => (
                <label key={u.userId} className={`dm-form-check${assignOwnerIds.includes(u.userId) ? ' disabled' : ''}`}>
                  <input
                    type="checkbox"
                    checked={assignViewerIds.includes(u.userId)}
                    disabled={assignOwnerIds.includes(u.userId)}
                    onChange={(e) => {
                      setAssignViewerIds((prev) =>
                        e.target.checked ? [...prev, u.userId] : prev.filter((id) => id !== u.userId)
                      );
                    }}
                  />
                  <span>{u.displayName}</span>
                  <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                  {assignOwnerIds.includes(u.userId) && (
                    <span style={{ fontSize: 11, color: '#64748b', marginLeft: 4 }}>(已是所有者)</span>
                  )}
                </label>
              ))}
              {users.filter((u) => u.role !== 'admin').length === 0 && (
                <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无普通用户</div>
              )}
            </div>
          </div>
        </Modal>
      )}

      {/* Fine-grained Permissions Edit Modal */}
      {permsEditTarget && permServer && (
        <Modal
          title={`权限配置 — ${permsEditTarget.displayName} @ ${permServer.name}`}
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

          {/* 快速预设 */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>快速预设：</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {(['none', 'view', 'use', 'manage'] as const).map((preset) => (
                <button key={preset} className="btn" style={{ fontSize: 12 }} onClick={() => applyPreset(preset)}>
                  {{ none: '无权限', view: '查看', use: '使用', manage: '管理' }[preset]}
                </button>
              ))}
            </div>
          </div>

          {/* 服务器可见性 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Server size={13} /> 服务器访问</div>
            <label className="dm-form-check">
              <input type="checkbox" checked={permsForm.server_visible} onChange={pf('server_visible')} />
              可见该服务器（用户能在列表中看到此服务器）
            </label>
          </div>

          {/* 镜像权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Image size={13} /> 镜像权限</div>
            <div className="dm-perm-checks">
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.img_pull} onChange={pf('img_pull')} />
                拉取镜像（docker pull）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.img_delete} onChange={pf('img_delete')} />
                删除镜像（docker rmi）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.img_copy} onChange={pf('img_copy')} />
                跨服务器复制镜像（save | ssh | load）
              </label>
            </div>
          </div>

          {/* 容器权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Box size={13} /> 容器权限</div>
            <div className="dm-perm-checks">
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_view_own} onChange={pf('ctr_view_own')} />
                查看自己的容器（列表 / 日志）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_view_all} onChange={pf('ctr_view_all')} />
                查看所有人的容器
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_create_run} onChange={pf('ctr_create_run')} />
                创建容器（docker run 模式）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_create_compose} onChange={pf('ctr_create_compose')} />
                创建容器（docker compose 模式）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_create_template} onChange={pf('ctr_create_template')} />
                从模板创建容器
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_manage_own} onChange={pf('ctr_manage_own')} />
                管理自己的容器（启/停/重启/删除）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_manage_all} onChange={pf('ctr_manage_all')} />
                管理所有人的容器
              </label>
            </div>
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
            <div className="dm-perm-section-title"><HardDrive size={13} /> 卷权限</div>
            <div className="dm-perm-checks">
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.vol_create} onChange={pf('vol_create')} />
                创建卷（docker volume create）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.vol_delete_own} onChange={pf('vol_delete_own')} />
                删除自己创建的卷
              </label>
               <label className="dm-form-check">
                 <input type="checkbox" checked={permsForm.vol_delete_all} onChange={pf('vol_delete_all')} />
                 删除所有人的卷
               </label>
               <label className="dm-form-check">
                 <input type="checkbox" checked={permsForm.vol_copy} onChange={pf('vol_copy')} />
                 复制卷到其他服务器（tar | SSH 流式传输）
               </label>
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
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.tpl_use} onChange={pf('tpl_use')} />
                使用模板（从模板部署容器）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.tpl_create} onChange={pf('tpl_create')} />
                创建/上传模板
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.tpl_edit} onChange={pf('tpl_edit')} />
                编辑/删除模板
              </label>
            </div>
          </div>

          {/* CUDA / GPU 权限 */}
          {permServer?.cudaAvailable && (permServer.gpuInfo ?? []).length > 0 ? (
            <div className="dm-perm-section">
              <div className="dm-perm-section-title"><Cpu size={13} /> CUDA / GPU 权限</div>
              <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8 }}>
                选择该用户可挂载的显卡序号（未选中则不允许使用 CUDA）：
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {/* 全选 / 全不选快捷按钮 */}
                <button
                  className="btn"
                  style={{ fontSize: 11 }}
                  onClick={() => setPermsForm((p) => ({ ...p, cuda_gpu_indices: (permServer.gpuInfo ?? []).map((g) => g.index) }))}
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
                {(permServer.gpuInfo ?? []).map((gpu) => {
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
          ) : permServer && !permServer.cudaAvailable ? (
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

  const categories = [...new Set(templates.map((t) => t.category))];

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
