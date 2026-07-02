// ============================================================
// SSH Workspace Tool — New Session Picker Modal
// ============================================================

import { useEffect, useState } from 'react';
import { Loader2, Monitor, Plus, RefreshCw, Server, SquareTerminal, Terminal } from 'lucide-react';

import { apiGet } from '../../../frontend/src/api/client';
import { Alert, Badge, Modal, Spin } from './components';
import { API, messageFromError } from './utils';
import type { NewSessionPick, ScreenSession, SessionMode, SshServer } from './types';

export type NewSessionModalProps = {
  servers: SshServer[];
  onClose: () => void;
  onConfirm: (pick: NewSessionPick) => void;
};

export function NewSessionModal({ servers, onClose, onConfirm }: NewSessionModalProps) {
  const [selectedServerId, setSelectedServerId] = useState<string>('');
  const [mode, setMode] = useState<SessionMode>('native');
  const [screenSessions, setScreenSessions] = useState<ScreenSession[]>([]);
  const [selectedScreen, setSelectedScreen] = useState<string>('');
  const [newScreenName, setNewScreenName] = useState('');
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [error, setError] = useState('');

  // Auto-select first server
  useEffect(() => {
    if (servers.length > 0 && !selectedServerId) {
      setSelectedServerId(servers[0].id);
    }
  }, [servers, selectedServerId]);

  // Get selected server
  const selectedServer = servers.find((s) => s.id === selectedServerId) || null;

  // Reset mode when server changes
  useEffect(() => {
    if (selectedServer) {
      if (!selectedServer.hasScreen && mode !== 'native') {
        setMode('native');
      }
      setSelectedScreen('');
      setNewScreenName('');
    }
  }, [selectedServerId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load screen sessions when screen mode is selected
  useEffect(() => {
    if (!selectedServerId || mode !== 'screen_existing') {
      setScreenSessions([]);
      return;
    }
    if (!selectedServer?.hasScreen) return;
    void loadScreenSessions();
  }, [selectedServerId, mode]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadScreenSessions() {
    if (!selectedServerId) return;
    setLoadingSessions(true);
    setError('');
    try {
      const r = await apiGet<{ sessions: ScreenSession[] }>(
        `${API}/servers/${selectedServerId}/screen/sessions?refresh=true`,
      );
      setScreenSessions(r.sessions);
      if (r.sessions.length > 0 && !selectedScreen) {
        setSelectedScreen(r.sessions[0].sessionName);
      }
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setLoadingSessions(false);
    }
  }

  function handleConfirm() {
    if (!selectedServerId) return;
    if (mode === 'native') {
      onConfirm({ serverId: selectedServerId, mode: 'native', screenSession: '' });
    } else if (mode === 'screen_existing') {
      if (!selectedScreen) {
        setError('请选择一个 screen 会话');
        return;
      }
      onConfirm({ serverId: selectedServerId, mode: 'screen_existing', screenSession: selectedScreen });
    } else if (mode === 'screen_new') {
      const name = newScreenName.trim() || `ssh_${Date.now()}`;
      onConfirm({ serverId: selectedServerId, mode: 'screen_new', screenSession: name });
    }
  }

  if (servers.length === 0) {
    return (
      <Modal title="新建终端会话" onClose={onClose}>
        <Alert type="info">暂无服务器，请先在「服务器」页添加。</Alert>
        <div className="sw-modal-foot" style={{ marginTop: 16 }}>
          <button className="sw-btn sw-btn-primary" onClick={onClose} type="button">知道了</button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      title="新建终端会话"
      onClose={onClose}
      foot={
        <>
          <button className="sw-btn sw-btn-secondary" onClick={onClose} type="button">取消</button>
          <button className="sw-btn sw-btn-primary" onClick={handleConfirm} type="button">
            <Terminal size={14} /> 打开会话
          </button>
        </>
      }
    >
      <div className="sw-session-form">
        {error && <Alert type="error">{error}</Alert>}

        {/* Server picker */}
        <div className="sw-form-field sw-full-col">
          <label>选择服务器</label>
          <div className="sw-server-pick-grid">
            {servers.map((s) => (
              <button
                key={s.id}
                className={`sw-server-pick${selectedServerId === s.id ? ' active' : ''}`}
                onClick={() => setSelectedServerId(s.id)}
                type="button"
              >
                <Server size={14} />
                <span className="sw-server-pick-name">{s.name}</span>
                <span className="sw-server-pick-host">{s.host}:{s.port}</span>
                {s.hasScreen && <Badge color="green">screen</Badge>}
              </button>
            ))}
          </div>
        </div>

        {/* Mode picker */}
        <div className="sw-form-field sw-full-col">
          <label>会话类型</label>
          <div className="sw-mode-pick">
            <button
              className={`sw-mode-card${mode === 'native' ? ' active' : ''}`}
              onClick={() => setMode('native')}
              type="button"
            >
              <SquareTerminal size={16} />
              <span className="sw-mode-title">原生 SSH</span>
              <span className="sw-mode-desc">直接连接，关闭后不保留</span>
            </button>

            {selectedServer?.hasScreen && (
              <>
                <button
                  className={`sw-mode-card${mode === 'screen_existing' ? ' active' : ''}`}
                  onClick={() => setMode('screen_existing')}
                  type="button"
                >
                  <Monitor size={16} />
                  <span className="sw-mode-title">连接现有 screen</span>
                  <span className="sw-mode-desc">恢复已有的后台会话</span>
                </button>
                <button
                  className={`sw-mode-card${mode === 'screen_new' ? ' active' : ''}`}
                  onClick={() => setMode('screen_new')}
                  type="button"
                >
                  <Plus size={16} />
                  <span className="sw-mode-title">新建 screen 会话</span>
                  <span className="sw-mode-desc">创建持久后台会话</span>
                </button>
              </>
            )}
          </div>
        </div>

        {/* Screen session selector */}
        {mode === 'screen_existing' && selectedServer?.hasScreen && (
          <div className="sw-form-field sw-full-col">
            <label>
              选择 screen 会话
              <button
                className="sw-btn sw-btn-sm sw-btn-ghost"
                style={{ marginLeft: 8 }}
                onClick={() => void loadScreenSessions()}
                type="button"
              >
                <RefreshCw size={12} /> 刷新
              </button>
            </label>
            {loadingSessions ? (
              <div className="sw-loading-inline"><Spin /> 加载中…</div>
            ) : screenSessions.length === 0 ? (
              <Alert type="info">暂无 screen 会话，可创建新的。</Alert>
            ) : (
              <div className="sw-screen-pick-list">
                {screenSessions.map((ss) => (
                  <label key={ss.id} className={`sw-screen-pick${selectedScreen === ss.sessionName ? ' active' : ''}`}>
                    <input
                      type="radio"
                      name="screen"
                      checked={selectedScreen === ss.sessionName}
                      onChange={() => setSelectedScreen(ss.sessionName)}
                    />
                    <span className="sw-screen-pick-name">{ss.sessionName}</span>
                    {ss.pid && <span className="sw-screen-pick-pid">pid:{ss.pid}</span>}
                    <Badge color={ss.status === 'running' ? 'green' : 'default'}>{ss.status}</Badge>
                  </label>
                ))}
              </div>
            )}
          </div>
        )}

        {/* New screen name */}
        {mode === 'screen_new' && selectedServer?.hasScreen && (
          <div className="sw-form-field sw-full-col">
            <label>screen 会话名（可选，留空自动生成）</label>
            <input
              className="sw-input"
              type="text"
              value={newScreenName}
              onChange={(e) => setNewScreenName(e.target.value)}
              placeholder="例如：dev_session"
            />
          </div>
        )}

        {!selectedServer?.hasScreen && mode !== 'native' && (
          <Alert type="info">该服务器未检测到 screen，仅支持原生 SSH。</Alert>
        )}
      </div>
    </Modal>
  );
}
