import { useEffect, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { AlertCircle, CheckCircle, Info, Loader2, X } from 'lucide-react';

export function Alert({ type, children }: { type: 'error' | 'success' | 'info'; children: ReactNode }) {
  const Icon = type === 'success' ? CheckCircle : type === 'info' ? Info : AlertCircle;
  return <div className={`csm-alert ${type}`}><Icon size={16} /><span>{children}</span></div>;
}

export function Spin({ size = 16 }: { size?: number }) {
  return <Loader2 size={size} className="csm-spin" />;
}

export function EmptyState({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: string }) {
  return <div className="csm-empty">{icon && <div>{icon}</div>}<strong>{title}</strong>{hint && <span>{hint}</span>}</div>;
}

export function Field({ label, children, full, style }: { label: ReactNode; children: ReactNode; full?: boolean; style?: CSSProperties }) {
  return <div className={`csm-form-field${full ? ' csm-full-col' : ''}`} style={style}><label>{label}</label>{children}</div>;
}

export function Modal({ title, onClose, children, foot, width }: { title: string; onClose: () => void; children: ReactNode; foot?: ReactNode; width?: number }) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) { if (event.key === 'Escape') onClose(); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return <div className="csm-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><section className="csm-modal" role="dialog" aria-modal="true" aria-label={title} style={width ? { width } : undefined}><header className="csm-modal-head"><h3>{title}</h3><button className="csm-btn-icon" type="button" aria-label="关闭" onClick={onClose}><X size={17} /></button></header><div className="csm-modal-body">{children}</div>{foot && <footer className="csm-modal-foot">{foot}</footer>}</section></div>;
}

export function Badge({ children, color = 'default' }: { children: ReactNode; color?: 'default' | 'green' | 'red' | 'blue' | 'amber' }) {
  return <span className={`csm-badge csm-badge-${color}`}>{children}</span>;
}

export function useConfirm() {
  const [state, setState] = useState<{ title: string; message: ReactNode; onConfirm: () => void | Promise<void> } | null>(null);
  const [busy, setBusy] = useState(false);
  const dialog = state && <Modal title={state.title} onClose={() => !busy && setState(null)} foot={<><button className="csm-btn csm-btn-secondary" type="button" disabled={busy} onClick={() => setState(null)}>取消</button><button className="csm-btn csm-btn-danger" type="button" disabled={busy} onClick={() => { setBusy(true); Promise.resolve(state.onConfirm()).finally(() => { setBusy(false); setState(null); }); }}>{busy ? <><Spin size={14} />处理中</> : '确认'}</button></>}><div className="csm-confirm-body">{state.message}</div></Modal>;
  return { confirm: (value: { title: string; message: ReactNode; onConfirm: () => void | Promise<void> }) => setState(value), dialog };
}
