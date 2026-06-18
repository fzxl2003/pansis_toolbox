// ============================================================
// Volumes Panel — Docker Manager
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import { Box, Copy, HardDrive, Info, Plus, RefreshCw, Shield, Trash2 } from 'lucide-react';
import { apiDelete, apiGet, apiPost } from '../../../frontend/src/api/client';
import type { AuthUser } from '../../../frontend/src/api/auth';
import { Alert, CopyTruncText, Field, Modal, ServerSelector, Spin } from './components';
import { API, useErrorMsg } from './utils';
import type { DmServer, DockerVolume, VolumeDetail } from './types';

export function VolumesPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  const [serverId, setServerId] = useState<string | null>(servers[0]?.id ?? null);
  const [volumes, setVolumes] = useState<DockerVolume[]>([]);
  const [quota, setQuota] = useState<{ volumeTotalGb: number | null; volumeUsedGb: number | null }>({ volumeTotalGb: null, volumeUsedGb: null });
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newSizeGb, setNewSizeGb] = useState('0');
  const [creating, setCreating] = useState(false);
  // 卷复制状态
  const [copyTarget, setCopyTarget] = useState<DockerVolume | null>(null);
  const [copyDstServerId, setCopyDstServerId] = useState<string>('');
  const [copyDstName, setCopyDstName] = useState('');
  const [copying, setCopying] = useState(false);
  const [copyResult, setCopyResult] = useState<string>('');
  // 卷详情状态
  const [detailTarget, setDetailTarget] = useState<DockerVolume | null>(null);
  const [detail, setDetail] = useState<VolumeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(async (sid: string) => {
    setLoading(true);
    clearError();
    try {
      const r = await apiGet<{ volumes: DockerVolume[]; quota: { volumeTotalGb: number | null; volumeUsedGb: number | null } }>(`${API}/servers/${sid}/volumes`);
      setVolumes(r.volumes);
      setQuota(r.quota);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { if (serverId) void load(serverId); }, [serverId, load]);

  async function doCreate() {
    if (!serverId || !newName.trim()) return;
    setCreating(true);
    clearError();
    try {
      await apiPost(`${API}/servers/${serverId}/volumes`, { name: newName.trim(), sizeGb: parseFloat(newSizeGb) || 0 });
      setShowCreate(false);
      setNewName('');
      setNewSizeGb('0');
      void load(serverId);
    } catch (e) {
      setError(e);
    } finally {
      setCreating(false);
    }
  }

  async function doDelete(name: string) {
    if (!serverId || !confirm(`确定删除卷 ${name}？此操作不可恢复！`)) return;
    clearError();
    try {
      await apiDelete(`${API}/servers/${serverId}/volumes/${encodeURIComponent(name)}`);
      void load(serverId);
    } catch (e) {
      setError(e);
    }
  }

  async function openDetail(vol: DockerVolume) {
    if (!serverId) return;
    setDetailTarget(vol);
    setDetail(null);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const r = await apiGet<VolumeDetail>(`${API}/servers/${serverId}/volumes/${encodeURIComponent(vol.name)}/detail`);
      setDetail(r);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : String(e));
    } finally {
      setDetailLoading(false);
    }
  }

  function closeDetail() {
    setDetailTarget(null);
    setDetail(null);
    setDetailError(null);
  }

  function openCopy(vol: DockerVolume) {
    setCopyTarget(vol);
    setCopyDstServerId(serverId ?? servers[0]?.id ?? '');
    setCopyDstName(`${vol.name}-copy`);
    setCopyResult('');
    clearError();
  }

  async function doCopy() {
    if (!serverId || !copyTarget || !copyDstServerId || !copyDstName.trim()) return;
    setCopying(true);
    setCopyResult('');
    clearError();
    try {
      const r = await apiPost<{
        success: boolean; transferredBytes: number; dstVolumeName: string; dstServerId: string;
      }>(`${API}/volumes/copy`, {
        srcServerId: serverId,
        srcVolumeName: copyTarget.name,
        dstServerId: copyDstServerId,
        dstVolumeName: copyDstName.trim(),
      });
      const mb = (r.transferredBytes / 1024 / 1024).toFixed(1);
      setCopyResult(`✓ 复制完成！传输 ${mb} MB，目标卷：${r.dstVolumeName}`);
      if (copyDstServerId === serverId) void load(serverId);
    } catch (e) {
      setError(e);
    } finally {
      setCopying(false);
    }
  }

  const usedPct = quota.volumeTotalGb && quota.volumeUsedGb != null
    ? Math.min(100, (quota.volumeUsedGb / quota.volumeTotalGb) * 100)
    : null;

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <ServerSelector servers={servers} selected={serverId} onSelect={(id) => { setServerId(id); setVolumes([]); }} />
      {error && <Alert type="error">{error}</Alert>}

      {quota.volumeTotalGb != null && quota.volumeTotalGb > 0 && (
        <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 8, padding: '12px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 8 }}>
            <span>卷空间配额</span>
            <span style={{ color: '#526071' }}>已用 {quota.volumeUsedGb?.toFixed(2)} GB / {quota.volumeTotalGb} GB</span>
          </div>
          <div className="dm-quota-track">
            <div className={`dm-quota-fill${usedPct && usedPct > 90 ? ' danger' : usedPct && usedPct > 70 ? ' warn' : ''}`}
              style={{ width: `${usedPct ?? 0}%` }} />
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> 创建卷</button>
        <button className="btn" onClick={() => serverId && load(serverId)} disabled={loading}><RefreshCw size={14} /> 刷新</button>
      </div>

      {loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : volumes.length === 0 ? (
        <div className="dm-empty"><HardDrive size={32} /> 暂无卷</div>
      ) : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '1.4fr 1fr 1fr 1fr auto' }}>
            <span>卷名称</span><span>驱动</span><span>大小</span><span>所有者</span><span>操作</span>
          </div>
          {volumes.map((v) => (
            <div key={v.name} className="dm-table-row" style={{ gridTemplateColumns: '1.4fr 1fr 1fr 1fr auto' }}>
              <span style={{ fontFamily: 'monospace', fontSize: 13, minWidth: 0 }}><CopyTruncText text={v.name} /></span>
              <span style={{ color: '#526071' }}>{v.driver}</span>
              <span style={{ color: '#526071' }}>{v.sizeGb != null ? `${v.sizeGb} GB` : '—'}</span>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>
                {v.platformManaged ? (v.ownerUserId === me.id ? '本人' : (v.ownerUserId ?? '未知')) : '平台外'}
              </span>
              <span style={{ display: 'flex', gap: 4 }}>
                <button className="dm-btn-icon" title="卷详情" onClick={() => void openDetail(v)}>
                  <Info size={13} />
                </button>
                <button className="dm-btn-icon" title="复制卷（本地 / 跨服务器）" onClick={() => openCopy(v)}>
                  <Copy size={13} />
                </button>
                {(v.ownerUserId === me.id || me.role === 'admin') && (
                  <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(v.name)}>
                    <Trash2 size={13} />
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 创建卷弹窗 */}
      {showCreate && (
        <Modal title="创建卷" onClose={() => setShowCreate(false)}
          foot={
            <>
              <button className="btn" onClick={() => setShowCreate(false)}>取消</button>
              <button className="btn btn-primary" onClick={doCreate} disabled={creating || !newName.trim()}>
                {creating ? <Spin /> : <Plus size={14} />} 创建
              </button>
            </>
          }>
          {error && <Alert type="error">{error}</Alert>}
          <div className="dm-form-grid">
            <Field label="卷名称"><input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="my-data-volume" /></Field>
            <Field label="预估大小 (GB)">
              <input type="number" min="0" step="0.5" value={newSizeGb} onChange={(e) => setNewSizeGb(e.target.value)} />
            </Field>
          </div>
          {quota.volumeTotalGb != null && quota.volumeTotalGb > 0 && (
            <Alert type="info">剩余配额：{((quota.volumeTotalGb ?? 0) - (quota.volumeUsedGb ?? 0)).toFixed(2)} GB</Alert>
          )}
        </Modal>
      )}

      {/* 卷详情弹窗 */}
      {detailTarget && (
        <Modal
          title={`卷详情 — ${detailTarget.name.length > 28 ? detailTarget.name.slice(0, 28) + '…' : detailTarget.name}`}
          onClose={closeDetail}
          foot={<button className="btn btn-primary" onClick={closeDetail}>关闭</button>}
        >
          {detailLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#526071', padding: '24px 0', justifyContent: 'center' }}>
              <Spin /> 加载详情中…
            </div>
          )}
          {detailError && <Alert type="error">{detailError}</Alert>}
          {detail && !detailLoading && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* 基本信息 */}
              <div className="dm-perm-section">
                <div className="dm-perm-section-title"><HardDrive size={13} /> 基本信息</div>
                <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr max-content 1fr', gap: '8px 12px', fontSize: 13, alignItems: 'start' }}>
                  <span style={{ color: '#94a3b8', whiteSpace: 'nowrap' }}>卷名称：</span>
                  <span style={{ fontFamily: 'monospace', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }} title={detail.name}>{detail.name}</span>
                  <span style={{ color: '#94a3b8', whiteSpace: 'nowrap' }}>大小：</span>
                  <span>{detail.sizeGb != null ? `${detail.sizeGb} GB` : '未知'}</span>
                  <span style={{ color: '#94a3b8', whiteSpace: 'nowrap' }}>创建时间：</span>
                  <span>{detail.createdAt ? new Date(detail.createdAt).toLocaleString('zh-CN') : '未知'}</span>
                  <span style={{ color: '#94a3b8', whiteSpace: 'nowrap' }}>平台管理：</span>
                  <span>{detail.platformManaged ? '是' : '否（平台外创建）'}</span>
                </div>
              </div>

              {/* 权限角色 */}
              <div className="dm-perm-section">
                <div className="dm-perm-section-title"><Shield size={13} /> 权限角色</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ color: '#94a3b8', minWidth: 60 }}>创建者：</span>
                    {detail.roles.creator ? (
                      <span className="dm-role-tag creator">
                        {detail.roles.creator.displayName}
                        <small style={{ color: '#64748b', marginLeft: 4 }}>@{detail.roles.creator.username}</small>
                      </span>
                    ) : <span style={{ color: '#94a3b8' }}>—</span>}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ color: '#94a3b8', minWidth: 60, paddingTop: 2 }}>所有者：</span>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {detail.roles.owners.length > 0 ? detail.roles.owners.map((o) => (
                        <span key={o.userId} className="dm-role-tag owner">
                          {o.displayName}
                          <small style={{ color: '#64748b', marginLeft: 4 }}>@{o.username}</small>
                        </span>
                      )) : <span style={{ color: '#94a3b8' }}>—</span>}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ color: '#94a3b8', minWidth: 60, paddingTop: 2 }}>查看者：</span>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {detail.roles.viewers.length > 0 ? detail.roles.viewers.map((v) => (
                        <span key={v.userId} className="dm-role-tag viewer">
                          {v.displayName}
                          <small style={{ color: '#64748b', marginLeft: 4 }}>@{v.username}</small>
                        </span>
                      )) : <span style={{ color: '#94a3b8' }}>—</span>}
                    </div>
                  </div>
                </div>
              </div>

              {/* 挂载容器 */}
              <div className="dm-perm-section">
                <div className="dm-perm-section-title">
                  <Box size={13} /> 挂载容器
                  {detail.hiddenContainerCount > 0 && (
                    <span style={{ marginLeft: 8, fontSize: 11, color: '#94a3b8', fontWeight: 400 }}>
                      （另有 {detail.hiddenContainerCount} 个无权查看的容器）
                    </span>
                  )}
                </div>
                {detail.mountedContainers.length === 0 && detail.hiddenContainerCount === 0 ? (
                  <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 4 }}>无容器挂载此卷</div>
                ) : detail.mountedContainers.length === 0 ? (
                  <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 4 }}>您无权查看任何挂载容器</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                    {detail.mountedContainers.map((c) => (
                      <div key={c.id} style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        background: '#f8fafc', borderRadius: 6, padding: '6px 10px', fontSize: 12
                      }}>
                        <span style={{
                          width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                          background: c.state === 'running' ? '#22c55e' : '#94a3b8'
                        }} />
                        <span style={{ fontFamily: 'monospace', fontWeight: 600, flex: '0 0 auto' }}>{c.name}</span>
                        <span style={{ color: '#94a3b8', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.image}</span>
                        <span style={{
                          fontSize: 11, padding: '1px 6px', borderRadius: 4,
                          background: c.state === 'running' ? '#dcfce7' : '#f1f5f9',
                          color: c.state === 'running' ? '#166534' : '#526071'
                        }}>{c.status}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </Modal>
      )}

      {/* 卷复制弹窗 */}
      {copyTarget && (
        <Modal
          title={`复制卷 — ${copyTarget.name}`}
          onClose={() => { setCopyTarget(null); setCopyResult(''); }}
          foot={
            copyResult ? (
              <button className="btn btn-primary" onClick={() => { setCopyTarget(null); setCopyResult(''); }}>关闭</button>
            ) : (
              <>
                <button className="btn" onClick={() => { setCopyTarget(null); setCopyResult(''); }}>取消</button>
                <button
                  className="btn btn-primary"
                  onClick={doCopy}
                  disabled={copying || !copyDstServerId || !copyDstName.trim()
                    || (copyDstServerId === serverId && copyDstName.trim() === copyTarget.name)}
                >
                  {copying ? <Spin /> : <Copy size={14} />}
                  {copying ? ' 复制中（数据流式传输）…' : (copyDstServerId === serverId ? ' 本地复制' : ' 跨服务器复制')}
                </button>
              </>
            )
          }
        >
          {error && <Alert type="error">{error}</Alert>}
          {copyResult ? (
            <Alert type="success">{copyResult}</Alert>
          ) : (
            <>
              {copyDstServerId === serverId ? (
                <Alert type="info">
                  <strong>本地复制</strong>：在同一服务器内新建一个卷并复制数据，通过 <code>tar</code> 管道完成，速度较快。
                </Alert>
              ) : (
                <Alert type="info">
                  <strong>跨服务器复制</strong>：通过 <code>tar | SSH 管道</code> 流式传输，数据不在任何中间节点落盘。
                  复制时间取决于卷数据量和网络速度。
                </Alert>
              )}
              <div className="dm-form-grid">
                <Field label="源服务器">
                  <input value={servers.find((s) => s.id === serverId)?.name ?? serverId ?? ''} disabled />
                </Field>
                <Field label="源卷名称">
                  <input value={copyTarget.name} disabled style={{ fontFamily: 'monospace' }} />
                </Field>
                <Field label="目标服务器">
                  <select value={copyDstServerId} onChange={(e) => setCopyDstServerId(e.target.value)}>
                    {servers.map((s) => (
                      <option key={s.id} value={s.id}>{s.name} ({s.host})</option>
                    ))}
                  </select>
                </Field>
                <Field label="目标卷名称">
                  <input
                    value={copyDstName}
                    onChange={(e) => setCopyDstName(e.target.value)}
                    placeholder={`${copyTarget.name}-copy`}
                    style={{ fontFamily: 'monospace' }}
                  />
                </Field>
              </div>
              {copyDstServerId === serverId && copyDstName.trim() === copyTarget.name && (
                <Alert type="error">目标卷名称不能与源卷名称相同（同一服务器内复制时）</Alert>
              )}
            </>
          )}
        </Modal>
      )}
    </div>
  );
}
