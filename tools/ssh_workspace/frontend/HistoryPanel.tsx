// ============================================================
// SSH Workspace Tool — Command History Panel
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import { History } from 'lucide-react';

import { apiGet } from '../../../frontend/src/api/client';
import { Alert, Spin } from './components';
import { API, formatRelativeTime, messageFromError } from './utils';
import type { CommandHistory, SshServer } from './types';

export type HistoryPanelProps = {
  servers: SshServer[];
};

export function HistoryPanel({ servers }: HistoryPanelProps) {
  const [history, setHistory] = useState<CommandHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filterServer, setFilterServer] = useState<string>('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const url = filterServer
        ? `${API}/history?serverId=${filterServer}&limit=200`
        : `${API}/history?limit=200`;
      const r = await apiGet<{ history: CommandHistory[] }>(url);
      setHistory(r.history);
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setLoading(false);
    }
  }, [filterServer]);

  useEffect(() => { void load(); }, [load]);

  function serverName(id: string | null): string {
    if (!id) return '—';
    return servers.find((s) => s.id === id)?.name || '已删除';
  }

  return (
    <div className="sw-panel">
      <div className="sw-panel-head">
        <div>
          <h2 className="sw-panel-title"><History size={18} /> 命令历史</h2>
          <p className="sw-panel-desc">查看通过终端和定时任务执行的命令记录</p>
        </div>
        <select
          className="sw-select"
          value={filterServer}
          onChange={(e) => setFilterServer(e.target.value)}
        >
          <option value="">全部服务器</option>
          {servers.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
      </div>

      {error && <Alert type="error">{error}</Alert>}

      {loading ? (
        <div className="sw-loading-block"><Spin /> 加载历史记录…</div>
      ) : history.length === 0 ? (
        <div className="sw-empty">
          <div className="sw-empty-icon"><History size={32} /></div>
          <div className="sw-empty-title">暂无历史记录</div>
        </div>
      ) : (
        <div className="sw-table-wrap">
          <table className="sw-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>服务器</th>
                <th>来源</th>
                <th>命令</th>
                <th>screen 会话</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.id}>
                  <td className="sw-td-time">{formatRelativeTime(h.createdAt)}</td>
                  <td>{serverName(h.serverId)}</td>
                  <td>
                    <span className={`sw-source-badge sw-source-${h.source}`}>
                      {h.source === 'terminal' ? '终端' : h.source === 'scheduled_task' ? '定时' : h.source}
                    </span>
                  </td>
                  <td><code className="sw-code-cell">{h.command}</code></td>
                  <td>{h.screenSession || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
