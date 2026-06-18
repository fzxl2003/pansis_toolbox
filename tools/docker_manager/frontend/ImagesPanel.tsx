// ============================================================
// Images Panel — Docker Manager
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import { Copy, Download, Image, RefreshCw, Trash2 } from 'lucide-react';
import { apiDelete, apiGet, apiPost } from '../../../frontend/src/api/client';
import type { AuthUser } from '../../../frontend/src/api/auth';
import { Alert, Modal, Field, ServerSelector, Spin, TruncText } from './components';
import { API, useErrorMsg } from './utils';
import type { DmServer, DockerImage } from './types';

export function ImagesPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  const [serverId, setServerId] = useState<string | null>(servers[0]?.id ?? null);
  const [images, setImages] = useState<DockerImage[]>([]);
  const [loading, setLoading] = useState(false);
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
    return me.role === 'admin' || s?.permissionLevel === 'manage';
  };

  const canUse = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || s?.permissionLevel === 'manage' || s?.permissionLevel === 'use';
  };

  const load = useCallback(async (sid: string) => {
    setLoading(true);
    clearError();
    try {
      const r = await apiGet<{ images: DockerImage[] }>(`${API}/servers/${sid}/images`);
      setImages(r.images);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { if (serverId) void load(serverId); }, [serverId, load]);

  async function doPull() {
    if (!serverId || !pullRef.trim()) return;
    setPulling(true);
    setPullMsg('');
    clearError();
    try {
      const r = await apiPost<{ output: string }>(`${API}/servers/${serverId}/images/pull`, { imageRef: pullRef.trim() });
      setPullMsg(r.output.slice(-400));
      void load(serverId);
      setPullRef('');
    } catch (e) {
      setError(e);
    } finally {
      setPulling(false);
    }
  }

  async function doDelete(imageRef: string) {
    if (!serverId) return;
    if (!confirm(`确定要删除镜像 ${imageRef} 吗？`)) return;
    clearError();
    try {
      await apiDelete(`${API}/servers/${serverId}/images/${encodeURIComponent(imageRef)}`);
      void load(serverId);
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

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <ServerSelector servers={servers} selected={serverId} onSelect={(id) => { setServerId(id); setImages([]); }} />
      {error && <Alert type="error">{error}</Alert>}

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
          {canManage(serverId) && servers.length > 1 && (
            <button className="btn" onClick={() => setShowCopy(true)}><Copy size={14} /> 跨服务器复制</button>
          )}
          <button className="btn" onClick={() => serverId && load(serverId)} disabled={loading}>
            <RefreshCw size={14} /> 刷新
          </button>
        </div>
      )}

      {pullMsg && <div className="dm-logs-box" style={{ maxHeight: 180 }}>{pullMsg}</div>}

      {loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : images.length === 0 ? (
        <div className="dm-empty"><Image size={32} /> 暂无镜像</div>
      ) : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 1fr 1fr auto' }}>
            <span>镜像</span><span>标签</span><span>大小</span><span>创建时间</span><span></span>
          </div>
          {images.map((img) => (
            <div key={img.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1fr 1fr 1fr auto' }}>
              <span style={{ fontFamily: 'monospace', fontSize: 13, minWidth: 0 }}><TruncText text={img.repo} /></span>
              <span><code style={{ background: '#f1f5f9', padding: '2px 6px', borderRadius: 4 }}>{img.tag}</code></span>
              <span style={{ color: '#526071' }}>{img.size}</span>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>{img.created}</span>
              <span style={{ display: 'flex', gap: 4 }}>
                {canManage(serverId) && (
                  <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(`${img.repo}:${img.tag}`)}>
                    <Trash2 size={13} />
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

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
              <input value={copyRef} onChange={(e) => setCopyRef(e.target.value)} placeholder="nginx:latest" />
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
