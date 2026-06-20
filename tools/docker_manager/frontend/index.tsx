// ============================================================
// Docker Manager Tool — Entry Point
// ============================================================

import './style.css';
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Box,
  ClipboardList,
  Database,
  HardDrive,
  Image,
  Layers,
  Server,
  Shield,
} from 'lucide-react';

import { apiGet } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

import { Spin } from './components';
import { API } from './utils';
import type { DmServer, TabId } from './types';

import { ServersOverviewPanel } from './ServersOverviewPanel';
import { ImagesPanel } from './ImagesPanel';
import { ContainersPanel } from './ContainersPanel';
import { TemplatesPanel, MyResourcesPanel } from './TemplatesPanel';
import { VolumesPanel } from './VolumesPanel';
import { AdminServersPanel, AdminTemplatesPanel } from './AdminPanel';

// ---- Tab 配置 ----

const TAB_LABELS: { id: TabId; label: string; icon: ReactNode; adminOnly?: boolean; userOnly?: boolean }[] = [
  { id: 'servers',          label: '服务器',    icon: <Server size={14} /> },
  { id: 'images',           label: '镜像',      icon: <Image size={14} /> },
  { id: 'containers',       label: '容器',      icon: <Box size={14} /> },
  { id: 'templates',        label: '模板',      icon: <ClipboardList size={14} /> },
  { id: 'volumes',          label: '卷',        icon: <Database size={14} /> },
  { id: 'my_resources',     label: '资源管理',  icon: <HardDrive size={14} />, userOnly: true },
  { id: 'admin_servers',    label: '服务器管理',icon: <Shield size={14} />, adminOnly: true },
  { id: 'admin_templates',  label: '模板管理',  icon: <Layers size={14} />, adminOnly: true },
];

// ---- Main Component ----

export default function DockerManagerTool() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TabId>('servers');
  const [servers, setServers] = useState<DmServer[]>([]);
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
      const r = await apiGet<{ servers: DmServer[] }>(`${API}/servers`);
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
      <div className="tool-page dm-tool">
        <div className="dm-empty"><Spin /> 正在初始化…</div>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="tool-page dm-tool">
        <LoginPanel onSuccess={loadMe} />
      </div>
    );
  }

  const isAdmin = me.role === 'admin';
  const visibleTabs = TAB_LABELS.filter((t) => {
    if (t.adminOnly) return isAdmin;
    if (t.userOnly) return !isAdmin;
    return true;
  });

  return (
    <div className="tool-page dm-tool">
      <div className="tool-header">
        <div>
          <h1 className="tool-title">Docker 多租户管理</h1>
          <p className="tool-subtitle">实验室多服务器 Docker 资源统一管理平台</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {isAdmin && <span style={{ fontSize: 12, background: '#7c3aed', color: '#fff', padding: '3px 10px', borderRadius: 999, fontWeight: 700 }}>管理员</span>}
          <span style={{ fontSize: 13, color: '#526071' }}>{me.displayName}</span>
        </div>
      </div>

      {/* Navigation Tabs */}
      <nav className="dm-nav">
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            className={`dm-nav-tab${activeTab === t.id ? ' active' : ''}${t.adminOnly ? ' admin-tab' : ''}`}
            onClick={() => setActiveTab(t.id)}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </nav>

      {/* Panel Content */}
      <div className="tool-body">
        {activeTab === 'servers' && (
          <ServersOverviewPanel servers={servers} me={me} />
        )}
        {activeTab === 'images' && (
          <ImagesPanel servers={servers} me={me} />
        )}
        {activeTab === 'containers' && (
          <ContainersPanel servers={servers} me={me} />
        )}
        {activeTab === 'templates' && (
          <TemplatesPanel me={me} />
        )}
        {activeTab === 'volumes' && (
          <VolumesPanel servers={servers} me={me} />
        )}
        {activeTab === 'my_resources' && !isAdmin && (
          <MyResourcesPanel me={me} />
        )}
        {activeTab === 'admin_servers' && isAdmin && (
          <AdminServersPanel onRefresh={() => void loadServers()} />
        )}
        {activeTab === 'admin_templates' && isAdmin && (
          <AdminTemplatesPanel />
        )}
      </div>
    </div>
  );
}
