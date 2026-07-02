// ============================================================
// SSH Workspace Tool — Utilities
// ============================================================

import { ApiError } from '../../../frontend/src/api/client';

export const API = '/api/tools/ssh-workspace';

// ---- Error handling ----

export function messageFromError(exc: unknown): string {
  if (exc instanceof ApiError) return exc.message;
  if (exc instanceof Error) return exc.message;
  return '操作失败';
}

// ---- Time formatting ----

export function formatRelativeTime(iso: string | null): string {
  if (!iso) return '从未';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '未知';
  const diffSec = Math.floor((Date.now() - then) / 1000);
  if (diffSec < 0) return '刚刚';
  if (diffSec < 60) return `${diffSec} 秒前`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分钟前`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小时前`;
  return `${Math.floor(diffSec / 86400)} 天前`;
}

export function intervalLabel(sec: number): string {
  if (sec >= 86400) return `${(sec / 86400).toFixed(1)} 天`;
  if (sec >= 3600) return `${(sec / 3600).toFixed(1)} 小时`;
  if (sec >= 60) return `${(sec / 60).toFixed(1)} 分钟`;
  return `${sec} 秒`;
}

// ---- ID generation (for client-side tab IDs) ----

export function genId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

// ---- Label helpers ----

export function serverLabel(name: string, mode: string, screenSession: string): string {
  if (mode === 'native') return name;
  if (mode === 'screen_new') return `${name} · 新建 screen`;
  if (mode === 'screen_existing') return `${name} · ${screenSession}`;
  return name;
}

// ---- WebSocket URL ----

export function buildTerminalWsUrl(
  serverId: string,
  mode: string,
  screenSession: string,
  cols: number,
  rows: number,
): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const base = `${proto}://${window.location.host}${API}/ws/terminal`;
  const params = new URLSearchParams({
    serverId,
    mode,
    cols: String(cols),
    rows: String(rows),
  });
  if (screenSession) params.set('screenSession', screenSession);
  return `${base}?${params.toString()}`;
}
