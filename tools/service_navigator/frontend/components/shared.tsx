import type { ReactNode } from "react";
import { X } from "lucide-react";
import type { Run } from "../types";

export function Modal({
  title,
  onClose,
  children,
  className = "",
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className="sn-modal-backdrop"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section className={`sn-modal ${className}`}>
        <header>
          <h2>{title}</h2>
          <button className="sn-icon-button" onClick={onClose}>
            <X size={17} />
          </button>
        </header>
        <div className="sn-modal-body">{children}</div>
      </section>
    </div>
  );
}

export function Field({
  label,
  children,
  full = false,
}: {
  label: string;
  children: ReactNode;
  full?: boolean;
}) {
  return (
    <label className={`sn-field${full ? " full" : ""}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}

export function Panel({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <header className="sn-panel-heading">
      <div>
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {action}
    </header>
  );
}

export function Metric({
  label,
  value,
  onClick,
}: {
  label: string;
  value: number;
  onClick: () => void;
}) {
  return (
    <button className="sn-metric" onClick={onClick}>
      <span>{label}</span>
      <strong>{value}</strong>
    </button>
  );
}

export function Alert({ text, error = false }: { text: string; error?: boolean }) {
  return <div className={`sn-alert ${error ? "error" : "success"}`}>{text}</div>;
}

export function Progress({ run }: { run: Run }) {
  const total = run.summary.portCount ?? run.summary.targetCount ?? 0;
  const done = run.summary.completedPortCount ?? run.summary.completedTargetCount ?? 0;
  const percent = total ? Math.round((done * 100) / total) : 0;
  return (
    <span className="sn-progress">
      <span>
        扫描进度 {done}/{total}
      </span>
      <span className="sn-progress-track">
        <i style={{ width: `${percent}%` }} />
      </span>
      <small>{percent}%</small>
    </span>
  );
}

export function RunRow({ run }: { run: Run }) {
  return (
    <div className="sn-run-list">
      <div>
        <span className="sn-badge">{run.status}</span>
        <span>{new Date(run.requestedAt).toLocaleString()}</span>
        {(run.status === "running" || run.status === "queued") && (
          <Progress run={run} />
        )}
        {run.error && <small className="sn-error-text">{run.error}</small>}
      </div>
    </div>
  );
}
