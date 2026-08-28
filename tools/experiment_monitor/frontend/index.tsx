import './style.css';
import { FormEvent, useEffect, useRef, useState } from 'react';
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
  GripVertical,
  Mail,
  Monitor,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Server,
  Settings,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';

import { ApiError, apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';
import { ScatterChart } from '../../../frontend/src/components/ScatterChart';

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
  repeatIntervalSeconds: number;
  maxRepeatCount: number;
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

type QueueItem = {
  id: string;
  command: string;
  position: number;
};

type HistoryItem = {
  id: string;
  command: string;
  triggeredAt: string;
  screenSession: string | null;
};

type ScreenSession = {
  id: string;
  sessionName: string;
  command: string;
  status: 'running' | 'done' | 'unknown';
  startedAt: string;
  checkedAt: string | null;
  historyId: string | null;
};

type ScriptGroup = {
  id: string;
  actionId: string;
  name: string;
  screenNamePrefix: string;
  sortOrder: number;
  createdAt: string;
  queue: QueueItem[];
  history: HistoryItem[];
  sessions: ScreenSession[];
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
  repeatCount: number;
  repeatExhausted: boolean;
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

type SshTestResult = {
  connected: boolean;
  username?: string;
  hasScreen?: boolean;
  error?: string;
};

type TaskDetailState = {
  samples: Sample[];
  events: AlertEvent[];
  alertState: AlertState | null;
};

// ============================================================
// Form States
// ============================================================

type ServerFormState = {
  serverId: string;
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
  repeatIntervalSeconds: number;
  maxRepeatCount: number;
  enabled: boolean;
};

type EmailActionFormState = {
  emailRecipients: string;
  emailSubjectTemplate: string;
  emailBodyTemplate: string;
};


// ============================================================
// Constants
// ============================================================

const emptyServer: ServerFormState = {
  serverId: '',
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
  repeatIntervalSeconds: 0,
  maxRepeatCount: 0,
  enabled: true,
};

const emptyEmailAction: EmailActionFormState = {
  emailRecipients: '',
  emailSubjectTemplate: '实验监控报警: {task_name}',
  emailBodyTemplate: `实验监控报警通知

监控任务: {task_name}
服务器: {server_name}
触发原因: {reason}
触发时间: {time}

阈值条件: {threshold}
报警前进程数: {prev_count}
当前进程数: {current_count}

── 报警前的进程列表（基准）──
{prev_processes}

── 当前采样到的进程列表 ──
{current_processes}

此邮件由实验监控系统自动发送。`,
};

const emptyTaskDetail: TaskDetailState = {
  samples: [],
  events: [],
  alertState: null,
};


// ============================================================
// Main Component
// ============================================================

export default function ExperimentMonitorTool() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [servers, setServers] = useState<EmServer[]>([]);
  const [tasks, setTasks] = useState<MonitorTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>('');
  const [taskDetailsById, setTaskDetailsById] = useState<Record<string, TaskDetailState>>({});
  const [actionsByTaskId, setActionsByTaskId] = useState<Record<string, AlertAction[]>>({});

  // Log filter state
  const [logFilter, setLogFilter] = useState<'all' | LogEntry['type']>('all');

  // Modal states
  const [modal, setModal] = useState<'server-list' | 'server-create' | 'server-edit' | 'task-create' | 'task-edit'
    | 'action-email'
    | 'script-groups' | 'script-group-create' | null>(null);
  const [editingServerId, setEditingServerId] = useState<string>('');
  const [editingTaskId, setEditingTaskId] = useState<string>('');
  const [editingActionId, setEditingActionId] = useState<string>('');
  const [actionModalTaskId, setActionModalTaskId] = useState<string>('');
  // currently open action for script groups panel
  const [groupsActionId, setGroupsActionId] = useState<string>('');
  // currently open single group id for the single-group config panel
  const [singleGroupId, setSingleGroupId] = useState<string>('');

  // Form states
  const [serverForm, setServerForm] = useState<ServerFormState>(emptyServer);
  const [taskForm, setTaskForm] = useState<TaskFormState>(emptyTask);
  const [emailActionForm, setEmailActionForm] = useState<EmailActionFormState>(emptyEmailAction);

  // Script groups state (keyed by action id)
  const [scriptGroupsMap, setScriptGroupsMap] = useState<Record<string, ScriptGroup[]>>({});

  // UI state
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [expandedTasks, setExpandedTasks] = useState<Set<string>>(new Set());
  const [sshTestResult, setSshTestResult] = useState<SshTestResult | null>(null);

  const isAdmin = me?.role === 'admin';
  const selectedTask = tasks.find((t) => t.id === selectedTaskId) || null;
  const selectedTaskDetail = selectedTaskId ? (taskDetailsById[selectedTaskId] || emptyTaskDetail) : emptyTaskDetail;

  useEffect(() => {
    fetchMe().then((state) => setMe(state.user)).catch(() => setMe(null));
    void loadServers();
    void loadTasks();
  }, []);

  useEffect(() => {
    const taskIds = new Set(tasks.map((task) => task.id));
    setSelectedTaskId((prev) => {
      if (tasks.length === 0) return '';
      return prev && taskIds.has(prev) ? prev : tasks[0].id;
    });
    setExpandedTasks((prev) => new Set([...prev].filter((taskId) => taskIds.has(taskId))));
    setTaskDetailsById((prev) => pickRecord(prev, taskIds));
    setActionsByTaskId((prev) => pickRecord(prev, taskIds));
  }, [tasks]);

  useEffect(() => {
    if (!selectedTaskId) return;
    void loadTaskData(selectedTaskId);
  }, [selectedTaskId]);

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
    } catch (err) {
      handleError(err);
    }
  }

  async function loadTaskData(taskId: string) {
    await Promise.all([loadTaskDetail(taskId), loadActions(taskId)]);
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
      setTaskDetailsById((prev) => ({ ...prev, [taskId]: payload }));
    } catch (err) {
      handleError(err);
    }
  }

  async function loadAlertState(taskId: string) {
    try {
      const payload = await apiGet<{ alertState: AlertState | null }>(
        `/api/tools/experiment-monitor/tasks/${taskId}/alert-state`,
      );
      setTaskDetailsById((prev) => ({
        ...prev,
        [taskId]: {
          ...(prev[taskId] || emptyTaskDetail),
          alertState: payload.alertState,
        },
      }));
    } catch (err) {
      handleError(err);
    }
  }

  async function loadActions(taskId: string) {
    try {
      const payload = await apiGet<{ actions: AlertAction[] }>(
        `/api/tools/experiment-monitor/tasks/${taskId}/actions`,
      );
      setActionsByTaskId((prev) => ({ ...prev, [taskId]: payload.actions }));
      // Also preload groups for script actions
      for (const action of payload.actions) {
        if (action.actionType === 'script') {
          void loadScriptGroups(action.id);
        }
      }
    } catch (err) {
      handleError(err);
    }
  }

  async function loadScriptGroups(actionId: string) {
    try {
      const payload = await apiGet<{ groups: ScriptGroup[] }>(
        `/api/tools/experiment-monitor/actions/${actionId}/groups`,
      );
      setScriptGroupsMap((prev) => ({ ...prev, [actionId]: payload.groups }));
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
      serverId: server.id,
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
      const payload = { serverId: serverForm.serverId };
      if (modal === 'server-edit' && editingServerId) {
        await apiPut(`/api/tools/experiment-monitor/servers/${editingServerId}`, payload);
      } else {
        await apiPost('/api/tools/experiment-monitor/servers', payload);
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
    setEditingTaskId('');
    setModal('task-create');
  }

  function openEditTask(task: MonitorTask) {
    setSelectedTaskId(task.id);
    setEditingTaskId(task.id);
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
      repeatIntervalSeconds: task.repeatIntervalSeconds,
      maxRepeatCount: task.maxRepeatCount,
      enabled: task.enabled,
    });
    setModal('task-edit');
  }

  async function saveTask(event: FormEvent) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    try {
      let savedTaskId = editingTaskId;
      if (modal === 'task-edit' && editingTaskId) {
        const response = await apiPut<{ task: MonitorTask }>(
          `/api/tools/experiment-monitor/tasks/${editingTaskId}`,
          taskForm,
        );
        savedTaskId = response.task.id;
      } else {
        const response = await apiPost<{ task: MonitorTask }>('/api/tools/experiment-monitor/tasks', taskForm);
        savedTaskId = response.task.id;
      }
      setModal(null);
      setEditingTaskId('');
      showSuccess('监控任务保存成功');
      await loadTasks();
      if (savedTaskId) {
        setSelectedTaskId(savedTaskId);
        void loadTaskData(savedTaskId);
      }
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
      setExpandedTasks((prev) => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
      setTaskDetailsById((prev) => omitRecordKey(prev, taskId));
      setActionsByTaskId((prev) => omitRecordKey(prev, taskId));
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

  function openCreateEmailAction(taskId: string) {
    if (!tasks.some((task) => task.id === taskId)) return;
    setSelectedTaskId(taskId);
    setEmailActionForm({ ...emptyEmailAction });
    setEditingActionId('');
    setActionModalTaskId(taskId);
    setModal('action-email');
  }

  /**
   * 点击「脚本」按钮时调用：
   * - 若当前 task 下还没有 script action，先自动创建一个
   * - 然后弹出「新建分组」表单（只填分组名称即可快速创建一个分组）
   */
  async function openCreateGroupModal(taskId: string) {
    const task = tasks.find((item) => item.id === taskId);
    if (!task) return;
    setSelectedTaskId(taskId);
    let scriptAction = (actionsByTaskId[taskId] || []).find((a) => a.actionType === 'script');
    if (!scriptAction) {
      try {
        const res = await apiPost<{ action: AlertAction }>(
          `/api/tools/experiment-monitor/tasks/${task.id}/actions`,
          { actionType: 'script' as ActionType, scriptCommands: [], scriptScreenName: '', scriptsPerTrigger: 1 },
        );
        scriptAction = res.action;
        await loadActions(task.id);
      } catch (err) {
        handleError(err);
        return;
      }
    }
    setGroupsActionId(scriptAction.id);
    setModal('script-group-create');
  }

  /**
   * 打开某个分组的专属配置面板（单分组模式）
   */
  function openSingleGroupPanel(groupId: string, actionId: string) {
    setSingleGroupId(groupId);
    setGroupsActionId(actionId);
    setModal('script-groups');
    void loadScriptGroups(actionId);
  }

  function editAction(action: AlertAction, taskId: string) {
    if (action.actionType === 'email') {
      setSelectedTaskId(taskId);
      setEditingActionId(action.id);
      setActionModalTaskId(taskId);
      setEmailActionForm({
        emailRecipients: action.emailRecipients.join(', '),
        emailSubjectTemplate: action.emailSubjectTemplate,
        emailBodyTemplate: action.emailBodyTemplate,
      });
      setModal('action-email');
    } else {
      // 对于脚本类型，直接打开分组管理面板
      openScriptGroups(action.id);
    }
  }

  async function saveEmailAction(event: FormEvent) {
    event.preventDefault();
    const targetTaskId = actionModalTaskId || selectedTaskId;
    if (!targetTaskId) return;
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
        await apiPost(`/api/tools/experiment-monitor/tasks/${targetTaskId}/actions`, payload);
      }
      setModal(null);
      setEditingActionId('');
      setActionModalTaskId('');
      showSuccess('邮件动作保存成功');
      await loadActions(targetTaskId);
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function removeAction(actionId: string, taskId: string) {
    if (!window.confirm('确认删除该报警动作？')) return;
    try {
      await apiDelete(`/api/tools/experiment-monitor/actions/${actionId}`);
      showSuccess('动作已删除');
      setScriptGroupsMap((prev) => omitRecordKey(prev, actionId));
      await loadActions(taskId);
    } catch (err) {
      handleError(err);
    }
  }

  function openScriptGroups(actionId: string) {
    setGroupsActionId(actionId);
    setModal('script-groups');
    void loadScriptGroups(actionId);
  }

  // ============================================================
  // Script Group Management
  // ============================================================

  async function createGroup(actionId: string, name: string, screenNamePrefix: string) {
    try {
      await apiPost<{ group: ScriptGroup }>(
        `/api/tools/experiment-monitor/actions/${actionId}/groups`,
        { name, screenNamePrefix },
      );
      await loadScriptGroups(actionId);
      showSuccess('脚本分组已创建');
    } catch (err) {
      handleError(err);
    }
  }

  async function deleteGroup(groupId: string, actionId: string) {
    if (!window.confirm('确认删除该脚本分组及其所有队列和历史？')) return;
    try {
      await apiDelete(`/api/tools/experiment-monitor/groups/${groupId}`);
      await loadScriptGroups(actionId);
      showSuccess('分组已删除');
    } catch (err) {
      handleError(err);
    }
  }

  async function addQueueItem(groupId: string, command: string, actionId: string) {
    try {
      await apiPost(`/api/tools/experiment-monitor/groups/${groupId}/queue`, { command });
      await loadScriptGroups(actionId);
    } catch (err) {
      handleError(err);
    }
  }

  async function deleteQueueItem(itemId: string, actionId: string) {
    try {
      await apiDelete(`/api/tools/experiment-monitor/queue/${itemId}`);
      await loadScriptGroups(actionId);
    } catch (err) {
      handleError(err);
    }
  }

  async function reorderQueue(groupId: string, orderedIds: string[], actionId: string) {
    try {
      await apiPost(`/api/tools/experiment-monitor/groups/${groupId}/queue/reorder`, { orderedIds });
      await loadScriptGroups(actionId);
    } catch (err) {
      handleError(err);
    }
  }

  async function restoreHistoryItem(historyId: string, actionId: string) {
    try {
      await apiPost(`/api/tools/experiment-monitor/history/${historyId}/restore`, {});
      await loadScriptGroups(actionId);
      showSuccess('已添加回队列');
    } catch (err) {
      handleError(err);
    }
  }

  async function deleteHistoryItem(historyId: string, actionId: string) {
    try {
      await apiDelete(`/api/tools/experiment-monitor/history/${historyId}`);
      await loadScriptGroups(actionId);
    } catch (err) {
      handleError(err);
    }
  }

  async function refreshSessions(groupId: string, actionId: string) {
    try {
      await apiPost(`/api/tools/experiment-monitor/groups/${groupId}/sessions/refresh`, {});
      await loadScriptGroups(actionId);
      showSuccess('Session 状态已刷新');
    } catch (err) {
      handleError(err);
    }
  }

  // ============================================================
  // UI Helpers
  // ============================================================

  function selectTask(taskId: string, options?: { toggleExpanded?: boolean }) {
    setSelectedTaskId(taskId);
    if (options?.toggleExpanded) {
      toggleExpand(taskId);
    } else {
      void loadTaskData(taskId);
    }
  }

  function toggleExpand(taskId: string) {
    const willExpand = !expandedTasks.has(taskId);
    setExpandedTasks((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) next.delete(taskId); else next.add(taskId);
      return next;
    });
    if (willExpand) {
      void loadTaskData(taskId);
    }
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
              <button className="chip" type="button" onClick={() => setModal('server-list')}>
                <Server size={15} />服务器
              </button>
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
          tasks.map((task) => {
            const detail = taskDetailsById[task.id] || emptyTaskDetail;
            return (
              <TaskCard
                key={task.id}
                task={task}
                server={servers.find((s) => s.id === task.serverId)}
                isExpanded={expandedTasks.has(task.id)}
                isSelected={selectedTaskId === task.id}
                actions={actionsByTaskId[task.id] || []}
                alertState={detail.alertState}
                latestSample={detail.samples[detail.samples.length - 1] || null}
                scriptGroupsMap={scriptGroupsMap}
                onSelect={() => selectTask(task.id, { toggleExpanded: true })}
                onToggleExpand={() => selectTask(task.id, { toggleExpanded: true })}
                onEdit={() => openEditTask(task)}
                onDelete={() => removeTask(task.id)}
                onCheckNow={() => void runCheckNow(task.id)}
                onResetAlert={() => void resetAlert(task.id)}
                onAddEmailAction={() => openCreateEmailAction(task.id)}
                onAddScriptAction={() => void openCreateGroupModal(task.id)}
                onOpenGroupPanel={(groupId, actionId) => openSingleGroupPanel(groupId, actionId)}
                onEditAction={(action) => editAction(action, task.id)}
                onDeleteAction={(actionId) => void removeAction(actionId, task.id)}
                onDeleteGroup={(groupId, actionId) => void deleteGroup(groupId, actionId)}
              />
            );
          })
        )}
      </section>

      {/* History Panel (when a task is selected and expanded) */}
      {selectedTask && expandedTasks.has(selectedTaskId) && (
        <section className="em-detail-panel">
          <div className="result-header">
            <span><Clock size={15} />监控日志</span>
            <span className="muted">{selectedTaskDetail.samples.length + selectedTaskDetail.events.length} 条记录</span>
          </div>

          {/* Alert State */}
{selectedTaskDetail.alertState?.isAlerting && (
<div className="em-alert-state alerting">
<span className="em-alert-state-info">
<span className="badge danger">报警中</span>
{selectedTaskDetail.alertState.repeatCount > 0 && (
<span className="badge">已报警 {selectedTaskDetail.alertState.repeatCount} 次{selectedTask && selectedTask.maxRepeatCount > 0 ? ` / ${selectedTask.maxRepeatCount}` : ''}</span>
)}
{selectedTaskDetail.alertState.repeatExhausted && <span className="badge warning">已达重复上限</span>}
</span>
<button className="chip small" type="button" onClick={() => void resetAlert(selectedTaskId)}>
<RotateCcw size={13} />重置报警
</button>
</div>
)}

          {/* Process Count Chart */}
          <MiniChart samples={selectedTaskDetail.samples} />

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
            samples={selectedTaskDetail.samples}
            events={selectedTaskDetail.events}
            filter={logFilter}
          />
        </section>
      )}

      {!me && <LoginPanel onSuccess={() => window.location.reload()} />}

      {/* Modals */}
      {modal === 'server-list' && (
        <Modal title="服务器管理" onClose={() => setModal(null)}>
          <ServerListPanel
            servers={servers}
            onAdd={() => { openCreateServer(); }}
            onEdit={(id) => { openEditServer(id); }}
            onDelete={(id) => { void removeServer(id); }}
          />
        </Modal>
      )}

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


      {modal === 'script-groups' && groupsActionId && singleGroupId && (() => {
        const group = (scriptGroupsMap[groupsActionId] || []).find((g) => g.id === singleGroupId);
        return group ? (
          <Modal title={`脚本分组 · ${group.name}`} onClose={() => setModal(null)}>
            <ScriptGroupsPanel
              actionId={groupsActionId}
              groups={[group]}
              singleMode
              onDeleteGroup={(groupId) => void deleteGroup(groupId, groupsActionId)}
              onAddQueueItem={(groupId, cmd) => void addQueueItem(groupId, cmd, groupsActionId)}
              onDeleteQueueItem={(itemId) => void deleteQueueItem(itemId, groupsActionId)}
              onReorderQueue={(groupId, ids) => void reorderQueue(groupId, ids, groupsActionId)}
              onRestoreHistory={(histId) => void restoreHistoryItem(histId, groupsActionId)}
              onDeleteHistory={(histId) => void deleteHistoryItem(histId, groupsActionId)}
              onRefreshSessions={(groupId) => void refreshSessions(groupId, groupsActionId)}
            />
          </Modal>
        ) : null;
      })()}

      {modal === 'script-group-create' && groupsActionId && (
        <Modal title="新建脚本分组" onClose={() => setModal(null)}>
          <CreateGroupForm
            onSubmit={async (name, prefix) => {
              await createGroup(groupsActionId, name, prefix);
              setModal(null);
            }}
            onCancel={() => setModal(null)}
          />
        </Modal>
      )}
    </div>
  );
}

function pickRecord<T>(record: Record<string, T>, keys: Set<string>): Record<string, T> {
  const next: Record<string, T> = {};
  for (const key of keys) {
    if (record[key] !== undefined) {
      next[key] = record[key];
    }
  }
  return next;
}

function omitRecordKey<T>(record: Record<string, T>, keyToOmit: string): Record<string, T> {
  const next = { ...record };
  delete next[keyToOmit];
  return next;
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
  scriptGroupsMap: Record<string, ScriptGroup[]>;
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
  onDeleteGroup: (groupId: string, actionId: string) => void;
  onOpenGroupPanel: (groupId: string, actionId: string) => void;
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
            {task.maxRepeatCount > 0 && alertState && (
              <span className={alertState.repeatExhausted ? 'text-warning' : ''}>
                重复报警: <strong>{alertState.repeatCount}</strong>/{task.maxRepeatCount}
                {alertState.repeatExhausted && ' · 已达上限'}
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
{(alertState?.isAlerting || alertState?.repeatExhausted || (alertState && alertState.repeatCount > 0)) && (
<div className="em-alert-state inline alerting">
<span className="em-alert-state-info">
{alertState?.isAlerting && <span className="badge danger">报警中</span>}
{alertState && alertState.repeatCount > 0 && (
<span className="badge">已报警 {alertState.repeatCount} 次{props.task.maxRepeatCount > 0 ? ` / ${props.task.maxRepeatCount}` : ''}</span>
)}
{alertState?.repeatExhausted && <span className="badge warning">已达重复上限</span>}
</span>
<button className="chip tiny" type="button" onClick={(e) => { e.stopPropagation(); props.onResetAlert(); }}>
<RotateCcw size={12} />重置报警
</button>
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
              props.actions.flatMap((action) => {
                if (action.actionType === 'email') {
                  return [
                    <div className="em-action-item" key={action.id}>
                      <span className="action-icon"><Mail size={14} /></span>
                      <div className="action-info">
                        <strong>邮件通知</strong>
                        <small className="muted">收件人: {action.emailRecipients.join(', ') || '未设置'}</small>
                      </div>
                      <button className="icon-button tiny" type="button" onClick={(e) => { e.stopPropagation(); props.onEditAction(action); }} title="编辑"><Settings size={13} /></button>
                      <button className="icon-button tiny danger" type="button" onClick={(e) => { e.stopPropagation(); props.onDeleteAction(action.id); }} title="删除"><Trash2 size={13} /></button>
                    </div>,
                  ];
                }
                // script action: 展开显示每个分组
                const groups = props.scriptGroupsMap?.[action.id] || [];
                if (groups.length === 0) {
                  return [
                    <div className="em-action-item" key={`${action.id}-empty`}>
                      <span className="action-icon"><Terminal size={14} /></span>
                      <div className="action-info">
                        <strong>脚本触发</strong>
                        <small className="muted">暂无分组，点击「脚本」添加分组</small>
                      </div>
                      <button className="icon-button tiny danger" type="button" onClick={(e) => { e.stopPropagation(); props.onDeleteAction(action.id); }} title="删除脚本动作"><Trash2 size={13} /></button>
                    </div>,
                  ];
                }
                return groups.map((group) => (
                  <div className="em-action-item" key={`${action.id}-${group.id}`}>
                    <span className="action-icon"><Terminal size={14} /></span>
                    <div className="action-info">
                      <strong>{group.name}</strong>
                      <small className="muted">
                        {group.queue.length > 0 ? `队列 ${group.queue.length} 条` : '队列为空'}
                        {group.sessions.filter((s) => s.status === 'running').length > 0 &&
                          ` · ${group.sessions.filter((s) => s.status === 'running').length} 运行中`}
                      </small>
                    </div>
                    <button className="icon-button tiny" type="button" onClick={(e) => { e.stopPropagation(); props.onOpenGroupPanel(group.id, action.id); }} title="配置该分组"><Settings size={13} /></button>
                    <button className="icon-button tiny danger" type="button" onClick={(e) => { e.stopPropagation(); if (window.confirm(`确认删除分组「${group.name}」及其所有队列和历史？`)) { props.onDeleteGroup(group.id, action.id); } }} title="删除分组"><Trash2 size={13} /></button>
                  </div>
                ));
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function MiniChart({ samples }: { samples: Sample[] }) {
  if (samples.length < 2) return null;
  const chartPoints = samples.map((s) => ({
    x: s.checkedAt,
    y: s.processCount,
    label: `进程数: ${s.processCount}`,
  }));
  return (
    <ScatterChart
      title="进程数趋势"
      subLabel={`最近 ${samples.length} 次采样`}
      points={chartPoints}
      yLabel="进程数"
      timeAxis
      formatY={(v) => String(Math.round(v))}
      formatTooltipY={(v) => `${Math.round(v)} 个进程`}
    />
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

  // 限制渲染条目数量：24h × 30s 间隔可达数千条采样，全部渲染会严重卡顿 DOM。
  // entries 已按时间倒序排列，截取最近 MAX_LOG_ENTRIES 条即可。
  const MAX_LOG_ENTRIES = 200;
  const truncated = filtered.length > MAX_LOG_ENTRIES;
  const visibleEntries = truncated ? filtered.slice(0, MAX_LOG_ENTRIES) : filtered;

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
      {truncated && (
        <div className="em-log-truncated">仅显示最近 {MAX_LOG_ENTRIES} 条记录（共 {filtered.length} 条）</div>
      )}
      {visibleEntries.map((entry) => (
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
  const [globalServers, setGlobalServers] = useState<EmServer[]>([]);
  useEffect(() => { void apiGet<{ servers: EmServer[] }>('/api/settings/ssh-servers').then((result) => setGlobalServers(result.servers)).catch(() => setGlobalServers([])); }, []);
  return (
    <form className="em-form" onSubmit={props.onSubmit}>
      <div className="form-group">
        <label>选择服务器 *</label>
        <select className="text-input" value={props.form.serverId} disabled={props.isEdit} onChange={(e) => {
          if (e.target.value === '__add_server__') { window.location.assign('/settings'); return; }
          props.onChange({ serverId: e.target.value });
        }}>
          <option value="">请选择服务器...</option>
          {globalServers.map((server) => <option key={server.id} value={server.id}>{server.name}（{server.sshUsername}@{server.host}:{server.port}）</option>)}
          {!props.isEdit && <option value="__add_server__">＋ 添加服务器…</option>}
        </select>
      </div>

      {props.testResult && (
        <div className={`em-test-result ${props.testResult.connected ? 'success' : 'error'}`}>
          {props.testResult.connected ? (
            <span>✓ 连接成功，用户：{props.testResult.username}{props.testResult.hasScreen ? '，已安装 screen' : ''}</span>
          ) : (
            <span>✗ 连接失败：{props.testResult.error}</span>
          )}
        </div>
      )}

      <div className="em-form-footer">
        <button className="primary-button" type="submit" disabled={props.isLoading}>
          <Server size={16} />{props.isEdit ? '保存服务器' : '添加服务器'}
        </button>
        {props.isEdit && (
          <button className="chip" type="button" disabled={props.isLoading} onClick={() => props.onTest()}>
            <Activity size={15} />测试连接
          </button>
        )}
      </div>
    </form>
  );
}

type FilterPreviewResult = {
  totalCount: number;
  matchedCount: number;
  matchedProcesses: string[];
};

function TaskForm(props: {
  form: TaskFormState;
  servers: EmServer[];
  isLoading: boolean;
  isEdit?: boolean;
  onChange: (form: TaskFormState) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const { form } = props;
  const [previewResult, setPreviewResult] = useState<FilterPreviewResult | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [isPreviewing, setIsPreviewing] = useState(false);

  async function runPreview() {
    if (!form.serverId) {
      setPreviewError('请先选择服务器');
      return;
    }
    if (!form.matchPattern.trim()) {
      setPreviewError('请先填写匹配字符串');
      return;
    }
    setIsPreviewing(true);
    setPreviewError(null);
    setPreviewResult(null);
    try {
      const result = await apiPost<FilterPreviewResult>(
        '/api/tools/experiment-monitor/preview-filter',
        {
          serverId: form.serverId,
          matchMode: form.matchMode,
          matchPattern: form.matchPattern,
          filterUser: form.filterUser,
        },
      );
      setPreviewResult(result);
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : '预览失败');
    } finally {
      setIsPreviewing(false);
    }
  }

  // Clear preview when filter fields change
  function handleFilterChange(updated: TaskFormState) {
    props.onChange(updated);
    setPreviewResult(null);
    setPreviewError(null);
  }

  return (
    <form className="em-form" onSubmit={props.onSubmit}>
      <div className="form-group">
        <label>服务器 *</label>
        <select className="text-input" value={form.serverId} onChange={(e) => handleFilterChange({ ...form, serverId: e.target.value })}>
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
            <button type="button" className={form.matchMode === 'simple' ? 'active' : ''} onClick={() => handleFilterChange({ ...form, matchMode: 'simple' })}>简单匹配（子串）</button>
            <button type="button" className={form.matchMode === 'regex' ? 'active' : ''} onClick={() => handleFilterChange({ ...form, matchMode: 'regex' })}>正则表达式</button>
          </div>
        </div>
        <div className="form-group">
          <label>匹配字符串 *</label>
          <input className="text-input" placeholder={form.matchMode === 'simple' ? '如：python train.py' : '如：python.*train'} value={form.matchPattern} onChange={(e) => handleFilterChange({ ...form, matchPattern: e.target.value })} />
          <small className="form-hint">
            {form.matchMode === 'simple' ? '简单模式：在进程命令行中搜索该子串（不区分大小写）' : '正则模式：使用 Python 正则表达式匹配进程命令行'}
          </small>
        </div>
        <div className="form-group">
          <label>筛选用户（可选）</label>
          <input className="text-input" placeholder="留空表示监控所有用户的进程" value={form.filterUser} onChange={(e) => handleFilterChange({ ...form, filterUser: e.target.value })} />
        </div>

        {/* Filter Preview */}
        <div className="em-preview-section">
          <div className="em-preview-header">
            <span className="em-preview-title"><Monitor size={13} />筛选预览</span>
            <button
              type="button"
              className="chip"
              onClick={runPreview}
              disabled={isPreviewing || !form.serverId || !form.matchPattern.trim()}
            >
              {isPreviewing ? <RefreshCw size={13} className="spin" /> : <Play size={13} />}
              {isPreviewing ? '查询中…' : '预览匹配结果'}
            </button>
          </div>
          {previewError && (
            <div className="em-preview-error">{previewError}</div>
          )}
          {previewResult && (
            <div className="em-preview-result">
              <div className="em-preview-summary">
                <span>共 <strong>{previewResult.totalCount}</strong> 个进程</span>
                <span className={previewResult.matchedCount > 0 ? 'em-preview-matched' : 'em-preview-unmatched'}>
                  匹配到 <strong>{previewResult.matchedCount}</strong> 个
                </span>
              </div>
              {previewResult.matchedCount === 0 ? (
                <p className="em-preview-empty muted">当前筛选条件没有匹配到任何进程</p>
              ) : (
                <div className="em-preview-list">
                  {previewResult.matchedProcesses.map((proc, idx) => (
                    <code key={idx} className="em-preview-proc">{proc}</code>
                  ))}
                </div>
              )}
            </div>
          )}
          {!previewResult && !previewError && !isPreviewing && (
            <p className="em-preview-hint muted">点击「预览匹配结果」可实时查询服务器上满足当前筛选条件的进程列表</p>
          )}
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

      <fieldset className="em-fieldset">
        <legend>重复报警控制</legend>
        <div className="form-group">
          <label>重复报警冷却时间（秒）</label>
          <input className="text-input" type="number" min="0" value={form.repeatIntervalSeconds} onChange={(e) => props.onChange({ ...form, repeatIntervalSeconds: Number(e.target.value) })} />
          <small className="form-hint">
            {form.repeatIntervalSeconds > 0
              ? `报警后 ${form.repeatIntervalSeconds} 秒内不重复报警（冷却期内条件仍满足时会在冷却结束后立即再次触发）`
              : '设为 0 表示不限制冷却，每次确认达标都会触发'}
          </small>
        </div>
        <div className="form-group">
          <label>最多重复报警次数</label>
          <input className="text-input" type="number" min="0" value={form.maxRepeatCount} onChange={(e) => props.onChange({ ...form, maxRepeatCount: Number(e.target.value) })} />
          <small className="form-hint">
            {form.maxRepeatCount > 0
              ? `达到 ${form.maxRepeatCount} 次后停止重复报警，需在任务卡片中手动「重置」后才能继续`
              : '设为 0 表示不限制重复次数（持续按冷却时间重复）'}
          </small>
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
      <div className="admin-notice" style={{ marginBottom: '12px' }}>
        <Mail size={14} />
        <span>邮件发送（SMTP）配置由管理员在「<a href="/settings" style={{ color: 'inherit', textDecoration: 'underline' }}>平台设置</a>」中统一管理，此处仅配置本通知动作的收件人和邮件内容。</span>
      </div>
      <div className="form-group">
        <label>收件人邮箱（每行一个或逗号分隔）*</label>
        <textarea className="text-input" rows={3} placeholder="user1@example.com&#10;user2@example.com" value={props.form.emailRecipients} onChange={(e) => props.onChange({ ...props.form, emailRecipients: e.target.value })} />
      </div>
      <div className="em-template-vars-box">
        <div className="em-template-vars-title">可用模板变量</div>
        <div className="em-template-vars-grid">
          <div className="em-template-var-item">
            <code>{'{task_name}'}</code>
            <span>监控任务名称</span>
          </div>
          <div className="em-template-var-item">
            <code>{'{server_name}'}</code>
            <span>服务器名称</span>
          </div>
          <div className="em-template-var-item">
            <code>{'{current_count}'}</code>
            <span>报警时的进程数</span>
          </div>
          <div className="em-template-var-item">
            <code>{'{prev_count}'}</code>
            <span>报警前的进程数（基准）</span>
          </div>
          <div className="em-template-var-item">
            <code>{'{threshold}'}</code>
            <span>阈值条件描述</span>
          </div>
          <div className="em-template-var-item">
            <code>{'{reason}'}</code>
            <span>触发原因说明</span>
          </div>
          <div className="em-template-var-item">
            <code>{'{time}'}</code>
            <span>触发时间（UTC）</span>
          </div>
          <div className="em-template-var-item em-template-var-highlight">
            <code>{'{current_processes}'}</code>
            <span>报警时采样到的进程列表</span>
          </div>
          <div className="em-template-var-item em-template-var-highlight">
            <code>{'{prev_processes}'}</code>
            <span>报警触发前的进程列表（基准）</span>
          </div>
        </div>
      </div>
      <div className="form-group">
        <label>邮件主题模板</label>
        <input className="text-input" value={props.form.emailSubjectTemplate} onChange={(e) => props.onChange({ ...props.form, emailSubjectTemplate: e.target.value })} />
      </div>
      <div className="form-group">
        <label>邮件正文模板</label>
        <textarea className="text-input" rows={8} value={props.form.emailBodyTemplate} onChange={(e) => props.onChange({ ...props.form, emailBodyTemplate: e.target.value })} />
      </div>
      <button className="primary-button" type="submit" disabled={props.isLoading}>
        <Mail size={16} />{props.isEdit ? '保存' : '添加'}
      </button>
    </form>
  );
}


// ============================================================
// Create Group Form (standalone modal form)
// ============================================================

function CreateGroupForm(props: {
  onSubmit: (name: string, prefix: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [prefix, setPrefix] = useState('');

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    props.onSubmit(name.trim(), prefix.trim());
  }

  return (
    <form className="em-form" onSubmit={handleSubmit}>
      <div className="form-group">
        <label>分组名称 *</label>
        <input
          className="text-input"
          placeholder="如：实验 A、训练任务 1"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
        <small className="form-hint">每次报警触发时，该分组队列头部的命令将自动执行</small>
      </div>
      <div className="form-group">
        <label>Screen 会话名前缀（可选）</label>
        <input
          className="text-input"
          placeholder="留空则自动生成"
          value={prefix}
          onChange={(e) => setPrefix(e.target.value)}
        />
        <small className="form-hint">仅当远程服务器安装了 screen 时生效</small>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="primary-button" type="submit" disabled={!name.trim()}>
          <Plus size={16} />创建分组
        </button>
        <button className="chip" type="button" onClick={props.onCancel}>
          <X size={14} />取消
        </button>
      </div>
    </form>
  );
}

// ============================================================
// Script Groups Panel
// ============================================================

function ScriptGroupsPanel(props: {
  actionId: string;
  groups: ScriptGroup[];
  singleMode?: boolean;
  onCreateGroup?: (name: string, screenNamePrefix: string) => void;
  onDeleteGroup: (groupId: string) => void;
  onAddQueueItem: (groupId: string, command: string) => void;
  onDeleteQueueItem: (itemId: string) => void;
  onReorderQueue: (groupId: string, orderedIds: string[]) => void;
  onRestoreHistory: (histId: string) => void;
  onDeleteHistory: (histId: string) => void;
  onRefreshSessions: (groupId: string) => void;
}) {
  const [newGroupName, setNewGroupName] = useState('');
  const [newGroupPrefix, setNewGroupPrefix] = useState('');
  const [showCreateGroup, setShowCreateGroup] = useState(false);
  const [activeGroupTab, setActiveGroupTab] = useState<Record<string, 'queue' | 'history' | 'sessions'>>({});
  const [newCommands, setNewCommands] = useState<Record<string, string>>({});

  function getGroupTab(groupId: string): 'queue' | 'history' | 'sessions' {
    return activeGroupTab[groupId] || 'queue';
  }

  function setGroupTab(groupId: string, tab: 'queue' | 'history' | 'sessions') {
    setActiveGroupTab((prev) => ({ ...prev, [groupId]: tab }));
  }

  function handleCreateGroup(e: FormEvent) {
    e.preventDefault();
    if (!newGroupName.trim()) return;
    props.onCreateGroup?.(newGroupName.trim(), newGroupPrefix.trim());
    setNewGroupName('');
    setNewGroupPrefix('');
    setShowCreateGroup(false);
  }

  function handleAddCmd(groupId: string) {
    const cmd = (newCommands[groupId] || '').trim();
    if (!cmd) return;
    props.onAddQueueItem(groupId, cmd);
    setNewCommands((prev) => ({ ...prev, [groupId]: '' }));
  }

  // Drag-to-reorder state
  const dragIdRef = useRef<string | null>(null);

  function handleDragStart(itemId: string) {
    dragIdRef.current = itemId;
  }

  function handleDrop(groupId: string, targetId: string, queue: QueueItem[]) {
    const dragId = dragIdRef.current;
    if (!dragId || dragId === targetId) return;
    const ids = queue.map((q) => q.id);
    const from = ids.indexOf(dragId);
    const to = ids.indexOf(targetId);
    if (from < 0 || to < 0) return;
    const newIds = [...ids];
    newIds.splice(from, 1);
    newIds.splice(to, 0, dragId);
    props.onReorderQueue(groupId, newIds);
    dragIdRef.current = null;
  }

  return (
    <div className="sg-panel">
      <div className="sg-header">
        <p className="sg-desc">每个分组维护独立的脚本队列。每次报警触发时，各分组依次从队列头部取出一条命令执行，执行完毕后自动归入历史存档。</p>
        {!props.singleMode && (
          <button
            className="chip"
            type="button"
            onClick={() => setShowCreateGroup((v) => !v)}
          >
            <Plus size={14} />添加分组
          </button>
        )}
      </div>

      {!props.singleMode && showCreateGroup && (
        <form className="sg-create-form em-fieldset" onSubmit={handleCreateGroup}>
          <div className="em-form-grid2">
            <div className="form-group">
              <label>分组名称 *</label>
              <input
                className="text-input"
                placeholder="如：实验 A"
                value={newGroupName}
                onChange={(e) => setNewGroupName(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Screen 会话名前缀</label>
              <input
                className="text-input"
                placeholder="留空则自动生成"
                value={newGroupPrefix}
                onChange={(e) => setNewGroupPrefix(e.target.value)}
              />
            </div>
          </div>
          <div className="sg-create-form-footer">
            <button className="primary-button" type="submit"><Plus size={14} />创建分组</button>
            <button className="chip" type="button" onClick={() => setShowCreateGroup(false)}><X size={14} />取消</button>
          </div>
        </form>
      )}

      {props.groups.length === 0 && !showCreateGroup && (
        <div className="empty-state sg-empty">
          <Terminal size={28} />
          <p>暂无脚本分组，点击「添加分组」创建第一个脚本分组。</p>
        </div>
      )}

      {props.groups.map((group) => {
        const tab = getGroupTab(group.id);
        const runningSessions = group.sessions.filter((s) => s.status === 'running');
        return (
          <div className="sg-group" key={group.id}>
            <div className="sg-group-header">
              <span className="sg-group-name"><Terminal size={14} />{group.name}</span>
              {group.screenNamePrefix && (
                <span className="sg-group-prefix muted">screen: {group.screenNamePrefix}_*</span>
              )}
              <div className="sg-group-badges">
                {group.queue.length > 0 && (
                  <span className="badge info">{group.queue.length} 队列</span>
                )}
                {runningSessions.length > 0 && (
                  <span className="badge warning">{runningSessions.length} 运行中</span>
                )}
              </div>
              <button
                className="icon-button tiny danger"
                type="button"
                title="删除分组"
                onClick={() => props.onDeleteGroup(group.id)}
              >
                <Trash2 size={13} />
              </button>
            </div>

            {/* Tabs */}
            <div className="sg-tabs">
              <button
                className={`sg-tab ${tab === 'queue' ? 'active' : ''}`}
                type="button"
                onClick={() => setGroupTab(group.id, 'queue')}
              >
                队列 {group.queue.length > 0 && <span className="badge info">{group.queue.length}</span>}
              </button>
              <button
                className={`sg-tab ${tab === 'history' ? 'active' : ''}`}
                type="button"
                onClick={() => setGroupTab(group.id, 'history')}
              >
                历史存档 {group.history.length > 0 && <span className="badge">{group.history.length}</span>}
              </button>
              <button
                className={`sg-tab ${tab === 'sessions' ? 'active' : ''}`}
                type="button"
                onClick={() => setGroupTab(group.id, 'sessions')}
              >
                Screen 会话 {runningSessions.length > 0 && <span className="badge warning">{runningSessions.length}</span>}
              </button>
            </div>

            {/* Queue Tab */}
            {tab === 'queue' && (
              <div className="sg-tab-content">
                <p className="sg-tab-hint">队列头部的命令将在下次报警触发时执行。可拖拽调整顺序。</p>
                {group.queue.length === 0 ? (
                  <p className="muted sg-empty-inline">队列为空，下次触发将跳过此分组。</p>
                ) : (
                  <div className="sg-queue-list">
                    {group.queue.map((item, idx) => (
                      <div
                        key={item.id}
                        className={`sg-queue-item ${idx === 0 ? 'next' : ''}`}
                        draggable
                        onDragStart={() => handleDragStart(item.id)}
                        onDragOver={(e) => e.preventDefault()}
                        onDrop={() => handleDrop(group.id, item.id, group.queue)}
                      >
                        <span className="sg-drag-handle"><GripVertical size={14} /></span>
                        <span className="sg-queue-idx">{idx + 1}</span>
                        {idx === 0 && <span className="badge success sg-next-badge">下次</span>}
                        <code className="sg-cmd">{item.command}</code>
                        <button
                          className="icon-button tiny danger"
                          type="button"
                          title="删除"
                          onClick={() => props.onDeleteQueueItem(item.id)}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                {/* Add command input */}
                <div className="sg-add-cmd">
                  <textarea
                    className="text-input monospace sg-cmd-input"
                    rows={2}
                    placeholder="输入命令，如：cd /data && python train.py --exp exp1"
                    value={newCommands[group.id] || ''}
                    onChange={(e) => setNewCommands((prev) => ({ ...prev, [group.id]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); handleAddCmd(group.id); } }}
                  />
                  <button
                    className="chip"
                    type="button"
                    onClick={() => handleAddCmd(group.id)}
                    disabled={!(newCommands[group.id] || '').trim()}
                  >
                    <Plus size={14} />加入队列
                  </button>
                </div>
              </div>
            )}

            {/* History Tab */}
            {tab === 'history' && (
              <div className="sg-tab-content">
                <p className="sg-tab-hint">以下为已触发过的命令存档，可点击「恢复」将命令重新加入队列末尾。</p>
                {group.history.length === 0 ? (
                  <p className="muted sg-empty-inline">暂无历史记录。</p>
                ) : (
                  <div className="sg-history-list">
                    {group.history.map((item) => (
                      <div className="sg-history-item" key={item.id}>
                        <div className="sg-history-main">
                          <code className="sg-cmd">{item.command}</code>
                          {item.screenSession && (
                            <span className="sg-screen-tag muted">screen: {item.screenSession}</span>
                          )}
                        </div>
                        <div className="sg-history-meta">
                          <span className="muted">{formatTime(item.triggeredAt)}</span>
                          <button
                            className="chip tiny"
                            type="button"
                            title="恢复到队列"
                            onClick={() => props.onRestoreHistory(item.id)}
                          >
                            <RotateCcw size={12} />恢复
                          </button>
                          <button
                            className="icon-button tiny danger"
                            type="button"
                            title="删除记录"
                            onClick={() => props.onDeleteHistory(item.id)}
                          >
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Sessions Tab */}
            {tab === 'sessions' && (
              <div className="sg-tab-content">
                <div className="sg-sessions-header">
                  <p className="sg-tab-hint">每条命令在独立的 screen 窗口中运行，可刷新检测是否执行完毕。</p>
                  <button
                    className="chip"
                    type="button"
                    onClick={() => props.onRefreshSessions(group.id)}
                  >
                    <RefreshCw size={13} />刷新状态
                  </button>
                </div>
                {group.sessions.length === 0 ? (
                  <p className="muted sg-empty-inline">暂无 Screen 会话记录。</p>
                ) : (
                  <div className="sg-sessions-list">
                    {group.sessions.map((sess) => (
                      <div className={`sg-session-item ${sess.status}`} key={sess.id}>
                        <span className={`sg-status-dot ${sess.status}`} />
                        <div className="sg-session-info">
                          <code className="sg-session-name">{sess.sessionName}</code>
                          <code className="sg-cmd sg-cmd-sm">{sess.command}</code>
                        </div>
                        <div className="sg-session-meta">
                          <span className={`badge ${sess.status === 'running' ? 'warning' : sess.status === 'done' ? 'success' : ''}`}>
                            {sess.status === 'running' ? '运行中' : sess.status === 'done' ? '已完成' : '未知'}
                          </span>
                          <span className="muted">{formatTime(sess.startedAt)}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ============================================================
// Server List Panel
// ============================================================

function ServerListPanel(props: {
  servers: EmServer[];
  onAdd: () => void;
  onEdit: (serverId: string) => void;
  onDelete: (serverId: string) => void;
}) {
  return (
    <div className="em-server-list-panel">
      <div className="em-server-list-header">
        <p className="muted">管理 SSH 服务器连接，编辑或删除已有服务器。</p>
        <button className="chip" type="button" onClick={props.onAdd}>
          <Plus size={14} />添加服务器
        </button>
      </div>

      {props.servers.length === 0 ? (
        <div className="empty-state">
          <Server size={28} />
          <p>暂无服务器，点击「添加服务器」创建第一个连接。</p>
        </div>
      ) : (
        <div className="em-server-list-items">
          {props.servers.map((server) => (
            <div className="em-server-item" key={server.id}>
              <div className="em-server-item-info">
                <strong>{server.name}</strong>
                <small className="muted">{server.sshUsername}@{server.host}:{server.port}</small>
              </div>
              <div className="em-server-item-actions">
                <button className="icon-button tiny" type="button" onClick={() => props.onEdit(server.id)} title="编辑"><Settings size={14} /></button>
                <button className="icon-button tiny danger" type="button" onClick={() => props.onDelete(server.id)} title="删除"><Trash2 size={14} /></button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
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
