import "./style.css";
import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  Activity,
  Globe2,
  HeartPulse,
  ListFilter,
  Pencil,
  Plus,
  Radar,
  RefreshCw,
  Server,
  Settings2,
  Trash2,
  X,
} from "lucide-react";
import {
  ApiError,
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
} from "../../../frontend/src/api/client";
import { LoginPanel } from "../../../frontend/src/components/LoginPanel";

const API = "/api/tools/service-navigator";
type View = "overview" | "targets" | "services" | "health" | "scans";
type Target = {
  id: string;
  label: string;
  address: string;
  customPorts: string;
};
type Service = {
  id: string;
  targetId: string;
  port: number;
  protocol: string;
  state: string;
  serviceType: "http" | "port";
  serviceName: string;
  product: string;
  version: string;
  extraInfo: string;
  resolvedAddresses: string[];
  httpTitle: string;
  detectedUrl: string;
  displayName: string;
  description: string;
  navigationUrl: string;
  connectionCommand: string;
  faviconUrl: string;
  healthEnabled: boolean;
  healthUrl: string;
  healthStatus: "healthy" | "unhealthy" | "unknown";
};
type Run = {
  id: string;
  targetId: string | null;
  status: string;
  requestedAt: string;
  error: string;
  summary: {
    targetCount?: number;
    completedTargetCount?: number;
    successCount?: number;
  };
};
type Site = {
  title: string;
  slug: string;
  description: string;
  visibility: "public" | "private";
};
type Detail = {
  site: Site;
  targets: Target[];
  services: Service[];
  runs: Run[];
};
const blankTarget = { label: "", address: "", customPorts: "" };

export default function ServiceNavigatorTool() {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [view, setView] = useState<View>("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [target, setTarget] = useState<Target | null | false>(false);
  const [targetForm, setTargetForm] = useState(blankTarget);
  const [service, setService] = useState<Service | null>(null);
  const [siteOpen, setSiteOpen] = useState(false);
  const [siteForm, setSiteForm] = useState({
    title: "",
    slug: "",
    description: "",
  });
  const activeRun = detail?.runs.find(
    (run) => run.status === "queued" || run.status === "running",
  );
  const stats = useMemo(
    () => ({
      online:
        detail?.services.filter((item) => item.state === "online").length || 0,
      count: detail?.services.length || 0,
    }),
    [detail],
  );
  const showError = (reason: unknown) =>
    setError(
      reason instanceof ApiError || reason instanceof Error
        ? reason.message
        : "操作失败",
    );
  const load = async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const result = await apiGet<{ site: Detail | null }>(`${API}/site`);
      setDetail(result.site);
      if (result.site) setSiteForm(result.site.site);
    } catch (reason) {
      showError(reason);
    } finally {
      if (!quiet) setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    if (!activeRun) return;
    const timer = window.setInterval(() => void load(true), 1500);
    return () => window.clearInterval(timer);
  }, [activeRun?.id]);
  const scan = async (targetId?: string) => {
    try {
      await apiPost(
        targetId ? `${API}/targets/${targetId}/scans` : `${API}/scans`,
        {},
      );
      setNotice("扫描任务已加入队列");
      await load(true);
    } catch (reason) {
      showError(reason);
    }
  };
  const saveTarget = async (event: FormEvent) => {
    event.preventDefault();
    try {
      if (target) await apiPut(`${API}/targets/${target.id}`, targetForm);
      else await apiPost(`${API}/targets`, targetForm);
      setTarget(false);
      await load(true);
    } catch (reason) {
      showError(reason);
    }
  };
  const saveService = async (event: FormEvent) => {
    event.preventDefault();
    if (!service) return;
    try {
      await apiPut(`${API}/services/${service.id}`, service);
      setService(null);
      await load(true);
    } catch (reason) {
      showError(reason);
    }
  };
  const saveSite = async (event: FormEvent) => {
    event.preventDefault();
    try {
      if (detail) await apiPut(`${API}/site`, siteForm);
      else await apiPost(`${API}/site`, siteForm);
      setSiteOpen(false);
      await load(true);
    } catch (reason) {
      showError(reason);
    }
  };
  if (loading)
    return (
      <div className="tool-surface">
        <div className="panel sn-loading">正在加载服务导航…</div>
      </div>
    );
  if (!detail)
    return (
      <div className="tool-surface service-navigator">
        <header className="tool-header">
          <h1>
            <Radar size={24} />
            服务导航
          </h1>
        </header>
        {error && <Alert text={error} error />}
        <section className="sn-empty-site">
          <Radar size={30} />
          <div>
            <h2>创建服务导航站</h2>
            <p>创建后可以扫描 TCP 服务，并编辑公开或私有导航页。</p>
          </div>
          <button className="primary-button" onClick={() => setSiteOpen(true)}>
            <Plus size={16} />
            创建站点
          </button>
        </section>
        {siteOpen && (
          <SiteModal
            form={siteForm}
            setForm={setSiteForm}
            onClose={() => setSiteOpen(false)}
            onSubmit={saveSite}
            create
          />
        )}
      </div>
    );
  const tabs: { id: View; label: string; icon: ReactNode }[] = [
    { id: "overview", label: "概览", icon: <Radar size={16} /> },
    { id: "targets", label: "扫描目标", icon: <Server size={16} /> },
    { id: "services", label: "全部服务", icon: <ListFilter size={16} /> },
    { id: "health", label: "健康设置", icon: <HeartPulse size={16} /> },
    { id: "scans", label: "扫描记录", icon: <RefreshCw size={16} /> },
  ];
  return (
    <div className="tool-surface service-navigator">
      <header className="tool-header sn-header">
        <div>
          <h1>
            <Radar size={24} />
            {detail.site.title}
          </h1>
          <p>{detail.site.description || "扫描、整理并发布服务导航。"}</p>
        </div>
        <div className="sn-header-actions">
          <a
            className="secondary-button"
            target="_blank"
            rel="noreferrer"
            href={`/service-nav/${detail.site.slug}`}
          >
            <Globe2 size={16} />
            查看访客页
          </a>
          <button
            className="secondary-button"
            onClick={() => setSiteOpen(true)}
          >
            <Settings2 size={16} />
            站点设置
          </button>
          <button
            className="primary-button"
            disabled={!detail.targets.length || !!activeRun}
            onClick={() => void scan()}
          >
            <Activity size={16} />
            {activeRun ? "扫描中" : "扫描全部"}
          </button>
        </div>
      </header>
      {error && <Alert text={error} error />}
      {notice && <Alert text={notice} />}
      <nav className="sn-topnav">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={view === tab.id ? "active" : ""}
            onClick={() => setView(tab.id)}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </nav>
      {view === "overview" && (
        <section className="sn-stack">
          <div className="sn-metrics">
            <Metric
              label="扫描目标"
              value={detail.targets.length}
              onClick={() => setView("targets")}
            />
            <Metric
              label="在线服务"
              value={stats.online}
              onClick={() => setView("services")}
            />
            <Metric
              label="全部服务"
              value={stats.count}
              onClick={() => setView("services")}
            />
          </div>
          <section className="sn-panel">
            <h2>访客入口</h2>
            <a
              href={`/service-nav/${detail.site.slug}`}
              target="_blank"
              rel="noreferrer"
            >
              /service-nav/{detail.site.slug}
            </a>
            <p className="sn-muted">
              固定“全部服务”页按扫描目标展示；前台导航布局请进入访客页编辑。
            </p>
          </section>
        </section>
      )}
      {view === "targets" && (
        <section className="sn-panel">
          <Panel
            title="扫描目标"
            action={
              <button
                className="primary-button"
                onClick={() => {
                  setTargetForm(blankTarget);
                  setTarget(null);
                }}
              >
                <Plus size={15} />
                添加目标
              </button>
            }
          />
          <TargetTable
            targets={detail.targets}
            active={activeRun}
            onScan={scan}
            onEdit={(item) => {
              setTargetForm(item);
              setTarget(item);
            }}
            onDelete={(item) => {
              if (confirm(`删除目标「${item.label}」？`))
                void apiDelete(`${API}/targets/${item.id}`)
                  .then(() => load(true))
                  .catch(showError);
            }}
          />
        </section>
      )}
      {view === "services" && (
        <section className="sn-panel">
          <Panel title="全部服务" />
          <ServicesByTarget
            targets={detail.targets}
            services={detail.services}
            onEdit={setService}
          />
        </section>
      )}
      {view === "health" && (
        <HealthSettings
          services={detail.services}
          onChanged={() => void load(true)}
          onError={showError}
        />
      )}
      {view === "scans" && (
        <section className="sn-panel">
          <Panel title="扫描记录" />
          {detail.runs.map((run) => (
            <RunRow key={run.id} run={run} />
          ))}
        </section>
      )}
      {target !== false && (
        <TargetModal
          target={target}
          form={targetForm}
          setForm={setTargetForm}
          onClose={() => setTarget(false)}
          onSubmit={saveTarget}
        />
      )}
      {service && (
        <ServiceModal
          service={service}
          setService={setService}
          onClose={() => setService(null)}
          onSubmit={saveService}
        />
      )}
      {siteOpen && (
        <SiteModal
          form={siteForm}
          setForm={setSiteForm}
          onClose={() => setSiteOpen(false)}
          onSubmit={saveSite}
        />
      )}
    </div>
  );
}

function ServicesByTarget({
  targets,
  services,
  onEdit,
}: {
  targets: Target[];
  services: Service[];
  onEdit: (item: Service) => void;
}) {
  const [health, setHealth] = useState<Service | null>(null);
  const [snapshots, setSnapshots] = useState<
    {
      checkedAt: string;
      status: string;
      statusCode: number | null;
      latencyMs: number | null;
      error: string;
    }[]
  >([]);
  const openHealth = async (item: Service) => {
    try {
      const result = await apiGet<{ snapshots: typeof snapshots }>(
        `${API}/services/${item.id}/health/snapshots`,
      );
      setSnapshots(result.snapshots);
      setHealth(item);
    } catch {
      setSnapshots([]);
      setHealth(item);
    }
  };
  return (
    <>
      {targets.map((target) => (
        <section key={target.id} className="sn-target-service-group">
          <h3>
            {target.label}
            <small>{target.address}</small>
          </h3>
          <table className="sn-table">
            <thead>
              <tr>
                <th>服务</th>
                <th>端点</th>
                <th>状态</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {services
                .filter((item) => item.targetId === target.id)
                .map((item) => (
                  <tr key={item.id}>
                    <td>
                      <strong>
                        {item.displayName || item.httpTitle || item.serviceName}
                      </strong>
                      <small>
                        {item.serviceType === "http" ? "HTTP 服务" : "端口服务"}
                      </small>
                    </td>
                    <td>
                      <code>
                        {item.protocol}/{item.port}
                      </code>
                    </td>
                    <td>
                      <span
                        className={`sn-badge ${item.healthStatus === "healthy" ? "green" : item.healthStatus === "unhealthy" ? "red" : "muted"}`}
                      >
                        {item.healthEnabled
                          ? item.healthStatus === "healthy"
                            ? "健康"
                            : item.healthStatus === "unhealthy"
                              ? "异常"
                              : "尚未检测"
                          : item.state}
                      </span>
                    </td>
                    <td>
                      {item.healthEnabled && (
                        <button
                          className="sn-btn-link"
                          onClick={() => void openHealth(item)}
                        >
                          <HeartPulse size={14} />
                          检测
                        </button>
                      )}
                      <button
                        className="sn-btn-link"
                        onClick={() => onEdit(item)}
                      >
                        <Pencil size={14} />
                        整理
                      </button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      ))}
      {health && (
        <Modal
          title={`${health.displayName || health.serviceName} · 健康度`}
          onClose={() => setHealth(null)}
        >
          <div className="sn-health-dialog">
            <div className="sn-health-dialog-current">
              <span
                className={`sn-badge ${health.healthStatus === "healthy" ? "green" : health.healthStatus === "unhealthy" ? "red" : "amber"}`}
              >
                {health.healthStatus === "healthy"
                  ? "健康"
                  : health.healthStatus === "unhealthy"
                    ? "异常"
                    : "尚未检测"}
              </span>
              <button
                className="secondary-button"
                onClick={() =>
                  void apiPost(
                    `${API}/services/${health.id}/health/check`,
                    {},
                  ).then(() => openHealth(health))
                }
              >
                立即检测
              </button>
            </div>
            <h3>最近快照</h3>
            <div className="sn-snapshot-list">
              {snapshots.length ? (
                snapshots.map((snapshot, index) => (
                  <div key={`${snapshot.checkedAt}-${index}`}>
                    <span className="sn-badge">{snapshot.status}</span>
                    <span>
                      <strong>
                        {new Date(snapshot.checkedAt).toLocaleString()}
                      </strong>
                      <small>
                        {snapshot.statusCode
                          ? `HTTP ${snapshot.statusCode}`
                          : snapshot.error || "无状态码"}{" "}
                        · {snapshot.latencyMs ?? "—"} ms
                      </small>
                    </span>
                  </div>
                ))
              ) : (
                <p className="sn-muted">暂无快照。</p>
              )}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
function HealthSettings({
  services,
  onChanged,
  onError,
}: {
  services: Service[];
  onChanged: () => void;
  onError: (reason: unknown) => void;
}) {
  const [settings, setSettings] = useState<{
    checkIntervalSeconds: number;
    emailRecipients: string[];
    confirmCount: number;
    repeatIntervalSeconds: number;
    maxRepeatCount: number;
  } | null>(null);
  useEffect(() => {
    void apiGet<{ settings: NonNullable<typeof settings> }>(
      `${API}/health/settings`,
    )
      .then((result) => setSettings(result.settings))
      .catch(onError);
  }, [onError]);
  if (!settings) return <section className="sn-panel">加载健康设置…</section>;
  return (
    <section className="sn-panel">
      <Panel
        title="健康度告警设置"
        subtitle={`已启用 HTTP 健康检测：${services.filter((item) => item.healthEnabled).length} 个服务`}
        action={
          <button
            className="secondary-button"
            onClick={() =>
              void apiPost(`${API}/health/checks`, {})
                .then(onChanged)
                .catch(onError)
            }
          >
            立即检测全部
          </button>
        }
      />
      <form
        className="sn-form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          void apiPut(`${API}/health/settings`, settings)
            .then(onChanged)
            .catch(onError);
        }}
      >
        <Field label="检测间隔（秒）">
          <input
            type="number"
            min="60"
            max="86400"
            value={settings.checkIntervalSeconds}
            onChange={(event) =>
              setSettings({
                ...settings,
                checkIntervalSeconds: Number(event.target.value),
              })
            }
          />
        </Field>
        <Field label="连续失败确认次数">
          <input
            type="number"
            min="1"
            max="20"
            value={settings.confirmCount}
            onChange={(event) =>
              setSettings({
                ...settings,
                confirmCount: Number(event.target.value),
              })
            }
          />
        </Field>
        <Field label="重复告警冷却（秒）">
          <input
            type="number"
            min="0"
            value={settings.repeatIntervalSeconds}
            onChange={(event) =>
              setSettings({
                ...settings,
                repeatIntervalSeconds: Number(event.target.value),
              })
            }
          />
        </Field>
        <Field label="最大重复告警次数">
          <input
            type="number"
            min="0"
            value={settings.maxRepeatCount}
            onChange={(event) =>
              setSettings({
                ...settings,
                maxRepeatCount: Number(event.target.value),
              })
            }
          />
        </Field>
        <Field label="邮件收件人" full>
          <textarea
            value={settings.emailRecipients.join("\n")}
            onChange={(event) =>
              setSettings({
                ...settings,
                emailRecipients: event.target.value
                  .split(/[\n,]/)
                  .map((value) => value.trim())
                  .filter(Boolean),
              })
            }
          />
        </Field>
        <div className="sn-form-actions">
          <button className="primary-button">保存健康设置</button>
        </div>
      </form>
    </section>
  );
}
function TargetTable({
  targets,
  active,
  onScan,
  onEdit,
  onDelete,
}: {
  targets: Target[];
  active?: Run;
  onScan: (id?: string) => void;
  onEdit: (item: Target) => void;
  onDelete: (item: Target) => void;
}) {
  return (
    <table className="sn-table">
      <thead>
        <tr>
          <th>目标</th>
          <th>地址</th>
          <th>额外端口</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {targets.map((item) => (
          <tr key={item.id}>
            <td>
              <strong>{item.label}</strong>
              {active?.targetId === item.id && <Progress run={active} />}
            </td>
            <td>
              <code>{item.address}</code>
            </td>
            <td>
              <code>{item.customPorts || "无"}</code>
            </td>
            <td>
              <button
                className="sn-btn-link"
                disabled={!!active}
                onClick={() => onScan(item.id)}
              >
                扫描
              </button>
              <button className="sn-icon-button" onClick={() => onEdit(item)}>
                <Pencil size={14} />
              </button>
              <button
                className="sn-icon-button danger"
                onClick={() => onDelete(item)}
              >
                <Trash2 size={14} />
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
function ServiceModal({
  service,
  setService,
  onClose,
  onSubmit,
}: {
  service: Service;
  setService: (value: Service) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const http = service.serviceType === "http";
  return (
    <Modal title="整理服务" onClose={onClose}>
      <form className="sn-form-grid" onSubmit={onSubmit}>
        <Field label="展示名称">
          <input
            value={service.displayName}
            onChange={(event) =>
              setService({ ...service, displayName: event.target.value })
            }
          />
        </Field>
        <Field label="服务类型">
          <select
            value={service.serviceType}
            onChange={(event) =>
              setService({
                ...service,
                serviceType: event.target.value as Service["serviceType"],
              })
            }
          >
            <option value="http">HTTP 服务</option>
            <option value="port">端口服务</option>
          </select>
        </Field>
        {http ? (
          <>
            <Field label="网页导航 URL" full>
              <input
                value={service.navigationUrl}
                onChange={(event) =>
                  setService({ ...service, navigationUrl: event.target.value })
                }
                placeholder={service.detectedUrl}
              />
            </Field>
            <div className="sn-health-row">
              <label className="sn-check">
                <input
                  type="checkbox"
                  checked={service.healthEnabled}
                  onChange={(event) =>
                    setService({
                      ...service,
                      healthEnabled: event.target.checked,
                    })
                  }
                />
                启用 HTTP 健康检测
              </label>
              {service.healthEnabled && (
                <Field label="独立健康检查 URL">
                  <input
                    value={service.healthUrl}
                    onChange={(event) =>
                      setService({ ...service, healthUrl: event.target.value })
                    }
                  />
                </Field>
              )}
            </div>
          </>
        ) : (
          <Field label="连接命令" full>
            <input
              value={service.connectionCommand}
              onChange={(event) =>
                setService({
                  ...service,
                  connectionCommand: event.target.value,
                })
              }
            />
          </Field>
        )}
        <Field label="描述" full>
          <textarea
            value={service.description}
            onChange={(event) =>
              setService({ ...service, description: event.target.value })
            }
          />
        </Field>
        <div className="sn-form-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button">保存</button>
        </div>
      </form>
    </Modal>
  );
}
function TargetModal({
  target,
  form,
  setForm,
  onClose,
  onSubmit,
}: {
  target: Target | null;
  form: typeof blankTarget;
  setForm: (value: typeof blankTarget) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <Modal title={target ? "编辑扫描目标" : "添加扫描目标"} onClose={onClose}>
      <form className="sn-form-grid" onSubmit={onSubmit}>
        <Field label="显示名称">
          <input
            value={form.label}
            onChange={(event) =>
              setForm({ ...form, label: event.target.value })
            }
          />
        </Field>
        <Field label="IP 或域名">
          <input
            required
            value={form.address}
            onChange={(event) =>
              setForm({ ...form, address: event.target.value })
            }
          />
        </Field>
        <Field label="额外端口" full>
          <input
            value={form.customPorts}
            onChange={(event) =>
              setForm({ ...form, customPorts: event.target.value })
            }
            placeholder="22,8000-8100"
          />
          <small>支持逗号和端口区间，不限制数量；端口最大值为 65535。</small>
        </Field>
        <div className="sn-form-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button">保存</button>
        </div>
      </form>
    </Modal>
  );
}
function SiteModal({
  form,
  setForm,
  onClose,
  onSubmit,
  create = false,
}: {
  form: { title: string; slug: string; description: string };
  setForm: (value: {
    title: string;
    slug: string;
    description: string;
  }) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
  create?: boolean;
}) {
  return (
    <Modal title={create ? "创建服务导航站" : "站点设置"} onClose={onClose}>
      <form className="sn-form-grid" onSubmit={onSubmit}>
        <Field label="站点标题">
          <input
            required
            value={form.title}
            onChange={(event) =>
              setForm({ ...form, title: event.target.value })
            }
          />
        </Field>
        <Field label="公开地址 slug">
          <input
            required
            value={form.slug}
            onChange={(event) => setForm({ ...form, slug: event.target.value })}
          />
        </Field>
        <Field label="简介" full>
          <textarea
            value={form.description}
            onChange={(event) =>
              setForm({ ...form, description: event.target.value })
            }
          />
        </Field>
        <div className="sn-form-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button">保存</button>
        </div>
      </form>
    </Modal>
  );
}
function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div
      className="sn-modal-backdrop"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section className="sn-modal">
        <header>
          <h2>{title}</h2>
          <button className="sn-icon-button" onClick={onClose}>
            <X size={17} />
          </button>
        </header>
        <div className="sn-modal-body">{children}</div>
      </section>
    </div>
  );
}
function Field({
  label,
  children,
  full = false,
}: {
  label: string;
  children: ReactNode;
  full?: boolean;
}) {
  return (
    <label className={`sn-field${full ? " full" : ""}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}
function Panel({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <header className="sn-panel-heading">
      <div>
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {action}
    </header>
  );
}
function Metric({
  label,
  value,
  onClick,
}: {
  label: string;
  value: number;
  onClick: () => void;
}) {
  return (
    <button className="sn-metric" onClick={onClick}>
      <span>{label}</span>
      <strong>{value}</strong>
    </button>
  );
}
function Alert({ text, error = false }: { text: string; error?: boolean }) {
  return (
    <div className={`sn-alert ${error ? "error" : "success"}`}>{text}</div>
  );
}
function Progress({ run }: { run: Run }) {
  const total = run.summary.targetCount || 0;
  const done = run.summary.completedTargetCount || 0;
  const percent = total ? Math.round((done * 100) / total) : 0;
  return (
    <span className="sn-progress">
      <span>
        扫描进度 {done}/{total}
      </span>
      <span className="sn-progress-track">
        <i style={{ width: `${percent}%` }} />
      </span>
      <small>{percent}%</small>
    </span>
  );
}
function RunRow({ run }: { run: Run }) {
  return (
    <div className="sn-run-list">
      <div>
        <span className="sn-badge">{run.status}</span>
        <span>{new Date(run.requestedAt).toLocaleString()}</span>
        {(run.status === "running" || run.status === "queued") && (
          <Progress run={run} />
        )}
        {run.error && <small className="sn-error-text">{run.error}</small>}
      </div>
    </div>
  );
}
