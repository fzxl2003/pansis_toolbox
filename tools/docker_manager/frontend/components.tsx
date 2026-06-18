// ============================================================
// Shared UI Components — Docker Manager
// ============================================================

import { useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  AlertCircle,
  CheckCircle,
  Loader2,
  Server,
  X,
} from 'lucide-react';
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

export function Field({ label, children, full }: { label: string; children: ReactNode; full?: boolean }) {
  return (
    <div className={`dm-form-field${full ? ' dm-full-col' : ''}`}>
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
