// ============================================================
// TensorBoard Dashboard Tool — Shared UI Components
// ============================================================

import { useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { AlertCircle, CheckCircle, Loader2, X } from 'lucide-react';

// ---- Alert ----

export function Alert({ type, children }: { type: 'error' | 'success' | 'info'; children: ReactNode }) {
  const icon = type === 'success' ? <CheckCircle size={16} /> : <AlertCircle size={16} />;
  return <div className={`tb-alert ${type}`}>{icon}<span>{children}</span></div>;
}

// ---- Spin ----

export function Spin({ size = 16 }: { size?: number }) {
  return <Loader2 size={size} className="spin" style={{ display: 'inline-block' }} />;
}

// ---- EmptyState ----

export function EmptyState({ icon, title, hint }: { icon?: ReactNode; title: string; hint?: string }) {
  return (
    <div className="tb-empty">
      {icon && <div className="tb-empty-icon">{icon}</div>}
      <div className="tb-empty-title">{title}</div>
      {hint && <div className="tb-empty-hint">{hint}</div>}
    </div>
  );
}

// ---- Modal ----

export function Modal({
  title,
  onClose,
  children,
  foot,
  width,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  foot?: ReactNode;
  width?: number;
}) {
  return (
    <div className="tb-modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="tb-modal" style={width ? { width } : undefined}>
        <div className="tb-modal-head">
          <h3>{title}</h3>
          <button className="tb-btn-icon" onClick={onClose} type="button"><X size={16} /></button>
        </div>
        <div className="tb-modal-body">{children}</div>
        {foot && <div className="tb-modal-foot">{foot}</div>}
      </div>
    </div>
  );
}

// ---- ConfirmModal ----

export function ConfirmModal({
  title,
  message,
  confirmLabel = '确认',
  cancelLabel = '取消',
  danger,
  onConfirm,
  onClose,
}: {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <div className="tb-confirm-body">{message}</div>
      <div className="tb-modal-foot" style={{ marginTop: 16 }}>
        <button className="tb-btn tb-btn-secondary" onClick={onClose} type="button">{cancelLabel}</button>
        <button
          className={`tb-btn ${danger ? 'tb-btn-danger' : 'tb-btn-primary'}`}
          onClick={() => { onConfirm(); onClose(); }}
          type="button"
        >
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

// ---- Field ----

export function Field({
  label,
  children,
  full,
  style,
}: {
  label: ReactNode;
  children: ReactNode;
  full?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div className={`tb-form-field${full ? ' tb-full-col' : ''}`} style={style}>
      <label>{label}</label>
      {children}
    </div>
  );
}

// ---- Confirm hook ----

export function useConfirm() {
  const [state, setState] = useState<{
    open: boolean;
    title: string;
    message: ReactNode;
    onConfirm: () => void;
  }>({ open: false, title: '', message: '', onConfirm: () => {} });

  function confirm(options: { title: string; message: ReactNode; onConfirm: () => void }) {
    setState({ open: true, ...options });
  }

  function close() {
    setState((s) => ({ ...s, open: false }));
  }

  const dialog = state.open ? (
    <ConfirmModal
      title={state.title}
      message={state.message}
      onConfirm={state.onConfirm}
      onClose={close}
    />
  ) : null;

  return { confirm, dialog };
}

// ---- Badge ----

export function Badge({ children, color = 'default' }: { children: ReactNode; color?: 'default' | 'green' | 'red' | 'blue' | 'amber' }) {
  return <span className={`tb-badge tb-badge-${color}`}>{children}</span>;
}
