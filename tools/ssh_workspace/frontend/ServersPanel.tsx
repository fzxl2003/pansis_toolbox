// ============================================================
// SSH Workspace Tool — Servers Management Panel
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import {
  CheckCircle,
  ChevronDown,
  ChevronUp,
  ClipboardList,
  Clock,
  Loader2,
  Monitor,
  Pencil,
  Play,
  Plus,
  Server,
  Trash2,
  XCircle,
} from 'lucide-react';

import { apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { Alert, Badge, Field, Modal, Spin, useConfirm } from './components';
import { API, intervalLabel, messageFromError } from './utils';
import type {
  CommandTemplate,
  ScheduledTask,
  ScreenSession,
  ServerForm,
  SshServer,
  TaskForm,
  TemplateForm,
} from './types';
import { EMPTY_SERVER_FORM, EMPTY_TASK_FORM, EMPTY_TEMPLATE_FORM } from './types';

export type ServersPanelProps = {
  servers: SshServer[];
  loading: boolean;
  isAdmin: boolean;
  onRefresh: () => void;
};

export function ServersPanel({ servers, loading, isAdmin: _isAdmin, onRefresh }: ServersPanelProps) {
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const { confirm, dialog } = useConfirm();

  function handleAdd() {
    setEditingId(null);
    setShowForm(true);
  }

  async function handleTest(srv: SshServer) {
    try {
      await apiPost(`${API}/servers/${srv.id}/test`, {});
      onRefresh();
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  return (
    <div className="sw-panel">
      <div className="sw-panel-head">
        <div>
          <h2 className="sw-panel-title"><Server size={18} /> 服务器管理</h2>
          <p className="sw-panel-desc">命令模板与定时任务绑定到具体服务器</p>
        </div>
        <button className="sw-btn sw-btn-primary" onClick={handleAdd} type="button">
          <Plus size={14} /> 添加服务器
        </button>
      </div>

      {error && <Alert type="error">{error}</Alert>}

      {loading ? (
        <div className="sw-loading-block"><Spin /> 加载服务器列表…</div>
      ) : servers.length === 0 ? (
        <div className="sw-empty">
          <div className="sw-empty-icon"><Server size={32} /></div>
          <div className="sw-empty-title">暂无服务器</div>
          <div className="sw-empty-hint">请由管理员在设置中配置并授权 SSH 服务器</div>
        </div>
      ) : (
        <div className="sw-server-list">
          {servers.map((srv) => (
            <ServerCard
              key={srv.id}
              server={srv}
              expanded={expandedId === srv.id}
              onToggle={() => setExpandedId(expandedId === srv.id ? null : srv.id)}
              onTest={() => void handleTest(srv)}
            />
          ))}
        </div>
      )}

      {showForm && (
        <ServerFormModal
          serverId={editingId}
          onClose={() => { setShowForm(false); setEditingId(null); }}
          onSaved={() => { setShowForm(false); setEditingId(null); onRefresh(); }}
        />
      )}
      {dialog}
    </div>
  );
}

// ============================================================
// Server Card (expandable)
// ============================================================

type ServerCardProps = {
  server: SshServer;
  expanded: boolean;
  onToggle: () => void;
  onTest: () => void;
};

function ServerCard({ server, expanded, onToggle, onTest }: ServerCardProps) {
  const [testing, setTesting] = useState(false);
  const testStatusIcon = server.lastTestStatus === 'ok'
    ? <CheckCircle size={13} className="sw-text-green" />
    : server.lastTestStatus === 'failed'
      ? <XCircle size={13} className="sw-text-red" />
      : <span className="sw-text-muted">未测试</span>;

  async function handleTest() {
    setTesting(true);
    try {
      await onTest();
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className={`sw-server-card${expanded ? ' expanded' : ''}`}>
      <div className="sw-server-card-head" onClick={onToggle}>
        <div className="sw-server-card-info">
          <span className="sw-server-card-name">{server.name}</span>
          <span className="sw-server-card-host">{server.sshUsername}@{server.host}:{server.port}</span>
        </div>
        <div className="sw-server-card-badges">
          {server.hasScreen && <Badge color="green"><Monitor size={10} /> screen</Badge>}
          <span className="sw-test-status">{testing ? <Loader2 size={13} className="spin" /> : testStatusIcon}</span>
          {expanded ? <ChevronUp size={16} className="sw-text-muted" /> : <ChevronDown size={16} className="sw-text-muted" />}
        </div>
      </div>
      <div className="sw-server-card-actions">
        <button className="sw-btn sw-btn-sm sw-btn-ghost" onClick={handleTest} type="button" disabled={testing}><Play size={12} /> 测试连接</button>
      </div>

      {expanded && (
        <ServerDetail server={server} />
      )}
    </div>
  );
}

// ============================================================
// Server Detail (sub-tabs: templates / tasks / screen)
// ============================================================

function ServerDetail({ server }: { server: SshServer }) {
  const [subTab, setSubTab] = useState<'templates' | 'tasks' | 'screen'>('templates');
  const [templates, setTemplates] = useState<CommandTemplate[]>([]);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [sessions, setSessions] = useState<ScreenSession[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [tplR, taskR] = await Promise.all([
        apiGet<{ templates: CommandTemplate[] }>(`${API}/servers/${server.id}/templates`),
        apiGet<{ tasks: ScheduledTask[] }>(`${API}/scheduled-tasks?serverId=${server.id}`),
      ]);
      setTemplates(tplR.templates);
      setTasks(taskR.tasks);
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setLoading(false);
    }
  }, [server.id]);

  const loadSessions = useCallback(async () => {
    if (!server.hasScreen) return;
    try {
      const r = await apiGet<{ sessions: ScreenSession[] }>(`${API}/servers/${server.id}/screen/sessions?refresh=true`);
      setSessions(r.sessions);
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }, [server.id, server.hasScreen]);

  useEffect(() => { void loadAll(); }, [loadAll]);
  useEffect(() => { if (subTab === 'screen') void loadSessions(); }, [subTab, loadSessions]);

  return (
    <div className="sw-server-detail">
      <div className="sw-subtabs">
        <button className={`sw-subtab${subTab === 'templates' ? ' active' : ''}`} onClick={() => setSubTab('templates')} type="button">
          <ClipboardList size={13} /> 命令模板 ({templates.length})
        </button>
        <button className={`sw-subtab${subTab === 'tasks' ? ' active' : ''}`} onClick={() => setSubTab('tasks')} type="button">
          <Clock size={13} /> 定时任务 ({tasks.length})
        </button>
        {server.hasScreen && (
          <button className={`sw-subtab${subTab === 'screen' ? ' active' : ''}`} onClick={() => setSubTab('screen')} type="button">
            <Monitor size={13} /> screen 会话 ({sessions.length})
          </button>
        )}
      </div>

      {error && <Alert type="error">{error}</Alert>}
      {loading && <div className="sw-loading-inline"><Spin /> 加载中…</div>}

      {subTab === 'templates' && (
        <TemplatesSection serverId={server.id} templates={templates} onChanged={loadAll} />
      )}
      {subTab === 'tasks' && (
        <TasksSection serverId={server.id} serverName={server.name} tasks={tasks} onChanged={loadAll} />
      )}
      {subTab === 'screen' && server.hasScreen && (
        <ScreenSection serverId={server.id} sessions={sessions} onChanged={loadSessions} />
      )}
    </div>
  );
}

// ============================================================
// Templates Section
// ============================================================

function TemplatesSection({
  serverId,
  templates,
  onChanged,
}: {
  serverId: string;
  templates: CommandTemplate[];
  onChanged: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<CommandTemplate | null>(null);
  const { confirm, dialog } = useConfirm();

  function handleAdd() {
    setEditing(null);
    setShowForm(true);
  }

  function handleEdit(tpl: CommandTemplate) {
    setEditing(tpl);
    setShowForm(true);
  }

  function handleDelete(tpl: CommandTemplate) {
    confirm({
      title: '删除模板',
      message: `确认删除「${tpl.name}」？`,
      onConfirm: async () => {
        await apiDelete(`${API}/templates/${tpl.id}`);
        onChanged();
      },
    });
  }

  return (
    <div className="sw-subsection">
      <div className="sw-subsection-head">
        <span className="sw-subsection-title">命令模板</span>
        <button className="sw-btn sw-btn-sm sw-btn-primary" onClick={handleAdd} type="button"><Plus size={12} /> 新建</button>
      </div>

      {templates.length === 0 ? (
        <div className="sw-empty-inline">暂无命令模板</div>
      ) : (
        <div className="sw-tpl-list">
          {templates.map((tpl) => (
            <div key={tpl.id} className="sw-tpl-item">
              <div className="sw-tpl-item-main">
                <span className="sw-tpl-name">{tpl.name}</span>
                <code className="sw-tpl-cmd">{tpl.command}</code>
                {tpl.description && <span className="sw-tpl-desc">{tpl.description}</span>}
              </div>
              <div className="sw-tpl-item-actions">
                <button className="sw-btn sw-btn-sm sw-btn-ghost" onClick={() => handleEdit(tpl)} type="button"><Pencil size={11} /></button>
                <button className="sw-btn sw-btn-sm sw-btn-danger-ghost" onClick={() => handleDelete(tpl)} type="button"><Trash2 size={11} /></button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <TemplateFormModal
          serverId={serverId}
          template={editing}
          onClose={() => { setShowForm(false); setEditing(null); }}
          onSaved={() => { setShowForm(false); setEditing(null); onChanged(); }}
        />
      )}
      {dialog}
    </div>
  );
}

// ============================================================
// Tasks Section
// ============================================================

function TasksSection({
  serverId,
  serverName,
  tasks,
  onChanged,
}: {
  serverId: string;
  serverName: string;
  tasks: ScheduledTask[];
  onChanged: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<ScheduledTask | null>(null);
  const { confirm, dialog } = useConfirm();

  async function toggleTask(task: ScheduledTask) {
    try {
      await apiPut(`${API}/scheduled-tasks/${task.id}`, { enabled: !task.enabled });
      onChanged();
    } catch (exc) {
      // ignore
    }
  }

  function handleDelete(task: ScheduledTask) {
    confirm({
      title: '删除定时任务',
      message: `确认删除「${task.name}」？`,
      onConfirm: async () => {
        await apiDelete(`${API}/scheduled-tasks/${task.id}`);
        onChanged();
      },
    });
  }

  return (
    <div className="sw-subsection">
      <div className="sw-subsection-head">
        <span className="sw-subsection-title">定时任务（通过 screen 后台执行）</span>
        <button className="sw-btn sw-btn-sm sw-btn-primary" onClick={() => { setEditing(null); setShowForm(true); }} type="button"><Plus size={12} /> 新建</button>
      </div>

      {tasks.length === 0 ? (
        <div className="sw-empty-inline">暂无定时任务</div>
      ) : (
        <div className="sw-task-list">
          {tasks.map((task) => (
            <div key={task.id} className="sw-task-item">
              <div className="sw-task-item-main">
                <div className="sw-task-item-head">
                  <span className="sw-task-name">{task.name}</span>
                  <Badge color={task.enabled ? 'green' : 'default'}>{task.enabled ? '启用' : '停用'}</Badge>
                  <span className="sw-task-interval">{intervalLabel(task.intervalSeconds)}</span>
                </div>
                <code className="sw-task-cmd">{task.command}</code>
              </div>
              <div className="sw-task-item-actions">
                <button className="sw-btn sw-btn-sm sw-btn-ghost" onClick={() => toggleTask(task)} type="button">
                  {task.enabled ? '停用' : '启用'}
                </button>
                <button className="sw-btn sw-btn-sm sw-btn-ghost" onClick={() => { setEditing(task); setShowForm(true); }} type="button"><Pencil size={11} /></button>
                <button className="sw-btn sw-btn-sm sw-btn-danger-ghost" onClick={() => handleDelete(task)} type="button"><Trash2 size={11} /></button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showForm && (
        <TaskFormModal
          serverId={serverId}
          serverName={serverName}
          task={editing}
          onClose={() => { setShowForm(false); setEditing(null); }}
          onSaved={() => { setShowForm(false); setEditing(null); onChanged(); }}
        />
      )}
      {dialog}
    </div>
  );
}

// ============================================================
// Screen Section
// ============================================================

function ScreenSection({
  serverId,
  sessions,
  onChanged,
}: {
  serverId: string;
  sessions: ScreenSession[];
  onChanged: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [newName, setNewName] = useState('');
  const { confirm, dialog } = useConfirm();

  async function createSession() {
    const name = newName.trim() || `ssh_${Date.now()}`;
    try {
      await apiPost(`${API}/servers/${serverId}/screen/sessions`, { name, command: '' });
      setNewName('');
      setShowForm(false);
      onChanged();
    } catch (exc) {
      // ignore
    }
  }

  function handleDelete(ss: ScreenSession) {
    confirm({
      title: '删除 screen 会话',
      message: `确认删除 screen 会话「${ss.sessionName}」？该操作会终止远程会话。`,
      onConfirm: async () => {
        await apiDelete(`${API}/servers/${serverId}/screen/sessions/${ss.sessionName}`);
        onChanged();
      },
    });
  }

  return (
    <div className="sw-subsection">
      <div className="sw-subsection-head">
        <span className="sw-subsection-title">screen 会话</span>
        <button className="sw-btn sw-btn-sm sw-btn-primary" onClick={() => setShowForm(true)} type="button"><Plus size={12} /> 新建</button>
      </div>

      {showForm && (
        <div className="sw-inline-form">
          <input
            className="sw-input"
            placeholder="会话名（可选）"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <button className="sw-btn sw-btn-sm sw-btn-primary" onClick={() => void createSession()} type="button">创建</button>
          <button className="sw-btn sw-btn-sm sw-btn-secondary" onClick={() => setShowForm(false)} type="button">取消</button>
        </div>
      )}

      {sessions.length === 0 ? (
        <div className="sw-empty-inline">暂无 screen 会话</div>
      ) : (
        <div className="sw-screen-list">
          {sessions.map((ss) => (
            <div key={ss.id} className="sw-screen-item">
              <Monitor size={14} />
              <span className="sw-screen-name">{ss.sessionName}</span>
              {ss.pid && <span className="sw-screen-pid">pid:{ss.pid}</span>}
              <Badge color={ss.status === 'running' ? 'green' : 'default'}>{ss.status}</Badge>
              <button className="sw-btn sw-btn-sm sw-btn-danger-ghost" onClick={() => handleDelete(ss)} type="button"><Trash2 size={11} /></button>
            </div>
          ))}
        </div>
      )}
      {dialog}
    </div>
  );
}

// ============================================================
// Server Form Modal
// ============================================================

function ServerFormModal({
  serverId,
  onClose,
  onSaved,
}: {
  serverId: string | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<ServerForm>({ ...EMPTY_SERVER_FORM });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [globalServers, setGlobalServers] = useState<SshServer[]>([]);

  useEffect(() => {
    void (async () => {
      try {
        const r = await apiGet<{ servers: SshServer[] }>('/api/settings/ssh-servers');
        setGlobalServers(r.servers);
        if (serverId) setForm({ serverId });
      } catch (exc) {
        setError(messageFromError(exc));
      } finally {
        setLoading(false);
      }
    })();
  }, [serverId]);

  async function handleSave() {
    setSaving(true);
    setError('');
    try {
      if (serverId) {
        await apiPut(`${API}/servers/${serverId}`, { serverId });
      } else {
        await apiPost(`${API}/servers`, form);
      }
      onSaved();
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <Modal title="加载中…" onClose={onClose}>
        <div className="sw-loading-block"><Spin /> 加载中…</div>
      </Modal>
    );
  }

  return (
    <Modal
      title={serverId ? '查看服务器' : '添加服务器'}
      onClose={onClose}
      foot={
        <>
          <button className="sw-btn sw-btn-secondary" onClick={onClose} type="button" disabled={saving}>取消</button>
          <button className="sw-btn sw-btn-primary" onClick={() => void handleSave()} type="button" disabled={saving}>
            {saving ? <><Loader2 size={14} className="spin" /> 保存中</> : '保存'}
          </button>
        </>
      }
    >
      <div className="sw-form-grid">
        {error && <Alert type="error">{error}</Alert>}
        <Field label="选择服务器" full>
          <select className="sw-input" value={form.serverId} disabled={!!serverId} onChange={(e) => {
            if (e.target.value === '__add_server__') { window.location.assign('/settings'); return; }
            setForm({ ...form, serverId: e.target.value });
          }}>
            <option value="">请选择服务器</option>
            {globalServers.map((srv) => <option key={srv.id} value={srv.id}>{srv.name}（{srv.sshUsername}@{srv.host}:{srv.port}）</option>)}
            {!serverId && <option value="__add_server__">＋ 添加服务器…</option>}
          </select>
        </Field>
      </div>
    </Modal>
  );
}

// ============================================================
// Template Form Modal
// ============================================================

function TemplateFormModal({
  serverId,
  template,
  onClose,
  onSaved,
}: {
  serverId: string;
  template: CommandTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<TemplateForm>({
    ...EMPTY_TEMPLATE_FORM,
    serverId,
    name: template?.name || '',
    command: template?.command || '',
    description: template?.description || '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function handleSave() {
    setSaving(true);
    setError('');
    try {
      if (template) {
        await apiPut(`${API}/templates/${template.id}`, {
          name: form.name,
          command: form.command,
          description: form.description,
        });
      } else {
        await apiPost(`${API}/templates`, {
          serverId: form.serverId,
          name: form.name,
          command: form.command,
          description: form.description,
        });
      }
      onSaved();
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={template ? '编辑命令模板' : '新建命令模板'}
      onClose={onClose}
      foot={
        <>
          <button className="sw-btn sw-btn-secondary" onClick={onClose} type="button" disabled={saving}>取消</button>
          <button className="sw-btn sw-btn-primary" onClick={() => void handleSave()} type="button" disabled={saving}>
            {saving ? <><Loader2 size={14} className="spin" /> 保存中</> : '保存'}
          </button>
        </>
      }
    >
      <div className="sw-form-grid">
        {error && <Alert type="error">{error}</Alert>}
        <Field label="模板名称" full><input className="sw-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label="命令" full>
          <textarea className="sw-textarea" rows={4} value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} placeholder="支持 {{变量}} 占位符" />
        </Field>
        <Field label="描述（可选）" full><input className="sw-input" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}

// ============================================================
// Task Form Modal
// ============================================================

function TaskFormModal({
  serverId,
  serverName,
  task,
  onClose,
  onSaved,
}: {
  serverId: string;
  serverName: string;
  task: ScheduledTask | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<TaskForm>({
    ...EMPTY_TASK_FORM,
    serverId,
    name: task?.name || '',
    command: task?.command || '',
    intervalSeconds: task?.intervalSeconds || 3600,
    screenNamePrefix: task?.screenNamePrefix || 'ssh_task',
    enabled: task?.enabled ?? true,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function handleSave() {
    setSaving(true);
    setError('');
    try {
      if (task) {
        await apiPut(`${API}/scheduled-tasks/${task.id}`, form);
      } else {
        await apiPost(`${API}/scheduled-tasks`, form);
      }
      onSaved();
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={task ? '编辑定时任务' : '新建定时任务'}
      onClose={onClose}
      foot={
        <>
          <button className="sw-btn sw-btn-secondary" onClick={onClose} type="button" disabled={saving}>取消</button>
          <button className="sw-btn sw-btn-primary" onClick={() => void handleSave()} type="button" disabled={saving}>
            {saving ? <><Loader2 size={14} className="spin" /> 保存中</> : '保存'}
          </button>
        </>
      }
    >
      <div className="sw-form-grid">
        {error && <Alert type="error">{error}</Alert>}
        <div className="sw-form-hint"><Server size={12} /> 绑定到服务器：{serverName}</div>
        <Field label="任务名称" full><input className="sw-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        <Field label="执行命令" full>
          <textarea className="sw-textarea" rows={3} value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} />
        </Field>
        <Field label="执行间隔（秒，最小 60）"><input className="sw-input" type="number" min={60} value={form.intervalSeconds} onChange={(e) => setForm({ ...form, intervalSeconds: parseInt(e.target.value) || 60 })} /></Field>
        <Field label="screen 会话前缀"><input className="sw-input" value={form.screenNamePrefix} onChange={(e) => setForm({ ...form, screenNamePrefix: e.target.value })} /></Field>
        <Field label="启用" full>
          <label className="sw-checkbox">
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
            启用此定时任务
          </label>
        </Field>
      </div>
    </Modal>
  );
}
