// ============================================================
// Images Panel — Docker Manager
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import { Box, Copy, Download, Image, RefreshCw, Trash2 } from 'lucide-react';
import { apiDelete, apiGet, apiPost } from '../../../frontend/src/api/client';
import type { AuthUser } from '../../../frontend/src/api/auth';
import { Alert, Modal, Field, ResourceUsagePanel, ServerSelector, Spin, TruncText } from './components';
import { API, containerStateClass, useErrorMsg } from './utils';
import type { DmServer, DockerContainer, DockerImage, ServerResourceOverview } from './types';

// ---- 子 Tab 类型 ----
type ImageSubTab = 'list' | 'containers';

// ---- 镜像名匹配辅助函数 ----
// 容器的 Image 字段可能是 "nginx:latest"、"nginx" 或 sha256:xxx
function imageMatchesContainer(img: DockerImage, ctr: DockerContainer): boolean {
  const ctrImage = (ctr.Image ?? '').trim();
  if (!ctrImage) return false;
  const imgFull = `${img.repo}:${img.tag}`;
  // 精确匹配 repo:tag
  if (ctrImage === imgFull) return true;
  // tag 为 latest 时，容器可能只写 repo
  if (img.tag === 'latest' && ctrImage === img.repo) return true;
  // 短 ID 匹配（前12位）
  if (img.id && ctrImage.startsWith(img.id.slice(0, 12))) return true;
  return false;
}

// ---- 容器状态徽章 ----
function ContainerStateBadge({ state }: { state: string | undefined }) {
  const s = (state ?? '').toLowerCase();
  return (
    <span className={`dm-status ${containerStateClass(s)}`} style={{ fontSize: 12 }}>
      <span className="dm-status-dot" />
      {s || '—'}
    </span>
  );
}

export function ImagesPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  const [serverId, setServerId] = useState<string | null>(servers[0]?.id ?? null);
  const [subTab, setSubTab] = useState<ImageSubTab>('list');
  const [images, setImages] = useState<DockerImage[]>([]);
  const [containers, setContainers] = useState<DockerContainer[]>([]);
  const [loading, setLoading] = useState(false);
  const [containersLoading, setContainersLoading] = useState(false);
  const [serverOverview, setServerOverview] = useState<ServerResourceOverview | null>(null);
  const [error, setError, clearError] = useErrorMsg();
  const [pullRef, setPullRef] = useState('');
  const [pulling, setPulling] = useState(false);
  const [pullMsg, setPullMsg] = useState('');
  const [showCopy, setShowCopy] = useState(false);
  const [copyDst, setCopyDst] = useState('');
  const [copyRef, setCopyRef] = useState('');
  const [copying, setCopying] = useState(false);

  const canManage = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || s?.permissionLevel === 'manage' || !!s?.perms?.img_manage_all;
  };

  const canUse = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || s?.permissionLevel === 'manage' || s?.permissionLevel === 'use';
  };

  const loadImages = useCallback(async (sid: string) => {
    setLoading(true);
    clearError();
    try {
      const [r, ovr] = await Promise.all([
        apiGet<{ images: DockerImage[] }>(`${API}/servers/${sid}/images`),
        apiGet<ServerResourceOverview>(`${API}/servers/${sid}/resource-overview`).catch(() => null),
      ]);
      setImages(r.images);
      setServerOverview(ovr);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  const loadContainers = useCallback(async (sid: string) => {
    setContainersLoading(true);
    try {
      const r = await apiGet<{ containers: DockerContainer[] }>(`${API}/servers/${sid}/containers?all=true`);
      setContainers(r.containers);
    } catch {
      setContainers([]);
    } finally {
      setContainersLoading(false);
    }
  }, []);

  useEffect(() => {
    if (serverId) {
      void loadImages(serverId);
      void loadContainers(serverId);
    }
  }, [serverId, loadImages, loadContainers]);

  // 计算每个镜像正在使用它的容器列表
  const getImageContainers = (img: DockerImage): DockerContainer[] =>
    containers.filter((c) => imageMatchesContainer(img, c));

  async function doPull() {
    if (!serverId || !pullRef.trim()) return;
    setPulling(true);
    setPullMsg('');
    clearError();
    try {
      const r = await apiPost<{ output: string }>(`${API}/servers/${serverId}/images/pull`, { imageRef: pullRef.trim() });
      setPullMsg(r.output.slice(-400));
      void loadImages(serverId);
      void loadContainers(serverId);
      setPullRef('');
    } catch (e) {
      setError(e);
    } finally {
      setPulling(false);
    }
  }

  async function doDelete(imageRef: string, img: DockerImage) {
    if (!serverId) return;
    const imageCntrs = getImageContainers(img);
    if (imageCntrs.length > 0) {
      // 有正在挂载/使用该镜像的容器，不允许删除
      return;
    }
    if (!confirm(`确定要删除镜像 ${imageRef} 吗？`)) return;
    clearError();
    try {
      await apiDelete(`${API}/servers/${serverId}/images/${encodeURIComponent(imageRef)}`);
      void loadImages(serverId);
    } catch (e) {
      setError(e);
    }
  }

  async function doCopy() {
    if (!serverId || !copyDst || !copyRef) return;
    setCopying(true);
    clearError();
    try {
      await apiPost(`${API}/images/copy`, { srcServerId: serverId, dstServerId: copyDst, imageRef: copyRef });
      alert('镜像复制成功');
      setShowCopy(false);
    } catch (e) {
      setError(e);
    } finally {
      setCopying(false);
    }
  }

  function handleServerChange(id: string) {
    setServerId(id);
    setImages([]);
    setContainers([]);
    setServerOverview(null);
  }

  function doRefresh() {
    if (!serverId) return;
    void loadImages(serverId);
    void loadContainers(serverId);
  }

  // ---- 按镜像分组的容器视图 ----
  const imagesWithContainers = images.filter((img) => getImageContainers(img).length > 0);

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <ServerSelector servers={servers} selected={serverId} onSelect={handleServerChange} />
      {serverId && (
        <ResourceUsagePanel overview={serverOverview} resourceType="image" loading={loading && !serverOverview} />
      )}
      {error && <Alert type="error">{error}</Alert>}

      {/* 工具栏 */}
      {serverId && canUse(serverId) && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="dm-form-field" style={{ flex: '1 1 260px' }}>
            <label>拉取镜像</label>
            <input value={pullRef} onChange={(e) => setPullRef(e.target.value)} placeholder="nginx:latest 或 registry/image:tag"
              onKeyDown={(e) => e.key === 'Enter' && doPull()} />
          </div>
          <button className="btn btn-primary" onClick={doPull} disabled={pulling || !pullRef.trim()}>
            {pulling ? <Spin /> : <Download size={14} />} 拉取
          </button>
          <button className="btn" onClick={doRefresh} disabled={loading || containersLoading}>
            <RefreshCw size={14} /> 刷新
          </button>
        </div>
      )}

      {pullMsg && <div className="dm-logs-box" style={{ maxHeight: 180 }}>{pullMsg}</div>}

      {/* 子 Tab 导航 */}
      {serverId && (
        <div className="dm-sub-tabs">
          <button
            className={`dm-sub-tab${subTab === 'list' ? ' active' : ''}`}
            onClick={() => setSubTab('list')}
          >
            <Image size={13} /> 镜像列表
          </button>
          <button
            className={`dm-sub-tab${subTab === 'containers' ? ' active' : ''}`}
            onClick={() => setSubTab('containers')}
          >
            <Box size={13} /> 使用中的容器
            {!containersLoading && imagesWithContainers.length > 0 && (
              <span className="dm-sub-tab-badge">{imagesWithContainers.length}</span>
            )}
          </button>
        </div>
      )}

      {/* 镜像列表 Tab */}
      {subTab === 'list' && (
        loading ? (
          <div className="dm-empty"><Spin /> 加载中…</div>
        ) : images.length === 0 ? (
          <div className="dm-empty"><Image size={32} /> 暂无镜像</div>
        ) : (
          <div className="dm-table">
            <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 1fr 1fr 1fr auto' }}>
              <span>镜像</span><span>标签</span><span>大小</span><span>创建时间</span><span>使用容器</span><span></span>
            </div>
            {images.map((img) => {
              const imageCntrs = getImageContainers(img);
              const hasContainers = imageCntrs.length > 0;
              return (
                <div key={img.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1fr 1fr 1fr 1fr auto' }}>
                  <span style={{ fontFamily: 'monospace', fontSize: 13, minWidth: 0 }}><TruncText text={img.repo} /></span>
                  <span><code style={{ background: '#f1f5f9', padding: '2px 6px', borderRadius: 4 }}>{img.tag}</code></span>
                  <span style={{ color: '#526071' }}>{img.size}</span>
                  <span style={{ color: '#94a3b8', fontSize: 12 }}>{img.created}</span>
                  <span>
                    {containersLoading ? (
                      <Spin />
                    ) : hasContainers ? (
                      <span
                        style={{ display: 'flex', flexWrap: 'wrap', gap: 4, cursor: 'pointer' }}
                        title={imageCntrs.map(c => c.Names ?? c.ID ?? '').join(', ')}
                        onClick={() => setSubTab('containers')}
                      >
                        {imageCntrs.map((c) => (
                           <span
                             key={c.ID ?? c.Names}
                             className={`dm-status ${containerStateClass((c.State ?? '').toLowerCase())}`}
                             style={{ fontSize: 11, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                           >
                             <span className="dm-status-dot" />
                             {c.Names ?? c.ID?.slice(0, 8) ?? '—'}
                           </span>
                         ))}
                      </span>
                    ) : (
                      <span style={{ color: '#cbd5e1', fontSize: 12 }}>—</span>
                    )}
                  </span>
                  <span style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                    {img.canManage && servers.length > 1 && (
                      <button className="dm-btn-icon" title="跨服务器复制" onClick={() => { setCopyRef(`${img.repo}:${img.tag}`); setCopyDst(''); setShowCopy(true); }}>
                        <Copy size={13} />
                      </button>
                    )}
                    {img.canManage && !hasContainers && (
                      <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(`${img.repo}:${img.tag}`, img)}>
                        <Trash2 size={13} />
                      </button>
                    )}
                    {img.canManage && hasContainers && (
                      <span
                        title="该镜像正被容器使用，无法删除"
                        style={{ display: 'flex', alignItems: 'center', padding: '3px 4px', color: '#94a3b8', cursor: 'not-allowed' }}
                      >
                        <Trash2 size={13} />
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        )
      )}

      {/* 使用中的容器 Tab */}
      {subTab === 'containers' && (
        containersLoading || loading ? (
          <div className="dm-empty"><Spin /> 加载中…</div>
        ) : imagesWithContainers.length === 0 ? (
          <div className="dm-empty"><Box size={32} /> 当前无镜像被任何容器使用</div>
        ) : (
          <div style={{ display: 'grid', gap: 12 }}>
            {imagesWithContainers.map((img) => {
              const imageCntrs = getImageContainers(img);
              return (
                <div key={img.id} className="dm-card" style={{ padding: '12px 16px' }}>
                  {/* 镜像标题行 */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                    <Image size={14} style={{ color: '#7c3aed', flexShrink: 0 }} />
                    <span style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 600, color: '#1e293b' }}>
                      {img.repo}
                    </span>
                    <code style={{ background: '#f1f5f9', padding: '2px 6px', borderRadius: 4, fontSize: 12 }}>{img.tag}</code>
                    <span style={{ color: '#94a3b8', fontSize: 12 }}>{img.size}</span>
                    <span style={{ marginLeft: 'auto', fontSize: 12, color: '#526071' }}>
                      {imageCntrs.length} 个容器
                    </span>
                  </div>
                  {/* 容器列表 */}
                  <div className="dm-table">
                    <div className="dm-table-header" style={{ gridTemplateColumns: '1.5fr 1fr 1fr 1fr' }}>
                      <span>容器名称</span><span>状态</span><span>端口</span><span>创建时间</span>
                    </div>
                    {imageCntrs.map((ctr) => (
                      <div key={ctr.ID ?? ctr.Names} className="dm-table-row" style={{ gridTemplateColumns: '1.5fr 1fr 1fr 1fr' }}>
                        <span style={{ fontFamily: 'monospace', fontSize: 13, minWidth: 0 }}>
                          <TruncText text={ctr.Names ?? ctr.ID?.slice(0, 12) ?? '—'} />
                        </span>
                        <span><ContainerStateBadge state={ctr.State} /></span>
                        <span style={{ color: '#526071', fontSize: 12 }}>
                          {ctr.Ports ? <TruncText text={ctr.Ports} /> : <span style={{ color: '#cbd5e1' }}>—</span>}
                        </span>
                        <span style={{ color: '#94a3b8', fontSize: 12 }}>{ctr.CreatedAt ?? '—'}</span>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )
      )}

      {/* 跨服务器复制模态框 */}
      {showCopy && (
        <Modal title="跨服务器复制镜像" onClose={() => setShowCopy(false)}
          foot={
            <>
              <button className="btn" onClick={() => setShowCopy(false)}>取消</button>
              <button className="btn btn-primary" onClick={doCopy} disabled={copying || !copyDst || !copyRef}>
                {copying ? <Spin /> : <Copy size={14} />} 开始复制
              </button>
            </>
          }>
          {error && <Alert type="error">{error}</Alert>}
          <Alert type="info">将从当前服务器（{servers.find(s => s.id === serverId)?.name}）通过 SSH 管道传输到目标服务器，大镜像可能耗时较长。</Alert>
          <div className="dm-form-grid">
            <Field label="源镜像（repo:tag）">
              <input value={copyRef} readOnly placeholder="nginx:latest" style={{ background: '#f1f5f9', cursor: 'not-allowed' }} />
            </Field>
            <Field label="目标服务器">
              <select value={copyDst} onChange={(e) => setCopyDst(e.target.value)}>
                <option value="">请选择…</option>
                {servers.filter(s => s.id !== serverId).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>
          </div>
        </Modal>
      )}
    </div>
  );
}
