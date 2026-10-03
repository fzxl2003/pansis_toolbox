import "./style.css";
import { type FormEvent, type ReactNode, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Globe2,
  HeartPulse,
  ListFilter,
  Plus,
  Radar,
  RefreshCw,
  Server,
  Settings2,
} from "lucide-react";
import {
  ApiError,
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
} from "../../../frontend/src/api/client";
import {
  API,
  blankTarget,
  type Detail,
  type Service,
  type Target,
  type View,
} from "./types";
import { Alert, Metric, Panel, RunRow } from "./components/shared";
import { HealthSettings } from "./components/Health";
import { ServicesByTarget, ServiceModal } from "./components/Services";
import { TargetModal, TargetTable } from "./components/Targets";
import { SiteModal } from "./components/Site";

export default function ServiceNavigatorTool() {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [siteForm, setSiteForm] = useState({
    title: "",
    slug: "",
    description: "",
  });
  const [view, setView] = useState<View>("overview");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [target, setTarget] = useState<Target | null | false>(false);
  const [targetForm, setTargetForm] = useState(blankTarget);
  const [service, setService] = useState<Service | null>(null);
  const [siteOpen, setSiteOpen] = useState(false);
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
