// ============================================================
// SSH Workspace Tool — Entry Point
// ============================================================

import './style.css';
import '@xterm/xterm/css/xterm.css';

import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { ClipboardList, History, Server, SquareTerminal } from 'lucide-react';

import { apiGet } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

import { Spin } from './components';
import { API } from './utils';
import type { SshServer, TopTabId } from './types';

import { TerminalWorkspace } from './TerminalWorkspace';
import { ServersPanel } from './ServersPanel';
import { TemplatesPanel } from './TemplatesPanel';
import { HistoryPanel } from './HistoryPanel';

// ---- Tab config ----

const TABS: { id: TopTabId; label: string; icon: ReactNode }[] = [
  { id: 'terminal', label: '终端', icon: <SquareTerminal size={14} /> },
  { id: 'servers', label: '服务器', icon: <Server size={14} /> },
  { id: 'templates', label: '命令模板', icon: <ClipboardList size={14} /> },
  { id: 'history', label: '历史', icon: <History size={14} /> },
];

export default function SshWorkspaceTool() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TopTabId>('terminal');
  const [servers, setServers] = useState<SshServer[]>([]);
  const [serversLoading, setServersLoading] = useState(false);

  async function loadMe() {
    try {
      const s = await fetchMe();
      setMe(s.user);
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }

  async function loadServers() {
    if (!me) return;
    setServersLoading(true);
    try {
      const r = await apiGet<{ servers: SshServer[] }>(`${API}/servers`);
      setServers(r.servers);
    } catch {
      setServers([]);
    } finally {
      setServersLoading(false);
    }
  }

  useEffect(() => { void loadMe(); }, []);
  useEffect(() => { if (me) void loadServers(); }, [me]);

  if (loading) {
    return (
      <div className="tool-page sshw-tool">
        <div className="sw-empty"><Spin size={24} /> 正在初始化…</div>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="tool-page sshw-tool">
        <LoginPanel onSuccess={loadMe} />
      </div>
    );
  }

  return (
    <div className="tool-page sshw-tool">
      <div className="tool-header">
        <div>
          <h1 className="tool-title">SSH 工作区</h1>
          <p className="tool-subtitle">多服务器 SSH 终端、screen 会话与定时任务管理</p>
        </div>
        <span style={{ fontSize: 13, color: '#526071' }}>{me.displayName}</span>
      </div>

      <nav className="sw-topnav">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`sw-topnav-tab${activeTab === tab.id ? ' active' : ''}`}
            type="button"
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.icon} {tab.label}
          </button>
        ))}
      </nav>

      <div className="tool-body sw-body">
        {activeTab === 'terminal' && (
          <TerminalWorkspace servers={servers} serversLoading={serversLoading} />
        )}
        {activeTab === 'servers' && (
          <ServersPanel servers={servers} loading={serversLoading} isAdmin={me.role === 'admin'} onRefresh={() => void loadServers()} />
        )}
        {activeTab === 'templates' && (
          <TemplatesPanel servers={servers} />
        )}
        {activeTab === 'history' && (
          <HistoryPanel servers={servers} />
        )}
      </div>
    </div>
  );
}
