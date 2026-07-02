// ============================================================
// SSH Workspace Tool — Terminal Workspace (multi-tab)
// ============================================================

import { useCallback, useEffect, useRef, useState } from 'react';
import { Monitor, PanelRightClose, PanelRightOpen, Plus, SquareTerminal, X } from 'lucide-react';

import { apiGet, apiPut } from '../../../frontend/src/api/client';
import { Alert, EmptyState } from './components';
import { API, genId, messageFromError, serverLabel } from './utils';
import type { NewSessionPick, SshServer, TerminalApi, TerminalTab } from './types';
import { TerminalSession } from './TerminalSession';
import { NewSessionModal } from './NewSessionModal';
import { TerminalSidebar } from './TerminalSidebar';

export type TerminalWorkspaceProps = {
  servers: SshServer[];
  serversLoading: boolean;
};

type RuntimeTab = TerminalTab & { _key: string };

const SIDEBAR_KEY = 'sshw_sidebar_visible';

export function TerminalWorkspace({ servers, serversLoading }: TerminalWorkspaceProps) {
  const [tabs, setTabs] = useState<RuntimeTab[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showNewModal, setShowNewModal] = useState(false);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [sidebarVisible, setSidebarVisible] = useState(() => {
    try { return localStorage.getItem(SIDEBAR_KEY) !== 'false'; } catch { return true; }
  });
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [terminalApi, setTerminalApi] = useState<TerminalApi | null>(null);

  // Stable callback for TerminalSession to register/unregister its API
  const registerApi = useCallback((api: TerminalApi | null) => {
    setTerminalApi(api);
  }, []);

  // ---- Persist sidebar visibility ----
  useEffect(() => {
    try { localStorage.setItem(SIDEBAR_KEY, String(sidebarVisible)); } catch { /* ignore */ }
  }, [sidebarVisible]);

  // ---- Load persisted tabs on mount ----
  useEffect(() => {
    void loadTabs();
  }, []);

  async function loadTabs() {
    try {
      const r = await apiGet<{ tabs: TerminalTab[] }>(`${API}/terminal-tabs`);
      const runtimeTabs: RuntimeTab[] = r.tabs.map((t) => ({ ...t, _key: genId() }));
      setTabs(runtimeTabs);
      if (runtimeTabs.length > 0) {
        setActiveId(runtimeTabs[0].id);
      }
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setLoaded(true);
    }
  }

  // ---- Persist tabs (debounced) ----
  const persistTabs = useCallback((currentTabs: RuntimeTab[]) => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      const payload = currentTabs.map(({ _key, ...rest }) => rest);
      void apiPut(`${API}/terminal-tabs`, { tabs: payload }).catch(() => {
        // silently ignore persistence errors
      });
    }, 500);
  }, []);

  useEffect(() => {
    if (!loaded) return;
    persistTabs(tabs);
  }, [tabs, loaded, persistTabs]);

  // ---- Get server name by ID ----
  const serverName = useCallback(
    (serverId: string): string => servers.find((s) => s.id === serverId)?.name || '未知服务器',
    [servers],
  );

  // ---- Add a new tab ----
  function addTab(pick: NewSessionPick) {
    const srv = servers.find((s) => s.id === pick.serverId);
    if (!srv) return;
    const label = serverLabel(
      srv.name,
      pick.mode,
      pick.mode === 'screen_existing' ? pick.screenSession : '',
    );
    const newTab: RuntimeTab = {
      id: genId(),
      serverId: pick.serverId,
      mode: pick.mode,
      screenSession: pick.screenSession,
      label,
      tabOrder: tabs.length,
      _key: genId(),
    };
    setTabs((prev) => [...prev, newTab]);
    setActiveId(newTab.id);
    setShowNewModal(false);
  }

  // ---- Close a tab ----
  function closeTab(tabId: string) {
    setTabs((prev) => {
      const filtered = prev.filter((t) => t.id !== tabId);
      const reordered = filtered.map((t, idx) => ({ ...t, tabOrder: idx }));
      if (activeId === tabId) {
        const closedIdx = prev.findIndex((t) => t.id === tabId);
        const newActive = reordered[Math.min(closedIdx, reordered.length - 1)]?.id || null;
        setActiveId(newActive);
      }
      return reordered;
    });
  }

  // ---- Server map for validation ----
  const serverIds = new Set(servers.map((s) => s.id));
  const validTabs = tabs.filter((t) => serverIds.has(t.serverId));

  // ---- Active server info ----
  const activeTab = validTabs.find((t) => t.id === activeId) || null;
  const activeServerId = activeTab?.serverId || null;
  const activeServerName = activeServerId ? serverName(activeServerId) : '';

  return (
    <div className={`sw-workspace${sidebarVisible ? ' sidebar-visible' : ''}`}>
      <div className="sw-workspace-main">
        {/* Tab bar */}
        <div className="sw-tab-bar">
          <div className="sw-tabs-scroll">
            {validTabs.map((tab) => {
              const isActive = tab.id === activeId;
              return (
                <div
                  key={tab._key}
                  className={`sw-tab-item${isActive ? ' active' : ''}`}
                  onClick={() => setActiveId(tab.id)}
                >
                  <span className="sw-tab-icon">
                    {tab.mode === 'native' ? <SquareTerminal size={12} /> : <Monitor size={12} />}
                  </span>
                  <span className="sw-tab-text">{tab.label}</span>
                  <button
                    className="sw-tab-close"
                    onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }}
                    type="button"
                  >
                    <X size={12} />
                  </button>
                </div>
              );
            })}
          </div>
          <div className="sw-tab-bar-actions">
            <button
              className="sw-tab-add"
              onClick={() => setShowNewModal(true)}
              type="button"
              title="新建会话"
              disabled={serversLoading}
            >
              <Plus size={16} />
            </button>
            <button
              className="sw-tab-sidebar-toggle"
              onClick={() => setSidebarVisible((v) => !v)}
              type="button"
              title={sidebarVisible ? '隐藏侧边栏' : '显示侧边栏'}
            >
              {sidebarVisible ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
            </button>
          </div>
        </div>

        {/* Terminal area */}
        <div className="sw-term-area">
          {error && <Alert type="error">{error}</Alert>}
          {serversLoading && !loaded && (
            <EmptyState icon={<SquareTerminal size={32} />} title="加载中…" hint="正在获取服务器列表" />
          )}
          {!serversLoading && servers.length === 0 && (
            <EmptyState
              icon={<SquareTerminal size={32} />}
              title="暂无服务器"
              hint="请先在「服务器」页添加 SSH 服务器"
            />
          )}
          {servers.length > 0 && validTabs.length === 0 && loaded && (
            <EmptyState
              icon={<SquareTerminal size={32} />}
              title="还没有终端会话"
              hint="点击上方 + 号新建一个 SSH 会话"
            />
          )}
          {validTabs.map((tab) => (
            <TerminalSession
              key={tab._key}
              tab={tab}
              serverName={serverName(tab.serverId)}
              active={tab.id === activeId}
              registerApi={registerApi}
            />
          ))}
        </div>
      </div>

      {/* Right sidebar */}
      <TerminalSidebar
        serverId={activeServerId}
        serverName={activeServerName}
        terminalApi={terminalApi}
        visible={sidebarVisible}
      />

      {showNewModal && (
        <NewSessionModal
          servers={servers}
          onClose={() => setShowNewModal(false)}
          onConfirm={addTab}
        />
      )}
    </div>
  );
}
