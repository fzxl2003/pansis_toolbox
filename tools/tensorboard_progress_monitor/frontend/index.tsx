import "./style.css";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BarChart3,
  ChevronDown,
  ChevronRight,
  Clock3,
  Copy,
  ExternalLink,
  Loader2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Server,
  Square,
  Trash2,
  TriangleAlert,
} from "lucide-react";

import {
  ApiError,
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
} from "../../../frontend/src/api/client";
import { fetchMe, type AuthUser } from "../../../frontend/src/api/auth";
import { LoginPanel } from "../../../frontend/src/components/LoginPanel";
import {
  Alert,
  Badge,
  EmptyState,
  Field,
  Modal,
  Spin,
  useConfirm,
} from "./components";

const API = "/api/tools/tensorboard-progress-monitor";
const DEFAULT_YAML = `tensorboard_root: /path/to/tensorboard/logs
progress_tag: train/step
progress_mode: event_step
tail_bytes: 1048576
report_interval_seconds: 60
rate_report_count: 5
stale_after_seconds: 180
overall_concurrency: 1
tb_custom_params: ""
groups:
  - name: all
    pattern: "*"
    target_step: 1000000
    total_runs:
    include_unmatched_children: false
    children: []
`;

type ServerItem = {
  id: string;
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  enabled: boolean;
  tbPythonMode: "conda" | "path";
  tbCondaBasePath: string;
  tbCondaEnv: string;
  tbPythonPath: string;
};
type TbUrlParamGroup = { label: string; params: string };
type Task = {
  id: string;
  name: string;
  serverId: string;
  configSource: "inline" | "remote_yaml";
  inlineYaml: string;
  remoteYamlPath: string;
  pythonCommand: string;
  reportIntervalSeconds: number;
  enabled: boolean;
  showInTabs: boolean;
  displayOrder: number;
  tbExtraParams: TbUrlParamGroup[];
  tbDefaultParams: string;
  lastReportAt: string | null;
  lastConfigError: string;
};
type TbSession = {
  id: string;
  taskId: string;
  serverId: string;
  logdir: string;
  status: "running" | "stopped" | "failed";
  error: string;
  startedAt: string | null;
  stoppedAt: string | null;
};
type Group = {
  name: string;
  shortName?: string;
  depth?: number;
  pattern: string;
  targetStep: number;
  totalRuns: number | null;
  completedRuns: number;
  activeRuns: number;
  queuedRuns: number | null;
  medianDurationSeconds: number | null;
  durationSource?: "completed" | "running_estimate" | "unavailable";
  etaSeconds: number | null;
  reason: string;
};
type Run = {
  runKey: string;
  relativePath: string;
  groupName: string | null;
  progress: number | null;
  targetStep: number | null;
  startedAt: number | null;
  status: string;
  ratePerSecond: number | null;
  etaSeconds: number | null;
  error: string;
};
type Summary = {
  counts: Record<string, number>;
  groups: Group[];
  overallEtaSeconds: number | null;
  overallEtaReason: string;
  overallEtaMethod?: string;
  remoteErrors: { path: string; error: string }[];
  collector?: { readFileCount: number; skippedFileCount: number };
};
type Report = {
  reportId: string;
  reportedAt: string;
  success: boolean;
  summary: Summary;
  error: string;
  runs: Run[];
};
type ServerForm = {
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  sshPassword: string;
  tbPythonMode: "conda" | "path";
  tbCondaBasePath: string;
  tbCondaEnv: string;
  tbPythonPath: string;
};
type TaskForm = {
  name: string;
  serverId: string;
  configSource: "inline" | "remote_yaml";
  inlineYaml: string;
  remoteYamlPath: string;
  pythonCommand: string;
  reportIntervalSeconds: number;
  enabled: boolean;
  showInTabs: boolean;
  tbExtraParams: TbUrlParamGroup[];
  tbDefaultParams: string;
};
type VisualGroup = {
  id: string;
  name: string;
  pattern: string;
  targetStep: string;
  totalRuns: string;
  includeUnmatchedChildren: boolean;
  parentId: string;
};
type VisualConfig = {
  tensorboardRoot: string;
  progressTag: string;
  progressMode: "event_step" | "scalar_value";
  tailBytes: string;
  reportIntervalSeconds: string;
  rateReportCount: string;
  staleAfterSeconds: string;
  overallConcurrency: string;
  groups: VisualGroup[];
};

const EMPTY_SERVER: ServerForm = {
  name: "",
  host: "",
  port: 22,
  sshUsername: "",
  sshPassword: "",
  tbPythonMode: "conda",
  tbCondaBasePath: "",
  tbCondaEnv: "",
  tbPythonPath: "",
};
const EMPTY_TASK: TaskForm = {
  name: "",
  serverId: "",
  configSource: "inline",
  inlineYaml: DEFAULT_YAML,
  remoteYamlPath: "",
  pythonCommand: "python3",
  reportIntervalSeconds: 60,
  enabled: true,
  showInTabs: true,
  tbExtraParams: [],
  tbDefaultParams: "",
};
const EMPTY_VISUAL_CONFIG: VisualConfig = {
  tensorboardRoot: "",
  progressTag: "train/step",
  progressMode: "event_step",
  tailBytes: "1048576",
  reportIntervalSeconds: "60",
  rateReportCount: "5",
  staleAfterSeconds: "180",
  overallConcurrency: "1",
  groups: [
    {
      id: "root",
      name: "all",
      pattern: "*",
      targetStep: "1000000",
      totalRuns: "",
      includeUnmatchedChildren: false,
      parentId: "",
    },
  ],
};

function hasTbEnvironment(server: ServerItem | undefined): boolean {
  return Boolean(
    server &&
    (server.tbPythonMode === "conda"
      ? server.tbCondaBasePath && server.tbCondaEnv
      : server.tbPythonPath),
  );
}

export default function TensorBoardProgressMonitor() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [servers, setServers] = useState<ServerItem[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [serverModal, setServerModal] = useState<{
    open: boolean;
    server: ServerItem | null;
  }>({ open: false, server: null });
  const [page, setPage] = useState<"reports" | "manage">("reports");
  const [manageSection, setManageSection] = useState<"reports" | "servers">(
    "reports",
  );
  const [taskModal, setTaskModal] = useState<Task | null | false>(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const selected = useMemo(
    () => tasks.find((item) => item.id === selectedId) ?? null,
    [tasks, selectedId],
  );

  useEffect(() => {
    void fetchMe()
      .then((value) => setMe(value.user))
      .catch(() => setMe(null));
  }, []);
  useEffect(() => {
    if (me) void loadAll();
  }, [me]);
  useEffect(() => {
    if (selected) void loadReport(selected.id);
    else setReport(null);
  }, [selected?.id]);
  useEffect(() => {
    const visible = tasks.filter((task) => task.showInTabs);
    if (!selectedId || !tasks.some((task) => task.id === selectedId))
      setSelectedId(visible[0]?.id ?? "");
  }, [tasks, selectedId]);

  async function loadAll() {
    setLoading(true);
    try {
      const [serverData, taskData] = await Promise.all([
        apiGet<{ servers: ServerItem[] }>(`${API}/servers`),
        apiGet<{ tasks: Task[] }>(`${API}/tasks`),
      ]);
      setServers(serverData.servers);
      setTasks(taskData.tasks);
    } catch (caught) {
      setError(message(caught));
    } finally {
      setLoading(false);
    }
  }
  async function loadReport(taskId: string) {
    try {
      setReport(
        (
          await apiGet<{ report: Report | null }>(
            `${API}/tasks/${taskId}/report`,
          )
        ).report,
      );
    } catch (caught) {
      setError(message(caught));
    }
  }
  function showTask(task: Task) {
    setSelectedId(task.id);
    void loadReport(task.id);
  }
  if (!me)
    return (
      <div className="tool-page tbd-tool">
        <LoginPanel
          onSuccess={() => void fetchMe().then((item) => setMe(item.user))}
        />
      </div>
    );
  const tabTasks = tasks.filter((task) => task.showInTabs);
  return (
    <div className="tool-page tbd-tool tpm-tool">
      <header className="tool-header">
        <div>
          <h1 className="tool-title">TensorBoard 实验进度监控</h1>
          <p className="tool-subtitle">
            远端本地读取 event 尾部，仅回传进度摘要与剩余时间
          </p>
        </div>
        <div className="tpm-header-actions">
          <span className="tpm-user">{me.displayName}</span>
        </div>
      </header>
      <nav className="tb-topnav tpm-primary-tabs">
        <button
          className={`tb-topnav-tab${page === "reports" ? " active" : ""}`}
          type="button"
          onClick={() => setPage("reports")}
        >
          <BarChart3 size={14} /> 报表
        </button>
        <button
          className={`tb-topnav-tab${page === "manage" ? " active" : ""}`}
          type="button"
          onClick={() => setPage("manage")}
        >
          <Server size={14} /> 服务器、报表管理
        </button>
      </nav>
      <div className="tb-body">
        {error && <Alert type="error">{error}</Alert>}
        {notice && <Alert type="success">{notice}</Alert>}
        {page === "reports" ? (
          <>
            <nav className="tpm-report-tabs" aria-label="报表选择">
              {tabTasks.map((task) => (
                <button
                  key={task.id}
                  className={task.id === selected?.id ? "active" : ""}
                  type="button"
                  onClick={() => showTask(task)}
                >
                  <Activity size={14} /> {task.name}
                  {!task.enabled && <small>已暂停</small>}
                </button>
              ))}
              {!tabTasks.length && (
                <span className="tpm-no-tabs">
                  暂无显示中的报表，请在“服务器、报表管理”中创建或启用显示。
                </span>
              )}
            </nav>
            <TaskWorkspace
              task={selected}
              servers={servers}
              report={report}
              loading={loading}
              onReload={() => void loadAll()}
              onRefreshReport={async (taskId) => {
                await apiPost(`${API}/tasks/${taskId}/refresh`, {});
                await loadReport(taskId);
              }}
              onSelect={showTask}
              onEdit={(task) => setTaskModal(task)}
              onNotice={setNotice}
              onError={setError}
            />
          </>
        ) : (
          <ManagementWorkspace
            section={manageSection}
            onSectionChange={setManageSection}
            servers={servers}
            tasks={tasks}
            loading={loading}
            onReload={() => void loadAll()}
            onAddServer={() => setServerModal({ open: true, server: null })}
            onEditServer={(server) => setServerModal({ open: true, server })}
            onAddReport={() => setTaskModal(null)}
            onEditReport={(task) => setTaskModal(task)}
            onSelectReport={(task) => {
              showTask(task);
              setPage("reports");
            }}
            onNotice={setNotice}
            onError={setError}
          />
        )}
      </div>
      {serverModal.open && (
        <ServerModal
          server={serverModal.server}
          onClose={() => setServerModal({ open: false, server: null })}
          onSaved={async (messageText) => {
            setServerModal({ open: false, server: null });
            setNotice(messageText);
            await loadAll();
          }}
        />
      )}
      {taskModal !== false && (
        <TaskFormModal
          task={taskModal}
          servers={servers}
          onClose={() => setTaskModal(false)}
          onSaved={async (saved, messageText) => {
            setTaskModal(false);
            setSelectedId(saved.id);
            setNotice(messageText);
            await loadAll();
          }}
        />
      )}
    </div>
  );
}

function ManagementWorkspace({
  section,
  onSectionChange,
  servers,
  tasks,
  loading,
  onReload,
  onAddServer,
  onEditServer,
  onAddReport,
  onEditReport,
  onSelectReport,
  onNotice,
  onError,
}: {
  section: "reports" | "servers";
  onSectionChange: (section: "reports" | "servers") => void;
  servers: ServerItem[];
  tasks: Task[];
  loading: boolean;
  onReload: () => void;
  onAddServer: () => void;
  onEditServer: (server: ServerItem) => void;
  onAddReport: () => void;
  onEditReport: (task: Task) => void;
  onSelectReport: (task: Task) => void;
  onNotice: (value: string) => void;
  onError: (value: string) => void;
}) {
  return (
    <div className="tpm-management">
      <nav className="tpm-manage-tabs">
        <button
          className={section === "reports" ? "active" : ""}
          type="button"
          onClick={() => onSectionChange("reports")}
        >
          <BarChart3 size={14} /> 报表管理
        </button>
        <button
          className={section === "servers" ? "active" : ""}
          type="button"
          onClick={() => onSectionChange("servers")}
        >
          <Server size={14} /> 服务器管理
        </button>
      </nav>
      {section === "reports" ? (
        <ReportsManagerPanel
          tasks={tasks}
          servers={servers}
          loading={loading}
          onReload={onReload}
          onNew={onAddReport}
          onEdit={onEditReport}
          onSelect={onSelectReport}
          onNotice={onNotice}
          onError={onError}
        />
      ) : (
        <ServersPanel
          servers={servers}
          loading={loading}
          onReload={onReload}
          onAdd={onAddServer}
          onEdit={onEditServer}
          onNotice={onNotice}
          onError={onError}
        />
      )}
    </div>
  );
}

function ReportsManagerPanel({
  tasks,
  servers,
  loading,
  onReload,
  onSelect,
  onNew,
  onEdit,
  onNotice,
  onError,
}: {
  tasks: Task[];
  servers: ServerItem[];
  loading: boolean;
  onReload: () => void;
  onSelect: (task: Task) => void;
  onNew: () => void;
  onEdit: (task: Task) => void;
  onNotice: (value: string) => void;
  onError: (value: string) => void;
}) {
  const { confirm, dialog } = useConfirm();
  const serverName = (serverId: string) =>
    servers.find((server) => server.id === serverId)?.name ?? "未知服务器";
  async function refresh(task: Task) {
    try {
      await apiPost(`${API}/tasks/${task.id}/refresh`, {});
      onNotice("已生成最新报表");
    } catch (caught) {
      onError(message(caught));
    }
  }
  async function toggle(task: Task, patch: Partial<Task>) {
    try {
      await apiPut(`${API}/tasks/${task.id}`, { ...task, ...patch });
      onNotice(
        patch.showInTabs === undefined
          ? task.enabled
            ? "报表已暂停"
            : "报表已启用"
          : patch.showInTabs
            ? "报表已显示"
            : "报表已隐藏",
      );
      onReload();
    } catch (caught) {
      onError(message(caught));
    }
  }
  async function copy(task: Task) {
    try {
      const result = await apiPost<{ task: Task }>(
        `${API}/tasks/${task.id}/copy`,
        {},
      );
      onNotice(`已复制为「${result.task.name}」`);
      await onReload();
    } catch (caught) {
      onError(message(caught));
    }
  }
  async function move(task: Task, direction: "up" | "down") {
    try {
      await apiPost(`${API}/tasks/${task.id}/move`, { direction });
      await onReload();
    } catch (caught) {
      onError(message(caught));
    }
  }
  function remove(task: Task) {
    confirm({
      title: "删除报表",
      message: `确认删除「${task.name}」及其全部历史报表？`,
      onConfirm: async () => {
        try {
          await apiDelete(`${API}/tasks/${task.id}`);
          onNotice("报表已删除");
          onReload();
        } catch (caught) {
          onError(message(caught));
        }
      },
    });
  }
  return (
    <div className="tb-panel">
      <div className="tb-toolbar">
        <div className="tb-toolbar-left">
          <h2>
            <BarChart3 size={18} /> 报表管理
          </h2>
        </div>
        <div className="tb-toolbar-right">
          <button
            className="tb-btn tb-btn-secondary"
            type="button"
            disabled={loading}
            onClick={onReload}
          >
            <RefreshCw size={14} className={loading ? "spin" : ""} /> 刷新
          </button>
          <button
            className="tb-btn tb-btn-primary"
            type="button"
            disabled={!servers.length}
            onClick={onNew}
          >
            <Plus size={14} /> 新建报表
          </button>
        </div>
      </div>
      {loading ? (
        <div className="tb-loading-overlay">
          <Spin /> 加载报表…
        </div>
      ) : tasks.length === 0 ? (
        <EmptyState
          icon={<BarChart3 size={32} />}
          title="暂无报表"
          hint={
            servers.length
              ? "点击「新建报表」开始"
              : "请先在“服务器管理”中添加 SSH 服务器"
          }
        />
      ) : (
        <div className="tb-table-wrap">
          <table className="tb-table">
            <thead>
              <tr>
                <th>报表</th>
                <th>服务器</th>
                <th>显示</th>
                <th>显示排序</th>
                <th>状态</th>
                <th>最近报表</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task, index) => (
                <tr key={task.id}>
                  <td>
                    <strong>{task.name}</strong>
                    {task.lastConfigError && (
                      <div className="tpm-inline-error">最近采集失败</div>
                    )}
                  </td>
                  <td>{serverName(task.serverId)}</td>
                  <td>
                    <label className="tpm-visibility">
                      <input
                        type="checkbox"
                        checked={task.showInTabs}
                        onChange={(event) =>
                          void toggle(task, {
                            showInTabs: event.target.checked,
                          })
                        }
                      />{" "}
                      {task.showInTabs ? "显示" : "隐藏"}
                    </label>
                  </td>
                  <td>
                    <div className="tpm-order">
                      <span>
                        {task.showInTabs ? task.displayOrder + 1 : "隐藏"}
                      </span>
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost"
                        type="button"
                        title="上移"
                        disabled={
                          !task.showInTabs ||
                          index === 0 ||
                          !tasks[index - 1]?.showInTabs
                        }
                        onClick={() => void move(task, "up")}
                      >
                        <ArrowUp size={12} />
                      </button>
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost"
                        type="button"
                        title="下移"
                        disabled={
                          !task.showInTabs || !tasks[index + 1]?.showInTabs
                        }
                        onClick={() => void move(task, "down")}
                      >
                        <ArrowDown size={12} />
                      </button>
                    </div>
                  </td>
                  <td>
                    <Badge color={task.enabled ? "green" : "default"}>
                      {task.enabled ? "已启用" : "已暂停"}
                    </Badge>
                  </td>
                  <td>{relative(task.lastReportAt)}</td>
                  <td>
                    <div className="tb-table-actions">
                      <button
                        className="tb-btn tb-btn-sm tb-btn-primary"
                        type="button"
                        onClick={() => onSelect(task)}
                      >
                        查看
                      </button>
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost"
                        type="button"
                        title="立即刷新"
                        onClick={() => void refresh(task)}
                      >
                        <RefreshCw size={12} />
                      </button>
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost"
                        type="button"
                        title="编辑"
                        onClick={() => onEdit(task)}
                      >
                        <Pencil size={12} />
                      </button>
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost"
                        type="button"
                        title="复制报表"
                        onClick={() => void copy(task)}
                      >
                        <Copy size={12} />
                      </button>
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost"
                        type="button"
                        title={task.enabled ? "暂停" : "启用"}
                        onClick={() =>
                          void toggle(task, { enabled: !task.enabled })
                        }
                      >
                        {task.enabled ? "暂停" : "启用"}
                      </button>
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost tpm-danger"
                        type="button"
                        title="删除"
                        onClick={() => remove(task)}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog}
    </div>
  );
}

function ServersPanel({
  servers,
  loading,
  onReload,
  onAdd,
  onEdit,
  onNotice,
  onError,
}: {
  servers: ServerItem[];
  loading: boolean;
  onReload: () => void;
  onAdd: () => void;
  onEdit: (server: ServerItem) => void;
  onNotice: (value: string) => void;
  onError: (value: string) => void;
}) {
  const { confirm, dialog } = useConfirm();
  async function test(server: ServerItem) {
    try {
      const result = await apiPost<{
        connected: boolean;
        python: string;
        error: string;
      }>(`${API}/servers/${server.id}/test`, {});
      result.connected
        ? onNotice(`连接成功：${result.python}`)
        : onError(result.error);
    } catch (caught) {
      onError(message(caught));
    }
  }
  async function testTb(server: ServerItem) {
    try {
      const result = await apiPost<{
        ok: boolean;
        output: string;
        error: string;
      }>(`${API}/servers/${server.id}/check-tb-environment`, {});
      result.ok
        ? onNotice(`TensorBoard 环境可用：${result.output}`)
        : onError(result.error);
    } catch (caught) {
      onError(message(caught));
    }
  }
  function remove(server: ServerItem) {
    confirm({
      title: "删除服务器",
      message: `确认删除「${server.name}」？未删除关联任务前无法删除服务器。`,
      onConfirm: async () => {
        try {
          await apiDelete(`${API}/servers/${server.id}`);
          onNotice("服务器已删除");
          onReload();
        } catch (caught) {
          onError(message(caught));
        }
      },
    });
  }
  return (
    <div className="tb-panel">
      <div className="tb-toolbar">
        <div className="tb-toolbar-left">
          <h2>
            <Server size={18} /> 服务器管理
          </h2>
        </div>
        <div className="tb-toolbar-right">
          <button
            className="tb-btn tb-btn-primary"
            type="button"
            onClick={onAdd}
          >
            <Plus size={14} /> 添加服务器
          </button>
        </div>
      </div>
      {loading ? (
        <div className="tb-loading-overlay">
          <Spin /> 加载服务器列表…
        </div>
      ) : servers.length === 0 ? (
        <EmptyState
          icon={<Server size={32} />}
          title="暂无服务器"
          hint="点击「添加服务器」开始"
        />
      ) : (
        <div className="tb-table-wrap">
          <table className="tb-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>主机</th>
                <th>用户名</th>
                <th>状态</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {servers.map((server) => (
                <tr key={server.id}>
                  <td>
                    <strong>{server.name}</strong>
                  </td>
                  <td>
                    <span className="tb-code">
                      {server.host}:{server.port}
                    </span>
                  </td>
                  <td>{server.sshUsername}</td>
                  <td>
                    <Badge color={server.enabled ? "green" : "default"}>
                      {server.enabled ? "已启用" : "已停用"}
                    </Badge>
                  </td>
                  <td>
                    <div className="tb-table-actions">
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost"
                        type="button"
                        title="测试连接"
                        onClick={() => void test(server)}
                      >
                        <Play size={12} />
                      </button>
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost"
                        type="button"
                        title="编辑"
                        onClick={() => onEdit(server)}
                      >
                        <Pencil size={12} />
                      </button>
                      <button
                        className="tb-btn tb-btn-sm tb-btn-ghost tpm-danger"
                        type="button"
                        title="删除"
                        onClick={() => remove(server)}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog}
    </div>
  );
}

function TaskWorkspace({
  task,
  servers,
  report,
  loading,
  onReload,
  onRefreshReport,
  onSelect,
  onEdit,
  onNotice,
  onError,
}: {
  task: Task | null;
  servers: ServerItem[];
  report: Report | null;
  loading: boolean;
  onReload: () => void;
  onRefreshReport: (taskId: string) => Promise<void>;
  onSelect: (task: Task) => void;
  onEdit: (task: Task) => void;
  onNotice: (value: string) => void;
  onError: (value: string) => void;
}) {
  const { confirm, dialog } = useConfirm();
  const [tbSession, setTbSession] = useState<TbSession | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  useEffect(() => {
    if (task)
      void apiGet<{ session: TbSession | null }>(
        `${API}/tasks/${task.id}/tb-session`,
      )
        .then((result) => setTbSession(result.session))
        .catch((caught) => onError(message(caught)));
    else setTbSession(null);
  }, [task?.id]);
  if (!task)
    return (
      <div className="tb-panel">
        <EmptyState
          icon={<BarChart3 size={32} />}
          title="未选择报表"
          hint="请在“服务器、报表管理”中创建报表，或开启其显示。"
        />
      </div>
    );
  const currentTask = task;
  const tbAvailable = hasTbEnvironment(
    servers.find((server) => server.id === currentTask.serverId),
  );
  async function toggle() {
    try {
      await apiPut(`${API}/tasks/${currentTask.id}`, {
        ...currentTask,
        enabled: !currentTask.enabled,
      });
      onNotice(currentTask.enabled ? "任务已暂停" : "任务已启用");
      onReload();
    } catch (caught) {
      onError(message(caught));
    }
  }
  async function tbAction(action: "start" | "stop") {
    try {
      const result = await apiPost<{ session: TbSession | null }>(
        `${API}/tasks/${currentTask.id}/tb-session/${action}`,
        {},
      );
      setTbSession(result.session);
      onNotice(
        action === "start"
          ? "TensorBoard 会话已启动"
          : "TensorBoard 会话已停止",
      );
    } catch (caught) {
      onError(message(caught));
    }
  }
  async function refreshReport() {
    setRefreshing(true);
    try {
      await onRefreshReport(currentTask.id);
      onReload();
      onNotice("已生成最新报表");
    } catch (caught) {
      onError(message(caught));
    } finally {
      setRefreshing(false);
    }
  }
  function remove() {
    confirm({
      title: "删除监控任务",
      message: `确认删除「${currentTask.name}」及其全部历史报表？`,
      onConfirm: async () => {
        try {
          await apiDelete(`${API}/tasks/${currentTask.id}`);
          onNotice("任务已删除");
          onReload();
        } catch (caught) {
          onError(message(caught));
        }
      },
    });
  }
  return (
    <div className="tb-panel">
      <div className="tb-toolbar">
        <div className="tb-toolbar-left">
          <h2>
            <Activity size={18} /> {task.name}
          </h2>
          <Badge color={task.enabled ? "green" : "default"}>
            {task.enabled ? "已启用" : "已暂停"}
          </Badge>
          {tbAvailable && (
            <Badge
              color={
                tbSession?.status === "running"
                  ? "green"
                  : tbSession?.status === "failed"
                    ? "red"
                    : "default"
              }
            >
              TB{" "}
              {tbSession?.status === "running"
                ? "运行中"
                : tbSession?.status === "failed"
                  ? "失败"
                  : "未运行"}
            </Badge>
          )}
        </div>
        <div className="tb-toolbar-right">
          <button
            className="tb-btn tb-btn-primary"
            type="button"
            disabled={refreshing}
            onClick={() => void refreshReport()}
          >
            <RefreshCw size={14} className={refreshing ? "spin" : ""} />
            {refreshing ? "刷新中…" : "立即刷新"}
          </button>
          {tbAvailable && tbSession?.status === "running" ? (
            <button
              className="tb-btn tb-btn-secondary"
              type="button"
              onClick={() => void tbAction("stop")}
            >
              <Square size={14} /> 停止 TB
            </button>
          ) : tbAvailable ? (
            <button
              className="tb-btn tb-btn-secondary"
              type="button"
              onClick={() => void tbAction("start")}
            >
              <Play size={14} /> 启动 TB
            </button>
          ) : null}
          <button
            className="tb-btn tb-btn-secondary"
            type="button"
            onClick={() => onEdit(task)}
          >
            <Pencil size={14} /> 编辑
          </button>
          <button
            className="tb-btn tb-btn-secondary"
            type="button"
            onClick={() => void toggle()}
          >
            {task.enabled ? "暂停" : "启用"}
          </button>
          <button
            className="tb-btn tb-btn-danger"
            type="button"
            onClick={remove}
          >
            <Trash2 size={14} /> 删除
          </button>
        </div>
      </div>
      {tbSession?.error && (
        <Alert type="error">TensorBoard：{tbSession.error}</Alert>
      )}
      {task.lastConfigError && (
        <Alert type="error">最近配置/采集错误：{task.lastConfigError}</Alert>
      )}
      <ReportPanel
        task={task}
        report={report}
        tbAvailable={tbAvailable}
        tbRunning={tbSession?.status === "running"}
        onError={onError}
      />
      {dialog}
    </div>
  );
}

function ServerModal({
  server,
  onClose,
  onSaved,
}: {
  server: ServerItem | null;
  onClose: () => void;
  onSaved: (messageText: string) => void;
}) {
  const [form, setForm] = useState<ServerForm>(
    server
      ? {
          name: server.name,
          host: server.host,
          port: server.port,
          sshUsername: server.sshUsername,
          sshPassword: "",
          tbPythonMode: server.tbPythonMode,
          tbCondaBasePath: server.tbCondaBasePath,
          tbCondaEnv: server.tbCondaEnv,
          tbPythonPath: server.tbPythonPath,
        }
      : EMPTY_SERVER,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [condaEnvs, setCondaEnvs] = useState<string[]>([]);
  const [condaLoading, setCondaLoading] = useState(false);
  const [condaError, setCondaError] = useState("");
  const condaPathSaved =
    Boolean(server) &&
    form.tbCondaBasePath.trim() === server?.tbCondaBasePath.trim() &&
    Boolean(form.tbCondaBasePath.trim());
  useEffect(() => {
    if (!server || form.tbPythonMode !== "conda" || !condaPathSaved) {
      setCondaEnvs([]);
      setCondaError("");
      return;
    }
    setCondaLoading(true);
    setCondaError("");
    void apiGet<{ envs: string[]; error: string }>(
      API + "/servers/" + server.id + "/conda-envs",
    )
      .then((result) => {
        setCondaEnvs(result.envs);
        setCondaError(result.error);
      })
      .catch((caught) => setCondaError(message(caught)))
      .finally(() => setCondaLoading(false));
  }, [server?.id, form.tbPythonMode, form.tbCondaBasePath, condaPathSaved]);
  async function save() {
    setSaving(true);
    setError("");
    try {
      if (server) await apiPut(`${API}/servers/${server.id}`, form);
      else await apiPost(`${API}/servers`, form);
      onSaved(server ? "服务器已更新" : "服务器已添加");
    } catch (caught) {
      setError(message(caught));
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <Modal
        title={server ? "编辑服务器" : "添加服务器"}
        onClose={onClose}
        foot={
          <>
            <button
              className="tb-btn tb-btn-secondary"
              type="button"
              disabled={saving}
              onClick={onClose}
            >
              取消
            </button>
            <button
              className="tb-btn tb-btn-primary"
              type="button"
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? (
                <>
                  <Loader2 size={14} className="spin" /> 保存中
                </>
              ) : (
                "保存"
              )}
            </button>
          </>
        }
      >
        <div className="tb-form-grid">
          {error && <Alert type="error">{error}</Alert>}
          <Field label="名称">
            <input
              className="tb-input"
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
              placeholder="例如：训练服务器"
            />
          </Field>
          <Field label="主机地址">
            <input
              className="tb-input"
              value={form.host}
              onChange={(event) =>
                setForm({ ...form, host: event.target.value })
              }
              placeholder="例如：192.168.1.100"
            />
          </Field>
          <Field label="端口">
            <input
              className="tb-input"
              type="number"
              value={form.port}
              onChange={(event) =>
                setForm({ ...form, port: Number(event.target.value) || 22 })
              }
            />
          </Field>
          <Field label="SSH 用户名">
            <input
              className="tb-input"
              value={form.sshUsername}
              onChange={(event) =>
                setForm({ ...form, sshUsername: event.target.value })
              }
            />
          </Field>
          <Field label={server ? "SSH 密码（留空不修改）" : "SSH 密码"} full>
            <input
              className="tb-input"
              type="password"
              value={form.sshPassword}
              onChange={(event) =>
                setForm({ ...form, sshPassword: event.target.value })
              }
            />
          </Field>
          <Field label="TensorBoard Python 模式">
            <select
              className="tb-select"
              value={form.tbPythonMode}
              onChange={(event) =>
                setForm({
                  ...form,
                  tbPythonMode: event.target
                    .value as ServerForm["tbPythonMode"],
                })
              }
            >
              <option value="conda">Conda 环境</option>
              <option value="path">自定义 Python 路径</option>
            </select>
          </Field>
          {form.tbPythonMode === "conda" ? (
            <>
              <Field label="Anaconda 路径">
                <input
                  className="tb-input"
                  value={form.tbCondaBasePath}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      tbCondaBasePath: event.target.value,
                      tbCondaEnv: "",
                    })
                  }
                  placeholder="例如：/opt/conda"
                />
              </Field>
              <Field label="TensorBoard Conda 环境">
                <select
                  className="tb-select"
                  value={form.tbCondaEnv}
                  disabled={
                    !condaPathSaved || condaLoading || Boolean(condaError)
                  }
                  onChange={(event) =>
                    setForm({ ...form, tbCondaEnv: event.target.value })
                  }
                >
                  <option value="">
                    {condaLoading
                      ? "正在读取 Conda 环境…"
                      : condaPathSaved
                        ? "请选择 TensorBoard Conda 环境"
                        : "请先保存 Anaconda 路径后再选择"}
                  </option>
                  {condaEnvs.map((env) => (
                    <option key={env} value={env}>
                      {env}
                    </option>
                  ))}
                </select>
                {condaError && (
                  <small className="tpm-warning">{condaError}</small>
                )}
              </Field>
            </>
          ) : (
            <Field label="TensorBoard Python 路径" full>
              <input
                className="tb-input"
                value={form.tbPythonPath}
                onChange={(event) =>
                  setForm({ ...form, tbPythonPath: event.target.value })
                }
                placeholder="例如：/opt/venv/bin/python"
              />
            </Field>
          )}
        </div>
      </Modal>
    </>
  );
}

function TaskFormModal({
  task,
  servers,
  onClose,
  onSaved,
}: {
  task: Task | null;
  servers: ServerItem[];
  onClose: () => void;
  onSaved: (task: Task, messageText: string) => void;
}) {
  const [form, setForm] = useState<TaskForm>(
    task
      ? {
          name: task.name,
          serverId: task.serverId,
          configSource: task.configSource,
          inlineYaml: task.inlineYaml,
          remoteYamlPath: task.remoteYamlPath,
          pythonCommand: task.pythonCommand,
          reportIntervalSeconds: task.reportIntervalSeconds,
          enabled: task.enabled,
          showInTabs: task.showInTabs,
          tbExtraParams: task.tbExtraParams ?? [],
          tbDefaultParams: task.tbDefaultParams ?? "",
        }
      : { ...EMPTY_TASK, serverId: servers[0]?.id ?? "" },
  );
  const [mode, setMode] = useState<"form" | "yaml" | "remote_yaml">(
    task
      ? task.configSource === "remote_yaml"
        ? "remote_yaml"
        : "yaml"
      : "form",
  );
  const [visual, setVisual] = useState<VisualConfig>(EMPTY_VISUAL_CONFIG);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [remoteEditor, setRemoteEditor] = useState<{
    path: string;
    content: string;
  } | null>(null);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [remoteNotice, setRemoteNotice] = useState("");
  function updateVisual<K extends keyof VisualConfig>(
    key: K,
    value: VisualConfig[K],
  ) {
    setVisual({ ...visual, [key]: value });
  }
  function updateGroup(id: string, patch: Partial<VisualGroup>) {
    setVisual({
      ...visual,
      groups: visual.groups.map((group) =>
        group.id === id ? { ...group, ...patch } : group,
      ),
    });
  }
  function addGroup() {
    setVisual({
      ...visual,
      groups: [
        ...visual.groups,
        {
          id: crypto.randomUUID(),
          name: "",
          pattern: "*",
          targetStep: "1000000",
          totalRuns: "",
          includeUnmatchedChildren: false,
          parentId: "",
        },
      ],
    });
  }
  function removeGroup(id: string) {
    setVisual({
      ...visual,
      groups: visual.groups
        .filter((group) => group.id !== id)
        .map((group) =>
          group.parentId === id ? { ...group, parentId: "" } : group,
        ),
    });
  }
  function updateTbExtraParams(index: number, patch: Partial<TbUrlParamGroup>) {
    setForm({
      ...form,
      tbExtraParams: form.tbExtraParams.map((item, current) =>
        current === index ? { ...item, ...patch } : item,
      ),
    });
  }
  async function editRemoteYaml() {
    const path = form.remoteYamlPath.trim();
    if (!form.serverId || !path) {
      setError("请先选择服务器并输入远程 YAML 路径");
      return;
    }
    setRemoteBusy(true);
    setError("");
    setRemoteNotice("");
    try {
      const result = await apiPost<{ content: string }>(
        `${API}/servers/${form.serverId}/remote-yaml/read`,
        { path },
      );
      setRemoteEditor({ path, content: result.content });
    } catch (caught) {
      setError(message(caught));
    } finally {
      setRemoteBusy(false);
    }
  }
  async function saveRemoteYaml() {
    if (!remoteEditor) return;
    setRemoteBusy(true);
    setError("");
    try {
      const result = await apiPut<{
        updatedTaskCount: number;
        stoppedSessionCount: number;
      }>(`${API}/servers/${form.serverId}/remote-yaml`, remoteEditor);
      setRemoteEditor(null);
      setRemoteNotice(
        `远程 YAML 已保存；已重置 ${result.updatedTaskCount} 个关联报表${result.stoppedSessionCount ? `，并停止 ${result.stoppedSessionCount} 个 TB 会话` : ""}。`,
      );
    } catch (caught) {
      setError(message(caught));
    } finally {
      setRemoteBusy(false);
    }
  }
  async function save() {
    setSaving(true);
    setError("");
    try {
      const inlineYaml =
        mode === "form" ? visualConfigToYaml(visual) : form.inlineYaml;
      const payload: TaskForm = {
        ...form,
        configSource: mode === "remote_yaml" ? "remote_yaml" : "inline",
        inlineYaml,
        reportIntervalSeconds:
          mode === "form"
            ? Number(visual.reportIntervalSeconds) || 60
            : form.reportIntervalSeconds,
      };
      const result = task
        ? await apiPut<{ task: Task }>(`${API}/tasks/${task.id}`, payload)
        : await apiPost<{ task: Task }>(`${API}/tasks`, payload);
      onSaved(result.task, task ? "报表已更新" : "报表已创建");
    } catch (caught) {
      setError(message(caught));
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <Modal
        title={task ? "编辑报表" : "新建报表"}
        width={880}
        onClose={onClose}
        foot={
          <>
            <button
              className="tb-btn tb-btn-secondary"
              type="button"
              disabled={saving}
              onClick={onClose}
            >
              取消
            </button>
            <button
              className="tb-btn tb-btn-primary"
              type="button"
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? (
                <>
                  <Loader2 size={14} className="spin" /> 保存中
                </>
              ) : (
                "保存"
              )}
            </button>
          </>
        }
      >
        <div className="tb-form-grid">
          {error && <Alert type="error">{error}</Alert>}
          <Field label="报表名称">
            <input
              className="tb-input"
              value={form.name}
              onChange={(event) =>
                setForm({ ...form, name: event.target.value })
              }
            />
          </Field>
          <Field label="服务器">
            <select
              className="tb-select"
              value={form.serverId}
              onChange={(event) =>
                setForm({ ...form, serverId: event.target.value })
              }
            >
              {servers.map((server) => (
                <option key={server.id} value={server.id}>
                  {server.name} ({server.host})
                </option>
              ))}
            </select>
          </Field>
          <Field label="远端 Python 命令">
            <input
              className="tb-input"
              value={form.pythonCommand}
              onChange={(event) =>
                setForm({ ...form, pythonCommand: event.target.value })
              }
            />
          </Field>
          <Field label="报告周期（秒）">
            <input
              className="tb-input"
              type="number"
              min="30"
              value={
                mode === "form"
                  ? visual.reportIntervalSeconds
                  : form.reportIntervalSeconds
              }
              onChange={(event) =>
                mode === "form"
                  ? updateVisual("reportIntervalSeconds", event.target.value)
                  : setForm({
                      ...form,
                      reportIntervalSeconds: Number(event.target.value) || 60,
                    })
              }
            />
          </Field>
          <Field label="显示位置" full>
            <label className="tb-radio-label">
              <input
                type="checkbox"
                checked={form.showInTabs}
                onChange={(event) =>
                  setForm({ ...form, showInTabs: event.target.checked })
                }
              />{" "}
              在“报表”页中显示
            </label>
          </Field>
          <Field label="配置方式" full>
            <div className="tb-radio-group">
              <label className="tb-radio-label">
                <input
                  type="radio"
                  checked={mode === "form"}
                  onChange={() => setMode("form")}
                />{" "}
                表单配置（保存时生成 YAML）
              </label>
              <label className="tb-radio-label">
                <input
                  type="radio"
                  checked={mode === "yaml"}
                  onChange={() => setMode("yaml")}
                />{" "}
                直接编辑 YAML
              </label>
              <label className="tb-radio-label">
                <input
                  type="radio"
                  checked={mode === "remote_yaml"}
                  onChange={() => setMode("remote_yaml")}
                />{" "}
                远程 YAML 文件
              </label>
            </div>
          </Field>
          {mode === "form" ? (
            <>
              <Field label="TB 默认 URL 参数" full>
                <input
                  className="tb-input"
                  value={form.tbDefaultParams}
                  placeholder="?smoothing=0.6#timeseries"
                  onChange={(event) =>
                    setForm({ ...form, tbDefaultParams: event.target.value })
                  }
                />
                <small className="tpm-muted">
                  YAML 的 tb_custom_params
                  可覆盖同名默认参数；分类筛选和同色参数始终由系统覆盖。
                </small>
              </Field>
              <TbUrlParameterGroups
                values={form.tbExtraParams}
                onChange={updateTbExtraParams}
                onAdd={() =>
                  setForm({
                    ...form,
                    tbExtraParams: [
                      ...form.tbExtraParams,
                      { label: "", params: "" },
                    ],
                  })
                }
                onRemove={(index) =>
                  setForm({
                    ...form,
                    tbExtraParams: form.tbExtraParams.filter(
                      (_, current) => current !== index,
                    ),
                  })
                }
              />
              <VisualConfigForm
                config={visual}
                update={updateVisual}
                updateGroup={updateGroup}
                addGroup={addGroup}
                removeGroup={removeGroup}
              />
            </>
          ) : mode === "yaml" ? (
            <Field label="YAML" full>
              <textarea
                className="tb-textarea tpm-yaml"
                rows={18}
                spellCheck={false}
                value={form.inlineYaml}
                onChange={(event) =>
                  setForm({ ...form, inlineYaml: event.target.value })
                }
              />
            </Field>
          ) : (
            <Field label="远程 YAML 文件" full>
              <div className="tpm-remote-yaml-path">
                <input
                  className="tb-input"
                  value={form.remoteYamlPath}
                  onChange={(event) =>
                    setForm({ ...form, remoteYamlPath: event.target.value })
                  }
                  placeholder="例如：/data/configs/monitor.yaml"
                />
                <button
                  className="tb-btn tb-btn-secondary"
                  type="button"
                  disabled={
                    remoteBusy || !form.serverId || !form.remoteYamlPath.trim()
                  }
                  onClick={() => void editRemoteYaml()}
                >
                  {remoteBusy ? <Spin size={14} /> : <Pencil size={14} />}
                  编辑远程 YAML
                </button>
              </div>
              <small className="tpm-muted">
                先输入路径，再在弹窗中读取和修改该服务器上的文件；保存报表后才会将此路径设为当前配置。
              </small>
              {remoteNotice && <Alert type="success">{remoteNotice}</Alert>}
            </Field>
          )}
        </div>
      </Modal>
      {remoteEditor && (
        <Modal
          title={`编辑远程 YAML：${remoteEditor.path}`}
          width={780}
          onClose={() => !remoteBusy && setRemoteEditor(null)}
          foot={
            <>
              <button
                className="tb-btn tb-btn-secondary"
                type="button"
                disabled={remoteBusy}
                onClick={() => setRemoteEditor(null)}
              >
                取消
              </button>
              <button
                className="tb-btn tb-btn-primary"
                type="button"
                disabled={remoteBusy}
                onClick={() => void saveRemoteYaml()}
              >
                {remoteBusy ? (
                  <>
                    <Spin size={14} /> 保存中
                  </>
                ) : (
                  "保存到远端"
                )}
              </button>
            </>
          }
        >
          <textarea
            className="tb-textarea tpm-yaml"
            rows={24}
            spellCheck={false}
            value={remoteEditor.content}
            onChange={(event) =>
              setRemoteEditor({ ...remoteEditor, content: event.target.value })
            }
          />
        </Modal>
      )}
    </>
  );
}

function TbUrlParameterGroups({
  values,
  onChange,
  onAdd,
  onRemove,
}: {
  values: TbUrlParamGroup[];
  onChange: (index: number, patch: Partial<TbUrlParamGroup>) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
}) {
  return (
    <Field label="TensorBoard URL 参数组" full>
      <div className="tpm-url-params">
        <small className="tpm-muted">
          仅适用于表单配置。可保存命名参数组，例如
          ?tagFilter=d4rl&smoothing=0.79#timeseries。
        </small>
        {values.map((item, index) => (
          <div key={index}>
            <input
              className="tb-input"
              value={item.label}
              placeholder="名称"
              onChange={(event) =>
                onChange(index, { label: event.target.value })
              }
            />
            <input
              className="tb-input"
              value={item.params}
              placeholder="?smoothing=0.6#timeseries"
              onChange={(event) =>
                onChange(index, { params: event.target.value })
              }
            />
            <button
              className="tb-btn tb-btn-sm tb-btn-ghost tpm-danger"
              type="button"
              title="删除参数组"
              onClick={() => onRemove(index)}
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
        <button
          className="tb-btn tb-btn-sm tb-btn-secondary"
          type="button"
          onClick={onAdd}
        >
          <Plus size={12} /> 添加参数组
        </button>
      </div>
    </Field>
  );
}

function VisualConfigForm({
  config,
  update,
  updateGroup,
  addGroup,
  removeGroup,
}: {
  config: VisualConfig;
  update: <K extends keyof VisualConfig>(
    key: K,
    value: VisualConfig[K],
  ) => void;
  updateGroup: (id: string, patch: Partial<VisualGroup>) => void;
  addGroup: () => void;
  removeGroup: (id: string) => void;
}) {
  return (
    <>
      <Field label="TensorBoard 日志根目录" full>
        <input
          className="tb-input"
          value={config.tensorboardRoot}
          onChange={(event) => update("tensorboardRoot", event.target.value)}
          placeholder="例如：/data/experiments/tensorboard"
        />
      </Field>
      <Field label="进度 Tag">
        <input
          className="tb-input"
          value={config.progressTag}
          onChange={(event) => update("progressTag", event.target.value)}
        />
      </Field>
      <Field label="进度取值">
        <select
          className="tb-select"
          value={config.progressMode}
          onChange={(event) =>
            update(
              "progressMode",
              event.target.value as VisualConfig["progressMode"],
            )
          }
        >
          <option value="event_step">事件 step</option>
          <option value="scalar_value">标量值</option>
        </select>
      </Field>
      <Field label="尾读字节数">
        <input
          className="tb-input"
          type="number"
          value={config.tailBytes}
          onChange={(event) => update("tailBytes", event.target.value)}
        />
      </Field>
      <Field label="最近报表数">
        <input
          className="tb-input"
          type="number"
          value={config.rateReportCount}
          onChange={(event) => update("rateReportCount", event.target.value)}
        />
      </Field>
      <Field label="停滞阈值（秒）">
        <input
          className="tb-input"
          type="number"
          value={config.staleAfterSeconds}
          onChange={(event) => update("staleAfterSeconds", event.target.value)}
        />
      </Field>
      <Field label="全局并发槽位">
        <input
          className="tb-input"
          type="number"
          value={config.overallConcurrency}
          onChange={(event) => update("overallConcurrency", event.target.value)}
        />
      </Field>
      <div className="tpm-group-editor tb-full-col">
        <div className="tpm-group-editor-head">
          <div>
            <strong>实验分类</strong>
            <p>通过“父分类”建立嵌套关系；每行都有完整配置。</p>
          </div>
          <button
            className="tb-btn tb-btn-secondary tb-btn-sm"
            type="button"
            onClick={addGroup}
          >
            <Plus size={12} /> 添加分类
          </button>
        </div>
        {config.groups.map((group, index) => (
          <div className="tpm-group-form" key={group.id}>
            <span className="tpm-group-number">{index + 1}</span>
            <input
              className="tb-input"
              placeholder="名称"
              value={group.name}
              onChange={(event) =>
                updateGroup(group.id, { name: event.target.value })
              }
            />
            <input
              className="tb-input"
              placeholder="匹配模式，如 abc-*"
              value={group.pattern}
              onChange={(event) =>
                updateGroup(group.id, { pattern: event.target.value })
              }
            />
            <input
              className="tb-input"
              type="number"
              placeholder="目标步数"
              value={group.targetStep}
              onChange={(event) =>
                updateGroup(group.id, { targetStep: event.target.value })
              }
            />
            <input
              className="tb-input"
              type="number"
              placeholder="总实验数（可空）"
              value={group.totalRuns}
              onChange={(event) =>
                updateGroup(group.id, { totalRuns: event.target.value })
              }
            />
            <select
              className="tb-select"
              value={group.parentId}
              onChange={(event) =>
                updateGroup(group.id, { parentId: event.target.value })
              }
            >
              <option value="">根分类</option>
              {config.groups
                .filter((candidate) => candidate.id !== group.id)
                .map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.name || "未命名分类"}
                  </option>
              ))}
            </select>
            <label
              className="tb-radio-label tpm-parent-unmatched-toggle"
              title="仅在该分类存在子群时生效"
            >
              <input
                type="checkbox"
                checked={group.includeUnmatchedChildren}
                onChange={(event) =>
                  updateGroup(group.id, {
                    includeUnmatchedChildren: event.target.checked,
                  })
                }
              />
              子群未命中时纳入父群
            </label>
            <button
              className="tb-btn tb-btn-sm tb-btn-ghost tpm-danger"
              type="button"
              disabled={config.groups.length === 1}
              onClick={() => removeGroup(group.id)}
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
      </div>
    </>
  );
}

function ReportPanel({
  task,
  report,
  tbAvailable,
  tbRunning,
  onError,
}: {
  task: Task;
  report: Report | null;
  tbAvailable: boolean;
  tbRunning: boolean;
  onError: (value: string) => void;
}) {
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [detailMode, setDetailMode] = useState<
    "running" | "group" | "abnormal"
  >("running");
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  useEffect(() => {
    const roots =
      report?.summary.groups
        .filter((group) => groupDepth(group) === 0)
        .map((group) => group.name) ?? [];
    setExpandedGroups(new Set(roots));
    setDetailMode("running");
    setSelectedGroup(null);
  }, [report?.reportId, task.id]);
  if (!report)
    return (
      <div className="tpm-report-empty">
        <Clock3 size={24} />「{task.name}」尚无报表，请在上方点击刷新。
      </div>
    );
  const summary = report.summary;
  const visibleGroups = summary.groups.filter((group) =>
    groupVisible(group, expandedGroups),
  );
  const selectedGroupLabel = summary.groups.find(
    (group) => group.name === selectedGroup,
  )?.name;
  const selectedGroupRuns = selectedGroup
    ? report.runs.filter(
        (run) =>
          run.groupName === selectedGroup ||
          run.groupName?.startsWith(`${selectedGroup}/`),
      )
    : [];
  const selectedGroupAbnormalRuns = selectedGroupRuns.filter(
    (run) => run.status === "stalled" || run.status === "waiting",
  );
  const shownRuns =
    detailMode === "running"
      ? report.runs.filter((run) => run.status === "running")
      : detailMode === "group"
        ? selectedGroupRuns
        : selectedGroupAbnormalRuns;
  const detailDescription =
    detailMode === "running"
      ? "仅显示当前运行中的实验。"
      : detailMode === "abnormal"
        ? selectedGroupLabel
          ? `显示分类「${selectedGroupLabel}」及其子群中的停滞或待 tag 实验。`
          : "请点击上方分类后查看异常实验。"
        : selectedGroupLabel
          ? `显示分类「${selectedGroupLabel}」及其子群的全部实验。`
          : "请点击上方分类进行筛选。";
  const detailEmptyTitle =
    detailMode === "running"
      ? "当前没有运行中的实验"
      : detailMode === "abnormal"
        ? "该分类下没有异常实验"
        : "请选择一个分类";
  const detailEmptyHint =
    detailMode !== "running" && selectedGroup
      ? "该分类下暂未发现符合条件的实验"
      : undefined;
  function toggleGroup(group: Group) {
    setExpandedGroups((previous) => {
      const next = new Set(previous);
      next.has(group.name) ? next.delete(group.name) : next.add(group.name);
      return next;
    });
  }
  function selectGroup(group: Group) {
    setSelectedGroup(group.name);
    setDetailMode("group");
  }
  async function openTb(group: Group) {
    try {
      const result = await apiGet<{ url: string }>(
        `${API}/tasks/${task.id}/tb-url?group=${encodeURIComponent(group.name)}`,
      );
      window.open(result.url, "_blank", "noopener,noreferrer");
    } catch (caught) {
      onError(message(caught));
    }
  }
  const rootGroups = summary.groups.filter((group) => groupDepth(group) === 0);
  const configuredTotals = rootGroups.map((group) => group.totalRuns);
  const totalRuns =
    configuredTotals.length && configuredTotals.every((value) => value !== null)
      ? configuredTotals.reduce((total, value) => total + (value ?? 0), 0)
      : null;
  const completedRuns = summary.counts.completed ?? 0;
  const overallCompletion = estimatedCompletion(
    report.reportedAt,
    summary.overallEtaSeconds,
  );
  const metrics = (
    <div className="tpm-metrics">
      <Metric
        label="整体剩余时间"
        value={seconds(summary.overallEtaSeconds)}
        note={
          summary.overallEtaReason ||
          summary.overallEtaMethod ||
          "按配置并发槽位模拟"
        }
        detail={`预计完成：${overallCompletion}`}
        icon={<Clock3 />}
      />
      <Metric
        label="进度"
        value={`${completedRuns} / ${totalRuns ?? "—"}`}
        note={
          totalRuns === null
            ? "请配置根分类 total_runs"
            : `${totalRuns ? ((completedRuns / totalRuns) * 100).toFixed(1) : 0}% 已完成`
        }
        icon={<BarChart3 />}
      />
      <Metric
        label="运行中"
        value={String(summary.counts.running ?? 0)}
        note="近期有进度更新"
        icon={<Activity />}
      />
      <Metric
        label="停滞/待定"
        value={String(
          (summary.counts.stalled ?? 0) + (summary.counts.waiting ?? 0),
        )}
        note="红色表示文件长时间未修改"
        icon={<TriangleAlert />}
      />
    </div>
  );
  return (
    <div className="tpm-report">
      <div className="tpm-report-title">
        <div>
          <h2>「{task.name}」进度报表</h2>
          <p>
            {report.success
              ? `生成于 ${stamp(report.reportedAt)}`
              : `报表失败：${report.error}`}
          </p>
        </div>
        <span className="tb-code">
          {task.configSource === "remote_yaml"
            ? task.remoteYamlPath
            : "页面 YAML"}
        </span>
      </div>
      {metrics}
      <section className="tpm-section">
        <div className="tpm-section-title">
          <div>
            <h3>分类工期</h3>
            <p>展开父群查看子群；点击分类筛选实验明细。</p>
          </div>
        </div>
        <div className="tb-table-wrap">
          <table className="tb-table">
            <thead>
              <tr>
                <th>分类</th>
                <th>完成 / 运行 / 待开始</th>
                <th>单次时长</th>
                <th>串行剩余时间</th>
                {tbAvailable && <th>TensorBoard</th>}
              </tr>
            </thead>
            <tbody>
              {visibleGroups.map((group) => {
                const hasChildren = summary.groups.some((candidate) =>
                  directChild(candidate, group),
                );
                const expanded = expandedGroups.has(group.name);
                const durationNote =
                  group.durationSource === "running_estimate"
                    ? "运行中预估"
                    : group.durationSource === "completed"
                      ? "已完成历史"
                      : "";
                return (
                  <tr
                    key={group.name}
                    className={
                      selectedGroup === group.name ? "tpm-selected-group" : ""
                    }
                  >
                    <td>
                      <div
                        className="tpm-group-cell"
                        style={{ paddingLeft: groupDepth(group) * 22 }}
                      >
                        <button
                          className="tpm-tree-toggle"
                          type="button"
                          disabled={!hasChildren}
                          onClick={() => hasChildren && toggleGroup(group)}
                          aria-label={expanded ? "收起子群" : "展开子群"}
                        >
                          {hasChildren ? (
                            expanded ? (
                              <ChevronDown size={15} />
                            ) : (
                              <ChevronRight size={15} />
                            )
                          ) : (
                            <span />
                          )}
                        </button>
                        <button
                          className="tpm-group-select"
                          type="button"
                          onClick={() => selectGroup(group)}
                        >
                          <strong>
                            {group.shortName ?? group.name.split("/").at(-1)}
                          </strong>
                          <span>
                            {group.pattern} · 目标 {number(group.targetStep)}
                          </span>
                        </button>
                      </div>
                    </td>
                    <td>
                      {group.completedRuns} / {group.activeRuns} /{" "}
                      {group.queuedRuns ?? "—"}
                    </td>
                    <td>
                      {seconds(group.medianDurationSeconds)}
                      {durationNote && (
                        <div className="tpm-muted">{durationNote}</div>
                      )}
                    </td>
                    <td>
                      {group.etaSeconds === null ? (
                        <span className="tpm-warning">{group.reason}</span>
                      ) : (
                        <>
                          {seconds(group.etaSeconds)}
                          {group.reason && (
                            <div className="tpm-warning">{group.reason}</div>
                          )}
                        </>
                      )}
                    </td>
                    {tbAvailable && (
                      <td>
                        <button
                          className="tb-btn tb-btn-sm tb-btn-secondary"
                          type="button"
                          disabled={!tbRunning}
                          title={
                            tbRunning
                              ? "仅查看该分类的曲线"
                              : "请先启动该报表的 TensorBoard 会话"
                          }
                          onClick={() => void openTb(group)}
                        >
                          <ExternalLink size={12} /> TB 查看
                        </button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      <section className="tpm-section">
        <div className="tpm-detail-heading">
          <div>
            <h3>实验明细</h3>
            <p>{detailDescription}</p>
          </div>
          <div className="tpm-detail-tabs">
            <button
              className={detailMode === "running" ? "active" : ""}
              type="button"
              onClick={() => setDetailMode("running")}
            >
              当前运行（{summary.counts.running ?? 0}）
            </button>
            <button
              className={detailMode === "group" ? "active" : ""}
              type="button"
              onClick={() => setDetailMode("group")}
              disabled={!selectedGroup}
            >
              分类筛选{selectedGroup ? `：${selectedGroup}` : ""}
            </button>
            <button
              className={detailMode === "abnormal" ? "active" : ""}
              type="button"
              onClick={() => setDetailMode("abnormal")}
              disabled={!selectedGroup}
            >
              分类异常
              {selectedGroup ? `（${selectedGroupAbnormalRuns.length}）` : ""}
            </button>
          </div>
        </div>
        {shownRuns.length === 0 ? (
          <EmptyState
            icon={<Activity size={26} />}
            title={detailEmptyTitle}
            hint={detailEmptyHint}
          />
        ) : (
          <RunTable runs={shownRuns} />
        )}
        {summary.remoteErrors.length > 0 && (
          <Alert type="error">
            部分 event 文件读取失败：
            {summary.remoteErrors
              .map((item) => `${item.path}: ${item.error}`)
              .join("；")}
          </Alert>
        )}
      </section>
    </div>
  );
}

function RunTable({ runs }: { runs: Run[] }) {
  return (
    <div className="tb-table-wrap">
      <table className="tb-table">
        <thead>
          <tr>
            <th>Run</th>
            <th>分类 / 状态</th>
            <th>进度 / 剩余时间</th>
            <th>速率</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const percent = progressPercent(run);
            const remaining =
              run.error || `剩余时间 ${seconds(run.etaSeconds)}`;
            return (
              <tr key={run.runKey}>
                <td>
                  <span className="tb-code">{run.relativePath}</span>
                </td>
                <td>
                  <StatusBadge status={run.status} />
                  <div className="tpm-muted">{run.groupName ?? "未匹配"}</div>
                  <div className="tpm-muted">
                    {run.startedAt
                      ? `开始于 ${stamp(new Date(run.startedAt * 1000).toISOString())}`
                      : "开始时间未知"}
                  </div>
                </td>
                <td className="tpm-progress-cell">
                  <div className="tpm-progress-label">
                    <span>
                      {run.progress === null
                        ? "进度未知"
                        : `${number(run.progress)} / ${number(run.targetStep)}`}
                    </span>
                    <span
                      className={
                        run.status === "stalled"
                          ? "tpm-stalled-note"
                          : "tpm-muted"
                      }
                    >
                      {remaining}
                    </span>
                  </div>
                  <div
                    className="tpm-progress-track"
                    role="progressbar"
                    aria-label={`${run.relativePath} 进度`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={percent ?? undefined}
                  >
                    <span
                      className={`tpm-progress-fill ${run.status}`}
                      style={{ width: `${percent ?? 0}%` }}
                    />
                  </div>
                </td>
                <td>
                  {run.ratePerSecond === null
                    ? "—"
                    : `${number(run.ratePerSecond)}/s`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function groupDepth(group: Group) {
  return group.depth ?? Math.max(0, group.name.split("/").length - 1);
}
function directChild(candidate: Group, parent: Group) {
  return (
    groupDepth(candidate) === groupDepth(parent) + 1 &&
    candidate.name.startsWith(`${parent.name}/`)
  );
}
function groupVisible(group: Group, expanded: Set<string>) {
  const parts = group.name.split("/");
  return parts
    .slice(0, -1)
    .every((_, index) => expanded.has(parts.slice(0, index + 1).join("/")));
}
function progressPercent(run: Run) {
  return run.progress === null || run.targetStep === null || run.targetStep <= 0
    ? null
    : Math.max(0, Math.min(100, (run.progress / run.targetStep) * 100));
}

function visualConfigToYaml(config: VisualConfig) {
  const byId = new Map(
    config.groups.map((group) => [
      group.id,
      { ...group, children: [] as VisualGroup[] },
    ]),
  );
  const roots: Array<VisualGroup & { children: VisualGroup[] }> = [];
  for (const group of byId.values()) {
    if (!group.name.trim() || !group.pattern.trim() || !group.targetStep.trim())
      throw new Error("每个实验分类都需要名称、匹配模式和目标步数");
    if (!group.parentId) roots.push(group);
    else {
      const parent = byId.get(group.parentId);
      if (!parent) throw new Error("分类的父分类不存在");
      parent.children.push(group);
    }
  }
  const seen = new Set<string>();
  const renderGroup = (
    group: VisualGroup & { children?: VisualGroup[] },
    indent: string,
  ): string[] => {
    if (seen.has(group.id)) throw new Error("分类层级存在循环，请调整父分类");
    seen.add(group.id);
    const lines = [
      `${indent}- name: ${JSON.stringify(group.name.trim())}`,
      `${indent}  pattern: ${JSON.stringify(group.pattern.trim())}`,
      `${indent}  target_step: ${Number(group.targetStep)}`,
    ];
    lines.push(
      group.totalRuns.trim()
        ? `${indent}  total_runs: ${Number(group.totalRuns)}`
        : `${indent}  total_runs:`,
    );
    lines.push(
      `${indent}  include_unmatched_children: ${group.includeUnmatchedChildren}`,
    );
    if (!group.children?.length) lines.push(`${indent}  children: []`);
    else {
      lines.push(`${indent}  children:`);
      group.children.forEach((child) =>
        lines.push(...renderGroup(child, `${indent}    `)),
      );
    }
    seen.delete(group.id);
    return lines;
  };
  if (!roots.length) throw new Error("至少需要一个根分类");
  return [
    `tensorboard_root: ${JSON.stringify(config.tensorboardRoot.trim())}`,
    `progress_tag: ${JSON.stringify(config.progressTag.trim())}`,
    `progress_mode: ${config.progressMode}`,
    `tail_bytes: ${Number(config.tailBytes)}`,
    `report_interval_seconds: ${Number(config.reportIntervalSeconds)}`,
    `rate_report_count: ${Number(config.rateReportCount)}`,
    `stale_after_seconds: ${Number(config.staleAfterSeconds)}`,
    `overall_concurrency: ${Number(config.overallConcurrency)}`,
    'tb_custom_params: ""',
    "groups:",
    ...roots.flatMap((group) => renderGroup(group, "  ")),
    "",
  ].join("\n");
}

function Metric({
  label,
  value,
  note,
  detail,
  icon,
}: {
  label: string;
  value: string;
  note: string;
  detail?: string;
  icon: ReactNode;
}) {
  return (
    <div className="tpm-metric">
      <span>
        {icon} {label}
      </span>
      <strong>{value}</strong>
      <small>
        {note}
        {detail && (
          <>
            <br />
            {detail}
          </>
        )}
      </small>
    </div>
  );
}
function StatusBadge({ status }: { status: string }) {
  const labels: Record<string, string> = {
    running: "运行中",
    completed: "完成",
    stalled: "停滞",
    waiting: "待 tag",
    unmatched: "未匹配",
  };
  const colors: Record<string, "green" | "red" | "blue" | "amber" | "default"> =
    {
      running: "blue",
      completed: "green",
      stalled: "red",
      waiting: "amber",
      unmatched: "default",
    };
  return (
    <Badge color={colors[status] ?? "default"}>
      {labels[status] ?? status}
    </Badge>
  );
}
function relative(value: string | null) {
  if (!value) return "从未";
  const delta = Math.max(0, (Date.now() - new Date(value).valueOf()) / 1000);
  return delta < 60
    ? `${Math.floor(delta)} 秒前`
    : delta < 3600
      ? `${Math.floor(delta / 60)} 分钟前`
      : `${Math.floor(delta / 3600)} 小时前`;
}
function number(value: number | null | undefined) {
  return value === null || value === undefined
    ? "—"
    : Number.isInteger(value)
      ? String(value)
      : value.toFixed(2);
}
function seconds(value: number | null | undefined) {
  if (value === null || value === undefined) return "不可估算";
  if (value < 60) return `${Math.round(value)} 秒`;
  if (value < 3600) return `${(value / 60).toFixed(1)} 分`;
  if (value < 86400) return `${(value / 3600).toFixed(1)} 小时`;
  return `${(value / 86400).toFixed(1)} 天`;
}
function estimatedCompletion(
  reportedAt: string,
  etaSeconds: number | null | undefined,
) {
  if (etaSeconds === null || etaSeconds === undefined) return "不可估算";
  const generated = new Date(reportedAt);
  return Number.isNaN(generated.valueOf())
    ? "不可估算"
    : stamp(new Date(generated.valueOf() + etaSeconds * 1000).toISOString());
}
function stamp(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
function message(error: unknown) {
  return error instanceof ApiError
    ? error.message
    : error instanceof Error
      ? error.message
      : "请求失败";
}
