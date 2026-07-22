// ============================================================
// TensorBoard Dashboard Tool — Entry Point
// ============================================================

import './style.css';

import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { BarChart3, Server } from 'lucide-react';

import { apiGet } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

import { Spin } from './components';
import { API } from './utils';
import type { TbServer, TopTabId } from './types';

import { SessionsPanel } from './SessionsPanel';
import { ServersPanel } from './ServersPanel';

// ---- Tab config ----

const TABS: { id: TopTabId; label: string; icon: ReactNode }[] = [
  { id: 'sessions', label: '会话', icon: <BarChart3 size={14} /> },
  { id: 'servers', label: '服务器', icon: <Server size={14} /> },
];

export default function TensorBoardDashboardTool() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TopTabId>('sessions');
  const [servers, setServers] = useState<TbServer[]>([]);
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
      const r = await apiGet<{ servers: TbServer[] }>(`${API}/servers`);
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
      <div className="tool-page tbd-tool">
        <div className="tb-empty"><Spin size={24} /> 正在初始化…</div>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="tool-page tbd-tool">
        <LoginPanel onSuccess={loadMe} />
      </div>
    );
  }

  return (
    <div className="tool-page tbd-tool">
      <div className="tool-header">
        <div>
          <h1 className="tool-title">TensorBoard 看板</h1>
          <p className="tool-subtitle">在远程服务器上启动 TensorBoard，通过反向代理在浏览器中直接访问</p>
        </div>
        <span style={{ fontSize: 13, color: '#526071' }}>{me.displayName}</span>
      </div>

      <nav className="tb-topnav">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`tb-topnav-tab${activeTab === tab.id ? ' active' : ''}`}
            type="button"
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.icon} {tab.label}
          </button>
        ))}
      </nav>

      <div className="tool-body tb-body">
        {activeTab === 'sessions' && (
          <SessionsPanel servers={servers} serversLoading={serversLoading} />
        )}
        {activeTab === 'servers' && (
          <ServersPanel servers={servers} loading={serversLoading} onRefresh={() => void loadServers()} />
        )}
      </div>
    </div>
  );
}
