// ============================================================
// Servers Overview Panel — Docker Manager
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import { ChevronRight, Cpu, Database, HardDrive, Server } from 'lucide-react';
import { apiGet } from '../../../frontend/src/api/client';
import type { AuthUser } from '../../../frontend/src/api/auth';
import { permColor, permLabel, formatSize, API } from './utils';
import type { DmServer, ServerResourceOverview } from './types';
import { Spin } from './components';

// ---- 资源概览卡片 ----

function VolumeQuotaCard({ vol }: { vol: ServerResourceOverview['volume'] }) {
  const unlimitd = vol.quotaGb === 0;
  const usedPct = unlimitd || vol.quotaGb === 0
    ? null
    : Math.min(100, (vol.usedSelfGb / vol.quotaGb) * 100);

  return (
    <div className="dm-overview-card">
      <div className="dm-overview-card-header">
        <Database size={13} /> 卷配额
      </div>
      <div className="dm-overview-card-body">
        {unlimitd ? (
          <div className="dm-overview-row">
            <span className="dm-overview-label">已用（我）</span>
            <span className="dm-overview-value">{formatSize(vol.usedSelfGb)}</span>
          </div>
        ) : (
          <>
            <div className="dm-overview-row">
              <span className="dm-overview-label">配额</span>
              <span className="dm-overview-value">{formatSize(vol.quotaGb)}</span>
            </div>
            <div className="dm-overview-row">
              <span className="dm-overview-label">已用</span>
              <span className="dm-overview-value">{formatSize(vol.usedSelfGb)}</span>
            </div>
            <div className="dm-overview-row">
              <span className="dm-overview-label">剩余</span>
              <span className="dm-overview-value" style={{ color: (vol.remainingGb ?? 0) < vol.quotaGb * 0.2 ? '#ef4444' : '#22c55e' }}>
                {vol.remainingGb !== null ? formatSize(vol.remainingGb) : '不限'}
              </span>
            </div>
            {usedPct !== null && (
              <div className="dm-overview-progress">
                <div
                  className="dm-overview-progress-bar"
                  style={{
                    width: `${usedPct}%`,
                    background: usedPct > 80 ? '#ef4444' : usedPct > 50 ? '#f59e0b' : '#22c55e',
                  }}
                />
              </div>
            )}
          </>
        )}
        <div className="dm-overview-row" style={{ marginTop: 4 }}>
          <span className="dm-overview-label" style={{ color: '#94a3b8' }}>全服务器已用</span>
          <span className="dm-overview-value" style={{ color: '#94a3b8' }}>{formatSize(vol.usedTotalGb)}</span>
        </div>
      </div>
    </div>
  );
}

function PathDiskCard({ paths }: { paths: ServerResourceOverview['paths'] }) {
  if (paths.length === 0) return null;
  return (
    <div className="dm-overview-card">
      <div className="dm-overview-card-header">
        <HardDrive size={13} /> 挂载路径磁盘
      </div>
      <div className="dm-overview-card-body">
        {paths.map((p) => {
          const usedPct = p.totalGb && p.usedGb !== null
            ? Math.min(100, (p.usedGb / p.totalGb) * 100)
            : null;
          return (
            <div key={p.path} style={{ marginBottom: 10 }}>
              <div style={{ fontFamily: 'monospace', fontSize: 11, color: '#475569', marginBottom: 3, wordBreak: 'break-all' }}>
                {p.path}
              </div>
              {p.totalGb !== null ? (
                <>
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    <span className="dm-overview-mini-label">总量 <strong>{formatSize(p.totalGb)}</strong></span>
                    {p.usedGb !== null && <span className="dm-overview-mini-label">已用 <strong>{formatSize(p.usedGb)}</strong></span>}
                    {p.availGb !== null && <span className="dm-overview-mini-label" style={{ color: '#22c55e' }}>可用 <strong>{formatSize(p.availGb)}</strong></span>}
                    {p.pathUsedGb !== null && <span className="dm-overview-mini-label">路径占用 <strong>{formatSize(p.pathUsedGb)}</strong></span>}
                  </div>
                  {usedPct !== null && (
                    <div className="dm-overview-progress" style={{ marginTop: 4 }}>
                      <div
                        className="dm-overview-progress-bar"
                        style={{
                          width: `${usedPct}%`,
                          background: usedPct > 85 ? '#ef4444' : usedPct > 60 ? '#f59e0b' : '#22c55e',
                        }}
                      />
                    </div>
                  )}
                </>
              ) : (
                <span style={{ fontSize: 12, color: '#94a3b8' }}>无法获取磁盘信息</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CudaCard({ cuda }: { cuda: ServerResourceOverview['cuda'] }) {
  if (!cuda.serverHasCuda) return null;
  return (
    <div className="dm-overview-card">
      <div className="dm-overview-card-header">
        <Cpu size={13} /> CUDA / GPU
      </div>
      <div className="dm-overview-card-body">
        {cuda.availableGpus.length === 0 ? (
          <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无分配显卡权限</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {cuda.availableGpus.map((gpu) => (
              <div key={gpu.index} className="dm-cuda-gpu-row">
                <span className="dm-cuda-badge" style={{ flexShrink: 0 }}><Cpu size={10} /> GPU {gpu.index}</span>
                <span style={{ fontSize: 12, color: '#1e293b', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{gpu.name}</span>
                <span style={{ fontSize: 11, color: '#94a3b8', flexShrink: 0 }}>{gpu.memoryTotal}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- 单台服务器的资源概览行 ----

function ServerOverviewRow({ server }: { server: DmServer }) {
  const [overview, setOverview] = useState<ServerResourceOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async () => {
    if (overview) return; // 已加载
    setLoading(true);
    try {
      const r = await apiGet<ServerResourceOverview>(`${API}/servers/${server.id}/resource-overview`);
      setOverview(r);
    } catch {
      // 静默失败
    } finally {
      setLoading(false);
    }
  }, [server.id, overview]);

  function toggle() {
    setExpanded((v) => !v);
    if (!overview && !loading) void load();
  }

  return (
    <div className="dm-card" style={{ padding: 0, overflow: 'hidden' }}>
      {/* 服务器标题行 */}
      <div
        className="dm-card-header"
        style={{ padding: '12px 16px', cursor: 'pointer', userSelect: 'none' }}
        onClick={toggle}
      >
        <span className="dm-card-title">
          <Server size={15} style={{ display: 'inline', marginRight: 6, verticalAlign: 'middle' }} />
          {server.name}
          {server.cudaAvailable && (
            <span style={{ marginLeft: 8, display: 'inline-flex', alignItems: 'center', gap: 3 }}>
              {(server.gpuInfo && server.gpuInfo.length > 0)
                ? server.gpuInfo.map((g) => (
                    <span key={g.index} className="dm-cuda-badge" style={{ fontSize: 10 }}>
                      <Cpu size={9} /> #{g.index}
                    </span>
                  ))
                : (
                    <span className="dm-cuda-badge" style={{ fontSize: 10 }}>
                      <Cpu size={9} /> {server.gpuCount ?? 0} GPU
                    </span>
                  )
              }
            </span>
          )}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={`dm-perm-badge ${permColor(server.permissionLevel)}`}>{permLabel(server.permissionLevel)}</span>
          <ChevronRight
            size={14}
            style={{
              color: '#94a3b8',
              transition: 'transform 0.2s',
              transform: expanded ? 'rotate(90deg)' : 'none',
            }}
          />
        </span>
      </div>

      {/* 连接信息（常显示） */}
      <div className="dm-card-meta" style={{ padding: '0 16px 10px' }}>
        <span>🖥 {server.host}:{server.port}</span>
        <span>👤 {server.sshUsername}</span>
        <span style={{ color: '#94a3b8', fontSize: 12 }}>添加于 {server.createdAt.slice(0, 10)}</span>
      </div>

      {/* 展开后的资源概览卡片 */}
      {expanded && (
        <div style={{ borderTop: '1px solid #f1f5f9', padding: '12px 16px' }}>
          {loading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#64748b' }}>
              <Spin /> 加载资源信息…
            </div>
          ) : overview ? (
            <div className="dm-overview-grid">
              <VolumeQuotaCard vol={overview.volume} />
              <PathDiskCard paths={overview.paths} />
              {overview.cuda.serverHasCuda && <CudaCard cuda={overview.cuda} />}
            </div>
          ) : (
            <div style={{ fontSize: 12, color: '#94a3b8' }}>无法加载资源信息</div>
          )}
        </div>
      )}
    </div>
  );
}

// ---- 主面板 ----

export function ServersOverviewPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  return (
    <div style={{ display: 'grid', gap: 12 }}>
      {servers.length === 0 ? (
        <div className="dm-empty"><Server size={32} /> 暂无可访问的服务器</div>
      ) : (
        servers.map((s) => <ServerOverviewRow key={s.id} server={s} />)
      )}
    </div>
  );
}
