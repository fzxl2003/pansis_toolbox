import { FormEvent, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Activity,
  AlertTriangle,
  Bell,
  BellOff,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Globe,
  Mail,
  Monitor,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Server,
  Settings,
  Shield,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';

import { ApiError, apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

// ============================================================
// Types
// ============================================================

type EmServer = {
  id: string;
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  updatedAt: string;
};

type MatchMode = 'simple' | 'regex';
type AlertCondition = 'below' | 'above' | 'changed';
type ActionType = 'email' | 'script';

type MonitorTask = {
  id: string;
  serverId: string;
  name: string;
  description: string;
  matchMode: MatchMode;
  matchPattern: string;
  filterUser: string;
  alertCondition: AlertCondition;
  alertThreshold: number;
  alertChangeAmount: number;
  confirmCount: number;
  checkIntervalSeconds: number;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
};

type AlertAction = {
  id: string;
  taskId: string;
  actionType: ActionType;
  emailRecipients: string[];
  emailSubjectTemplate: string;
  emailBodyTemplate: string;
  scriptCommands: string[];
  scriptScreenName: string;
  scriptsPerTrigger: number;
  sortOrder: number;
  enabled: boolean;
  createdAt: string;
};

type Sample = {
  id: string;
  checkedAt: string;
  processCount: number;
  matchedProcesses: string[];
  conditionMet: boolean;
  error: string | null;
};

type AlertEvent = {
  id: string;
  actionId: string | null;
  eventType: string;
  message: string;
  details: Record<string, unknown>;
  createdAt: string;
};

type AlertState = {
  taskId: string;
  consecutiveMeets: number;
  lastCheckCount: number | null;
  isAlerting: boolean;
  lastAlertedAt: string | null;
  resolvedAt: string | null;
  updatedAt: string;
};

// Unified log entry type (merges samples + events)
type LogEntry = {
  id: string;
  type: 'sample' | 'triggered' | 'email_sent' | 'email_failed' | 'script_executed' | 'script_failed' | 'resolved';
  timestamp: string;
  title: string;
  message: string;
  details?: Record<string, unknown>;
  // Sample-specific fields
  processCount?: number;
  conditionMet?: boolean;
  error?: string | null;
};

type EmailConfig = {
  smtpHost: string;
  smtpPort: number;
  smtpUsername: string;
  smtpFromAddress: string;
  smtpFromName: string;
  configured: boolean;
};

type SshTestResult = {
  connected: boolean;
  username?: string;
  hasScreen?: boolean;
  error?: string;
};

// ============================================================
// Form States
// ============================================================

type ServerFormState = {
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  sshPassword: string;
};

type TaskFormState = {
  serverId: string;
  name: string;
  description: string;
  matchMode: MatchMode;
  matchPattern: string;
  filterUser: string;
  alertCondition: AlertCondition;
  alertThreshold: number;
  alertChangeAmount: number;
  confirmCount: number;
  checkIntervalSeconds: number;
  enabled: boolean;
};

type EmailActionFormState = {
  emailRecipients: string;
  emailSubjectTemplate: string;
  emailBodyTemplate: string;
};

type ScriptActionFormState = {
  scriptCommands: string;
  scriptScreenName: string;
  scriptsPerTrigger: number;
};

type EmailConfigFormState = {
  smtpHost: string;
  smtpPort: number;
  smtpUsername: string;
  smtpPassword: string;
  smtpFromAddress: string;
  smtpFromName: string;
};

// ============================================================
// Constants
// ============================================================

const emptyServer: ServerFormState = {
  name: '',
  host: '',
  port: 22,
  sshUsername: '',
  sshPassword: '',
};

const emptyTask: TaskFormState = {
  serverId: '',
  name: '',
  description: '',
  matchMode: 'simple',
  matchPattern: '',
  filterUser: '',
  alertCondition: 'below',
  alertThreshold: 0,
  alertChangeAmount: 1,
  confirmCount: 3,
  checkIntervalSeconds: 30,
  enabled: true,
};

const emptyEmailAction: EmailActionFormState = {
  emailRecipients: '',
  emailSubjectTemplate: '实验监控报警: {task_name}',
  emailBodyTemplate: `实验监控报警通知

监控任务: {task_name}
服务器: {server_name}
当前进程数: {current_count}
阈值条件: {threshold}
触发原因: {reason}
触发时间: {time}

此邮件由实验监控系统自动发送。`,
};

const emptyScriptAction: ScriptActionFormState = {
  scriptCommands: '',
  scriptScreenName: '',
  scriptsPerTrigger: 1,
};

const emptyEmailConfig: EmailConfigFormState = {
  smtpHost: 'smtp.buaa.edu.cn',
  smtpPort: 465,
  smtpUsername: '',
  smtpPassword: '',
  smtpFromAddress: '',
  smtpFromName: 'Experiment Monitor',
};

// ============================================================
// Main Component
// ============================================================

export default function ExperimentMonitorTool() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [servers, setServers] = useState<EmServer[]>([]);
  const [tasks, setTasks] = useState<MonitorTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>('');
  const [history, setHistory] = useState<{ samples: Sample[]; events: AlertEvent[]; alertState: AlertState | null }>({
    samples: [],
    events: [],
    alertState: null,
  });

  // Log filter state
  const [logFilter, setLogFilter] = useState<'all' | LogEntry['type']>('all');

  // Modal states
  const [modal, setModal] = useState<'server-create' | 'server-edit' | 'task-create' | 'task-edit'
    | 'action-email' | 'action-script' | 'email-config' | null>(null);
  const [editingServerId, setEditingServerId] = useState<string>('');
  const [editingActionId, setEditingActionId] = useState<string>('');

  // Form states
  const [serverForm, setServerForm] = useState<ServerFormState>(emptyServer);
  const [taskForm, setTaskForm] = useState<TaskFormState>(emptyTask);
  const [emailActionForm, setEmailActionForm] = useState<EmailActionFormState>(emptyEmailAction);
  const [scriptActionForm, setScriptActionForm] = useState<ScriptActionFormState>(emptyScriptAction);
  const [emailConfigForm, setEmailConfigForm] = useState<EmailConfigFormState>(emptyEmailConfig);

  // UI state
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [expandedTasks, setExpandedTasks] = useState<Set<string>>(new Set());
  const [sshTestResult, setSshTestResult] = useState<SshTestResult | null>(null);

  // Actions for selected task
  const [actions, setActions] = useState<AlertAction[]>([]);

  const isAdmin = me?.role === 'admin';
  const selectedTask = tasks.find((t) => t.id === selectedTaskId) || tasks[0] || null;

  useEffect(() => {
    fetchMe().then((state) => setMe(state.user)).catch(() => setMe(null));
    void loadServers();
    void loadTasks();
  }, []);

  useEffect(() => {
    if (selectedTask) {
      setSelectedTaskId(selectedTask.id);
      void loadTaskDetail(selectedTask.id);
      void loadActions(selectedTask.id);
    }
  }, [selectedTask?.id]);

  // Auto-refresh history
  useEffect(() => {
    if (!selectedTaskId) return;
    const timer = window.setInterval(() => {
      void loadTaskHistory(selectedTaskId);
      void loadAlertState(selectedTaskId);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [selectedTaskId]);

  // ============================================================
  // Data Loading
  // ============================================================

  async function loadServers() {
    try {
      const payload = await apiGet<{ servers: EmServer[] }>('/api/tools/experiment-monitor/servers');
      setServers(payload.servers);
    } catch (err) {
      handleError(err);
    }
  }

  async function loadTasks(serverId?: string) {
    try {
      const url = serverId
        ? `/api/tools/experiment-monitor/tasks?serverId=${serverId}`
        : '/api/tools/experiment-monitor/tasks';
      const payload = await apiGet<{ tasks: MonitorTask[] }>(url);
      setTasks(payload.tasks);
      if (payload.tasks.length && !selectedTaskId && !serverId) {
        setSelectedTaskId(payload.tasks[0].id);
      }
    } catch (err) {
      handleError(err);
    }
  }

  async function loadTaskDetail(taskId: string) {
    try {
      await Promise.all([loadTaskHistory(taskId), loadAlertState(taskId)]);
    } catch (err) {
      handleError(err);
    }
  }

  async function loadTaskHistory(taskId: string) {
    try {
      const payload = await apiGet<{ samples: Sample[]; events: AlertEvent[]; alertState: AlertState | null }>(
        `/api/tools/experiment-monitor/tasks/${taskId}/history?hours=24`,
      );
      setHistory(payload);
    } catch (err) {
      handleError(err);
    }
  }

  async function loadAlertState(taskId: string) {
    try {
      const payload = await apiGet<{ alertState: AlertState | null }>(
        `/api/tools/experiment-monitor/tasks/${taskId}/alert-state`,
      );
      setHistory((prev) => ({ ...prev, alertState: payload.alertState }));
    } catch (err) {
      handleError(err);
    }
  }

  async function loadActions(taskId: string) {
    try {
      const payload = await apiGet<{ actions: AlertAction[] }>(
        `/api/tools/experiment-monitor/tasks/${taskId}/actions`,
      );
      setActions(payload.actions);
    } catch (err) {
      handleError(err);
    }
  }

  // ============================================================
  // Server CRUD
  // ============================================================

  function openCreateServer() {
    setServerForm(emptyServer);
    setSshTestResult(null);
    setModal('server-create');
  }

  function openEditServer(serverId: string) {
    const server = servers.find((s) => s.id === serverId);
    if (!server) return;
    setServerForm({
      name: server.name,
      host: server.host,
      port: server.port,
      sshUsername: server.sshUsername,
      sshPassword: '',
    });
    setEditingServerId(serverId);
    setSshTestResult(null);
    setModal('server-edit');
  }

  async function saveServer(event: FormEvent) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    try {
      const payload = {
        name: serverForm.name,
        host: serverForm.host,
        port: serverForm.port,
        sshUsername: serverForm.sshUsername,
        sshPassword: serverForm.sshPassword || undefined,
      };
      if (modal === 'server-edit' && editingServerId) {
        await apiPut(`/api/tools/experiment-monitor/servers/${editingServerId}`, payload);
      } else {
        await apiPost('/api/tools/experiment-monitor/servers', { ...payload, sshPassword: serverForm.sshPassword });
      }
      setModal(null);
      showSuccess('服务器保存成功');
      await loadServers();
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function removeServer(serverId: string) {
    const server = servers.find((s) => s.id === serverId);
    if (!server || !window.confirm(`确认删除服务器「${server.name}」？相关监控任务也将被禁用。`)) return;
    try {
      await apiDelete(`/api/tools/experiment-monitor/servers/${serverId}`);
      showSuccess('服务器已删除');
      await loadServers();
      await loadTasks();
    } catch (err) {
      handleError(err);
    }
  }

  async function testConnection(serverId: string) {
    try {
      const result = await apiGet<SshTestResult>(`/api/tools/experiment-monitor/servers/${serverId}/test`);
      setSshTestResult(result);
    } catch (err) {
      handleError(err);
    }
  }

  // ============================================================
  // Task CRUD
  // ============================================================

  function openCreateTask() {
    setTaskForm({ ...emptyTask, serverId: servers[0]?.id || '' });
    setModal('task-create');
  }

  function openEditTask(task: MonitorTask) {
    setTaskForm({
      serverId: task.serverId,
      name: task.name,
      description: task.description,
      matchMode: task.matchMode,
      matchPattern: task.matchPattern,
      filterUser: task.filterUser,
      alertCondition: task.alertCondition,
      alertThreshold: task.alertThreshold,
      alertChangeAmount: task.alertChangeAmount,
      confirmCount: task.confirmCount,
      checkIntervalSeconds: task.checkIntervalSeconds,
      enabled: task.enabled,
    });
    setModal('task-edit');
  }

  async function saveTask(event: FormEvent) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    try {
      if (modal === 'task-edit' && selectedTask) {
        await apiPut(`/api/tools/experiment-monitor/tasks/${selectedTask.id}`, taskForm);
      } else {
        await apiPost('/api/tools/experiment-monitor/tasks', taskForm);
      }
      setModal(null);
      showSuccess('监控任务保存成功');
      await loadTasks();
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function removeTask(taskId: string) {
    const task = tasks.find((t) => t.id === taskId);
    if (!task || !window.confirm(`确认删除监控任务「${task.name}」？`)) return;
    try {
      await apiDelete(`/api/tools/experiment-monitor/tasks/${taskId}`);
      showSuccess('任务已删除');
      if (selectedTaskId === taskId) setSelectedTaskId('');
      await loadTasks();
    } catch (err) {
      handleError(err);
    }
  }

  async function runCheckNow(taskId: string) {
    try {
      await apiPost(`/api/tools/experiment-monitor/tasks/${taskId}/check-now`, {});
      showSuccess('已触发手动检查');
      await loadTaskHistory(taskId);
      await loadAlertState(taskId);
    } catch (err) {
      handleError(err);
    }
  }

  async function resetAlert(taskId: string) {
    try {
      await apiPost(`/api/tools/experiment-monitor/tasks/${taskId}/reset-alert`, {});
      showSuccess('报警状态已重置');
      await loadAlertState(taskId);
      await loadTaskHistory(taskId);
    } catch (err) {
      handleError(err);
    }
  }

  // ============================================================
  // Action CRUD
  // ============================================================

  function openCreateEmailAction() {
    if (!selectedTask) return;
    setEmailActionForm({ ...emptyEmailAction });
    setEditingActionId('');
    setModal('action-email');
  }

  function openCreateScriptAction() {
    if (!selectedTask) return;
    setScriptActionForm({ ...emptyScriptAction });
    setEditingActionId('');
    setModal('action-script');
  }

  function editAction(action: AlertAction) {
    setEditingActionId(action.id);
    if (action.actionType === 'email') {
      setEmailActionForm({
        emailRecipients: action.emailRecipients.join(', '),
        emailSubjectTemplate: action.emailSubjectTemplate,
        emailBodyTemplate: action.emailBodyTemplate,
      });
      setModal('action-email');
    } else {
      setScriptActionForm({
        scriptCommands: action.scriptCommands.join('\n'),
        scriptScreenName: action.scriptScreenName,
        scriptsPerTrigger: action.scriptsPerTrigger,
      });
      setModal('action-script');
    }
  }

  async function saveEmailAction(event: FormEvent) {
    event.preventDefault();
    if (!selectedTask) return;
    setIsLoading(true);
    setError(null);
    try {
      const payload = {
        actionType: 'email' as ActionType,
        emailRecipients: splitLines(emailActionForm.emailRecipients),
        emailSubjectTemplate: emailActionForm.emailSubjectTemplate,
        emailBodyTemplate: emailActionForm.emailBodyTemplate,
      };
      if (editingActionId) {
        await apiPut(`/api/tools/experiment-monitor/actions/${editingActionId}`, payload);
      } else {
        await apiPost(`/api/tools/experiment-monitor/tasks/${selectedTask.id}/actions`, payload);
      }
      setModal(null);
      showSuccess('邮件动作保存成功');
      await loadActions(selectedTask.id);
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function saveScriptAction(event: FormEvent) {
    event.preventDefault();
    if (!selectedTask) return;
    setIsLoading(true);
    setError(null);
    try {
      const payload = {
        actionType: 'script' as ActionType,
        scriptCommands: splitLines(scriptActionForm.scriptCommands),
        scriptScreenName: scriptActionForm.scriptScreenName,
        scriptsPerTrigger: scriptActionForm.scriptsPerTrigger,
      };
      if (editingActionId) {
        await apiPut(`/api/tools/experiment-monitor/actions/${editingActionId}`, payload);
      } else {
        await apiPost(`/api/tools/experiment-monitor/tasks/${selectedTask.id}/actions`, payload);
      }
      setModal(null);
      showSuccess('脚本动作保存成功');
      await loadActions(selectedTask.id);
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function removeAction(actionId: string) {
    if (!window.confirm('确认删除该报警动作？')) return;
    try {
      await apiDelete(`/api/tools/experiment-monitor/actions/${actionId}`);
      showSuccess('动作已删除');
      if (selectedTask) await loadActions(selectedTask.id);
    } catch (err) {
      handleError(err);
    }
  }

  // ============================================================
  // Email Config (Admin)
  // ============================================================

  async function openEmailConfig() {
    if (!isAdmin) return;
    try {
      const config = await apiGet<EmailConfig>('/api/tools/experiment-monitor/email-config');
      setEmailConfigForm({
        smtpHost: config.smtpHost,
        smtpPort: config.smtpPort,
        smtpUsername: config.smtpUsername,
        smtpPassword: '',
        smtpFromAddress: config.smtpFromAddress,
        smtpFromName: config.smtpFromName,
      });
      setModal('email-config');
    } catch (err) {
      handleError(err);
    }
  }

  async function saveEmailConfig(event: FormEvent) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    try {
      await apiPost('/api/tools/experiment-monitor/email-config', emailConfigForm);
      setModal(null);
      showSuccess('邮件配置保存成功');
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function testEmailConfig() {
    if (!emailConfigForm.smtpFromAddress && !emailConfigForm.smtpUsername) {
      setError('请先填写发件人地址');
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const result = await apiPost<{ success: boolean; testTo: string }>('/api/tools/experiment-monitor/email-config/test', emailConfigForm);
      showSuccess(`测试邮件已发送至 ${result.testTo}，请查收`);
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  // ============================================================
  // UI Helpers
  // ============================================================

  function toggleExpand(taskId: string) {
    setExpandedTasks((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId); else next.add(taskId);
      return next;
    });
  }

  function showSuccess(msg: string) {
    setSuccessMsg(msg);
    setTimeout(() => setSuccessMsg(null), 3000);
  }

  function handleError(err: unknown) {
    if (err instanceof ApiError && err.code === 'LOGIN_REQUIRED') {
      setError('请先登录后再执行该操作');
      return;
    }
    setError(err instanceof Error ? err.message : '操作失败');
  }

  // ============================================================
  // Render
  // ============================================================

  return (
    <div className="tool-surface experiment-monitor">
      {/* Header */}
      <div className="tool-header em-titlebar">
        <div>
          <p className="eyebrow">Experiment Monitor</p>
          <h1>实验监控报警与触发</h1>
        </div>
        <div className="toolbar">
          {me && (
            <>
              <button className="primary-button" type="button" onClick={openCreateTask}>
                <Plus size={16} />新建监控
              </button>
              <button className="chip" type="button" onClick={openCreateServer}>
                <Server size={15} />服务器
              </button>
              {isAdmin && (
                <button className="chip" type="button" onClick={openEmailConfig}>
                  <Mail size={15} />邮件配置
                </button>
              )}
            </>
          )}
          <button className="chip" type="button" onClick={() => { void loadTasks(); void loadServers(); }}>
            <RefreshCw size={15} />刷新
          </button>
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}
      {successMsg && <div className="success-box">{successMsg}</div>}

      {/* Task List */}
      <section className="em-task-list">
        {tasks.length === 0 ? (
          <div className="empty-state">
            <Activity size={32} />
            <p>暂无监控任务，点击「新建监控」开始创建。</p>
          </div>
        ) : (
          tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              server={servers.find((s) => s.id === task.serverId)}
              isExpanded={expandedTasks.has(task.id)}
              isSelected={selectedTaskId === task.id}
              actions={selectedTaskId === task.id ? actions : []}
              alertState={selectedTaskId === task.id ? history.alertState : null}
              latestSample={selectedTaskId === task.id ? history.samples[history.samples.length - 1] : null}
              onSelect={() => { setSelectedTaskId(task.id); toggleExpand(task.id); }}
              onToggleExpand={() => toggleExpand(task.id)}
              onEdit={() => openEditTask(task)}
              onDelete={() => removeTask(task.id)}
              onCheckNow={() => void runCheckNow(task.id)}
              onResetAlert={() => void resetAlert(task.id)}
              onAddEmailAction={openCreateEmailAction}
              onAddScriptAction={openCreateScriptAction}
              onEditAction={(action) => editAction(action)}
              onDeleteAction={(actionId) => void removeAction(actionId)}
            />
          ))
        )}
      </section>

      {/* History Panel (when a task is selected and expanded) */}
      {selectedTask && expandedTasks.has(selectedTaskId) && (
        <section className="em-detail-panel">
          <div className="result-header">
            <span><Clock size={15} />监控日志</span>
            <span className="muted">{history.samples.length + history.events.length} 条记录</span>
          </div>

          {/* Alert State */}
          {history.alertState && (
            <div className={`em-alert-state ${history.alertState.isAlerting ? 'alerting' : ''}`}>
              {history.alertState.isAlerting ? (
                <><AlertTriangle size={16} /><strong>报警中</strong></>
              ) : (
                <><CheckCircle2 size={16} /><strong>正常</strong></>
              )}
              <span className="muted">
                连续满足 {history.alertState.consecutiveMeets}/{selectedTask.confirmCount} 次
                · 上次检查进程数: {history.alertState.lastCheckCount ?? '-'}
              </span>
              {history.alertState.isAlerting && (
                <button className="chip small" type="button" onClick={() => void resetAlert(selectedTaskId)}>
                  <RotateCcw size={13} />重置报警
                </button>
              )}
            </div>
          )}

          {/* Process Count Chart */}
          <MiniChart samples={history.samples} />

          {/* Log Filter Tabs */}
          <div className="em-log-filter">
            <button className={`log-tab ${logFilter === 'all' ? 'active' : ''}`} type="button" onClick={() => setLogFilter('all')}>全部</button>
            <button className={`log-tab ${logFilter === 'sample' ? 'active' : ''}`} type="button" onClick={() => setLogFilter('sample')}>采样</button>
            <button className={`log-tab ${logFilter === 'triggered' ? 'active' : ''}`} type="button" onClick={() => setLogFilter('triggered')}>报警</button>
            <button className={`log-tab ${logFilter === 'email_sent' || logFilter === 'email_failed' ? 'active' : ''}`} type="button" onClick={() => setLogFilter('email_sent')}>邮件</button>
            <button className={`log-tab ${logFilter === 'script_executed' || logFilter === 'script_failed' ? 'active' : ''}`} type="button" onClick={() => setLogFilter('script_executed')}>脚本</button>
            <button className={`log-tab ${logFilter === 'resolved' ? 'active' : ''}`} type="button" onClick={() => setLogFilter('resolved')}>重置</button>
          </div>

          {/* Unified Log Timeline */}
          <UnifiedLogPanel
            samples={history.samples}
            events={history.events}
            filter={logFilter}
          />
        </section>
      )}

      {!me && <LoginPanel onSuccess={() => window.location.reload()} />}

      {/* Modals */}
      {modal === 'server-create' && (
        <Modal title="添加服务器" onClose={() => setModal(null)}>
          <ServerForm form={serverForm} testResult={sshTestResult} isLoading={isLoading} onChange={setServerForm} onSubmit={saveServer} onTest={() => { /* test after save */ }} />
        </Modal>
      )}

      {modal === 'server-edit' && (
        <Modal title="编辑服务器" onClose={() => setModal(null)}>
          <ServerForm form={serverForm} testResult={sshTestResult} isLoading={isLoading} isEdit onChange={setServerForm} onSubmit={saveServer} onTest={() => editingServerId && void testConnection(editingServerId)} />
          {editingServerId && (
            <button className="danger-row modal-danger" type="button" onClick={() => { setModal(null); void removeServer(editingServerId); }}>
              <Trash2 size={15} />删除此服务器
            </button>
          )}
        </Modal>
      )}

      {(modal === 'task-create' || modal === 'task-edit') && (
        <Modal title={modal === 'task-edit' ? '编辑监控任务' : '新建监控任务'} onClose={() => setModal(null)}>
          <TaskForm form={taskForm} servers={servers} isLoading={isLoading} isEdit={modal === 'task-edit'} onChange={setTaskForm} onSubmit={saveTask} />
        </Modal>
      )}

      {modal === 'action-email' && (
        <Modal title={editingActionId ? '编辑邮件通知' : '添加邮件通知'} onClose={() => setModal(null)}>
          <EmailActionForm form={emailActionForm} isLoading={isLoading} isEdit={!!editingActionId} onChange={setEmailActionForm} onSubmit={saveEmailAction} />
        </Modal>
      )}

      {modal === 'action-script' && (
        <Modal title={editingActionId ? '编辑脚本触发' : '添加脚本触发'} onClose={() => setModal(null)}>
          <ScriptActionForm form={scriptActionForm} isLoading={isLoading} isEdit={!!editingActionId} onChange={setScriptActionForm} onSubmit={saveScriptAction} />
        </Modal>
      )}

      {modal === 'email-config' && (
      <Modal title="邮件发送配置（管理员）" onClose={() => setModal(null)}>
        <EmailConfigForm form={emailConfigForm} isLoading={isLoading} onChange={setEmailConfigForm} onSubmit={saveEmailConfig} onTest={testEmailConfig} />
      </Modal>
      )}
    </div>
  );
}

// ============================================================
// Sub-Components
// ============================================================

function TaskCard(props: {
  task: MonitorTask;
  server: EmServer | undefined;
  isExpanded: boolean;
  isSelected: boolean;
  actions: AlertAction[];
  alertState: AlertState | null;
  latestSample: Sample | null;
  onSelect: () => void;
  onToggleExpand: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onCheckNow: () => void;
  onResetAlert: () => void;
  onAddEmailAction: () => void;
  onAddScriptAction: () => void;
  onEditAction: (action: AlertAction) => void;
  onDeleteAction: (actionId: string) => void;
}) {
  const { task, server, alertState, latestSample } = props;

  return (
    <div className={`em-task-card ${props.isSelected ? 'selected' : ''} ${!task.enabled ? 'disabled' : ''}`}>
      <div className="em-task-header" onClick={props.onSelect}>
        <button className="expand-btn" type="button" onClick={(e) => { e.stopPropagation(); props.onToggleExpand(); }}>
          {props.isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <div className="em-task-info">
          <div className="em-task-title-row">
            <Activity size={16} className={alertState?.isAlerting ? 'alert-pulse' : ''} />
            <strong>{task.name}</strong>
            {task.description && <small className="muted">{task.description}</small>}
            {!task.enabled && <span className="badge disabled">已禁用</span>}
            {alertState?.isAlerting && <span className="badge danger">报警中</span>}
          </div>
          <div className="em-task-meta">
            <span><Server size={12} />{server?.name || '未知服务器'}</span>
            <span><Monitor size={12} />{task.matchMode === 'simple' ? '简单匹配' : '正则匹配'}: {escapeHtml(task.matchPattern)}</span>
            <span>{getConditionLabel(task)}</span>
            {latestSample && (
              <span className={latestSample.conditionMet ? 'text-warning' : ''}>
                进程数: <strong>{latestSample.processCount}</strong>
              </span>
            )}
          </div>
        </div>
        <div className="em-task-actions">
          <button className="icon-button tiny" type="button" onClick={(e) => { e.stopPropagation(); props.onCheckNow(); }} title="立即检查"><Play size={14} /></button>
          <button className="icon-button tiny" type="button" onClick={(e) => { e.stopPropagation(); props.onEdit(); }} title="编辑"><Settings size={14} /></button>
          <button className="icon-button tiny danger" type="button" onClick={(e) => { e.stopPropagation(); props.onDelete(); }} title="删除"><Trash2 size={14} /></button>
        </div>
      </div>

      {props.isExpanded && (
        <div className="em-task-detail">
          {/* Alert State */}
          {alertState && (
            <div className={`em-alert-state inline ${alertState.isAlerting ? 'alerting' : ''}`}>
              {alertState.isAlerting
                ? <><AlertTriangle size={14} /><span>报警中 · 连续满足 {alertState.consecutiveMeets}/{task.confirmCount}</span></>
                : <><CheckCircle2 size={14} /><span>正常 · 连续满足 {alertState.consecutiveMeets}/{task.confirmCount}</span></>
              }
              {alertState.isAlerting && (
                <button className="chip tiny" type="button" onClick={(e) => { e.stopPropagation(); props.onResetAlert(); }}>
                  <RotateCcw size={12} />重置
                </button>
              )}
            </div>
          )}

          {/* Actions */}
          <div className="em-actions-section">
            <div className="result-header">
              <span><Bell size={14} />报警动作</span>
              <div>
                <button className="chip tiny" type="button" onClick={(e) => { e.stopPropagation(); props.onAddEmailAction(); }}>
                  <Mail size={12} />邮件
                </button>
                <button className="chip tiny" type="button" onClick={(e) => { e.stopPropagation(); props.onAddScriptAction(); }}>
                  <Terminal size={12} />脚本
                </button>
              </div>
            </div>
            {props.actions.length === 0 ? (
              <p className="muted">尚未配置报警动作，添加邮件或脚本动作以在报警时自动执行。</p>
            ) : (
              props.actions.map((action) => (
                <div className="em-action-item" key={action.id}>
                  <span className="action-icon">
                    {action.actionType === 'email' ? <Mail size={14} /> : <Terminal size={14} />}
                  </span>
                  <div className="action-info">
                    <strong>{action.actionType === 'email' ? '邮件通知' : '脚本触发'}</strong>
                    {action.actionType === 'email' ? (
                      <small className="muted">收件人: {action.emailRecipients.join(', ') || '未设置'}</small>
                    ) : (
                      <small className="muted">{action.scriptCommands.length} 个脚本 · 每次 {action.scriptsPerTrigger} 个</small>
                    )}
                  </div>
                  <button className="icon-button tiny" type="button" onClick={(e) => { e.stopPropagation(); props.onEditAction(action); }} title="编辑"><Settings size={13} /></button>
                  <button className="icon-button tiny danger" type="button" onClick={(e) => { e.stopPropagation(); props.onDeleteAction(action.id); }} title="删除"><Trash2 size={13} /></button>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function MiniChart({ samples }: { samples: Sample[] }) {
  if (samples.length < 2) return null;
  const points = samples.map((s, i) => ({ index: i, value: s.processCount }));
  const maxVal = Math.max(...points.map((p) => p.value), 1);
  const path = points.map((p, i) => {
    const x = points.length === 1 ? 200 : (i / (points.length - 1)) * 400;
    const y = 80 - (p.value / maxVal) * 70;
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');

  return (
    <div className="mini-chart">
      <div className="result-header"><span>进程数趋势</span><small>最近 {samples.length} 次采样</small></div>
      <svg viewBox="0 0 400 90" role="img" aria-label="process-count-chart">
        <path d="M 0 85 L 400 85" className="chart-grid-line" />
        {path && <path className="chart-line" d={path} fill="none" />}
        {points.map((p) => {
          const x = points.length === 1 ? 200 : (p.index / (points.length - 1)) * 400;
          const y = 80 - (p.value / maxVal) * 70;
          return <circle key={p.index} cx={x} cy={y} r="2.5" className="chart-dot" />;
        })}
      </svg>
    </div>
  );
}

function UnifiedLogPanel({ samples, events, filter }: { samples: Sample[]; events: AlertEvent[]; filter: 'all' | LogEntry['type'] }) {
  // Merge and normalize all entries into a unified timeline
  const allEntries: LogEntry[] = [
    // Samples as log entries
    ...samples.map((s) => ({
      id: s.id,
      type: 'sample' as const,
      timestamp: s.checkedAt,
      title: `采样 · ${s.processCount} 个进程`,
      message: s.conditionMet ? '条件满足' : '条件未满足',
      processCount: s.processCount,
      conditionMet: s.conditionMet,
      error: s.error,
    })),
    // Events as log entries
    ...events.map((e) => ({
      id: e.id,
      type: e.eventType as LogEntry['type'],
      timestamp: e.createdAt,
      title: '',
      message: e.message,
      details: e.details,
    })),
  ].sort((a, b) => b.timestamp.localeCompare(a.timestamp));

  const filtered = filter === 'all' ? allEntries : allEntries.filter((e) => e.type === filter || (filter === 'email_sent' && e.type === 'email_failed') || (filter === 'script_executed' && e.type === 'script_failed'));

  if (filtered.length === 0) {
    return (
      <div className="em-log-empty">
        <Clock size={20} />
        <p>{filter === 'all' ? '暂无日志记录' : `暂无「{getFilterLabel(filter)}」类型的记录`}</p>
      </div>
    );
  }

  return (
    <div className="em-log-timeline">
      {filtered.map((entry) => (
        <div className={`em-log-entry ${entry.type}`} key={entry.id}>
          <span className={`log-type-icon ${entry.type}`}>
            {entry.type === 'sample' && <Monitor size={13} />}
            {entry.type === 'triggered' && <AlertTriangle size={13} />}
            {entry.type === 'email_sent' && <Mail size={13} />}
            {entry.type === 'email_failed' && <X size={13} />}
            {entry.type === 'script_executed' && <Terminal size={13} />}
            {entry.type === 'script_failed' && <X size={13} />}
            {entry.type === 'resolved' && <CheckCircle2 size={13} />}
          </span>
          <div className="log-body">
            <div className="log-primary">
              {entry.type === 'sample' ? (
                <>
                  <strong>采样</strong>
                  <span className="log-badge sample">{entry.processCount} 进程</span>
                  <span className={`log-status ${entry.conditionMet ? 'met' : 'unmet'}`}>{entry.conditionMet ? '满足' : '未满足'}</span>
                </>
              ) : (
                <strong>{getEventTypeLabel(entry.type)}</strong>
              )}
              <span className="log-msg">{entry.message}</span>
            </div>
            {entry.error && <span className="log-error">{entry.error.slice(0, 120)}</span>}
            {entry.details && Object.keys(entry.details).length > 0 && (
              <span className="log-details">
                {JSON.stringify(entry.details).slice(0, 100)}
              </span>
            )}
          </div>
          <span className="log-time">{formatTime(entry.timestamp)}</span>
        </div>
      ))}
    </div>
  );
}

function getFilterLabel(filter: string): string {
  const labels: Record<string, string> = {
    all: '全部',
    sample: '采样',
    triggered: '报警',
    email_sent: '邮件',
    script_executed: '脚本',
    resolved: '重置',
  };
  return labels[filter] || filter;
}

function getEventTypeLabel(type: string): string {
  const labels: Record<string, string> = {
    triggered: '报警触发',
    email_sent: '邮件发送',
    email_failed: '邮件失败',
    script_executed: '脚本执行',
    script_failed: '脚本失败',
    resolved: '报警重置',
  };
  return labels[type] || type;
}

// ============================================================
// Form Components
// ============================================================

function ServerForm(props: {
  form: ServerFormState;
  testResult: SshTestResult | null;
  isLoading: boolean;
  isEdit?: boolean;
  onChange: (form: ServerFormState) => void;
  onSubmit: (event: FormEvent) => void;
  onTest: () => void;
}) {
  return (
    <form className="em-form" onSubmit={props.onSubmit}>
      <input className="text-input" placeholder="名称，如：GPU 服务器 A" value={props.form.name} onChange={(e) => props.onChange({ ...props.form, name: e.target.value })} />
      <input className="text-input" placeholder="主机/IP 地址" value={props.form.host} onChange={(e) => props.onChange({ ...props.form, host: e.target.value })} />
      <input className="text-input" type="number" min="1" max="65535" placeholder="SSH 端口" value={props.form.port} onChange={(e) => props.onChange({ ...props.form, port: Number(e.target.value) })} />
      <input className="text-input" placeholder="SSH 用户名" value={props.form.sshUsername} onChange={(e) => props.onChange({ ...props.form, sshUsername: e.target.value })} />
      <input className="text-input" type="password" placeholder={props.isEdit ? '留空则不修改密码' : 'SSH 密码'} value={props.form.sshPassword} onChange={(e) => props.onChange({ ...props.form, sshPassword: e.target.value })} />
      <button className="primary-button" type="submit" disabled={props.isLoading}>
        <Server size={16} />{props.isEdit ? '保存' : '添加'}
      </button>
    </form>
  );
}

function TaskForm(props: {
  form: TaskFormState;
  servers: EmServer[];
  isLoading: boolean;
  isEdit?: boolean;
  onChange: (form: TaskFormState) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const { form } = props;
  return (
    <form className="em-form" onSubmit={props.onSubmit}>
      <div className="form-group">
        <label>服务器 *</label>
        <select className="text-input" value={form.serverId} onChange={(e) => props.onChange({ ...form, serverId: e.target.value })}>
          <option value="">选择服务器...</option>
          {props.servers.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.host})</option>)}
        </select>
      </div>

      <div className="form-group">
        <label>任务名称 *</label>
        <input className="text-input" placeholder="如：训练任务 python 进程监控" value={form.name} onChange={(e) => props.onChange({ ...form, name: e.target.value })} />
      </div>

      <div className="form-group">
        <label>描述</label>
        <input className="text-input" placeholder="可选描述信息" value={form.description} onChange={(e) => props.onChange({ ...form, description: e.target.value })} />
      </div>

      <fieldset className="em-fieldset">
        <legend>进程匹配规则</legend>
        <div className="form-group">
          <label>匹配模式</label>
          <div className="segmented-control">
            <button type="button" className={form.matchMode === 'simple' ? 'active' : ''} onClick={() => props.onChange({ ...form, matchMode: 'simple' })}>简单匹配（子串）</button>
            <button type="button" className={form.matchMode === 'regex' ? 'active' : ''} onClick={() => props.onChange({ ...form, matchMode: 'regex' })}>正则表达式</button>
          </div>
        </div>
        <div className="form-group">
          <label>匹配字符串 *</label>
          <input className="text-input" placeholder={form.matchMode === 'simple' ? '如：python train.py' : '如：python.*train'} value={form.matchPattern} onChange={(e) => props.onChange({ ...form, matchPattern: e.target.value })} />
          <small className="form-hint">
            {form.matchMode === 'simple' ? '简单模式：在进程命令行中搜索该子串（不区分大小写）' : '正则模式：使用 Python 正则表达式匹配进程命令行'}
          </small>
        </div>
        <div className="form-group">
          <label>筛选用户（可选）</label>
          <input className="text-input" placeholder="留空表示监控所有用户的进程" value={form.filterUser} onChange={(e) => props.onChange({ ...form, filterUser: e.target.value })} />
        </div>
      </fieldset>

      <fieldset className="em-fieldset">
        <legend>报警触发条件</legend>
        <div className="form-group">
          <label>条件类型</label>
          <div className="segmented-control">
            <button type="button" className={form.alertCondition === 'below' ? 'active' : ''} onClick={() => props.onChange({ ...form, alertCondition: 'below' })}>低于阈值</button>
            <button type="button" className={form.alertCondition === 'above' ? 'active' : ''} onClick={() => props.onChange({ ...form, alertCondition: 'above' })}>高于阈值</button>
            <button type="button" className={form.alertCondition === 'changed' ? 'active' : ''} onClick={() => props.onChange({ ...form, alertCondition: 'changed' })}>变动超过</button>
          </div>
        </div>
        {form.alertCondition !== 'changed' ? (
          <div className="form-group">
            <label>阈值（进程数）</label>
            <input className="text-input" type="number" min="0" value={form.alertThreshold} onChange={(e) => props.onChange({ ...form, alertThreshold: Number(e.target.value) })} />
            <small className="form-hint">
              {form.alertCondition === 'below' ? `当匹配进程数低于 ${form.alertThreshold || 0} 时触发报警` : `当匹配进程数高于 ${form.alertThreshold || 0} 时触发报警`}
            </small>
          </div>
        ) : (
          <div className="form-group">
            <label>变动阈值（进程数变化量）</label>
            <input className="text-input" type="number" min="1" value={form.alertChangeAmount} onChange={(e) => props.onChange({ ...form, alertChangeAmount: Number(e.target.value) })} />
            <small className="form-hint">当进程数变动绝对值 &gt;= {form.alertChangeAmount || 1} 时触发报警</small>
          </div>
        )}
        <div className="form-group">
          <label>连续确认次数</label>
          <input className="text-input" type="number" min="1" max="20" value={form.confirmCount} onChange={(e) => props.onChange({ ...form, confirmCount: Number(e.target.value) })} />
          <small className="form-hint">连续 {form.confirmCount || 3} 次检查都满足条件后才真正触发报警，避免误报</small>
        </div>
        <div className="form-group">
          <label>检查间隔（秒）</label>
          <input className="text-input" type="number" min="10" max="3600" value={form.checkIntervalSeconds} onChange={(e) => props.onChange({ ...form, checkIntervalSeconds: Number(e.target.value) })} />
        </div>
      </fieldset>

      <label className="check-row">
        <input type="checkbox" checked={form.enabled} onChange={(e) => props.onChange({ ...form, enabled: e.target.checked })} />
        启用此监控任务
      </label>

      <button className="primary-button" type="submit" disabled={props.isLoading}>
        <Activity size={16} />{props.isEdit ? '保存修改' : '创建任务'}
      </button>
    </form>
  );
}

function EmailActionForm(props: {
  form: EmailActionFormState;
  isLoading: boolean;
  isEdit?: boolean;
  onChange: (form: EmailActionFormState) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <form className="em-form" onSubmit={props.onSubmit}>
      <div className="form-group">
        <label>收件人邮箱（每行一个或逗号分隔）*</label>
        <textarea className="text-input" rows={3} placeholder="user1@example.com&#10;user2@example.com" value={props.form.emailRecipients} onChange={(e) => props.onChange({ ...props.form, emailRecipients: e.target.value })} />
      </div>
      <div className="form-group">
        <label>邮件主题模板</label>
        <input className="text-input" value={props.form.emailSubjectTemplate} onChange={(e) => props.onChange({ ...props.form, emailSubjectTemplate: e.target.value })} />
        <small className="form-hint">可用变量: {'{task_name}'}, {'{server_name}'}, {'{current_count}'}, {'{threshold}'}, {'{time}'}, {'{reason}'}</small>
      </div>
      <div className="form-group">
        <label>邮件正文模板</label>
        <textarea className="text-input" rows={6} value={props.form.emailBodyTemplate} onChange={(e) => props.onChange({ ...props.form, emailBodyTemplate: e.target.value })} />
        <small className="form-hint">可用变量同上</small>
      </div>
      <button className="primary-button" type="submit" disabled={props.isLoading}>
        <Mail size={16} />{props.isEdit ? '保存' : '添加'}
      </button>
    </form>
  );
}

function ScriptActionForm(props: {
  form: ScriptActionFormState;
  isLoading: boolean;
  isEdit?: boolean;
  onChange: (form: ScriptActionFormState) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <form className="em-form" onSubmit={props.onSubmit}>
      <div className="form-group">
        <label>脚本命令（每行一个，按顺序组成队列）*</label>
        <textarea className="text-input monospace" rows={6} placeholder={'cd /data/project && python run_experiment.py --config exp1.json\nbash /data/scripts/backup_results.sh'} value={props.form.scriptCommands} onChange={(e) => props.onChange({ ...props.form, scriptCommands: e.target.value })} />
        <small className="form-hint">每次触发报警时按队列顺序执行脚本。如果服务器安装了 screen，脚本将在 screen 会话中运行。</small>
      </div>
      <div className="form-group">
        <label>Screen 会话名前缀（可选）</label>
        <input className="text-input" placeholder="默认自动生成前缀" value={props.form.scriptScreenName} onChange={(e) => props.onChange({ ...props.form, scriptScreenName: e.target.value })} />
        <small className="form-hint">仅当远程服务器安装了 screen 时生效</small>
      </div>
      <div className="form-group">
        <label>每次触发执行的脚本数量</label>
        <input className="text-input" type="number" min="1" value={props.form.scriptsPerTrigger} onChange={(e) => props.onChange({ ...props.form, scriptsPerTrigger: Number(e.target.value) })} />
        <small className="form-hint">设为 1 表示每次只执行队列中的下一个脚本；设为更大值可一次执行多个</small>
      </div>
      <button className="primary-button" type="submit" disabled={props.isLoading}>
        <Terminal size={16} />{props.isEdit ? '保存' : '添加'}
      </button>
    </form>
  );
}

function EmailConfigForm(props: {
  form: EmailConfigFormState;
  isLoading: boolean;
  onChange: (form: EmailConfigFormState) => void;
  onSubmit: (event: FormEvent) => void;
  onTest: () => void;
}) {
  const canTest = props.form.smtpFromAddress || props.form.smtpUsername;

  return (
    <form className="em-form" onSubmit={props.onSubmit}>
      <div className="form-group">
        <label>SMTP 服务器地址 *</label>
        <input className="text-input" placeholder="smtp.example.com" value={props.form.smtpHost} onChange={(e) => props.onChange({ ...props.form, smtpHost: e.target.value })} />
      </div>
      <div className="form-group">
        <label>端口</label>
        <input className="text-input" type="number" value={props.form.smtpPort} onChange={(e) => props.onChange({ ...props.form, smtpPort: Number(e.target.value) })} />
        <small className="form-hint">SSL 模式（端口 465）</small>
      </div>
      <div className="form-group">
        <label>SMTP 用户名</label>
        <input className="text-input" placeholder="用户名" value={props.form.smtpUsername} onChange={(e) => props.onChange({ ...props.form, smtpUsername: e.target.value })} />
      </div>
      <div className="form-group">
        <label>SMTP 密码</label>
        <input className="text-input" type="password" placeholder="留空则不修改" value={props.form.smtpPassword} onChange={(e) => props.onChange({ ...props.form, smtpPassword: e.target.value })} />
      </div>
      <div className="form-row">
        <div className="form-group">
          <label>发件人地址</label>
          <input className="text-input" placeholder="noreply@example.com" value={props.form.smtpFromAddress} onChange={(e) => props.onChange({ ...props.form, smtpFromAddress: e.target.value })} />
        </div>
        <div className="form-group">
          <label>发件人名称</label>
          <input className="text-input" placeholder="实验监控系统" value={props.form.smtpFromName} onChange={(e) => props.onChange({ ...props.form, smtpFromName: e.target.value })} />
        </div>
      </div>
      <div className="admin-notice">
        <Shield size={14} />
        <span>邮件配置仅对管理员可见和可编辑，所有工具共享同一套邮件配置。测试邮件将发送至填写的发件人地址。</span>
      </div>
      <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
        <button className="primary-button" type="submit" disabled={props.isLoading}>
          <Globe size={16} />保存邮件配置
        </button>
        <button className="chip" type="button" disabled={!canTest || props.isLoading} onClick={() => props.onTest()}>
          <Mail size={15} />发送测试邮件
        </button>
      </div>
    </form>
  );
}

function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal-panel" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="icon-button" type="button" onClick={onClose} title="关闭"><X size={17} /></button>
        </div>
        {children}
      </section>
    </div>
  );
}

// ============================================================
// Utility Functions
// ============================================================

function splitLines(value: string): string[] {
  return value.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
}

function formatTime(isoStr: string): string {
  try {
    return new Date(isoStr).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return isoStr;
  }
}

function escapeHtml(str: string): string {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function getConditionLabel(task: MonitorTask): string {
  switch (task.alertCondition) {
    case 'below': return `进程数 < ${task.alertThreshold}`;
    case 'above': return `进程数 > ${task.alertThreshold}`;
    case 'changed': return `变动 >= ${task.alertChangeAmount}`;
    default: return '';
  }
}
