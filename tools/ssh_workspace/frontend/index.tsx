import './style.css';
import '@xterm/xterm/css/xterm.css';

import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import type { MutableRefObject } from 'react';
import { FitAddon } from '@xterm/addon-fit';
import { Terminal as XTerm } from '@xterm/xterm';
import {
  CheckCircle2,
  Clock,
  KeyRound,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Save,
  Server,
  SquareTerminal,
  Trash2,
  XCircle,
} from 'lucide-react';

import { ApiError, apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

const API = '/api/tools/ssh-workspace';

type AuthType = 'password' | 'private_key';

type SshServer = {
  id: string;
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  authType: AuthType;
  hasScreen: boolean;
  lastTestStatus: string;
  lastTestError: string;
  lastTestedAt: string | null;
  updatedAt: string;
};

type ScreenSession = {
  id: string;
  serverId: string;
  sessionName: string;
  status: 'running' | 'done' | 'unknown';
  createdByTool: boolean;
  command: string;
  startedAt: string;
  checkedAt: string | null;
};

type CommandTemplate = {
  id: string;
  name: string;
  command: string;
  description: string;
  variables: string[];
  updatedAt: string;
};

type CommandHistory = {
  id: string;
  serverId: string | null;
  source: string;
  command: string;
  screenSession: string | null;
  createdAt: string;
};

type ScheduledTask = {
  id: string;
  serverId: string;
  name: string;
  command: string;
  intervalSeconds: number;
  screenNamePrefix: string;
  enabled: boolean;
  nextRunAt: string;
  lastRunAt: string | null;
};

type TaskRun = {
  id: string;
  taskId: string;
  command: string;
  screenSession: string | null;
  status: string;
  error: string;
  startedAt: string;
};

type ServerForm = {
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  authType: AuthType;
  sshPassword: string;
  privateKey: string;
  privateKeyPassphrase: string;
};

type TemplateForm = {
  id?: string;
  name: string;
  command: string;
  description: string;
};

type TaskForm = {
  id?: string;
  name: string;
  command: string;
  intervalSeconds: number;
  screenNamePrefix: string;
  enabled: boolean;
};

const emptyServerForm: ServerForm = {
  name: '',
  host: '',
  port: 22,
  sshUsername: '',
  authType: 'password',
  sshPassword: '',
  privateKey: '',
  privateKeyPassphrase: '',
};

const emptyTemplateForm: TemplateForm = { name: '', command: '', description: '' };
const emptyTaskForm: TaskForm = { name: '', command: '', intervalSeconds: 3600, screenNamePrefix: 'ssh_task', enabled: true };

export default function SshWorkspaceTool() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [servers, setServers] = useState<SshServer[]>([]);
  const [selectedServerId, setSelectedServerId] = useState('');
  const [sessions, setSessions] = useState<ScreenSession[]>([]);
  const [selectedSession, setSelectedSession] = useState('');
  const [templates, setTemplates] = useState<CommandTemplate[]>([]);
  const [history, setHistory] = useState<CommandHistory[]>([]);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [runsByTask, setRunsByTask] = useState<Record<string, TaskRun[]>>({});
  const [serverForm, setServerForm] = useState<ServerForm>(emptyServerForm);
  const [editingServerId, setEditingServerId] = useState('');
  const [templateForm, setTemplateForm] = useState<TemplateForm>(emptyTemplateForm);
  const [taskForm, setTaskForm] = useState<TaskForm>(emptyTaskForm);
  const [newSessionName, setNewSessionName] = useState('');
  const [error, setError] = useState('');
  const terminalHandle = useRef<TerminalHandle | null>(null);

  const selectedServer = useMemo(
    () => servers.find((server) => server.id === selectedServerId) ?? null,
    [servers, selectedServerId],
  );

  async function loadMe() {
    try {
      const result = await fetchMe();
      setMe(result.user);
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }

  async function loadAll(nextServerId = selectedServerId) {
    setError('');
    try {
      const [serverData, templateData, historyData, taskData] = await Promise.all([
        apiGet<{ servers: SshServer[] }>(`${API}/servers`),
        apiGet<{ templates: CommandTemplate[] }>(`${API}/templates`),
        apiGet<{ history: CommandHistory[] }>(`${API}/history?limit=80`),
        apiGet<{ tasks: ScheduledTask[] }>(`${API}/scheduled-tasks`),
      ]);
      setServers(serverData.servers);
      setTemplates(templateData.templates);
      setHistory(historyData.history);
      setTasks(taskData.tasks);
      const fallback = serverData.servers[0]?.id ?? '';
      const resolvedServerId = serverData.servers.some((server) => server.id === nextServerId) ? nextServerId : fallback;
      setSelectedServerId(resolvedServerId);
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  async function loadSessions(serverId = selectedServerId) {
    if (!serverId) {
      setSessions([]);
      return;
    }
    const server = servers.find((item) => item.id === serverId);
    if (server && !server.hasScreen) {
      setSessions([]);
      setSelectedSession('');
      return;
    }
    try {
      const data = await apiGet<{ sessions: ScreenSession[] }>(`${API}/servers/${serverId}/screen/sessions`);
      setSessions(data.sessions);
      if (selectedSession && !data.sessions.some((session) => session.sessionName === selectedSession)) {
        setSelectedSession('');
      }
    } catch (exc) {
      if (!(exc instanceof ApiError && exc.code === 'SCREEN_UNAVAILABLE')) setError(messageFromError(exc));
      setSessions([]);
    }
  }

  async function loadHistory(serverId = selectedServerId) {
    const suffix = serverId ? `?serverId=${encodeURIComponent(serverId)}&limit=80` : '?limit=80';
    const data = await apiGet<{ history: CommandHistory[] }>(`${API}/history${suffix}`);
    setHistory(data.history);
  }

  useEffect(() => { void loadMe(); }, []);
  useEffect(() => { if (me) void loadAll(); }, [me]);
  useEffect(() => { if (selectedServerId) { void loadSessions(selectedServerId); void loadHistory(selectedServerId); } }, [selectedServerId, servers.length]);

  async function submitServer(event: FormEvent) {
    event.preventDefault();
    setError('');
    const payload = {
      name: serverForm.name,
      host: serverForm.host,
      port: Number(serverForm.port) || 22,
      sshUsername: serverForm.sshUsername,
      authType: serverForm.authType,
      sshPassword: serverForm.sshPassword || undefined,
      privateKey: serverForm.privateKey || undefined,
      privateKeyPassphrase: serverForm.privateKeyPassphrase || undefined,
    };
    try {
      if (editingServerId) {
        await apiPut(`${API}/servers/${editingServerId}`, payload);
      } else {
        await apiPost(`${API}/servers`, payload);
      }
      setServerForm(emptyServerForm);
      setEditingServerId('');
      await loadAll(selectedServerId);
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  async function testSelectedServer(serverId: string) {
    setError('');
    try {
      await apiPost(`${API}/servers/${serverId}/test`, {});
      await loadAll(serverId);
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  async function removeServer(serverId: string) {
    await apiDelete(`${API}/servers/${serverId}`);
    if (selectedServerId === serverId) setSelectedServerId('');
    await loadAll();
  }

  function editServer(server: SshServer) {
    setEditingServerId(server.id);
    setServerForm({
      name: server.name,
      host: server.host,
      port: server.port,
      sshUsername: server.sshUsername,
      authType: server.authType,
      sshPassword: '',
      privateKey: '',
      privateKeyPassphrase: '',
    });
  }

  async function createSession() {
    if (!selectedServerId || !newSessionName.trim()) return;
    try {
      const data = await apiPost<{ session: ScreenSession }>(`${API}/servers/${selectedServerId}/screen/sessions`, {
        name: newSessionName.trim(),
      });
      setNewSessionName('');
      setSelectedSession(data.session.sessionName);
      await loadSessions(selectedServerId);
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  async function renameSession(sessionName: string) {
    const next = window.prompt('新的 screen 名称', sessionName);
    if (!next || next === sessionName || !selectedServerId) return;
    await apiPut(`${API}/servers/${selectedServerId}/screen/sessions/${encodeURIComponent(sessionName)}`, { name: next });
    setSelectedSession(next);
    await loadSessions(selectedServerId);
  }

  async function deleteSession(sessionName: string) {
    if (!selectedServerId) return;
    await apiDelete(`${API}/servers/${selectedServerId}/screen/sessions/${encodeURIComponent(sessionName)}`);
    if (selectedSession === sessionName) setSelectedSession('');
    await loadSessions(selectedServerId);
  }

  async function submitTemplate(event: FormEvent) {
    event.preventDefault();
    const payload = { name: templateForm.name, command: templateForm.command, description: templateForm.description };
    if (templateForm.id) {
      await apiPut(`${API}/templates/${templateForm.id}`, payload);
    } else {
      await apiPost(`${API}/templates`, payload);
    }
    setTemplateForm(emptyTemplateForm);
    const data = await apiGet<{ templates: CommandTemplate[] }>(`${API}/templates`);
    setTemplates(data.templates);
  }

  async function deleteTemplate(id: string) {
    await apiDelete(`${API}/templates/${id}`);
    setTemplates((items) => items.filter((item) => item.id !== id));
  }

  async function submitTask(event: FormEvent) {
    event.preventDefault();
    if (!selectedServerId) return;
    const payload = {
      serverId: selectedServerId,
      name: taskForm.name,
      command: taskForm.command,
      intervalSeconds: Number(taskForm.intervalSeconds),
      screenNamePrefix: taskForm.screenNamePrefix,
      enabled: taskForm.enabled,
    };
    try {
      if (taskForm.id) {
        await apiPut(`${API}/scheduled-tasks/${taskForm.id}`, payload);
      } else {
        await apiPost(`${API}/scheduled-tasks`, payload);
      }
      setTaskForm(emptyTaskForm);
      const data = await apiGet<{ tasks: ScheduledTask[] }>(`${API}/scheduled-tasks`);
      setTasks(data.tasks);
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  async function deleteTask(id: string) {
    await apiDelete(`${API}/scheduled-tasks/${id}`);
    setTasks((items) => items.filter((item) => item.id !== id));
  }

  async function loadRuns(taskId: string) {
    const data = await apiGet<{ runs: TaskRun[] }>(`${API}/scheduled-tasks/${taskId}/runs`);
    setRunsByTask((current) => ({ ...current, [taskId]: data.runs }));
  }

  async function recordCommand(command: string, source = 'terminal', screenSession = selectedSession || null) {
    if (!command.trim()) return;
    try {
      await apiPost(`${API}/history`, {
        serverId: selectedServerId || null,
        source,
        command: command.trim(),
        screenSession,
      });
      await loadHistory(selectedServerId);
    } catch {
      // Terminal history should never interrupt the terminal itself.
    }
  }

  function runTemplate(template: CommandTemplate) {
    let command = template.command;
    for (const variable of template.variables) {
      const value = window.prompt(variable, '');
      if (value === null) return;
      command = command.replaceAll(new RegExp(`{{\\s*${escapeRegExp(variable)}\\s*}}`, 'g'), value);
    }
    terminalHandle.current?.send(`${command}\r`);
    void recordCommand(command, 'template', selectedSession || null);
  }

  if (loading) {
    return <div className="tool-page sshw-page"><div className="sshw-empty">正在初始化…</div></div>;
  }

  if (!me) {
    return <div className="tool-page sshw-page"><LoginPanel onSuccess={loadMe} /></div>;
  }

  return (
    <div className="tool-page sshw-page">
      <header className="sshw-header">
        <div>
          <h1 className="tool-title">SSH 工作台</h1>
          <p className="tool-subtitle">服务器、终端、screen 会话、命令模板与定时任务</p>
        </div>
        <button className="sshw-icon-btn" type="button" onClick={() => void loadAll(selectedServerId)} title="刷新">
          <RefreshCw size={16} />
        </button>
      </header>

      {error && <div className="sshw-error">{error}</div>}

      <div className="sshw-layout">
        <aside className="sshw-sidebar">
          <section className="sshw-panel">
            <div className="sshw-section-head">
              <span><Server size={15} />服务器</span>
              <button className="sshw-icon-btn" type="button" onClick={() => { setEditingServerId(''); setServerForm(emptyServerForm); }} title="新增服务器">
                <Plus size={15} />
              </button>
            </div>
            <div className="sshw-server-list">
              {servers.map((server) => (
                <button
                  key={server.id}
                  className={`sshw-server-item${server.id === selectedServerId ? ' active' : ''}`}
                  type="button"
                  onClick={() => { setSelectedServerId(server.id); setSelectedSession(''); }}
                >
                  <strong>{server.name}</strong>
                  <small>{server.sshUsername}@{server.host}:{server.port}</small>
                  <span className={server.hasScreen ? 'sshw-status ok' : 'sshw-status muted'}>
                    {server.hasScreen ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
                    screen
                  </span>
                </button>
              ))}
            </div>
          </section>

          <form className="sshw-panel sshw-form" onSubmit={submitServer}>
            <div className="sshw-section-head">
              <span><KeyRound size={15} />{editingServerId ? '编辑账号' : '保存账号'}</span>
              {editingServerId && (
                <button className="sshw-icon-btn" type="button" onClick={() => { setEditingServerId(''); setServerForm(emptyServerForm); }} title="取消">
                  <XCircle size={15} />
                </button>
              )}
            </div>
            <input value={serverForm.name} onChange={(e) => setServerForm({ ...serverForm, name: e.target.value })} placeholder="名称" />
            <input value={serverForm.host} onChange={(e) => setServerForm({ ...serverForm, host: e.target.value })} placeholder="Host" />
            <div className="sshw-form-row">
              <input type="number" value={serverForm.port} onChange={(e) => setServerForm({ ...serverForm, port: Number(e.target.value) })} placeholder="端口" />
              <input value={serverForm.sshUsername} onChange={(e) => setServerForm({ ...serverForm, sshUsername: e.target.value })} placeholder="用户名" />
            </div>
            <select value={serverForm.authType} onChange={(e) => setServerForm({ ...serverForm, authType: e.target.value as AuthType })}>
              <option value="password">密码</option>
              <option value="private_key">私钥</option>
            </select>
            {serverForm.authType === 'password' ? (
              <input type="password" value={serverForm.sshPassword} onChange={(e) => setServerForm({ ...serverForm, sshPassword: e.target.value })} placeholder={editingServerId ? '新密码' : '密码'} />
            ) : (
              <>
                <textarea value={serverForm.privateKey} onChange={(e) => setServerForm({ ...serverForm, privateKey: e.target.value })} placeholder={editingServerId ? '新私钥' : '私钥内容'} rows={4} />
                <input type="password" value={serverForm.privateKeyPassphrase} onChange={(e) => setServerForm({ ...serverForm, privateKeyPassphrase: e.target.value })} placeholder="Passphrase" />
              </>
            )}
            <button className="sshw-primary" type="submit"><Save size={15} />保存</button>
          </form>

          {selectedServer && (
            <section className="sshw-panel">
              <div className="sshw-section-head">
                <span>操作</span>
              </div>
              <div className="sshw-action-grid">
                <button type="button" onClick={() => void testSelectedServer(selectedServer.id)}><RefreshCw size={14} />测试</button>
                <button type="button" onClick={() => editServer(selectedServer)}><Pencil size={14} />编辑</button>
                <button type="button" onClick={() => void removeServer(selectedServer.id)}><Trash2 size={14} />删除</button>
              </div>
            </section>
          )}
        </aside>

        <main className="sshw-terminal-zone">
          <div className="sshw-terminal-toolbar">
            <div>
              <strong>{selectedServer?.name ?? '未选择服务器'}</strong>
              {selectedSession && <small>screen: {selectedSession}</small>}
            </div>
            <button className="sshw-primary" type="button" disabled={!selectedServerId} onClick={() => terminalHandle.current?.connect()}>
              <SquareTerminal size={15} />连接
            </button>
          </div>
          <TerminalPane
            refHandle={terminalHandle}
            serverId={selectedServerId}
            screenSession={selectedSession}
            onCommand={(command) => void recordCommand(command)}
          />
        </main>

        <aside className="sshw-rightbar">
          <section className="sshw-panel">
            <div className="sshw-section-head">
              <span>Screen 会话</span>
              <button className="sshw-icon-btn" type="button" disabled={!selectedServer?.hasScreen} onClick={() => void loadSessions()} title="刷新会话">
                <RefreshCw size={15} />
              </button>
            </div>
            {selectedServer?.hasScreen ? (
              <>
                <div className="sshw-inline-create">
                  <input value={newSessionName} onChange={(e) => setNewSessionName(e.target.value)} placeholder="session 名" />
                  <button type="button" onClick={() => void createSession()}><Plus size={14} /></button>
                </div>
                <div className="sshw-list">
                  <button className={`sshw-list-row${selectedSession === '' ? ' active' : ''}`} type="button" onClick={() => setSelectedSession('')}>
                    <span>直接终端</span>
                    <small>ssh</small>
                  </button>
                  {sessions.map((session) => (
                    <div key={session.id} className={`sshw-list-row split${selectedSession === session.sessionName ? ' active' : ''}`}>
                      <button type="button" onClick={() => setSelectedSession(session.sessionName)}>
                        <span>{session.sessionName}</span>
                        <small>{session.status}</small>
                      </button>
                      <button type="button" onClick={() => void renameSession(session.sessionName)} title="重命名"><Pencil size={13} /></button>
                      <button type="button" onClick={() => void deleteSession(session.sessionName)} title="删除"><Trash2 size={13} /></button>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="sshw-empty compact">未启用 screen</div>
            )}
          </section>

          <section className="sshw-panel">
            <div className="sshw-section-head"><span>命令模板</span></div>
            <form className="sshw-form compact-form" onSubmit={submitTemplate}>
              <input value={templateForm.name} onChange={(e) => setTemplateForm({ ...templateForm, name: e.target.value })} placeholder="名称" />
              <textarea value={templateForm.command} onChange={(e) => setTemplateForm({ ...templateForm, command: e.target.value })} placeholder="命令，可用 {{name}}" rows={3} />
              <input value={templateForm.description} onChange={(e) => setTemplateForm({ ...templateForm, description: e.target.value })} placeholder="备注" />
              <button className="sshw-primary" type="submit"><Save size={14} />保存模板</button>
            </form>
            <div className="sshw-list">
              {templates.map((template) => (
                <div key={template.id} className="sshw-list-row split">
                  <button type="button" onClick={() => setTemplateForm(template)}>
                    <span>{template.name}</span>
                    <small>{template.command}</small>
                  </button>
                  <button type="button" onClick={() => runTemplate(template)} title="执行"><Play size={13} /></button>
                  <button type="button" onClick={() => void deleteTemplate(template.id)} title="删除"><Trash2 size={13} /></button>
                </div>
              ))}
            </div>
          </section>

          <section className="sshw-panel">
            <div className="sshw-section-head"><span><Clock size={15} />定时任务</span></div>
            <form className="sshw-form compact-form" onSubmit={submitTask}>
              <input value={taskForm.name} onChange={(e) => setTaskForm({ ...taskForm, name: e.target.value })} placeholder="任务名" disabled={!selectedServer?.hasScreen} />
              <textarea value={taskForm.command} onChange={(e) => setTaskForm({ ...taskForm, command: e.target.value })} placeholder="命令" rows={2} disabled={!selectedServer?.hasScreen} />
              <div className="sshw-form-row">
                <input type="number" min={60} value={taskForm.intervalSeconds} onChange={(e) => setTaskForm({ ...taskForm, intervalSeconds: Number(e.target.value) })} disabled={!selectedServer?.hasScreen} />
                <input value={taskForm.screenNamePrefix} onChange={(e) => setTaskForm({ ...taskForm, screenNamePrefix: e.target.value })} placeholder="screen 前缀" disabled={!selectedServer?.hasScreen} />
              </div>
              <label className="sshw-check"><input type="checkbox" checked={taskForm.enabled} onChange={(e) => setTaskForm({ ...taskForm, enabled: e.target.checked })} />启用</label>
              <button className="sshw-primary" type="submit" disabled={!selectedServer?.hasScreen}><Save size={14} />保存任务</button>
            </form>
            <div className="sshw-list">
              {tasks.filter((task) => task.serverId === selectedServerId).map((task) => (
                <div key={task.id} className="sshw-list-row split">
                  <button type="button" onClick={() => { setTaskForm(task); void loadRuns(task.id); }}>
                    <span>{task.name}</span>
                    <small>{task.enabled ? 'enabled' : 'paused'} · {task.intervalSeconds}s</small>
                  </button>
                  <button type="button" onClick={() => void loadRuns(task.id)} title="运行记录"><RefreshCw size={13} /></button>
                  <button type="button" onClick={() => void deleteTask(task.id)} title="删除"><Trash2 size={13} /></button>
                  {runsByTask[task.id]?.slice(0, 2).map((run) => (
                    <small key={run.id} className={run.status === 'failed' ? 'sshw-run failed' : 'sshw-run'}>{run.status} · {run.screenSession || run.error}</small>
                  ))}
                </div>
              ))}
            </div>
          </section>

          <section className="sshw-panel">
            <div className="sshw-section-head"><span>命令历史</span></div>
            <div className="sshw-list history">
              {history.map((item) => (
                <button key={item.id} className="sshw-list-row" type="button" onClick={() => terminalHandle.current?.send(`${item.command}\r`)}>
                  <span>{item.command}</span>
                  <small>{item.source}{item.screenSession ? ` · ${item.screenSession}` : ''}</small>
                </button>
              ))}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}

type TerminalHandle = {
  connect: () => void;
  send: (data: string) => void;
};

function TerminalPane(props: {
  refHandle: MutableRefObject<TerminalHandle | null>;
  serverId: string;
  screenSession: string;
  onCommand: (command: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<XTerm | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const commandBufferRef = useRef('');

  function disconnect() {
    socketRef.current?.close();
    socketRef.current = null;
  }

  function connect() {
    if (!props.serverId || !terminalRef.current) return;
    disconnect();
    terminalRef.current.reset();
    terminalRef.current.writeln('connecting...');
    const params = new URLSearchParams({ serverId: props.serverId });
    if (props.screenSession) params.set('screenSession', props.screenSession);
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${scheme}://${window.location.host}${API}/ws/terminal?${params.toString()}`);
    socketRef.current = socket;
    socket.onopen = () => {
      fitRef.current?.fit();
      const dims = fitRef.current?.proposeDimensions();
      if (dims) socket.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
    };
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(String(event.data));
        if (payload.type === 'output') terminalRef.current?.write(payload.data);
        if (payload.type === 'error') terminalRef.current?.writeln(`\r\n${payload.message}`);
      } catch {
        terminalRef.current?.write(String(event.data));
      }
    };
    socket.onclose = () => terminalRef.current?.writeln('\r\n[disconnected]');
  }

  function send(data: string) {
    socketRef.current?.send(data);
  }

  useEffect(() => {
    if (!containerRef.current) return;
    const terminal = new XTerm({
      cursorBlink: true,
      fontFamily: 'Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 13,
      theme: { background: '#101318', foreground: '#e6edf3', cursor: '#7dd3fc' },
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(containerRef.current);
    fit.fit();
    terminalRef.current = terminal;
    fitRef.current = fit;
    terminal.onData((data: string) => {
      socketRef.current?.send(data);
      if (data === '\r') {
        const command = commandBufferRef.current.trim();
        if (command) props.onCommand(command);
        commandBufferRef.current = '';
      } else if (data === '\u007f') {
        commandBufferRef.current = commandBufferRef.current.slice(0, -1);
      } else if (data >= ' ') {
        commandBufferRef.current += data;
      }
    });
    const observer = new ResizeObserver(() => {
      fit.fit();
      const dims = fit.proposeDimensions();
      if (dims && socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
      }
    });
    observer.observe(containerRef.current);
    props.refHandle.current = { connect, send };
    return () => {
      props.refHandle.current = null;
      observer.disconnect();
      disconnect();
      terminal.dispose();
    };
  }, []);

  useEffect(() => {
    if (socketRef.current) connect();
  }, [props.serverId, props.screenSession]);

  return <div className="sshw-terminal" ref={containerRef} />;
}

function messageFromError(exc: unknown): string {
  if (exc instanceof Error) return exc.message;
  return '操作失败';
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
