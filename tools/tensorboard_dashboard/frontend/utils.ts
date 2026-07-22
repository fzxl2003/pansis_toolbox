// ============================================================
// TensorBoard Dashboard Tool — Utilities
// ============================================================

import { ApiError } from '../../../frontend/src/api/client';

export const API = '/api/tools/tensorboard-dashboard';

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

// ---- Status helpers ----

export function statusLabel(status: string): string {
  switch (status) {
    case 'starting': return '启动中';
    case 'running': return '运行中';
    case 'stopped': return '已停止';
    case 'failed': return '已失败';
    default: return status;
  }
}

export function statusColor(status: string): 'green' | 'red' | 'blue' | 'amber' | 'default' {
  switch (status) {
    case 'running': return 'green';
    case 'failed': return 'red';
    case 'starting': return 'amber';
    case 'stopped': return 'default';
    default: return 'default';
  }
}
