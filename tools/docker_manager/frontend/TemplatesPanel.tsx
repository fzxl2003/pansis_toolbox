// ============================================================
// Templates Panel (User View) — Docker Manager
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Box,
  CheckCircle,
  ClipboardList,
  Database,
  FileText,
  Image,
  RefreshCw,
  Shield,
  Users,
} from 'lucide-react';
import { apiGet, apiPut } from '../../../frontend/src/api/client';
import type { AuthUser } from '../../../frontend/src/api/auth';
import { Alert, Modal, SkeletonRows, Spin, TruncText } from './components';
import { API, renderMarkdown, useErrorMsg } from './utils';
import type { BasicUser, MyOwnedResource, Template, TemplateDetail } from './types';

// ---- TemplatesPanel ----

export function TemplatesPanel({ me }: { me: AuthUser }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState<TemplateDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  useEffect(() => {
    setLoading(true);
    apiGet<{ templates: Template[] }>(`${API}/templates`)
      .then((r) => setTemplates(r.templates))
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  async function selectTemplate(id: string) {
    clearError();
    try {
      const r = await apiGet<{ template: TemplateDetail }>(`${API}/templates/${id}`);
      setSelected(r.template);
    } catch (e) {
      setError(e);
    }
  }

  const categories = [...new Set(templates.map((t) => t.category))];

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      {error && <Alert type="error">{error}</Alert>}

      {selected ? (
        <div style={{ display: 'grid', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button className="btn" onClick={() => setSelected(null)}>← 返回</button>
            <strong style={{ fontSize: 16 }}>{selected.name}</strong>
            <span className="dm-category-tag">{selected.category}</span>
          </div>
          {selected.description && <p style={{ color: '#526071', margin: 0 }}>{selected.description}</p>}
          {selected.docContent ? (
            <div className="dm-md-preview" dangerouslySetInnerHTML={{ __html: renderMarkdown(selected.docContent) }} />
          ) : (
            <Alert type="info">该模板暂无说明文档</Alert>
          )}
        </div>
      ) : loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : templates.length === 0 ? (
        <div className="dm-empty"><ClipboardList size={32} /> 暂无可用模板</div>
      ) : (
        categories.map((cat) => (
          <div key={cat} style={{ display: 'grid', gap: 10 }}>
            <div style={{ fontWeight: 700, color: '#526071', fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              {cat}
            </div>
            <div className="dm-card-grid">
              {templates.filter((t) => t.category === cat).map((t) => (
                <div key={t.id} className="dm-card" style={{ cursor: 'pointer' }} onClick={() => selectTemplate(t.id)}>
                  <div className="dm-card-header">
                    <span className="dm-card-title">{t.name}</span>
                    {t.hasDoc && <FileText size={14} style={{ color: '#94a3b8', flexShrink: 0 }} />}
                  </div>
                  {t.description && <span style={{ color: '#64748b', fontSize: 13 }}>{t.description}</span>}
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ---- MyResourcesPanel ----

export function MyResourcesPanel({ me }: { me: AuthUser }) {
  const [resources, setResources] = useState<MyOwnedResource[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [success, setSuccess] = useState<string | null>(null);
  const [allUsers, setAllUsers] = useState<BasicUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);

  // 查看者编辑弹窗状态
  const [editTarget, setEditTarget] = useState<MyOwnedResource | null>(null);
  const [editViewerIds, setEditViewerIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError, clearEditError] = useErrorMsg();

  // 资源类型过滤
  const [filterType, setFilterType] = useState<'all' | 'container' | 'image' | 'volume'>('all');

  const load = useCallback(async () => {
    setLoading(true);
    clearError();
    setSuccess(null);
    try {
      const r = await apiGet<{ resources: MyOwnedResource[] }>(`${API}/my-owned-resources`);
      setResources(r.resources);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  const loadUsers = useCallback(async () => {
    setUsersLoading(true);
    try {
      const r = await apiGet<{ users: BasicUser[] }>('/api/auth/users-basic');
      setAllUsers(r.users);
    } catch {
      // 加载失败不影响主功能
    } finally {
      setUsersLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadUsers();
  }, [load, loadUsers]);

  function openEdit(res: MyOwnedResource) {
    setEditTarget(res);
    setEditViewerIds(res.viewerUserIds);
    clearEditError();
  }

  async function saveViewers() {
    if (!editTarget) return;
    setSaving(true);
    clearEditError();
    try {
      await apiPut(`${API}/servers/${editTarget.serverId}/resource-viewers`, {
        resourceType: editTarget.resourceType,
        resourceRef: editTarget.resourceRef,
        viewerUserIds: editViewerIds,
      });
      setSuccess(`「${editTarget.resourceRef}」的查看者已更新`);
      setEditTarget(null);
      void load();
    } catch (e) {
      setEditError(e);
    } finally {
      setSaving(false);
    }
  }

  const resourceTypeLabel: Record<string, string> = {
    container: '容器',
    image: '镜像',
    volume: '卷',
  };
  const resourceTypeIcon: Record<string, ReactNode> = {
    container: <Box size={13} />,
    image: <Image size={13} />,
    volume: <Database size={13} />,
  };

  const filtered = resources.filter((r) => filterType === 'all' || r.resourceType === filterType);
  const selectableViewers = allUsers.filter((u) => u.id !== me.id);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: '#526071', fontWeight: 600 }}>我管理的资源</span>
        <span style={{ fontSize: 12, color: '#94a3b8' }}>（您作为所有者的资源，可以为其分配查看者）</span>
        <button className="btn" style={{ marginLeft: 'auto', padding: '4px 12px' }} onClick={() => void load()} disabled={loading}>
          {loading ? <Spin /> : <RefreshCw size={13} />} 刷新
        </button>
      </div>

      {/* 类型筛选 */}
      <div style={{ display: 'flex', gap: 6 }}>
        {(['all', 'container', 'image', 'volume'] as const).map((t) => (
          <button
            key={t}
            className={`dm-server-chip${filterType === t ? ' active' : ''}`}
            onClick={() => setFilterType(t)}
          >
            {t === 'all' ? '全部' : resourceTypeLabel[t]}
          </button>
        ))}
      </div>

      {error && <Alert type="error">{error}</Alert>}
      {success && <Alert type="success">{success}</Alert>}

      {loading ? (
        <SkeletonRows cols={['2fr', '1fr', '1.5fr', '1.5fr', 'auto']} />
      ) : filtered.length === 0 ? (
        <div className="dm-empty">
          <Shield size={32} />
          {resources.length === 0 ? '您目前没有任何作为所有者的资源' : '当前筛选条件下无资源'}
        </div>
      ) : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 2fr 2fr auto' }}>
            <span>资源名称</span>
            <span>类型</span>
            <span>所属服务器</span>
            <span>查看者</span>
            <span>操作</span>
          </div>

          {filtered.map((res) => (
            <div key={`${res.serverId}-${res.resourceType}-${res.resourceRef}`}
              className="dm-table-row"
              style={{ gridTemplateColumns: '2fr 1fr 2fr 2fr auto' }}>
              <span style={{ fontWeight: 500, minWidth: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
                {resourceTypeIcon[res.resourceType]}
                <TruncText text={res.resourceRef} />
              </span>
              <span>
                <span className="dm-role-tag" style={{ background: res.resourceType === 'container' ? '#dbeafe' : res.resourceType === 'image' ? '#fce7f3' : '#d1fae5', color: '#1e293b' }}>
                  {resourceTypeLabel[res.resourceType]}
                </span>
              </span>
              <span style={{ color: '#526071', fontSize: 13 }}>{res.serverName}</span>
              <span>
                {res.viewers.length === 0 ? (
                  <span style={{ color: '#94a3b8', fontSize: 12 }}>暂无查看者</span>
                ) : (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {res.viewers.map((v) => (
                      <span key={v.userId} className="dm-role-tag viewer">
                        {v.displayName} <small style={{ color: '#64748b' }}>@{v.username}</small>
                      </span>
                    ))}
                  </div>
                )}
              </span>
              <span>
                <button className="dm-btn-icon" title="管理查看者" onClick={() => openEdit(res)}>
                  <Users size={13} />
                </button>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 查看者编辑弹窗 */}
      {editTarget && (
        <Modal
          title={`管理查看者 — ${editTarget.resourceRef}`}
          onClose={() => setEditTarget(null)}
          foot={
            <>
              <button className="btn" onClick={() => setEditTarget(null)}>取消</button>
              <button className="btn btn-primary" onClick={saveViewers} disabled={saving}>
                {saving ? <Spin /> : <CheckCircle size={14} />} 保存
              </button>
            </>
          }
        >
          {editError && <Alert type="error">{editError}</Alert>}

          <div className="dm-perm-section">
            <div className="dm-perm-section-title" style={{ marginBottom: 4 }}>
              <Shield size={13} /> 资源信息
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 13, color: '#526071' }}>
              <span>类型：{resourceTypeLabel[editTarget.resourceType]}</span>
              <span>服务器：{editTarget.serverName}</span>
              {editTarget.creatorUserId && (
                <span>创建者 ID：{editTarget.creatorUserId}</span>
              )}
            </div>
          </div>

          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <FileText size={13} /> 查看者（勾选后可查看该资源）
            </div>
            {usersLoading ? (
              <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 6 }}><Spin /> 加载用户列表…</div>
            ) : (
              <div className="dm-roles-checklist">
                {selectableViewers.map((u) => (
                  <label key={u.id} className="dm-form-check">
                    <input
                      type="checkbox"
                      checked={editViewerIds.includes(u.id)}
                      onChange={(e) => {
                        setEditViewerIds((prev) =>
                          e.target.checked ? [...prev, u.id] : prev.filter((id) => id !== u.id)
                        );
                      }}
                    />
                    <span>{u.displayName}</span>
                    <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                  </label>
                ))}
                {selectableViewers.length === 0 && (
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无其他可选用户</div>
                )}
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
