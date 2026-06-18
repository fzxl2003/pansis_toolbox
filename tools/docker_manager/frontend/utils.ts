// ============================================================
// Utils — Docker Manager
// ============================================================

import { useCallback, useState } from 'react';
import { ApiError } from '../../../frontend/src/api/client';
import type { PermLevel } from './types';

// API 基础路径
export const API = '/api/tools/docker-manager';

// ---- 权限辅助 ----

export function permColor(level: PermLevel): string {
  if (level === 'manage') return 'manage';
  if (level === 'use') return 'use';
  return 'view';
}

export function permLabel(level: PermLevel): string {
  const m: Record<PermLevel, string> = { manage: '管理', use: '使用', view: '查看', none: '无权限' };
  return m[level] ?? level;
}

// ---- 容器状态辅助 ----

export function containerStateClass(state?: string): string {
  const s = (state ?? '').toLowerCase();
  if (s.includes('running') || s === 'up') return 'running';
  if (s.includes('exited')) return 'exited';
  if (s.includes('paused')) return 'paused';
  if (s.includes('created')) return 'created';
  return 'unknown';
}

// 解析状态时间，如 "Up 2 hours" → 提取 "Up" 和 "2 hours" 两部分
export function parseContainerStatus(status: string): { label: string; time: string } {
  const s = (status ?? '').trim();
  // "Up X hours/minutes/seconds" → label=Up, time=X hours
  const upMatch = s.match(/^(Up)\s+(.+?)(\s*\(.*\))?$/i);
  if (upMatch) return { label: 'Up', time: upMatch[2].trim() };
  // "Exited (N) X hours ago" → label=Exited, time=X hours ago
  const exitMatch = s.match(/^(Exited\s*(?:\(\d+\))?)\s+(.+)$/i);
  if (exitMatch) return { label: exitMatch[1].trim(), time: exitMatch[2].trim() };
  // "Created", "Paused", "Restarting"…
  const wordMatch = s.match(/^(\w+)\s*(.*)$/);
  if (wordMatch) return { label: wordMatch[1], time: wordMatch[2].trim() };
  return { label: s, time: '' };
}

// ---- 错误 Hook ----

export function useErrorMsg(): [string | null, (e: unknown) => void, () => void] {
  const [msg, setMsg] = useState<string | null>(null);
  const set = useCallback((e: unknown) => {
    if (e instanceof ApiError) setMsg(e.message);
    else if (e instanceof Error) setMsg(e.message);
    else setMsg(String(e));
  }, []);
  const clear = useCallback(() => setMsg(null), []);
  return [msg, set, clear];
}

// ---- Markdown 渲染 ----

export function renderMarkdown(md: string): string {
  let html = md
    // Code blocks
    .replace(/```[\s\S]*?```/g, (m) => {
      const inner = m.replace(/^```[^\n]*\n?/, '').replace(/```$/, '');
      return `<pre><code>${inner.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`;
    })
    // Inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // Headers
    .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // Bold & italic
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Blockquote
    .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
    // Unordered list
    .replace(/^[\-\*] (.+)$/gm, '<li>$1</li>')
    // Ordered list
    .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
    // Horizontal rule
    .replace(/^---$/gm, '<hr>')
    // Links
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    // Paragraphs (double newline)
    .replace(/\n\n+/g, '</p><p>')
    // Line breaks
    .replace(/\n/g, '<br>');

  // Wrap loose li in ul
  html = html.replace(/(<li>.*?<\/li>)+/gs, (m) => `<ul>${m}</ul>`);
  return `<p>${html}</p>`;
}
