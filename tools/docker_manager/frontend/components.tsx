// ============================================================
// Shared UI Components — Docker Manager
// ============================================================

import { useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import {
  AlertCircle,
  CheckCircle,
  ChevronDown,
  ChevronUp,
  Cpu,
  Database,
  HardDrive,
  Loader2,
  Server,
  X,
} from 'lucide-react';
import type { ServerResourceOverview } from './types';
import { permColor, permLabel } from './utils';
import type { DmServer } from './types';

// ---- 基础组件 ----

export function Alert({ type, children }: { type: 'error' | 'success' | 'info'; children: ReactNode }) {
  const icon = type === 'error' ? <AlertCircle size={16} /> : type === 'success' ? <CheckCircle size={16} /> : <AlertCircle size={16} />;
  return <div className={`dm-alert ${type}`}>{icon}<span>{children}</span></div>;
}

export function Spin() {
  return <Loader2 size={16} className="spin" style={{ display: 'inline-block' }} />;
}

// 骨架屏行：cols 是每列宽度比例数组，传几个就画几格占位
export function SkeletonRows({ cols, rows = 5 }: { cols: string[]; rows?: number }) {
  return (
    <div className="dm-table">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="dm-skeleton-row" style={{ gridTemplateColumns: cols.join(' ') }}>
          {cols.map((_, j) => (
            <div key={j} className={`dm-skeleton-cell ${j === 0 ? 'medium' : j % 2 === 0 ? 'short' : 'long'}`} />
          ))}
        </div>
      ))}
    </div>
  );
}

// 带加载遮罩的容器
export function ResourceLoadingWrapper({ loading, children }: { loading: boolean; children: ReactNode }) {
  return (
    <div className="dm-resource-loading">
      {children}
      {loading && (
        <div className="dm-resource-loading-overlay">
          <div className="dm-resource-loading-spinner" />
          <span>正在从服务器获取数据…</span>
        </div>
      )}
    </div>
  );
}

/** 超长文本省略显示，鼠标悬停时以 CSS 气泡显示完整内容 */
export function TruncText({ text, style: extraStyle }: { text: string; style?: React.CSSProperties }) {
  return (
    <span
      className="dm-tooltip-wrap"
      data-tip={text}
      style={extraStyle}
    >
      {text}
    </span>
  );
}

/**
 * 省略号截断文本，鼠标悬停显示全局置顶气泡（含「点击复制」提示），
 * 点击后复制到剪切板并短暂显示「已复制！」反馈。
 * 气泡使用 position:fixed 渲染，不受父级 overflow:hidden 影响。
 */
export function CopyTruncText({ text, style: extraStyle }: { text: string; style?: React.CSSProperties }) {
  const [copied, setCopied] = useState(false);
  const [tipPos, setTipPos] = useState<{ x: number; y: number } | null>(null);
  const wrapRef = useRef<HTMLSpanElement>(null);

  function handleMouseEnter() {
    if (!wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    setTipPos({ x: rect.left, y: rect.top });
  }

  function handleMouseLeave() {
    if (!copied) setTipPos(null);
  }

  function handleClick(e: React.MouseEvent) {
    e.stopPropagation();
    navigator.clipboard.writeText(text).catch(() => {
      const el = document.createElement('textarea');
      el.value = text;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
    });
    setCopied(true);
    setTimeout(() => {
      setCopied(false);
      setTipPos(null);
    }, 1600);
  }

  return (
    <>
      <span
        ref={wrapRef}
        className="dm-copy-trunc-wrap"
        onClick={handleClick}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        style={extraStyle}
      >
        <span className="dm-copy-trunc-text">{text}</span>
      </span>
      {tipPos && (
        <span
          className="dm-copy-trunc-tip-fixed"
          style={{ left: tipPos.x, top: tipPos.y - 8 }}
          onMouseEnter={() => setTipPos(tipPos)}
          onMouseLeave={() => { if (!copied) setTipPos(null); }}
        >
          {copied ? '✓ 已复制！' : text + '\n── 点击复制'}
        </span>
      )}
    </>
  );
}

export function Modal({ title, onClose, children, foot, wide }: { title: string; onClose: () => void; children: ReactNode; foot?: ReactNode; wide?: boolean }) {
  return (
    <div className="dm-modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dm-modal${wide ? ' wide' : ''}`}>
        <div className="dm-modal-head">
          <h3>{title}</h3>
          <button className="dm-btn-icon" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="dm-modal-body">{children}</div>
        {foot && <div className="dm-modal-foot">{foot}</div>}
      </div>
    </div>
  );
}

export function Field({ label, children, full, style }: { label: string; children: ReactNode; full?: boolean; style?: CSSProperties }) {
  return (
    <div className={`dm-form-field${full ? ' dm-full-col' : ''}`} style={style}>
      <label>{label}</label>
      {children}
    </div>
  );
}

export function ServerSelector({ servers, selected, onSelect }: { servers: DmServer[]; selected: string | null; onSelect: (id: string) => void }) {
  if (servers.length === 0) return <Alert type="info">暂无可访问的服务器，请联系管理员添加并授权</Alert>;
  return (
    <div className="dm-server-selector">
      <span style={{ fontSize: 13, color: '#526071', marginRight: 4 }}>选择服务器：</span>
      {servers.map((s) => (
        <button key={s.id} className={`dm-server-chip${selected === s.id ? ' active' : ''}`} onClick={() => onSelect(s.id)}>
          <Server size={13} />
          {s.name}
          <span className={`dm-perm-badge ${permColor(s.permissionLevel)}`}>{permLabel(s.permissionLevel)}</span>
        </button>
      ))}
    </div>
  );
}

// ---- 资源占用进度条组件 ----

function ProgressBar({ segments }: {
  segments: Array<{ value: number; max: number; color: string; label: string }>;
}) {
  const total = segments[0]?.max || 1;
  return (
    <div className="dm-progress-track">
      {segments.map((seg, i) => {
        const pct = total > 0 ? Math.min(100, (seg.value / total) * 100) : 0;
        return (
          <div
            key={i}
            className="dm-progress-fill"
            style={{ width: `${pct}%`, background: seg.color }}
            title={`${seg.label}: ${seg.value.toFixed(1)} GB`}
          />
        );
      })}
    </div>
  );
}

function fmtGb(v: number | null | undefined, fallback = '–') {
  if (v == null) return fallback;
  return v < 1 ? `${(v * 1024).toFixed(0)} MB` : `${v.toFixed(1)} GB`;
}

/**
 * ResourceUsagePanel：在服务器选择下方展示可折叠的资源占用情况
 * @param overview – 后端 /resource-overview 的返回数据
 * @param resourceType – 当前 tab 类型：'container' | 'image' | 'volume'
 * @param loading – 是否正在加载
 */
export function ResourceUsagePanel({
  overview,
  resourceType,
  loading = false,
}: {
  overview: ServerResourceOverview | null;
  resourceType: 'container' | 'image' | 'volume';
  loading?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  if (!overview && !loading) return null;

  const vol = overview?.volume;
  const cuda = overview?.cuda;
  const quotaMode = overview?.quotaModes?.[resourceType] ?? 'shared';

  const volQuota = vol?.quotaGb ?? 0;
  const volUsed = vol?.usedSelfGb ?? 0;
  const volExclusive = vol?.exclusiveUsedGb ?? 0;
  const volShared = vol?.sharedUsedGb ?? 0;
  const volTotal = vol?.usedTotalGb ?? 0;
  const volRemaining = vol?.remainingGb ?? null;

  const gpuTotal = cuda?.totalGpuCount ?? 0;
  const gpuAllowed = cuda?.allowedGpuIndices?.length ?? 0;

  return (
    <div className="dm-resource-usage-panel">
      <button
        className="dm-resource-usage-toggle"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        <span>资源占用概览</span>
        {loading && <Loader2 size={12} className="spin" style={{ marginLeft: 4 }} />}
        {!loading && overview && (
          <span className="dm-resource-usage-summary">
            {resourceType === 'container' && cuda?.serverHasCuda && (
              <span className="dm-usage-chip gpu">
                <Cpu size={11} /> GPU {gpuAllowed}/{gpuTotal}
              </span>
            )}
            {volQuota > 0 && (
              <span className={`dm-usage-chip${volRemaining !== null && volRemaining < volQuota * 0.2 ? ' warn' : ''}`}>
                <HardDrive size={11} /> {fmtGb(volUsed)}/{fmtGb(volQuota)}
              </span>
            )}
          </span>
        )}
      </button>

      {expanded && (
        <div className="dm-resource-usage-body">
          {loading ? (
            <div style={{ padding: '12px 0', color: '#94a3b8', fontSize: 12 }}>
              <Loader2 size={14} className="spin" style={{ marginRight: 6 }} />正在加载资源概览…
            </div>
          ) : overview ? (
            <div className="dm-resource-usage-content">
              {/* 卷配额区域 */}
              {volQuota > 0 && (
                <div className="dm-usage-section">
                  <div className="dm-usage-section-title">
                    <Database size={13} />
                    卷配额
                    <span className="dm-usage-mode-tag">{quotaMode === 'exclusive' ? '独占模式' : '均分模式'}</span>
                  </div>
                  <div className="dm-usage-row">
                    <span>我的占用</span>
                    <span>{fmtGb(volUsed)}</span>
                  </div>
                  {quotaMode === 'exclusive' && volExclusive > 0 && (
                    <div className="dm-usage-row accent">
                      <span>独占配额使用</span>
                      <span>{fmtGb(volExclusive)}</span>
                    </div>
                  )}
                  <div className="dm-usage-row muted">
                    <span>共享区占用</span>
                    <span>{fmtGb(volShared)}</span>
                  </div>
                  <div className="dm-usage-row muted">
                    <span>总占用</span>
                    <span>{fmtGb(volTotal)}</span>
                  </div>
                  <div className="dm-usage-row">
                    <span>配额上限</span>
                    <span className="dm-usage-quota">{fmtGb(volQuota)}</span>
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <ProgressBar segments={[
                      { value: volExclusive, max: volQuota, color: '#f59e0b', label: '独占配额' },
                      { value: volShared, max: volQuota, color: '#6366f1', label: '共享占用' },
                    ]} />
                    <div className="dm-progress-legend">
                      {volExclusive > 0 && (
                        <span><span className="dm-legend-dot" style={{ background: '#f59e0b' }} />独占</span>
                      )}
                      <span><span className="dm-legend-dot" style={{ background: '#6366f1' }} />共享</span>
                      <span><span className="dm-legend-dot" style={{ background: '#e2e8f0' }} />剩余 {fmtGb(volRemaining)}</span>
                    </div>
                  </div>
                </div>
              )}

              {/* GPU 区域（仅容器 Tab） */}
              {resourceType === 'container' && cuda?.serverHasCuda && (
                <div className="dm-usage-section">
                  <div className="dm-usage-section-title"><Cpu size={13} /> 可用 GPU</div>
                  <div className="dm-usage-row">
                    <span>有权使用</span>
                    <span>{gpuAllowed} / {gpuTotal} 张</span>
                  </div>
                  {cuda.availableGpus.length > 0 && (
                    <div className="dm-gpu-chips">
                      {cuda.availableGpus.map((g) => (
                        <span key={g.index} className="dm-gpu-chip" title={g.name}>
                          #{g.index} {g.name?.replace(/NVIDIA/i, '').trim()}
                          {g.memTotalMb ? ` (${(g.memTotalMb / 1024).toFixed(0)}G)` : ''}
                        </span>
                      ))}
                    </div>
                  )}
                  <div style={{ marginTop: 8 }}>
                    <ProgressBar segments={[
                      { value: gpuAllowed, max: Math.max(gpuTotal, 1), color: '#10b981', label: '可用 GPU' },
                    ]} />
                  </div>
                </div>
              )}

              {/* 挂载路径磁盘空间 */}
              {overview.paths.length > 0 && (
                <div className="dm-usage-section">
                  <div className="dm-usage-section-title"><HardDrive size={13} /> 路径磁盘空间</div>
                  {overview.paths.map((p) => (
                    <div key={p.path} className="dm-path-row">
                      <div className="dm-path-label" title={p.path}>{p.path}</div>
                      <div className="dm-usage-row muted">
                        <span>已用 / 总量</span>
                        <span>{fmtGb(p.usedGb)} / {fmtGb(p.totalGb)}</span>
                      </div>
                      {p.totalGb && p.usedGb != null && (
                        <ProgressBar segments={[
                          { value: p.usedGb, max: p.totalGb, color: '#3b82f6', label: '已用' },
                        ]} />
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
