import "./style.css";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  BarChart3,
  Check,
  Clipboard,
  Clock,
  Crosshair,
  Eye,
  Globe2,
  ExternalLink,
  FileCode2,
  FolderOpen,
  Gauge,
  Link,
  Pencil,
  Plus,
  RefreshCw,
  QrCode,
  Search,
  Send,
  Server,
  Settings2,
  ShieldAlert,
  Trash2,
  Zap,
} from "lucide-react";

import {
  ApiError,
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
} from "../../../frontend/src/api/client";
import {
  Alert,
  Badge,
  EmptyState,
  Field,
  Modal,
  Spin,
  useConfirm,
} from "./components";

const API = "/api/tools/clash-subscription-manager";
type View = "dashboard" | "nodes" | "profiles" | "rules" | "auto-update";
type ValidationMessage = { level: string; code?: string; message: string };
type Source = {
  id: string;
  name: string;
  url: string;
  userAgent: string;
  refreshSeconds: number;
  enabled: boolean;
  status: string;
  lastSuccessAt?: string;
  lastAttemptAt?: string;
  nextRefreshAt?: string;
  lastError?: string;
};
type Node = {
  id: string;
  stableIdentity: string;
  name: string;
  alias: string;
  displayName: string;
  protocol: string;
  server: string;
  port?: number;
  sources: string[];
  sourceIds: string[];
  supportedOutput: boolean;
  isCustom: boolean;
  lastSeenAt: string;
  country?: string | null;
  countryLabel?: string | null;
  resolvedIp?: string;
  geoError?: string;
  geoCheckedAt?: string | null;
  config: Record<string, unknown>;
  tcp?: {
    reachable: boolean;
    latencyMs?: number;
    error?: string;
    checkedAt?: string;
  };
};
type ProfileSettings = {
  mixedPort?: number;
  allowLan?: boolean;
  mode?: string;
  ipv6?: boolean;
  dns?: unknown;
  ruleProviderOutputMode?: "url" | "inline";
};
type Profile = {
  id: string;
  name: string;
  settings: ProfileSettings;
  ruleSetId?: string;
  ruleSetName?: string | null;
  ruleSetUpdatedAt?: string | null;
  publishedAt?: string;
  publishedStatus: string;
  validation: ValidationMessage[];
  subscriptionToken?: string;
  refreshSeconds: number;
  nextRefreshAt?: string | null;
  updatedAt?: string;
};
type ProfileRefreshRun = {
  id: string;
  profileId: string;
  profileName: string;
  status: string;
  startedAt: string;
  finishedAt?: string;
  durationMs?: number;
  error?: string;
};
type AutoUpdateSettings = {
  sourceRefreshSeconds: number;
  ruleProviderRefreshSeconds: number;
  nodeProbeSeconds: number;
  profileRefreshSeconds: number;
  lastRunAt: Partial<Record<AutoUpdateTaskKey, string | null>>;
};
type ProfilePreview = {
  yaml: string;
  valid: boolean;
  messages: ValidationMessage[];
  buildLog: string[];
};
type RuleSet = {
  id: string;
  name: string;
  rules: unknown[];
  providers: Record<string, unknown>;
  groups: Record<string, unknown>[];
  importMeta?: Record<string, unknown>;
  updatedAt?: string;
};
type DomainTestResult = {
  domain: string;
  matched: boolean;
  target: string;
  rule: string;
  ruleIndex: number | null;
  matches: { index: number; rule: string; target: string }[];
};

type RuleProviderLibraryItem = {
  id: string;
  name: string;
  providerKey: string;
  description: string;
  config: Record<string, unknown>;
  builtin: boolean;
  updatedAt?: string;
};
type RuleGroupDraft = {
  id: string;
  name: string;
  type: string;
  proxies: string[];
  providerIds: string[];
  nodeGroups: string[];
  extra: Record<string, unknown>;
  fallback?: boolean;
};
type NodeGroup = {
  id: string;
  name: string;
  kind: "custom" | "region" | "latency";
  config: Record<string, unknown>;
  members: string[];
  memberCount: number;
  createdAt?: string;
  updatedAt?: string;
};
type RefreshRun = {
  id: string;
  sourceId: string;
  sourceName: string;
  status: string;
  startedAt: string;
  finishedAt?: string;
  durationMs?: number;
  nodesBefore: number;
  nodesAfter: number;
  error?: string;
};
type Dashboard = {
  metrics: Record<string, number>;
  health: Record<string, number>;
  protocolDistribution: { name: string; value: number }[];
  regionDistribution: { name: string; value: number }[];
  sources: Source[];
  profiles: Profile[];
  alerts: { kind?: string; message: string }[];
};
export default function ClashSubscriptionManager() {
  const [view, setView] = useState<View>("dashboard");
  const [rulesTab, setRulesTab] = useState<"rules" | "providers">("providers");
  const [nodesTab, setNodesTab] = useState<"pool" | "groups">("pool");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [ruleSets, setRuleSets] = useState<RuleSet[]>([]);
  const [providerLibrary, setProviderLibrary] = useState<
    RuleProviderLibraryItem[]
  >([]);
  const [nodeGroups, setNodeGroups] = useState<NodeGroup[]>([]);
  const [runs, setRuns] = useState<RefreshRun[]>([]);
  const [autoUpdateSettings, setAutoUpdateSettings] = useState<AutoUpdateSettings | null>(null);
  const [profileLogModal, setProfileLogModal] = useState<Profile | null>(null);
  const [profileRuns, setProfileRuns] = useState<ProfileRefreshRun[]>([]);
  const [sourceModal, setSourceModal] = useState<Source | null | false>(false);
  const [sourceInitialUrl, setSourceInitialUrl] = useState("");
  const [profileModal, setProfileModal] = useState<Profile | null | false>(
    false,
  );
  const [ruleModal, setRuleModal] = useState<{
    mode: "edit" | "import" | "create";
    ruleSet?: RuleSet;
  } | null>(null);
  const [providerModal, setProviderModal] = useState<
    RuleProviderLibraryItem | null | false
  >(false);
  const [providerCopy, setProviderCopy] =
    useState<RuleProviderLibraryItem | null>(null);
  const [nodeGroupModal, setNodeGroupModal] = useState<
    NodeGroup | null | false
  >(false);
  const [addNodeOpen, setAddNodeOpen] = useState(false);
  const [qrImportOpen, setQrImportOpen] = useState(false);
  const [customNodeModal, setCustomNodeModal] = useState<Node | null | false>(
    false,
  );
  const [aliasNode, setAliasNode] = useState<Node | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const { confirm, dialog } = useConfirm();

  async function loadAll() {
    setLoading(true);
    try {
      const [
        dashboardData,
        sourceData,
        nodeData,
        profileData,
        ruleData,
        providerData,
        nodeGroupData,
        runData,
        autoUpdateData,
      ] = await Promise.all([
        apiGet<Dashboard>(`${API}/dashboard`),
        apiGet<{ sources: Source[] }>(`${API}/sources`),
        apiGet<{ nodes: Node[] }>(`${API}/nodes`),
        apiGet<{ profiles: Profile[] }>(`${API}/profiles`),
        apiGet<{ ruleSets: RuleSet[] }>(`${API}/rule-sets`),
        apiGet<{ providers: RuleProviderLibraryItem[] }>(
          `${API}/rule-providers`,
        ),
        apiGet<{ groups: NodeGroup[] }>(`${API}/node-groups`),
        apiGet<{ runs: RefreshRun[] }>(`${API}/refresh-runs?limit=100`),
        apiGet<AutoUpdateSettings>(`${API}/auto-update-settings`),
      ]);
      setDashboard(dashboardData);
      setSources(sourceData.sources);
      setNodes(nodeData.nodes);
      setProfiles(profileData.profiles);
      setRuleSets(ruleData.ruleSets);
      setProviderLibrary(providerData.providers);
      setNodeGroups(nodeGroupData.groups);
      setRuns(runData.runs);
      setAutoUpdateSettings(autoUpdateData);
      setError("");
    } catch (caught) {
      setError(message(caught));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadAll();
  }, []);

  async function action(
    key: string,
    work: () => Promise<unknown>,
    success: string,
    reload = true,
  ) {
    setPending(key);
    setError("");
    try {
      await work();
      setNotice(success);
      if (reload) await loadAll();
      return true;
    } catch (caught) {
      setError(message(caught));
      return false;
    } finally {
      setPending("");
    }
  }

  function removeSource(source: Source) {
    confirm({
      title: "删除订阅源",
      message: (
        <>
          确认删除「{source.name}」及其刷新历史？已发布订阅不会因此被立即覆盖。
        </>
      ),
      onConfirm: async () => {
        await action(
          `source-delete-${source.id}`,
          () => apiDelete(`${API}/sources/${source.id}`),
          "订阅源已删除",
        );
      },
    });
  }
  function removeCustomNode(node: Node) {
    confirm({
      title: "删除自定义节点",
      message: (
        <>
          确认删除「{node.displayName || node.name}
          」？聚合配置中对该节点的选择会失效。
        </>
      ),
      onConfirm: async () => {
        await action(
          `node-delete-${node.id}`,
          () => apiDelete(`${API}/nodes/${node.id}`),
          "自定义节点已删除",
        );
      },
    });
  }
  async function copySubscriptionNode(node: Node) {
    setPending(`node-copy-${node.id}`);
    setError("");
    try {
      const result = await apiPost<{ node: Node }>(
        `${API}/nodes/${node.id}/copy`,
        {},
      );
      setNotice("已复制为自定义节点，可以直接编辑");
      await loadAll();
      setCustomNodeModal(result.node);
    } catch (caught) {
      setError(message(caught));
    } finally {
      setPending("");
    }
  }
  function removeProfile(profile: Profile) {
    confirm({
      title: "删除聚合配置",
      message: <>确认删除「{profile.name}」？对应公开订阅链接将立即失效。</>,
      onConfirm: async () => {
        await action(
          `profile-delete-${profile.id}`,
          () => apiDelete(`${API}/profiles/${profile.id}`),
          "聚合配置已删除",
        );
      },
    });
  }
  function removeRuleSet(ruleSet: RuleSet) {
    confirm({
      title: "删除规则组",
      message: (
        <>确认删除「{ruleSet.name}」？引用它的聚合配置需要重新选择规则组。</>
      ),
      onConfirm: async () => {
        await action(
          `rule-delete-${ruleSet.id}`,
          () => apiDelete(`${API}/rule-sets/${ruleSet.id}`),
          "规则组已删除",
        );
      },
    });
  }
  function removeRuleProvider(provider: RuleProviderLibraryItem) {
    if (provider.builtin) return;
    confirm({
      title: "删除 Rule Provider",
      message: (
        <>
          确认删除「{provider.name}
          」？如果仍被策略组使用，需要先在规则组中取消选择。
        </>
      ),
      onConfirm: async () => {
        await action(
          `provider-delete-${provider.id}`,
          () => apiDelete(`${API}/rule-providers/${provider.id}`),
          "Rule Provider 已删除",
        );
      },
    });
  }
  function copyRuleProvider(provider: RuleProviderLibraryItem) {
    if (String(provider.config.type ?? "") !== "manual") {
      setProviderCopy(provider);
      return;
    }
    void action(
      `provider-copy-${provider.id}`,
      () =>
        apiPost(`${API}/rule-providers/${provider.id}/copy`, {
          mode: "original",
        }),
      "Rule Provider 已复制",
    );
  }
  function removeNodeGroup(group: NodeGroup) {
    confirm({
      title: "删除节点分组",
      message: (
        <>
          确认删除节点分组「{group.name}」？引用它的策略组将失去该分组内的节点。
        </>
      ),
      onConfirm: async () => {
        await action(
          `node-group-delete-${group.id}`,
          () => apiDelete(`${API}/node-groups/${group.id}`),
          "节点分组已删除",
        );
      },
    });
  }
  async function copy(value: string, success: string) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice(success);
    } catch {
      setError("浏览器拒绝了剪贴板访问，请手动复制。");
    }
  }
  const subscriptionUrl = (profile: Profile) =>
    profile.subscriptionToken
      ? `${window.location.origin}/sub/clash/${profile.subscriptionToken}`
      : "";
  const subscriptionDetailUrl = (profile: Profile) =>
    profile.subscriptionToken
      ? `${window.location.origin}/sub/clash/details/${profile.subscriptionToken}`
      : "";

  return (
    <div className="tool-page csm-tool">
      <header className="tool-header">
        <div>
          <h1 className="tool-title">Clash 订阅聚合器</h1>
          <p className="tool-subtitle">
            订阅解析、自定义节点、规则校验与 Clash Meta 安全发布
          </p>
        </div>
        <button
          className="csm-btn csm-btn-secondary"
          type="button"
          disabled={loading}
          onClick={() => void loadAll()}
        >
          <RefreshCw size={14} className={loading ? "csm-spin" : ""} />
          刷新
        </button>
      </header>
      <nav className="csm-topnav" aria-label="工具视图">
        <Tab
          active={view === "dashboard"}
          icon={<Gauge size={14} />}
          onClick={() => setView("dashboard")}
        >
          运营总览
        </Tab>
        <Tab
          active={view === "nodes"}
          icon={<Activity size={14} />}
          onClick={() => setView("nodes")}
        >
          节点
        </Tab>
        <Tab
          active={view === "rules"}
          icon={<FileCode2 size={14} />}
          onClick={() => setView("rules")}
        >
          规则
        </Tab>
        <Tab
          active={view === "profiles"}
          icon={<Send size={14} />}
          onClick={() => setView("profiles")}
        >
          聚合配置
        </Tab>
        <Tab
          active={view === "auto-update"}
          icon={<Settings2 size={14} />}
          onClick={() => setView("auto-update")}
        >
          自动更新
        </Tab>
      </nav>
      <div className="csm-body">
        {error && <Alert type="error">{error}</Alert>}
        {notice && <Alert type="success">{notice}</Alert>}
        {view === "dashboard" && (
          <DashboardView
            data={dashboard}
            runs={runs}
            autoUpdateSettings={autoUpdateSettings}
            loading={loading}
            onNavigate={setView}
          />
        )}
        {view === "nodes" && (
          <div className="csm-stack">
            <nav className="csm-subnav" aria-label="节点二级菜单">
              <button
                type="button"
                className={nodesTab === "pool" ? "active" : ""}
                onClick={() => setNodesTab("pool")}
              >
                <Activity size={14} />
                节点池
              </button>
              <button
                type="button"
                className={nodesTab === "groups" ? "active" : ""}
                onClick={() => setNodesTab("groups")}
              >
                <FolderOpen size={14} />
                节点分组
              </button>
            </nav>
            {nodesTab === "pool" ? (
              <NodesView
                sources={sources}
                nodes={nodes}
                loading={loading}
                pending={pending}
                onNodesChange={(updatedNodes) =>
                  setNodes((current) => {
                    const updatedById = new Map(
                      updatedNodes.map((node) => [node.id, node]),
                    );
                    return current.map(
                      (node) => updatedById.get(node.id) ?? node,
                    );
                  })
                }
                onAdd={() => setAddNodeOpen(true)}
                onEditSource={setSourceModal}
                onRemoveSource={removeSource}
                onRefreshSource={(source) =>
                  void action(
                    `source-refresh-${source.id}`,
                    () => apiPost(`${API}/sources/${source.id}/refresh`, {}),
                    "订阅源刷新完成",
                  )
                }
                onEditCustom={setCustomNodeModal}
                onRemoveCustom={removeCustomNode}
                onCopy={(node) => void copySubscriptionNode(node)}
                onAlias={setAliasNode}
                onProbe={(ids) =>
                  void action(
                    "nodes-probe",
                    () => apiPost(`${API}/nodes/probe`, { nodeIds: ids }),
                    "TCP 探测完成",
                  )
                }
              />
            ) : (
              <NodeGroupsView
                groups={nodeGroups}
                nodes={nodes}
                loading={loading}
                pending={pending}
                onAdd={() => setNodeGroupModal(null)}
                onEdit={setNodeGroupModal}
                onRemove={removeNodeGroup}
              />
            )}
          </div>
        )}
        {view === "profiles" && (
          <ProfilesView
            profiles={profiles}
            loading={loading}
            pending={pending}
            subscriptionUrl={subscriptionUrl}
            subscriptionDetailUrl={subscriptionDetailUrl}
            onAdd={() => setProfileModal(null)}
            onEdit={setProfileModal}
            onRemove={removeProfile}
            onRefresh={(profile) => void action(`profile-refresh-${profile.id}`, () => apiPost(`${API}/profiles/${profile.id}/refresh`, {}), "聚合配置更新完成")}
            onLogs={async (profile) => {
              try {
                const result = await apiGet<{ runs: ProfileRefreshRun[] }>(`${API}/profile-refresh-runs?profileId=${encodeURIComponent(profile.id)}&limit=100`);
                setProfileRuns(result.runs);
                setProfileLogModal(profile);
              } catch (caught) { setError(message(caught)); }
            }}
            onCopy={(profile) =>
              void copy(subscriptionUrl(profile), "订阅链接已复制")
            }
          />
        )}
        {view === "auto-update" && autoUpdateSettings && (
          <AutoUpdateView settings={autoUpdateSettings} onSaved={setAutoUpdateSettings} />
        )}
        {profileLogModal && (
          <ProfileRefreshLogModal profile={profileLogModal} runs={profileRuns} onClose={() => setProfileLogModal(null)} />
        )}
        {view === "rules" && (
          <div className="csm-stack">
            <nav className="csm-subnav" aria-label="规则二级菜单">
              <button
                type="button"
                className={rulesTab === "providers" ? "active" : ""}
                onClick={() => setRulesTab("providers")}
              >
                <FolderOpen size={14} />
                规则源
              </button>
              <button
                type="button"
                className={rulesTab === "rules" ? "active" : ""}
                onClick={() => setRulesTab("rules")}
              >
                <FileCode2 size={14} />
                规则组
              </button>
            </nav>
            {rulesTab === "rules" ? (
              <RulesView
                ruleSets={ruleSets}
                loading={loading}
                onImport={() => setRuleModal({ mode: "import" })}
                onCreate={() => setRuleModal({ mode: "create" })}
                onEdit={(ruleSet) => setRuleModal({ mode: "edit", ruleSet })}
                onRemove={removeRuleSet}
              />
            ) : (
              <RuleProvidersView
                providers={providerLibrary}
                loading={loading}
                pending={pending}
                onAdd={() => setProviderModal(null)}
                onEdit={setProviderModal}
                onCopy={copyRuleProvider}
                onRemove={removeRuleProvider}
              />
            )}
          </div>
        )}
      </div>
      {addNodeOpen && (
        <AddNodeModal
          onClose={() => setAddNodeOpen(false)}
          onAddSource={() => {
            setAddNodeOpen(false);
            setSourceInitialUrl("");
            setSourceModal(null);
          }}
          onAddCustom={() => {
            setAddNodeOpen(false);
            setCustomNodeModal(null);
          }}
          onImportQr={() => {
            setAddNodeOpen(false);
            setQrImportOpen(true);
          }}
        />
      )}
      {qrImportOpen && (
        <QrImportModal
          onClose={() => setQrImportOpen(false)}
          onImport={(url) => {
            setQrImportOpen(false);
            setSourceInitialUrl(url);
            setSourceModal(null);
          }}
        />
      )}
      {sourceModal !== false && (
        <SourceModal
          source={sourceModal}
          initialUrl={sourceInitialUrl}
          onClose={() => setSourceModal(false)}
          onSaved={async (text) => {
            setSourceModal(false);
            setSourceInitialUrl("");
            setNotice(text);
            await loadAll();
          }}
        />
      )}
      {profileModal !== false && (
        <ProfileModal
          profile={profileModal}
          ruleSets={ruleSets}
          onClose={() => setProfileModal(false)}
          onSaved={async (text) => {
            setProfileModal(false);
            setNotice(text);
            await loadAll();
          }}
        />
      )}
      {customNodeModal !== false && (
        <CustomNodeModal
          node={customNodeModal}
          onClose={() => setCustomNodeModal(false)}
          onSaved={async (text) => {
            setCustomNodeModal(false);
            setNotice(text);
            await loadAll();
          }}
        />
      )}
      {aliasNode && (
        <NodeAliasModal
          node={aliasNode}
          onClose={() => setAliasNode(null)}
          onSaved={async () => {
            setAliasNode(null);
            setNotice("节点别名已保存");
            await loadAll();
          }}
        />
      )}
      {ruleModal && (
        <RuleSetModal
          mode={ruleModal.mode}
          ruleSet={ruleModal.ruleSet}
          nodes={nodes}
          providerLibrary={providerLibrary}
          nodeGroups={nodeGroups}
          onClose={() => setRuleModal(null)}
          onSaved={async (text) => {
            setRuleModal(null);
            setNotice(text);
            await loadAll();
          }}
        />
      )}
      {nodeGroupModal !== false && (
        <NodeGroupModal
          group={nodeGroupModal}
          nodes={nodes}
          onNodesChange={(updatedNodes) =>
            setNodes((current) => {
              const updatedById = new Map(
                updatedNodes.map((node) => [node.id, node]),
              );
              return current.map((node) => updatedById.get(node.id) ?? node);
            })
          }
          onClose={() => setNodeGroupModal(false)}
          onSaved={async (text) => {
            setNodeGroupModal(false);
            setNotice(text);
            await loadAll();
          }}
        />
      )}
      {providerModal !== false && (
        <RuleProviderModal
          provider={providerModal}
          onClose={() => setProviderModal(false)}
          onSaved={async (text) => {
            setProviderModal(false);
            setNotice(text);
            await loadAll();
          }}
        />
      )}
      {providerCopy && (
        <RuleProviderCopyModal
          provider={providerCopy}
          onClose={() => setProviderCopy(null)}
          onSaved={async (text) => {
            setProviderCopy(null);
            setNotice(text);
            await loadAll();
          }}
        />
      )}
      {dialog}
    </div>
  );
}

function Tab({
  active,
  icon,
  onClick,
  children,
}: {
  active: boolean;
  icon: React.ReactNode;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button className={active ? "active" : ""} type="button" onClick={onClick}>
      {icon}
      {children}
    </button>
  );
}

function refreshRunStatus(run: RefreshRun) {
  return run.status === "success"
    ? "成功"
    : run.status === "failed"
      ? "失败"
      : "进行中";
}

function runStatusColor(run: RefreshRun) {
  return run.status === "success"
    ? "green"
    : run.status === "failed"
      ? "red"
      : "blue";
}

function RefreshRunLog({ title, runs }: { title: string; runs: RefreshRun[] }) {
  if (!runs.length) return null;
  return (
    <div className="csm-dashboard-log">
      <div className="csm-dashboard-log-title">
        <RefreshCw size={13} />
        <strong>{title}</strong>
      </div>
      <div className="csm-dashboard-log-list">
        {runs.map((run) => (
          <div className="csm-dashboard-log-row" key={run.id}>
            <Badge color={runStatusColor(run)}>{refreshRunStatus(run)}</Badge>
            <span>
              <strong>{run.sourceName}</strong>
              <small>
                节点 {run.nodesBefore} → {run.nodesAfter}
                {run.durationMs == null ? "" : ` · ${run.durationMs} ms`}
                {run.error ? ` · ${run.error}` : ""}
              </small>
            </span>
            <time>{stamp(run.startedAt)}</time>
          </div>
        ))}
      </div>
    </div>
  );
}

function DashboardDetailModal({
  detail,
  data,
  runs,
  onClose,
}: {
  detail: "protocol" | "alerts" | "sources";
  data: Dashboard | null;
  runs: RefreshRun[];
  onClose: () => void;
}) {
  const orderedRuns = [...runs].sort(
    (left, right) =>
      (Date.parse(right.startedAt) || 0) - (Date.parse(left.startedAt) || 0),
  );
  const latestRunBySource = new Map<string, RefreshRun>();
  for (const run of orderedRuns) {
    if (!latestRunBySource.has(run.sourceId))
      latestRunBySource.set(run.sourceId, run);
  }
  const title =
    detail === "protocol"
      ? "节点协议分布"
      : detail === "alerts"
        ? "告警中心"
        : "订阅源健康";
  return (
    <Modal title={title} onClose={onClose} width={640}>
      {detail === "protocol" && (
        <>
          {data?.protocolDistribution.length ? (
            <div className="csm-bars">
              {data.protocolDistribution.map((item) => (
                <div key={item.name}>
                  <code>{item.name}</code>
                  <span>
                    <i
                      style={{
                        width: `${Math.max(5, (item.value / Math.max(...data.protocolDistribution.map((entry) => entry.value))) * 100)}%`,
                      }}
                    />
                  </span>
                  <b>{item.value}</b>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无节点数据" />
          )}
          <RefreshRunLog
            title="节点更新日志"
            runs={orderedRuns.filter((run) => run.status === "success")}
          />
        </>
      )}
      {detail === "alerts" && (
        <>
          {data?.alerts.length ? (
            <div className="csm-alert-list">
              {data.alerts.map((alert, index) => (
                <div key={`${alert.message}-${index}`}>
                  <AlertTriangle size={15} />
                  <span>{alert.message}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState icon={<Check size={28} />} title="没有待处理告警" />
          )}
          <RefreshRunLog
            title="失败刷新日志"
            runs={orderedRuns.filter((run) => run.status === "failed")}
          />
        </>
      )}
      {detail === "sources" && (
        <>
          {data?.sources.length ? (
            <div className="csm-source-health-list">
              {data.sources.map((source) => {
                const run = latestRunBySource.get(source.id);
                return (
                  <div className="csm-source-health-row" key={source.id}>
                    <StatusDot status={source.status} />
                    <span>
                      <strong>{source.name}</strong>
                      <small>
                        {run
                          ? `最近刷新：${refreshRunStatus(run)} · 节点 ${run.nodesBefore} → ${run.nodesAfter}${run.durationMs == null ? "" : ` · ${run.durationMs} ms`}`
                          : "暂无刷新日志"}
                      </small>
                    </span>
                    <time>{run ? stamp(run.startedAt) : "—"}</time>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState title="暂无订阅源" />
          )}
          <RefreshRunLog title="全部刷新日志" runs={orderedRuns} />
        </>
      )}
    </Modal>
  );
}

type AutoUpdateTaskKey =
  | "sourceRefreshSeconds"
  | "ruleProviderRefreshSeconds"
  | "nodeProbeSeconds"
  | "profileRefreshSeconds";

function AutoUpdateView({
  settings,
  onSaved,
}: {
  settings: AutoUpdateSettings;
  onSaved: (settings: AutoUpdateSettings) => void;
}) {
  const [form, setForm] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [runningTask, setRunningTask] = useState<AutoUpdateTaskKey | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => setForm(settings), [settings]);
  const options = [
    [3600, "每小时"], [21600, "每 6 小时"], [43200, "每 12 小时"], [86400, "每天"],
  ] as const;
  const runTasks = {
    sourceRefreshSeconds: "sources",
    ruleProviderRefreshSeconds: "ruleProviders",
    nodeProbeSeconds: "probe",
    profileRefreshSeconds: "profiles",
  } as const;
  async function save() {
    setSaving(true); setError("");
    try {
      const next = await apiPut<AutoUpdateSettings>(`${API}/auto-update-settings`, form);
      onSaved(next);
    } catch (caught) { setError(message(caught)); }
    finally { setSaving(false); }
  }
  async function run(key: AutoUpdateTaskKey) {
    setRunningTask(key); setError(""); setNotice("");
    try {
      const result = await apiPost<{ message: string; lastRunAt: Partial<Record<AutoUpdateTaskKey, string | null>> }>(
        `${API}/auto-update/run/${runTasks[key]}`,
        {},
      );
      setNotice(result.message);
      onSaved({ ...settings, lastRunAt: result.lastRunAt });
    } catch (caught) { setError(message(caught)); }
    finally { setRunningTask(null); }
  }
  return (
    <div className="csm-panel">
      <Toolbar title="自动更新" icon={<Settings2 size={18} />} />
      <p className="csm-footnote">统一设置后会同步已有订阅源、远程 Rule Provider 和已发布聚合订阅链接的周期。</p>
      {error && <Alert type="error">{error}</Alert>}
      {notice && <Alert type="success">{notice}</Alert>}
      <div className="csm-form-grid">
        {([
          ["sourceRefreshSeconds", "节点订阅源", "拉取上游节点订阅"],
          ["ruleProviderRefreshSeconds", "Rule Provider", "拉取远程规则订阅"],
          ["nodeProbeSeconds", "节点可用性探测", "TCP 探测全部节点"],
          ["profileRefreshSeconds", "聚合订阅链接", "自动重新生成公开 Clash Meta YAML"],
        ] as const).map(([key, label, hint]) => (
          <Field label={label} key={key}>
            <div className="csm-cycle-row">
              <select className="csm-select" value={form[key]} onChange={(event) => setForm({ ...form, [key]: Number(event.target.value) })}>
                {options.map(([value, text]) => <option value={value} key={value}>{text}</option>)}
              </select>
              <button
                className="csm-btn csm-btn-sm csm-btn-secondary csm-btn-square"
                type="button"
                title="立即触发"
                aria-label={`立即触发${label}`}
                disabled={runningTask !== null}
                onClick={() => void run(key)}
              >
                {runningTask === key ? <Spin size={13} /> : <RefreshCw size={13} />}
              </button>
            </div>
            <small className="csm-muted">{hint}</small>
            <small className="csm-muted csm-cycle-last-run">
              最近触发：{stamp(settings.lastRunAt?.[key] ?? null)}
            </small>
          </Field>
        ))}
        <div className="csm-full-col">
          <button className="csm-btn csm-btn-primary" type="button" disabled={saving} onClick={() => void save()}>{saving ? "保存中…" : "保存自动更新设置"}</button>
        </div>
      </div>
    </div>
  );
}

function DashboardView({
  data,
  runs,
  autoUpdateSettings,
  loading,
  onNavigate,
}: {
  data: Dashboard | null;
  runs: RefreshRun[];
  autoUpdateSettings: AutoUpdateSettings | null;
  loading: boolean;
  onNavigate: (view: View) => void;
}) {
  const [detail, setDetail] = useState<
    "protocol" | "alerts" | "sources" | null
  >(null);
  if (loading && !data) return <Loading text="加载运营总览…" />;

  const metrics = data?.metrics ?? {};
  const health = data?.health ?? {};
  const value = (key: string, fallback = 0) =>
    Number.isFinite(metrics[key]) ? metrics[key] : fallback;
  const sourceTotal = value("sources");
  const healthySources = value("healthySources");
  const nodeTotal = value("nodes");
  const reachableNodes = value("tcpReachable");
  const profileTotal = value("profiles");
  const publishedProfiles = value("publishedProfiles");
  const requests24h = value("requests24h");
  const sourceRate = health.sourceHealthRate ?? 0;
  const nodeRate = health.nodeReachabilityRate ?? 0;
  const profileRate = health.profileHealthRate ?? 0;
  const dedupeRate = health.dedupeSavedRate ?? 0;
  const latency = (key: string) => {
    const raw = metrics[key];
    return raw == null ? "—" : `${raw} ms`;
  };
  const rings: Array<{ label: string; rate: number; caption: string; target: View }> = [
    { label: "订阅源健康", rate: sourceRate, caption: `${healthySources}/${sourceTotal} 正常`, target: "nodes" },
    { label: "节点可用", rate: nodeRate, caption: `${reachableNodes}/${value("probedNodes")} 可达`, target: "nodes" },
    { label: "配置健康", rate: profileRate, caption: `${publishedProfiles - value("degradedProfiles")}/${profileTotal} 可用`, target: "profiles" },
    { label: "去重效率", rate: dedupeRate, caption: `减少 ${value("nodesDeduplicated")} 条`, target: "nodes" },
  ];
  const kpis: Array<{ label: string; value: number; note: string; target: View }> = [
    { label: "订阅源", value: sourceTotal, note: `${healthySources} 正常 · ${value("errorSources")} 异常`, target: "nodes" },
    { label: "去重后节点", value: nodeTotal, note: `自定义 ${value("customNodes")} · 原始 ${value("nodesBeforeDedupe")}`, target: "nodes" },
    { label: "TCP 可达", value: reachableNodes, note: `P50 ${latency("latencyP50Ms")}`, target: "nodes" },
    { label: "聚合配置", value: profileTotal, note: `发布 ${publishedProfiles} · 草稿 ${value("draftProfiles")}`, target: "profiles" },
    { label: "规则资产", value: value("ruleSets"), note: `Provider ${value("ruleProviders")} · 分组 ${value("nodeGroups")}`, target: "rules" },
    { label: "24h 请求", value: requests24h, note: "公开订阅链接访问量", target: "profiles" },
  ];
  const autoRows = autoUpdateSettings
    ? ([
        ["sourceRefreshSeconds", "订阅源", "nodes"],
        ["ruleProviderRefreshSeconds", "Rule Provider", "rules"],
        ["nodeProbeSeconds", "节点探测", "nodes"],
        ["profileRefreshSeconds", "聚合配置", "profiles"],
      ] as const).map(([key, label, target]) => ({
        key,
        label,
        target: target as View,
        period: duration(autoUpdateSettings[key]),
        last: autoUpdateSettings.lastRunAt?.[key] ?? null,
      }))
    : [];

  return (
    <div className="csm-stack">
      <section className="csm-dashboard-hero">
        <div className="csm-dashboard-hero-copy">
          <span className="csm-dashboard-eyebrow">
            <Activity size={14} />
            Operations
          </span>
          <h2>订阅运营健康度</h2>
          <p>
            覆盖节点入库、去重、探测、规则与发布链路。当前待处理告警
            <strong>{value("alerts")}</strong> 个，平均可达延迟
            <strong>{latency("avgLatencyMs")}</strong>。
          </p>
          <div className="csm-dashboard-actions">
            <button type="button" className="csm-btn csm-btn-primary" onClick={() => onNavigate("nodes")}>
              管理节点
            </button>
            <button type="button" className="csm-btn csm-btn-secondary" onClick={() => onNavigate("auto-update")}>
              调整自动更新
            </button>
          </div>
        </div>
        <div className="csm-health-rings">
          {rings.map((item) => (
            <button type="button" className="csm-health-ring" key={item.label} onClick={() => onNavigate(item.target)}>
              <span
                style={{
                  background: `conic-gradient(var(--csm-ring-color) ${item.rate * 3.6}deg, #e2e8f0 0deg)`,
                }}
              >
                <i>{item.rate}%</i>
              </span>
              <strong>{item.label}</strong>
              <small>{item.caption}</small>
            </button>
          ))}
        </div>
      </section>

      <section className="csm-metrics csm-dashboard-kpis">
        {kpis.map((item) => (
          <button type="button" className="csm-metric" key={item.label} onClick={() => onNavigate(item.target)}>
            <span>{item.label}</span>
            <strong>{item.value}</strong>
            <small>{item.note}</small>
          </button>
        ))}
      </section>

      <section className="csm-dashboard-triptych">
        <div className="csm-panel">
          <PanelTitle
            icon={<BarChart3 size={18} />}
            title="节点画像"
            action={
              <button className="csm-link-button" type="button" onClick={() => setDetail("protocol")}>
                <Eye size={13} />
                协议详情
              </button>
            }
          />
          {data?.protocolDistribution.length ? (
            <div className="csm-bars">
              {data.protocolDistribution.slice(0, 6).map((item) => (
                <div key={item.name}>
                  <code>{item.name}</code>
                  <span>
                    <i style={{ width: `${Math.max(5, (item.value / Math.max(...data.protocolDistribution.map((entry) => entry.value))) * 100)}%` }} />
                  </span>
                  <b>{item.value}</b>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无节点数据" hint="成功刷新订阅源后显示协议分布" />
          )}
        </div>
        <div className="csm-panel">
          <PanelTitle icon={<Globe2 size={18} />} title="地区分布" />
          {data?.regionDistribution.length ? (
            <div className="csm-bars csm-region-bars">
              {data.regionDistribution.map((item) => (
                <div key={item.name}>
                  <code>{item.name}</code>
                  <span>
                    <i style={{ width: `${Math.max(5, (item.value / Math.max(...data.regionDistribution.map((entry) => entry.value))) * 100)}%` }} />
                  </span>
                  <b>{item.value}</b>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无地区数据" hint="GeoIP 解析完成后显示地区分布" />
          )}
        </div>
        <div className="csm-panel">
          <PanelTitle icon={<Clock size={18} />} title="自动更新态势" />
          {autoRows.length ? (
            <div className="csm-auto-summary">
              {autoRows.map((item) => (
                <div key={item.key}>
                  <strong>{item.label}</strong>
                  <small>{item.period} 周期</small>
                  <span>{item.last ? relativeStamp(item.last) : "从未触发"}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="自动更新设置加载中" />
          )}
        </div>
      </section>

      <section className="csm-grid-two">
        <div className="csm-panel">
          <PanelTitle
            icon={<Server size={18} />}
            title="订阅源健康"
            action={
              <button className="csm-link-button" type="button" onClick={() => setDetail("sources")}>
                <Eye size={13} />
                查看详情
              </button>
            }
          />
          {data?.sources.length ? (
            <div className="csm-summary-list">
              {data.sources.slice(0, 7).map((source) => (
                <div key={source.id}>
                  <StatusDot status={source.status} />
                  <strong>{source.name}</strong>
                  <span>
                    {source.status === "error" && source.lastError
                      ? source.lastError
                      : `成功 ${relativeStamp(source.lastSuccessAt)} · 下次 ${source.nextRefreshAt ? relativeStamp(source.nextRefreshAt) : "未排期"}`}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无订阅源" />
          )}
        </div>
        <div className="csm-panel">
          <PanelTitle icon={<Send size={18} />} title="聚合配置状态" />
          {data?.profiles.length ? (
            <div className="csm-summary-list">
              {data.profiles.slice(0, 7).map((profile) => (
                <div key={profile.id}>
                  <PublishBadge profile={profile} />
                  <strong>{profile.name}</strong>
                  <span>
                    {profile.ruleSetName || "未绑定规则"} ·{" "}
                    {profile.nextRefreshAt ? `下次 ${relativeStamp(profile.nextRefreshAt)}` : "未排期"}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无聚合配置" />
          )}
        </div>
      </section>

      <section className="csm-grid-two">
        <div className="csm-panel">
          <PanelTitle
            icon={<ShieldAlert size={18} />}
            title="告警中心"
            action={
              <button className="csm-link-button" type="button" onClick={() => setDetail("alerts")}>
                <Eye size={13} />
                查看详情
              </button>
            }
          />
          {data?.alerts.length ? (
            <div className="csm-alert-list">
              {data.alerts.slice(0, 6).map((alert, index) => (
                <div key={`${alert.message}-${index}`}>
                  <AlertTriangle size={15} />
                  <span>{alert.message}</span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState icon={<Check size={28} />} title="没有待处理告警" hint="订阅源和已发布配置状态正常" />
          )}
        </div>
        <div className="csm-panel">
          <PanelTitle icon={<Zap size={18} />} title="最近刷新活动" />
          {runs.length ? (
            <div className="csm-summary-list">
              {runs.slice(0, 7).map((run) => (
                <div key={run.id}>
                  <StatusDot status={run.status === "success" ? "healthy" : "error"} />
                  <strong>{run.sourceName}</strong>
                  <span>
                    {run.status === "success" ? "成功" : "失败"} · {run.nodesBefore} → {run.nodesAfter} ·{" "}
                    {run.durationMs == null ? "进行中" : `${run.durationMs} ms`}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无刷新记录" />
          )}
        </div>
      </section>

      {detail && (
        <DashboardDetailModal detail={detail} data={data} runs={runs} onClose={() => setDetail(null)} />
      )}
    </div>
  );
}

function NodesView({
  sources,
  nodes,
  loading,
  pending,
  onAdd,
  onEditSource,
  onRemoveSource,
  onRefreshSource,
  onEditCustom,
  onRemoveCustom,
  onCopy,
  onAlias,
  onProbe,
  onNodesChange,
}: {
  sources: Source[];
  nodes: Node[];
  loading: boolean;
  pending: string;
  onAdd: () => void;
  onEditSource: (source: Source) => void;
  onRemoveSource: (source: Source) => void;
  onRefreshSource: (source: Source) => void;
  onEditCustom: (node: Node) => void;
  onRemoveCustom: (node: Node) => void;
  onCopy: (node: Node) => void;
  onAlias: (node: Node) => void;
  onProbe: (ids: string[]) => void;
  onNodesChange: (nodes: Node[]) => void;
}) {
  const [search, setSearch] = useState("");
  const [protocol, setProtocol] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [probeClock, setProbeClock] = useState(() => Date.now());
  const [geoStatus, setGeoStatus] = useState<"idle" | "loading" | "partial">(
    "idle",
  );
  const geoAttemptedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const timer = window.setInterval(() => setProbeClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const pendingGeoNodes = useMemo(
    () =>
      nodes
        .filter(
          (node) =>
            (!node.country || node.geoError) &&
            !geoAttemptedRef.current.has(node.id),
        )
        .slice(0, 1000),
    [nodes],
  );
  const pendingGeoKey = pendingGeoNodes.map((node) => node.id).join(",");
  useEffect(() => {
    if (!pendingGeoKey) {
      setGeoStatus("idle");
      return;
    }
    const requestIds = pendingGeoKey.split(",");
    let stale = false;
    setGeoStatus("loading");
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const data = await apiPost<{ nodes: Node[] }>(`${API}/nodes/geoip`, {
            nodeIds: requestIds,
          });
          if (stale) return;
          requestIds.forEach((id) => geoAttemptedRef.current.add(id));
          onNodesChange(data.nodes);
          setGeoStatus(
            data.nodes.some((node) => !node.country) ? "partial" : "idle",
          );
        } catch {
          if (stale) return;
          requestIds.forEach((id) => geoAttemptedRef.current.add(id));
          setGeoStatus("partial");
        }
      })();
    }, 400);

    return () => {
      stale = true;
      clearTimeout(timer);
    };
    // Node objects are refreshed by the response; the request is keyed by IDs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingGeoKey]);

  const protocols = useMemo(
    () => Array.from(new Set(nodes.map((node) => node.protocol))).sort(),
    [nodes],
  );
  const filtered = useMemo(
    () =>
      nodes.filter(
        (node) =>
          (!protocol || node.protocol === protocol) &&
          `${node.name} ${node.alias} ${node.server} ${node.sources.join(" ")}`
            .toLowerCase()
            .includes(search.trim().toLowerCase()),
      ),
    [nodes, protocol, search],
  );
  const sourceNodeCount = (sourceId: string) =>
    nodes.filter((node) => !node.isCustom && node.sourceIds.includes(sourceId))
      .length;
  const nodesForSource = (sourceId: string) =>
    filtered.filter(
      (node) => !node.isCustom && node.sourceIds.includes(sourceId),
    );
  const customNodes = filtered.filter((node) => node.isCustom);
  const visibleNodeIds = Array.from(
    new Set([
      ...sources.flatMap((source) =>
        nodesForSource(source.id).map((node) => node.id),
      ),
      ...customNodes.map((node) => node.id),
    ]),
  );
  const recentProbe = (node: Node) => {
    if (!node.tcp?.checkedAt) return false;
    const checkedAt = new Date(node.tcp.checkedAt).valueOf();
    return Number.isFinite(checkedAt) && probeClock - checkedAt < 15000;
  };

  function toggleGroup(groupNodes: Node[], checked: boolean) {
    const ids = new Set(groupNodes.map((node) => node.id));
    setSelected((current) =>
      checked
        ? Array.from(new Set([...current, ...ids]))
        : current.filter((id) => !ids.has(id)),
    );
  }

  function nodeTable(groupNodes: Node[], emptyHint: string) {
    if (!groupNodes.length)
      return (
        <EmptyState
          icon={<Activity size={26} />}
          title="暂无节点"
          hint={emptyHint}
        />
      );
    const allGroupSelected = groupNodes.every((node) =>
      selected.includes(node.id),
    );
    return (
      <Table>
        <thead>
          <tr>
            <th>
              <input
                aria-label="选择本组节点"
                type="checkbox"
                checked={allGroupSelected}
                onChange={(event) =>
                  toggleGroup(groupNodes, event.target.checked)
                }
              />
            </th>
            <th className="csm-node-name-col">节点</th>
            <th className="csm-node-country-col">国家/地区</th>
            <th className="csm-node-probe-col">TCP 状态</th>
            <th className="csm-node-updated-col">最后更新</th>
            <th>协议</th>
            <th>端点</th>
            <th>兼容性</th>
            <th className="csm-node-actions-col">操作</th>
          </tr>
        </thead>
        <tbody>
          {groupNodes.map((node) => (
            <tr key={node.id}>
              <td>
                <input
                  aria-label={`选择节点 ${node.displayName || node.name}`}
                  type="checkbox"
                  checked={selected.includes(node.id)}
                  onChange={(event) =>
                    setSelected(
                      event.target.checked
                        ? Array.from(new Set([...selected, node.id]))
                        : selected.filter((id) => id !== node.id),
                    )
                  }
                />
              </td>
              <td className="csm-node-name-col">
                <strong title={node.displayName || node.name}>
                  {node.displayName || node.name}
                </strong>
                {node.alias && (
                  <small className="csm-cell-note" title={`原名：${node.name}`}>
                    原名：{node.name}
                  </small>
                )}
              </td>
              <td
                className="csm-node-country-col"
                title={[
                  node.country ? `国家/地区代码：${node.country}` : undefined,
                  node.resolvedIp ? `识别 IP：${node.resolvedIp}` : undefined,
                  node.geoError || undefined,
                ]
                  .filter(Boolean)
                  .join("\n")}
              >
                {node.countryLabel ||
                  node.country ||
                  (geoStatus === "loading" ? "识别中…" : "未知")}
              </td>
              <td className="csm-node-probe-col">
                {recentProbe(node) && node.tcp ? (
                  <small
                    className={`csm-probe-feedback ${node.tcp.reachable ? "reachable" : "failed"}`}
                    title={node.tcp.error || undefined}
                  >
                    {node.tcp.reachable
                      ? `TCP 可达 · ${node.tcp.latencyMs ?? "—"} ms`
                      : `TCP 失败${node.tcp.error ? ` · ${node.tcp.error}` : ""}`}
                  </small>
                ) : (
                  <span className="csm-muted">—</span>
                )}
              </td>
              <td
                className="csm-node-updated-col"
                title={node.lastSeenAt ? stamp(node.lastSeenAt) : undefined}
              >
                {relativeStamp(node.lastSeenAt)}
              </td>
              <td>
                <code className="csm-code">{node.protocol}</code>
              </td>
              <td>
                <code className="csm-code">
                  {node.server}:{node.port ?? "—"}
                </code>
              </td>
              <td>
                {node.supportedOutput ? (
                  <Badge color="green">可输出</Badge>
                ) : (
                  <Badge color="red">Clash Meta 不支持</Badge>
                )}
              </td>
              <td className="csm-node-actions-col">
                <Actions>
                  {node.isCustom ? (
                    <>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost csm-btn-square"
                        type="button"
                        title="编辑自定义节点配置和别名"
                        aria-label="编辑自定义节点配置和别名"
                        onClick={() => onEditCustom(node)}
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost csm-btn-square csm-danger-text"
                        type="button"
                        title="删除自定义节点"
                        aria-label="删除自定义节点"
                        disabled={pending === `node-delete-${node.id}`}
                        onClick={() => onRemoveCustom(node)}
                      >
                        <Trash2 size={13} />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost csm-btn-square"
                        type="button"
                        title="编辑订阅节点别名"
                        aria-label="编辑订阅节点别名"
                        onClick={() => onAlias(node)}
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost csm-btn-square"
                        type="button"
                        title="复制为自定义节点"
                        aria-label="复制为自定义节点"
                        disabled={pending === `node-copy-${node.id}`}
                        onClick={() => onCopy(node)}
                      >
                        {pending === `node-copy-${node.id}` ? (
                          <Spin size={13} />
                        ) : (
                          <Clipboard size={13} />
                        )}
                      </button>
                    </>
                  )}
                </Actions>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    );
  }

  return (
    <div className="csm-panel">
      <Toolbar
        title="节点池"
        icon={<Activity size={18} />}
        actions={
          <>
            <button
              className="csm-btn csm-btn-secondary"
              type="button"
              disabled={
                pending === "nodes-probe" ||
                (!selected.length && !visibleNodeIds.length)
              }
              onClick={() =>
                onProbe(selected.length ? selected : visibleNodeIds)
              }
            >
              {pending === "nodes-probe" ? (
                <Spin size={14} />
              ) : (
                <Activity size={14} />
              )}
              探测{selected.length ? `（${selected.length}）` : "当前结果"}
            </button>
            <button
              className="csm-btn csm-btn-primary"
              type="button"
              onClick={onAdd}
            >
              <Plus size={14} />
              添加
            </button>
          </>
        }
      />
      <div className="csm-filterbar csm-node-library-filter">
        <label className="csm-search">
          <Search size={15} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="按名称、别名、地址或订阅源搜索"
          />
        </label>
        <select
          className="csm-select csm-compact"
          value={protocol}
          onChange={(event) => setProtocol(event.target.value)}
        >
          <option value="">全部协议</option>
          {protocols.map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
      </div>
      {loading ? (
        <Loading text="加载订阅源与节点…" />
      ) : (
        <div className="csm-node-groups">
          {!sources.length && (
            <EmptyState
              icon={<Server size={28} />}
              title="暂无订阅源"
              hint="点击右上角“添加”可导入订阅源"
            />
          )}
          {sources.map((source) => {
            const groupNodes = nodesForSource(source.id);
            const total = sourceNodeCount(source.id);
            return (
              <section className="csm-node-group" key={source.id}>
                <div className="csm-node-group-head">
                  <div className="csm-node-group-title">
                    <h3>
                      <Server size={16} />
                      {source.name}
                    </h3>
                    <span title={source.url}>{source.url}</span>
                    <small>
                      刷新周期：{duration(source.refreshSeconds)} · 上次成功：
                      {stamp(source.lastSuccessAt)} · 下次刷新：
                      {source.enabled ? stamp(source.nextRefreshAt) : "已停用"}
                    </small>
                    {source.lastError && (
                      <small className="csm-cell-error">
                        {source.lastError}
                      </small>
                    )}
                  </div>
                  <div className="csm-node-group-actions">
                    <SourceBadge
                      status={source.status}
                      enabled={source.enabled}
                    />
                    <Badge color="blue">
                      {groupNodes.length === total
                        ? total
                        : `${groupNodes.length} / ${total}`}{" "}
                      个节点
                    </Badge>
                    <Actions>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost csm-btn-square"
                        type="button"
                        title="立即刷新"
                        aria-label={`立即刷新订阅源 ${source.name}`}
                        disabled={pending === `source-refresh-${source.id}`}
                        onClick={() => onRefreshSource(source)}
                      >
                        {pending === `source-refresh-${source.id}` ? (
                          <Spin size={13} />
                        ) : (
                          <RefreshCw size={13} />
                        )}
                      </button>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost csm-btn-square"
                        type="button"
                        title="编辑订阅源"
                        aria-label={`编辑订阅源 ${source.name}`}
                        onClick={() => onEditSource(source)}
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost csm-btn-square csm-danger-text"
                        type="button"
                        title="删除订阅源"
                        aria-label={`删除订阅源 ${source.name}`}
                        onClick={() => onRemoveSource(source)}
                      >
                        <Trash2 size={13} />
                      </button>
                    </Actions>
                  </div>
                </div>
                {nodeTable(
                  groupNodes,
                  total
                    ? "没有符合当前筛选条件的节点"
                    : "刷新订阅源后，节点会显示在这里",
                )}
              </section>
            );
          })}
          <section className="csm-node-group csm-custom-node-group">
            <div className="csm-node-group-head">
              <div className="csm-node-group-title">
                <h3>
                  <FileCode2 size={16} />
                  自定义节点
                </h3>
                <span>自行添加或从订阅节点复制，可完整编辑节点配置</span>
              </div>
              <Badge color="blue">
                {customNodes.length ===
                nodes.filter((node) => node.isCustom).length
                  ? customNodes.length
                  : `${customNodes.length} / ${nodes.filter((node) => node.isCustom).length}`}{" "}
                个节点
              </Badge>
            </div>
            {nodeTable(
              customNodes,
              nodes.some((node) => node.isCustom)
                ? "没有符合当前筛选条件的节点"
                : "点击右上角“添加”可创建自定义节点",
            )}
          </section>
        </div>
      )}
      <p className="csm-footnote">
        同一节点属于多个订阅源时会显示在对应的每个分组中。别名用于最终输出；规则组同时接受节点原名和别名。系统每
        2 分钟自动探测全部节点，探测结果会在节点名称下短暂显示；TCP
        可达仅表示目标地址和端口能够建立连接。
      </p>
    </div>
  );
}

function AddNodeModal({
  onClose,
  onAddSource,
  onAddCustom,
  onImportQr,
}: {
  onClose: () => void;
  onAddSource: () => void;
  onAddCustom: () => void;
  onImportQr: () => void;
}) {
  return (
    <Modal title="添加到节点池" onClose={onClose} width={720}>
      <div className="csm-add-node-options">
        <div className="csm-add-node-actions">
          <button type="button" onClick={onAddSource}>
            <span className="csm-add-node-icon">
              <Server size={24} />
            </span>
            <span><strong>添加订阅源</strong><small>通过 HTTP(S) 订阅链接批量导入节点，并按设置自动刷新。</small></span>
          </button>
          <button type="button" onClick={onAddCustom}>
            <span className="csm-add-node-icon">
              <FileCode2 size={24} />
            </span>
            <span><strong>添加自定义节点</strong><small>直接编写一个 Clash Meta 节点，可随时编辑或删除。</small></span>
          </button>
        </div>
        <button className="csm-qr-import-option" type="button" onClick={onImportQr}>
          <span className="csm-add-node-icon"><QrCode size={42} strokeWidth={1.6} /></span>
          <strong>二维码导入</strong>
          <small>选择含订阅链接的二维码图片，识别后自动填入订阅地址。</small>
        </button>
      </div>
    </Modal>
  );
}

function QrImportModal({ onClose, onImport }: { onClose: () => void; onImport: (url: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  const [scanning, setScanning] = useState(false);

  async function scan(file: File) {
    setScanning(true);
    setError("");
    try {
      const BarcodeDetectorClass = (window as Window & {
        BarcodeDetector?: new (options?: { formats?: string[] }) => { detect: (source: ImageBitmapSource) => Promise<Array<{ rawValue?: string }>> };
      }).BarcodeDetector;
      if (!BarcodeDetectorClass) {
        throw new Error("当前浏览器不支持二维码识别，请使用 Chrome 或 Edge，或手动添加订阅链接。");
      }
      const image = await createImageBitmap(file);
      try {
        const codes = await new BarcodeDetectorClass({ formats: ["qr_code"] }).detect(image);
        const value = codes[0]?.rawValue?.trim();
        if (!value) throw new Error("未识别到二维码，请确认图片清晰且包含订阅链接。");
        if (!/^https?:\/\//i.test(value)) throw new Error("二维码内容不是 HTTP(S) 订阅链接。");
        onImport(value);
      } finally {
        image.close();
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "二维码识别失败，请重试。");
    } finally {
      setScanning(false);
    }
  }

  return (
    <Modal title="二维码导入订阅" onClose={() => !scanning && onClose()} width={520}>
      <div className="csm-qr-import-modal">
        <span className="csm-qr-import-preview"><QrCode size={100} strokeWidth={1.35} /></span>
        <strong>上传订阅二维码</strong>
        <p>从图片中读取 HTTP(S) 订阅链接，识别成功后会打开订阅源表单。</p>
        {error && <Alert type="error">{error}</Alert>}
        <input ref={inputRef} className="csm-visually-hidden" type="file" accept="image/*" onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void scan(file);
          event.target.value = "";
        }} />
        <button className="csm-btn csm-btn-primary" type="button" disabled={scanning} onClick={() => inputRef.current?.click()}>
          {scanning ? <><Spin size={14} />识别中</> : "选择二维码图片"}
        </button>
      </div>
    </Modal>
  );
}

const CUSTOM_NODE_TEMPLATE = `name: 我的 VLESS 节点
type: vless
server: example.com
port: 443
uuid: 00000000-0000-0000-0000-000000000000
tls: true
servername: example.com
network: tcp`;

function CustomNodeModal({
  node,
  onClose,
  onSaved,
}: {
  node: Node | null;
  onClose: () => void;
  onSaved: (message: string) => Promise<void>;
}) {
  const [content, setContent] = useState(() =>
    node ? JSON.stringify(node.config, null, 2) : CUSTOM_NODE_TEMPLATE,
  );
  const [alias, setAlias] = useState(node?.alias ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setSaving(true);
    setError("");
    try {
      if (node) await apiPut(`${API}/nodes/${node.id}`, { content, alias });
      else await apiPost(`${API}/nodes`, { content, alias });
      await onSaved(node ? "自定义节点已更新" : "自定义节点已添加");
    } catch (caught) {
      setError(message(caught));
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal
      title={node ? `编辑自定义节点：${node.name}` : "添加自定义节点"}
      width={760}
      onClose={() => !saving && onClose()}
      foot={
        <>
          <button
            className="csm-btn csm-btn-secondary"
            type="button"
            disabled={saving}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? (
              <>
                <Spin size={14} />
                保存中
              </>
            ) : (
              "保存节点"
            )}
          </button>
        </>
      }
    >
      <div className="csm-stack">
        {error && <Alert type="error">{error}</Alert>}
        <Alert type="info">
          填写一个 Clash Meta <code>proxies</code> 列表中的节点对象，支持 YAML
          或 JSON。必须包含 <code>name</code>、<code>type</code>、
          <code>server</code> 和 <code>port</code>。
        </Alert>
        <Field label="节点别名（可选）">
          <input
            className="csm-input"
            value={alias}
            maxLength={120}
            onChange={(event) => setAlias(event.target.value)}
            placeholder="留空时使用配置中的 name"
          />
          <small className="csm-muted">
            别名用于最终输出，规则组仍可使用配置原名。
          </small>
        </Field>
        <Field label="节点配置">
          <textarea
            className="csm-textarea csm-code-editor csm-custom-node-editor"
            rows={18}
            spellCheck={false}
            value={content}
            onChange={(event) => setContent(event.target.value)}
          />
        </Field>
        {!node && (
          <div className="csm-custom-node-help">
            <span>
              当前已填入 VLESS 示例，可直接修改；也可以粘贴 Clash Meta YAML
              中的单个节点对象。
            </span>
            <button
              className="csm-btn csm-btn-sm csm-btn-secondary"
              type="button"
              onClick={() => setContent(CUSTOM_NODE_TEMPLATE)}
            >
              恢复示例
            </button>
          </div>
        )}
      </div>
    </Modal>
  );
}

function NodeAliasModal({
  node,
  onClose,
  onSaved,
}: {
  node: Node;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [alias, setAlias] = useState(node.alias || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setSaving(true);
    setError("");
    try {
      await apiPut(`${API}/nodes/${node.id}/alias`, { alias });
      await onSaved();
    } catch (caught) {
      setError(message(caught));
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal
      title={`编辑订阅节点：${node.name}`}
      width={620}
      onClose={() => !saving && onClose()}
      foot={
        <>
          <button
            className="csm-btn csm-btn-secondary"
            type="button"
            disabled={saving}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? (
              <>
                <Spin size={14} />
                保存中
              </>
            ) : (
              "保存修改"
            )}
          </button>
        </>
      }
    >
      <div className="csm-form-grid">
        {error && (
          <div className="csm-full-col">
            <Alert type="error">{error}</Alert>
          </div>
        )}
        <div className="csm-full-col">
          <Alert type="info">
            订阅节点的连接配置由订阅源管理，此处只能修改别名。清空后恢复使用订阅原名。
          </Alert>
        </div>
        <Field label="订阅原名" full>
          <input className="csm-input" value={node.name} disabled />
        </Field>
        <Field label="协议">
          <input
            className="csm-input csm-code-editor"
            value={node.protocol}
            disabled
          />
        </Field>
        <Field label="服务器">
          <input
            className="csm-input csm-code-editor"
            value={`${node.server}:${node.port ?? "—"}`}
            disabled
          />
        </Field>
        <Field label="节点别名" full>
          <input
            className="csm-input"
            value={alias}
            maxLength={120}
            onChange={(event) => setAlias(event.target.value)}
            placeholder="留空恢复使用订阅原名"
          />
          <small className="csm-muted">
            别名不会被订阅刷新覆盖，仅用于识别；规则组引用始终以节点原名为准。
          </small>
        </Field>
      </div>
    </Modal>
  );
}

function ProfilesView({
  profiles,
  loading,
  pending,
  subscriptionUrl,
  subscriptionDetailUrl,
  onAdd,
  onEdit,
  onRemove,
  onRefresh,
  onLogs,
  onCopy,
}: {
  profiles: Profile[];
  loading: boolean;
  pending: string;
  subscriptionUrl: (profile: Profile) => string;
  subscriptionDetailUrl: (profile: Profile) => string;
  onAdd: () => void;
  onEdit: (profile: Profile) => void;
  onRemove: (profile: Profile) => void;
  onRefresh: (profile: Profile) => void;
  onLogs: (profile: Profile) => void;
  onCopy: (profile: Profile) => void;
}) {
  return (
    <div className="csm-panel">
      <Toolbar
        title="聚合配置"
        icon={<Send size={18} />}
        actions={
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            onClick={onAdd}
          >
            <Plus size={14} />
            新建聚合配置
          </button>
        }
      />
      {loading ? (
        <Loading text="加载聚合配置…" />
      ) : !profiles.length ? (
        <EmptyState
          icon={<Send size={32} />}
          title="暂无聚合配置"
          hint="创建配置后关联规则组并发布"
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <th>名称</th>
              <th>规则组</th>
              <th>状态</th>
              <th>更新时间</th>
              <th>订阅链接</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {profiles.map((profile) => {
              const errors = profile.validation.filter(
                (item) => item.level === "error",
              ).length;
              const published = Boolean(
                profile.subscriptionToken && profile.publishedAt,
              );
              return (
                <tr key={profile.id}>
                  <td>
                    <strong>{profile.name}</strong>
                    {errors > 0 && (
                      <small className="csm-cell-error">
                        {errors} 个校验问题
                      </small>
                    )}
                  </td>
                  <td>{profile.ruleSetName || "未关联"}</td>
                  <td>
                    <PublishBadge profile={profile} />
                  </td>
                  <td>{profile.updatedAt ? stamp(profile.updatedAt) : "—"}</td>
                  <td>
                    {published ? (
                      <div className="csm-cell-links">
                        <a
                          className="csm-link-button"
                          href={subscriptionDetailUrl(profile)}
                          target="_blank"
                          rel="noreferrer"
                          title={subscriptionDetailUrl(profile)}
                        >
                          <ExternalLink size={13} />
                          可视化详情
                        </a>
                        <button
                          className="csm-link-button"
                          type="button"
                          title={subscriptionUrl(profile)}
                          onClick={() => onCopy(profile)}
                        >
                          <Clipboard size={13} />
                          YAML 订阅
                        </button>
                      </div>
                    ) : (
                      "发布后可用"
                    )}
                  </td>
                  <td>
                    <Actions>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost"
                        type="button"
                        title="编辑"
                        onClick={() => onEdit(profile)}
                      >
                        <Pencil size={13} />
                      </button>
                      <button className="csm-btn csm-btn-sm csm-btn-ghost" type="button" title="手动更新" disabled={pending === `profile-refresh-${profile.id}`} onClick={() => onRefresh(profile)}>
                        {pending === `profile-refresh-${profile.id}` ? <Spin size={13} /> : <RefreshCw size={13} />}
                      </button>
                      <button className="csm-btn csm-btn-sm csm-btn-ghost" type="button" title="更新日志" onClick={() => onLogs(profile)}>
                        <Eye size={13} />
                      </button>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost csm-danger-text"
                        type="button"
                        title="删除"
                        onClick={() => onRemove(profile)}
                      >
                        <Trash2 size={13} />
                      </button>
                    </Actions>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </div>
  );
}

function ProfileRefreshLogModal({
  profile,
  runs,
  onClose,
}: {
  profile: Profile;
  runs: ProfileRefreshRun[];
  onClose: () => void;
}) {
  return (
    <Modal title={`更新日志：${profile.name}`} width={820} onClose={onClose}>
      {!runs.length ? <p className="csm-muted">暂无更新记录。</p> : (
        <Table>
          <thead><tr><th>开始时间</th><th>状态</th><th>耗时</th><th>失败原因</th></tr></thead>
          <tbody>{runs.map((run) => (
            <tr key={run.id}>
              <td>{stamp(run.startedAt)}</td>
              <td><Badge color={run.status === "success" ? "green" : run.status === "failed" ? "red" : "amber"}>{run.status === "success" ? "成功" : run.status === "failed" ? "失败" : "进行中"}</Badge></td>
              <td>{run.durationMs == null ? "—" : `${run.durationMs} ms`}</td>
              <td className="csm-cell-error">{run.error || "—"}</td>
            </tr>
          ))}</tbody>
        </Table>
      )}
    </Modal>
  );
}

function RuleProvidersView({
  providers,
  loading,
  pending,
  onAdd,
  onEdit,
  onCopy,
  onRemove,
}: {
  providers: RuleProviderLibraryItem[];
  loading: boolean;
  pending: string;
  onAdd: () => void;
  onEdit: (provider: RuleProviderLibraryItem) => void;
  onCopy: (provider: RuleProviderLibraryItem) => void;
  onRemove: (provider: RuleProviderLibraryItem) => void;
}) {
  const [search, setSearch] = useState("");
  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return providers.filter(
      (provider) =>
        !keyword ||
        `${provider.name} ${provider.providerKey} ${provider.description} ${String(provider.config.url ?? "")} ${stringList(provider.config.payload).join(" ")}`
          .toLowerCase()
          .includes(keyword),
    );
  }, [providers, search]);

  return (
    <div className="csm-panel">
      <Toolbar
        title="Rule Provider"
        icon={<FolderOpen size={18} />}
        actions={
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            onClick={onAdd}
          >
            <Plus size={14} />
            新增 Provider
          </button>
        }
      />
      <Alert type="info">
        Rule Provider
        支持规则订阅和自定义两种方式。规则订阅由服务器下载并保存具体内容；绑定到策略组后会展开为通用
        Clash 规则，最终订阅中不会出现该规则订阅 URL。
      </Alert>
      <div className="csm-filterbar csm-provider-filter">
        <label className="csm-search">
          <Search size={15} />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索名称、Provider Key、规则或地址"
          />
        </label>
        <span className="csm-muted">共 {filtered.length} 个 Provider</span>
      </div>
      {loading ? (
        <Loading text="加载 Rule Provider…" />
      ) : !filtered.length ? (
        <EmptyState
          icon={<FolderOpen size={32} />}
          title="没有符合条件的 Rule Provider"
          hint="调整搜索条件，或新建规则订阅、自定义 Provider"
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <th>名称</th>
              <th>Provider Key</th>
              <th>来源 / Behavior</th>
              <th>内容</th>
              <th>属性</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((provider) => {
              const payload = stringList(provider.config.payload);
              const handwritten =
                String(provider.config.type ?? "") === "manual";
              const source = handwritten
                ? `自定义 ${payload.length} 条规则`
                : String(
                    provider.config.sourceUrl ??
                      provider.config.url ??
                      provider.config.path ??
                      "—",
                  );
              return (
                <tr key={provider.id}>
                  <td>
                    <strong>{provider.name}</strong>
                    {provider.description && (
                      <small className="csm-cell-note">
                        {provider.description}
                      </small>
                    )}
                  </td>
                  <td>
                    <code className="csm-code">
                      {providerKeyText(provider)}
                    </code>
                  </td>
                  <td>
                    <code className="csm-code">
                      {handwritten ? "自定义" : "规则订阅"} /{" "}
                      {String(provider.config.behavior ?? "—")}
                    </code>
                  </td>
                  <td>
                    <span className="csm-provider-source" title={source}>
                      {source}
                    </span>
                  </td>
                  <td>
                    {provider.builtin ? (
                      <Badge color="blue">内置</Badge>
                    ) : (
                      <Badge>自定义</Badge>
                    )}
                  </td>
                  <td>
                    <Actions>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost"
                        type="button"
                        title="编辑"
                        onClick={() => onEdit(provider)}
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        className="csm-btn csm-btn-sm csm-btn-ghost"
                        type="button"
                        title="复制"
                        disabled={pending === `provider-copy-${provider.id}`}
                        onClick={() => onCopy(provider)}
                      >
                        {pending === `provider-copy-${provider.id}` ? (
                          <Spin size={13} />
                        ) : (
                          <Clipboard size={13} />
                        )}
                      </button>
                      {!provider.builtin && (
                        <button
                          className="csm-btn csm-btn-sm csm-btn-ghost csm-danger-text"
                          type="button"
                          title="删除"
                          onClick={() => onRemove(provider)}
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </Actions>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </div>
  );
}

function RuleProviderCopyModal({
  provider,
  onClose,
  onSaved,
}: {
  provider: RuleProviderLibraryItem;
  onClose: () => void;
  onSaved: (message: string) => Promise<void>;
}) {
  const [saving, setSaving] = useState<"original" | "manual" | "">("");
  const [error, setError] = useState("");

  async function copy(mode: "original" | "manual") {
    setSaving(mode);
    setError("");
    try {
      await apiPost(`${API}/rule-providers/${provider.id}/copy`, { mode });
      await onSaved(
        mode === "manual" ? "已复制并转换为自定义规则" : "规则订阅已原样复制",
      );
    } catch (caught) {
      setError(message(caught));
    } finally {
      setSaving("");
    }
  }

  return (
    <Modal
      title={`复制 Rule Provider：${provider.name}`}
      width={640}
      onClose={() => !saving && onClose()}
      foot={
        <button
          className="csm-btn csm-btn-secondary"
          type="button"
          disabled={Boolean(saving)}
          onClick={onClose}
        >
          取消
        </button>
      }
    >
      <div className="csm-provider-copy-options">
        {error && <Alert type="error">{error}</Alert>}
        <button
          className="csm-provider-copy-option"
          type="button"
          disabled={Boolean(saving)}
          onClick={() => void copy("original")}
        >
          <span>
            <strong>原样复制规则订阅</strong>
            <small>
              保留原订阅链接、Behavior 和内容来源，创建一个独立副本。
            </small>
          </span>
          {saving === "original" ? <Spin size={16} /> : <Clipboard size={16} />}
        </button>
        <button
          className="csm-provider-copy-option"
          type="button"
          disabled={Boolean(saving)}
          onClick={() => void copy("manual")}
        >
          <span>
            <strong>下载并转为自定义规则</strong>
            <small>
              服务器重新下载链接内容，将规则保存到副本中；副本不再保留订阅链接。
            </small>
          </span>
          {saving === "manual" ? <Spin size={16} /> : <FileCode2 size={16} />}
        </button>
      </div>
    </Modal>
  );
}

function RuleProviderModal({
  provider,
  onClose,
  onSaved,
}: {
  provider: RuleProviderLibraryItem | null;
  onClose: () => void;
  onSaved: (message: string) => Promise<void>;
}) {
  const defaultConfig: Record<string, unknown> = {
    type: "cached",
    behavior: "domain",
    url: "",
  };
  const initialConfig = provider?.config ?? defaultConfig;
  const initialPayload = stringList(initialConfig.payload);
  const [name, setName] = useState(provider?.name ?? "自定义 Provider");
  const [providerKey, setProviderKey] = useState(provider?.providerKey ?? "");
  const [description, setDescription] = useState(provider?.description ?? "");
  const [config, setConfig] = useState<Record<string, unknown>>(initialConfig);
  const [sourceMode, setSourceMode] = useState<"subscription" | "manual">(
    String(initialConfig.type ?? "") === "manual" ? "manual" : "subscription",
  );
  const [manualContent, setManualContent] = useState(initialPayload.join("\n"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const behavior = String(config.behavior ?? "domain");
  const autoGeneratedProviderKey = /^rp-[0-9a-f]{32}$/.test(providerKey);

  function setConfigField(key: string, value: unknown) {
    setConfig((current) => ({ ...current, [key]: value }));
  }
  function visualConfig(): Record<string, unknown> {
    if (sourceMode === "manual")
      return {
        type: "manual",
        behavior,
        payload: manualContent
          .split(/\r?\n/)
          .map((item) => item.trim())
          .filter(Boolean),
      };
    return {
      type: "cached",
      behavior,
      url: config.sourceUrl ?? config.url ?? "",
    };
  }
  async function save() {
    if (!name.trim()) {
      setError("请输入名称。");
      return;
    }
    if (/[,\r\n]/.test(providerKey)) {
      setError("Provider Key 不能包含逗号或换行。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const material = visualConfig();
      if (material.type === "manual" && !stringList(material.payload).length)
        throw new Error("请至少填写一条自定义规则。");
      const payload = {
        name: name.trim(),
        providerKey: providerKey.trim(),
        description: description.trim(),
        config: material,
      };
      if (provider)
        await apiPut(`${API}/rule-providers/${provider.id}`, payload);
      else await apiPost(`${API}/rule-providers`, payload);
      await onSaved(provider ? "Rule Provider 已更新" : "Rule Provider 已创建");
    } catch (caught) {
      setError(message(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={
        provider ? `编辑 Rule Provider：${provider.name}` : "新增 Rule Provider"
      }
      width={820}
      onClose={() => !saving && onClose()}
      foot={
        <>
          <button
            className="csm-btn csm-btn-secondary"
            type="button"
            disabled={saving}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? (
              <>
                <Spin size={14} />
                保存中
              </>
            ) : (
              "保存 Provider"
            )}
          </button>
        </>
      }
    >
      <div className="csm-form-grid">
        {error && (
          <div className="csm-full-col">
            <Alert type="error">{error}</Alert>
          </div>
        )}
        {provider?.builtin && (
          <div className="csm-full-col">
            <Alert type="info">
              这是内置 Provider，可以修改但不能删除。被策略组使用时，配置和
              Provider Key 的修改会自动生效。
            </Alert>
          </div>
        )}
        <Field label="名称">
          <input
            className="csm-input"
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label="Provider Key（可选）">
          <input
            className="csm-input csm-code-editor"
            value={autoGeneratedProviderKey ? "" : providerKey}
            maxLength={120}
            onChange={(event) => setProviderKey(event.target.value)}
            placeholder={
              autoGeneratedProviderKey ? "已自动生成" : "留空时由服务器自动生成"
            }
          />
          <small className="csm-muted">
            可留空；自动生成的内部 Key 不会在界面中显示。
          </small>
        </Field>
        <Field label="描述" full>
          <input
            className="csm-input"
            value={description}
            maxLength={500}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>
        <div
          className="csm-full-col csm-provider-source-switch"
          role="group"
          aria-label="Provider 内容来源"
        >
          <span>内容来源</span>
          <button
            className={`csm-btn csm-btn-sm ${sourceMode === "subscription" ? "csm-btn-primary" : "csm-btn-secondary"}`}
            type="button"
            onClick={() => setSourceMode("subscription")}
          >
            规则订阅
          </button>
          <button
            className={`csm-btn csm-btn-sm ${sourceMode === "manual" ? "csm-btn-primary" : "csm-btn-secondary"}`}
            type="button"
            onClick={() => setSourceMode("manual")}
          >
            自定义
          </button>
        </div>
        <Field label="Behavior">
          <select
            className="csm-select"
            value={behavior}
            onChange={(event) => setConfigField("behavior", event.target.value)}
          >
            <option value="domain">domain（域名）</option>
            <option value="classical">classical（完整规则）</option>
            <option value="ipcidr">ipcidr（IP 网段）</option>
          </select>
        </Field>
        {sourceMode === "subscription" ? (
          <>
            <div className="csm-full-col">
              <Alert type="info">
                保存时由服务器下载并保存规则内容；最终订阅只会包含展开后的具体规则，不会包含此
                URL。
              </Alert>
            </div>
            <Field label="规则订阅链接" full>
              <input
                className="csm-input csm-code-editor"
                type="url"
                value={String(config.sourceUrl ?? config.url ?? "")}
                onChange={(event) =>
                  setConfig((current) => ({
                    ...current,
                    sourceUrl: event.target.value,
                    url: event.target.value,
                  }))
                }
                placeholder="https://example.com/provider.yaml"
              />
            </Field>
          </>
        ) : (
          <>
            <Field label="自定义规则（每行一条）" full>
              <textarea
                className="csm-textarea csm-code-editor"
                rows={12}
                spellCheck={false}
                value={manualContent}
                onChange={(event) => setManualContent(event.target.value)}
                placeholder="每行输入一条规则"
              />
              <small className="csm-muted">
                当前 behavior 为 <code>{behavior}</code>，每行会作为一条 payload
                保存，空行自动忽略。
              </small>
            </Field>
          </>
        )}
      </div>
    </Modal>
  );
}

function nodeGroupKindText(kind: NodeGroup["kind"]): string {
  return kind === "custom"
    ? "自定义分组"
    : kind === "region"
      ? "地域分组"
      : "延迟分组";
}

function nodeGroupKindBadge(kind: NodeGroup["kind"]) {
  const color =
    kind === "custom" ? "green" : kind === "region" ? "blue" : "amber";
  return <Badge color={color}>{nodeGroupKindText(kind)}</Badge>;
}

function nodeRef(node: Node): string {
  return node.alias || node.name;
}

function nodeGroupSummary(group: NodeGroup, nodes: Node[]): string {
  const config = group.config;
  if (group.kind === "custom") {
    return `已选 ${group.memberCount} 个节点`;
  }
  if (group.kind === "region") {
    const countries = stringList(config.countries);
    const labels = countries
      .map((code) => {
        const label = nodes.find((node) => node.country === code)?.countryLabel;
        return label ?? code;
      })
      .filter(Boolean);
    return `国家/地区：${labels.length ? labels.join("、") : countries.join("、")}`;
  }
  if (String(config.mode ?? "top") === "threshold") {
    return `延迟 ≤ ${config.thresholdMs ?? 0} ms`;
  }
  return `延迟最低前 ${config.count ?? 5} 个`;
}

function NodeGroupsView({
  groups,
  nodes,
  loading,
  pending,
  onAdd,
  onEdit,
  onRemove,
}: {
  groups: NodeGroup[];
  nodes: Node[];
  loading: boolean;
  pending: string;
  onAdd: () => void;
  onEdit: (group: NodeGroup) => void;
  onRemove: (group: NodeGroup) => void;
}) {
  return (
    <div className="csm-panel">
      <Toolbar
        title="节点分组"
        icon={<FolderOpen size={18} />}
        actions={
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            onClick={onAdd}
          >
            <Plus size={14} />
            新增分组
          </button>
        }
      />
      <Alert type="info">
        节点分组用于在策略组中批量选择节点：支持手动勾选的自定义分组、按国家/地区匹配的地域分组，以及按
        TCP 延迟排序的前 N 个或阈值以内节点的延迟分组。
      </Alert>
      {loading ? (
        <Loading text="加载节点分组…" />
      ) : !groups.length ? (
        <EmptyState
          icon={<FolderOpen size={32} />}
          title="暂无节点分组"
          hint="新增分组后，可在编辑规则组的策略组中直接选择"
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>匹配条件</th>
              <th>成员</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <tr key={group.id}>
                <td>
                  <strong>{group.name}</strong>
                </td>
                <td>{nodeGroupKindBadge(group.kind)}</td>
                <td>
                  <span className="csm-cell-note">
                    {nodeGroupSummary(group, nodes)}
                  </span>
                </td>
                <td>
                  <Badge color="blue">{group.memberCount} 个节点</Badge>
                </td>
                <td>{stamp(group.updatedAt)}</td>
                <td>
                  <Actions>
                    <button
                      className="csm-btn csm-btn-sm csm-btn-ghost"
                      type="button"
                      title="编辑"
                      onClick={() => onEdit(group)}
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      className="csm-btn csm-btn-sm csm-btn-ghost csm-danger-text"
                      type="button"
                      title="删除"
                      disabled={pending === `node-group-delete-${group.id}`}
                      onClick={() => onRemove(group)}
                    >
                      {pending === `node-group-delete-${group.id}` ? (
                        <Spin size={13} />
                      ) : (
                        <Trash2 size={13} />
                      )}
                    </button>
                  </Actions>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <p className="csm-footnote">
        地域与延迟分组为动态规则：策略组在发布时会按当前节点池实时解析成员；自定义分组则固定保存所选节点。系统每
        2 分钟自动进行一次全局 TCP 探测，延迟分组会随最新结果更新。
      </p>
    </div>
  );
}

function NodeGroupModal({
  group,
  nodes,
  onNodesChange,
  onClose,
  onSaved,
}: {
  group: NodeGroup | null;
  nodes: Node[];
  onNodesChange: (nodes: Node[]) => void;
  onClose: () => void;
  onSaved: (message: string) => Promise<void>;
}) {
  const config = group?.config ?? {};
  const [name, setName] = useState(group?.name ?? "节点分组");
  const [kind, setKind] = useState<NodeGroup["kind"]>(group?.kind ?? "custom");
  const [nodeIds, setNodeIds] = useState<string[]>(stringList(config.nodeIds));
  const [nodeSearch, setNodeSearch] = useState("");
  const [countries, setCountries] = useState<string[]>(
    stringList(config.countries),
  );
  const [latencyMode, setLatencyMode] = useState<"top" | "threshold">(
    String(config.mode ?? "top") === "threshold" ? "threshold" : "top",
  );
  const [latencyCount, setLatencyCount] = useState<number>(
    Number(config.count ?? 5),
  );
  const [latencyThreshold, setLatencyThreshold] = useState<number>(
    Number(config.thresholdMs ?? 200),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [geoStatus, setGeoStatus] = useState<"idle" | "loading" | "partial">(
    "idle",
  );
  const geoAttemptedRef = useRef<Set<string>>(new Set());

  const visibleNodes = useMemo(() => {
    const keyword = nodeSearch.trim().toLowerCase();
    return nodes.filter(
      (node) =>
        !keyword ||
        `${node.name} ${node.alias} ${node.displayName} ${node.protocol} ${node.server}`
          .toLowerCase()
          .includes(keyword),
    );
  }, [nodes, nodeSearch]);

  const selectedNodes = useMemo(
    () => nodes.filter((node) => nodeIds.includes(node.id)),
    [nodes, nodeIds],
  );
  const pendingGeoNodes = useMemo(
    () =>
      kind === "region"
        ? selectedNodes.filter(
            (node) =>
              (!node.country || node.geoError) &&
              !geoAttemptedRef.current.has(node.id),
          )
        : [],
    [kind, selectedNodes],
  );
  const pendingGeoKey = pendingGeoNodes.map((node) => node.id).join(",");

  const hasUnknownCountry =
    kind === "region" &&
    selectedNodes.some((node) => !node.country || Boolean(node.geoError));

  useEffect(() => {
    if (!pendingGeoKey) {
      setGeoStatus("idle");
      return;
    }
    const requestIds = pendingGeoKey.split(",");
    let stale = false;
    setGeoStatus("loading");
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const data = await apiPost<{ nodes: Node[] }>(`${API}/nodes/geoip`, {
            nodeIds: requestIds,
          });
          if (stale) return;
          requestIds.forEach((id) => geoAttemptedRef.current.add(id));
          onNodesChange(data.nodes);
          setGeoStatus(
            data.nodes.some((node) => !node.country) ? "partial" : "idle",
          );
        } catch {
          if (stale) return;
          requestIds.forEach((id) => geoAttemptedRef.current.add(id));
          setGeoStatus("partial");
        }
      })();
    }, 400);

    return () => {
      stale = true;
      clearTimeout(timer);
    };
    // The key intentionally drives this effect; individual node objects are
    // refreshed by the response and must not restart the same request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingGeoKey]);

  const countryOptions = useMemo(() => {
    const seen = new Map<string, string>();
    const sourceNodes = nodeIds.length ? selectedNodes : nodes;
    for (const node of sourceNodes) {
      if (node.country && !seen.has(node.country)) {
        seen.set(node.country, node.countryLabel || node.country);
      }
    }
    return Array.from(seen.entries()).map(([code, label]) => ({
      code,
      label,
    }));
  }, [nodes, selectedNodes, nodeIds.length]);

  const previewMembers = useMemo(() => {
    const refOf = (node: Node) => node.alias || node.name;
    const scoped = nodeIds.length
      ? nodes.filter((node) => nodeIds.includes(node.id))
      : nodes;
    if (kind === "custom") {
      return scoped.map(refOf);
    }
    if (kind === "region") {
      return scoped
        .filter((node) => node.country && countries.includes(node.country))
        .map(refOf);
    }
    const withLatency = scoped
      .filter((node) => node.tcp?.reachable && node.tcp.latencyMs != null)
      .sort((a, b) => (a.tcp?.latencyMs ?? 0) - (b.tcp?.latencyMs ?? 0));
    if (latencyMode === "threshold") {
      return withLatency
        .filter((node) => (node.tcp?.latencyMs ?? 0) <= latencyThreshold)
        .map(refOf);
    }
    return withLatency.slice(0, latencyCount).map(refOf);
  }, [
    kind,
    nodeIds,
    nodes,
    countries,
    latencyMode,
    latencyCount,
    latencyThreshold,
  ]);

  function buildConfig(): Record<string, unknown> {
    if (kind === "custom") return { nodeIds };
    if (kind === "region") {
      return { countries, nodeIds };
    }
    return {
      mode: latencyMode,
      count: Math.max(1, latencyCount || 5),
      thresholdMs: Math.max(0, latencyThreshold || 0),
      nodeIds,
    };
  }

  async function save() {
    if (!name.trim()) {
      setError("请输入分组名称。");
      return;
    }
    if (kind === "custom" && !nodeIds.length) {
      setError("请至少选择一个节点。");
      return;
    }
    if (kind === "region" && !countries.length) {
      setError("请至少选择一个国家或地区。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const payload = { name: name.trim(), kind, config: buildConfig() };
      if (group) await apiPut(`${API}/node-groups/${group.id}`, payload);
      else await apiPost(`${API}/node-groups`, payload);
      await onSaved(group ? "节点分组已更新" : "节点分组已创建");
    } catch (caught) {
      setError(message(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={group ? `编辑节点分组：${group.name}` : "新增节点分组"}
      width={760}
      onClose={() => !saving && onClose()}
      foot={
        <>
          <button
            className="csm-btn csm-btn-secondary"
            type="button"
            disabled={saving}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? (
              <>
                <Spin size={14} />
                保存中
              </>
            ) : (
              "保存分组"
            )}
          </button>
        </>
      }
    >
      <div className="csm-form-grid csm-strategy-editor">
        {error && (
          <div className="csm-full-col">
            <Alert type="error">{error}</Alert>
          </div>
        )}
        <Field label="分组名称">
          <input
            className="csm-input"
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：香港日本节点"
          />
        </Field>
        <Field label="分组类型">
          <div
            className="csm-provider-source-switch"
            role="group"
            aria-label="分组类型"
          >
            <button
              className={`csm-btn csm-btn-sm ${kind === "custom" ? "csm-btn-primary" : "csm-btn-secondary"}`}
              type="button"
              onClick={() => setKind("custom")}
            >
              自定义
            </button>
            <button
              className={`csm-btn csm-btn-sm ${kind === "region" ? "csm-btn-primary" : "csm-btn-secondary"}`}
              type="button"
              onClick={() => setKind("region")}
            >
              地域
            </button>
            <button
              className={`csm-btn csm-btn-sm ${kind === "latency" ? "csm-btn-primary" : "csm-btn-secondary"}`}
              type="button"
              onClick={() => setKind("latency")}
            >
              延迟
            </button>
          </div>
        </Field>
        <Field
          label={
            kind === "custom"
              ? `选择节点（已选 ${nodeIds.length}）`
              : `选择节点（已选 ${nodeIds.length}，默认全部）`
          }
          full
        >
          <div className="csm-strategy-picker">
            <label className="csm-search">
              <Search size={15} />
              <input
                value={nodeSearch}
                onChange={(event) => setNodeSearch(event.target.value)}
                placeholder="按节点名称、别名、协议或地址搜索"
              />
            </label>
            <div className="csm-strategy-node-list">
              {visibleNodes.map((node) => (
                <label key={node.id}>
                  <input
                    type="checkbox"
                    checked={nodeIds.includes(node.id)}
                    onChange={(event) =>
                      setNodeIds((current) =>
                        event.target.checked
                          ? [...new Set([...current, node.id])]
                          : current.filter((id) => id !== node.id),
                      )
                    }
                  />
                  <span>
                    <strong>{node.displayName || node.name}</strong>
                    {node.alias && <small>原名：{node.name}</small>}
                    <small>
                      {node.protocol} · {node.server}:{node.port ?? "—"}
                      {node.country
                        ? ` · ${node.countryLabel ?? node.country}`
                        : ""}
                    </small>
                  </span>
                </label>
              ))}
              {!visibleNodes.length && (
                <div className="csm-inline-empty">没有符合条件的节点</div>
              )}
            </div>
          </div>
        </Field>
        {kind === "region" && (
          <Field label={`国家 / 地区（多选，已选 ${countries.length}）`} full>
            {(geoStatus !== "idle" || hasUnknownCountry) && (
              <div className="csm-muted" style={{ marginBottom: 8 }}>
                {geoStatus === "loading"
                  ? "正在识别国家/地区…"
                  : "部分节点国家/地区识别失败，将按未知处理。"}
              </div>
            )}
            <div className="csm-check-grid">
              {countryOptions.map((option) => (
                <label className="csm-check" key={option.code}>
                  <input
                    type="checkbox"
                    checked={countries.includes(option.code)}
                    onChange={(event) =>
                      setCountries((current) =>
                        event.target.checked
                          ? [...new Set([...current, option.code])]
                          : current.filter((code) => code !== option.code),
                      )
                    }
                  />
                  {option.label}
                </label>
              ))}
              {!countryOptions.length && (
                <span className="csm-muted">暂无可识别的国家/地区。</span>
              )}
            </div>
          </Field>
        )}
        {kind === "latency" && (
          <>
            <Field label="延迟模式">
              <select
                className="csm-select"
                value={latencyMode}
                onChange={(event) =>
                  setLatencyMode(event.target.value as "top" | "threshold")
                }
              >
                <option value="top">延迟最低的前 N 个节点</option>
                <option value="threshold">延迟阈值以内的节点</option>
              </select>
            </Field>
            {latencyMode === "top" ? (
              <Field label="节点数量">
                <input
                  className="csm-input"
                  type="number"
                  min={1}
                  max={500}
                  value={latencyCount}
                  onChange={(event) =>
                    setLatencyCount(Number(event.target.value))
                  }
                />
              </Field>
            ) : (
              <Field label="延迟阈值（毫秒）">
                <input
                  className="csm-input"
                  type="number"
                  min={0}
                  max={100000}
                  value={latencyThreshold}
                  onChange={(event) =>
                    setLatencyThreshold(Number(event.target.value))
                  }
                />
              </Field>
            )}
            <Field label="说明" full>
              <small className="csm-muted">
                系统每 2 分钟自动探测一次全部节点，也支持手动探测。延迟仅表示
                TCP
                端口连通速度，不等同于代理真实延迟。没有可达探测记录的节点不会进入分组。
              </small>
            </Field>
          </>
        )}
        <Field label={`成员预览（${previewMembers.length} 个）`} full>
          <div className="csm-node-group-members">
            {previewMembers.slice(0, 12).map((member) => (
              <code key={member}>{member}</code>
            ))}
            {previewMembers.length > 12 && (
              <small>等 {previewMembers.length} 个</small>
            )}
            {!previewMembers.length && (
              <span className="csm-muted">当前条件下没有匹配到节点。</span>
            )}
          </div>
        </Field>
      </div>
    </Modal>
  );
}

function RuleDomainTestModal({
  ruleSet,
  onClose,
}: {
  ruleSet: RuleSet;
  onClose: () => void;
}) {
  const [domain, setDomain] = useState("");
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<DomainTestResult | null>(null);
  const [error, setError] = useState("");

  async function test(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = domain.trim();
    if (!value) {
      setError("请输入要测试的域名。");
      return;
    }
    setTesting(true);
    setError("");
    setResult(null);
    try {
      const data = await apiPost<DomainTestResult>(
        `${API}/rule-sets/${ruleSet.id}/test-domain`,
        { domain: value },
      );
      setResult(data);
    } catch (caught) {
      setError(message(caught));
    } finally {
      setTesting(false);
    }
  }

  return (
    <Modal
      title={`测试域名 · ${ruleSet.name}`}
      onClose={() => !testing && onClose()}
      foot={
        <button
          className="csm-btn csm-btn-secondary"
          type="button"
          disabled={testing}
          onClick={onClose}
        >
          关闭
        </button>
      }
    >
      <form
        className="csm-domain-test-form"
        onSubmit={(event) => void test(event)}
      >
        <Field label="域名">
          <input
            className="csm-input"
            type="text"
            value={domain}
            placeholder="example.com"
            autoComplete="off"
            onChange={(event) => setDomain(event.target.value)}
          />
        </Field>
        <button
          className="csm-btn csm-btn-primary"
          type="submit"
          disabled={testing}
        >
          {testing ? <Spin size={14} /> : <Crosshair size={14} />}
          测试命中
        </button>
        {error && <Alert type="error">{error}</Alert>}
        {result && (
          <div className="csm-domain-test-result">
            <span>命中策略</span>
            <strong>
              {result.matched ? result.target || "未知策略" : "未命中"}
            </strong>
            <code>
              {result.matched ? result.rule : `没有规则匹配 ${result.domain}`}
            </code>
            {result.ruleIndex !== null && (
              <small>第 {result.ruleIndex + 1} 条规则</small>
            )}
          </div>
        )}
      </form>
    </Modal>
  );
}

function RulesView({
  ruleSets,
  loading,
  onImport,
  onCreate,
  onEdit,
  onRemove,
}: {
  ruleSets: RuleSet[];
  loading: boolean;
  onImport: () => void;
  onCreate: () => void;
  onEdit: (rule: RuleSet) => void;
  onRemove: (rule: RuleSet) => void;
}) {
  const [testingRule, setTestingRule] = useState<RuleSet | null>(null);
  return (
    <div className="csm-panel">
      <Toolbar
        title="规则组"
        icon={<FileCode2 size={18} />}
        actions={
          <>
            <button
              className="csm-btn csm-btn-secondary"
              type="button"
              onClick={onCreate}
            >
              <Plus size={14} />
              手动新增
            </button>
            <button
              className="csm-btn csm-btn-primary"
              type="button"
              onClick={onImport}
            >
              <Link size={14} />
              从订阅链接 / YAML 导入
            </button>
          </>
        }
      />
      {loading ? (
        <Loading text="加载规则组…" />
      ) : !ruleSets.length ? (
        <EmptyState
          icon={<FileCode2 size={32} />}
          title="暂无规则组"
          hint="可以使用默认模板手动新增，或只从订阅导入规则"
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <th>名称</th>
              <th>规则</th>
              <th>策略组</th>
              <th>Rule Provider</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {ruleSets.map((rule) => (
              <tr key={rule.id}>
                <td>
                  <strong>{rule.name}</strong>
                </td>
                <td>{rule.rules.length}</td>
                <td>{rule.groups.length}</td>
                <td>{Object.keys(rule.providers).length}</td>
                <td>{stamp(rule.updatedAt)}</td>
                <td>
                  <Actions>
                    <button
                      className="csm-btn csm-btn-sm csm-btn-ghost"
                      type="button"
                      title="测试域名"
                      onClick={() => setTestingRule(rule)}
                    >
                      <Crosshair size={13} />
                    </button>
                    <button
                      className="csm-btn csm-btn-sm csm-btn-ghost"
                      type="button"
                      title="编辑"
                      onClick={() => onEdit(rule)}
                    >
                      <Pencil size={13} />
                    </button>
                    <button
                      className="csm-btn csm-btn-sm csm-btn-ghost csm-danger-text"
                      type="button"
                      title="删除"
                      onClick={() => onRemove(rule)}
                    >
                      <Trash2 size={13} />
                    </button>
                  </Actions>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <p className="csm-footnote">
        从订阅导入时只保存 rules、proxy-groups 和
        rule-providers，不会导入或覆盖节点池中的节点。
      </p>
      {testingRule && (
        <RuleDomainTestModal
          ruleSet={testingRule}
          onClose={() => setTestingRule(null)}
        />
      )}
    </div>
  );
}

function SourceModal({
  source,
  initialUrl,
  onClose,
  onSaved,
}: {
  source: Source | null;
  initialUrl: string;
  onClose: () => void;
  onSaved: (message: string) => Promise<void>;
}) {
  const [form, setForm] = useState({
    name: source?.name ?? "",
    url: source ? "" : initialUrl,
    userAgent: source?.userAgent ?? "",
    refreshSeconds: source?.refreshSeconds ?? 21600,
    enabled: source?.enabled ?? true,
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    if (!source && !form.url.trim()) {
      setError("请输入订阅地址。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (source)
        await apiPut(`${API}/sources/${source.id}`, {
          ...form,
          url: form.url.trim() || undefined,
        });
      else await apiPost(`${API}/sources`, form);
      await onSaved(source ? "订阅源已更新" : "订阅源已添加");
    } catch (caught) {
      setError(message(caught));
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal
      title={source ? "编辑订阅源" : "添加订阅源"}
      width={620}
      onClose={() => !saving && onClose()}
      foot={
        <>
          <button
            className="csm-btn csm-btn-secondary"
            type="button"
            disabled={saving}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? (
              <>
                <Spin size={14} />
                保存中
              </>
            ) : (
              "保存"
            )}
          </button>
        </>
      }
    >
      <div className="csm-form-grid">
        {error && (
          <div className="csm-full-col">
            <Alert type="error">{error}</Alert>
          </div>
        )}
        <Field label="名称">
          <input
            className="csm-input"
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="例如：主订阅"
          />
        </Field>
        <Field label="刷新周期">
          <select
            className="csm-select"
            value={form.refreshSeconds}
            onChange={(event) =>
              setForm({ ...form, refreshSeconds: Number(event.target.value) })
            }
          >
            <option value={3600}>每小时</option>
            <option value={21600}>每 6 小时</option>
            <option value={43200}>每 12 小时</option>
            <option value={86400}>每天</option>
          </select>
        </Field>
        <Field label={source ? "订阅地址（留空保持不变）" : "订阅地址"} full>
          <input
            className="csm-input"
            type="url"
            value={form.url}
            onChange={(event) => setForm({ ...form, url: event.target.value })}
            placeholder={
              source ? source.url : "https://example.com/subscription"
            }
          />
        </Field>
        <Field label="User-Agent" full>
          <input
            className="csm-input"
            value={form.userAgent}
            onChange={(event) =>
              setForm({ ...form, userAgent: event.target.value })
            }
            placeholder="留空使用工具默认值"
          />
        </Field>
        <Field label="状态" full>
          <label className="csm-check">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(event) =>
                setForm({ ...form, enabled: event.target.checked })
              }
            />
            启用定时刷新
          </label>
        </Field>
      </div>
    </Modal>
  );
}

function ProfileModal({
  profile,
  ruleSets,
  onClose,
  onSaved,
}: {
  profile: Profile | null;
  ruleSets: RuleSet[];
  onClose: () => void;
  onSaved: (message: string) => Promise<void>;
}) {
  const [name, setName] = useState(profile?.name ?? "");
  const [ruleSetId, setRuleSetId] = useState(profile?.ruleSetId ?? "");
  const [settings, setSettings] = useState<ProfileSettings>({
    mixedPort: 7890,
    allowLan: false,
    mode: "rule",
    ipv6: false,
    ruleProviderOutputMode: "inline",
    ...(profile?.settings ?? {}),
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [diagnostic, setDiagnostic] = useState<ProfilePreview | null>(null);

  async function save() {
    if (!name.trim()) {
      setError("请输入配置名称。");
      return;
    }
    if (!ruleSetId) {
      setError("请选择规则组；输出节点将由规则组决定。");
      return;
    }
    setSaving(true);
    setError("");
    setDiagnostic(null);
    let saved: Profile | undefined;
    try {
      const payload = {
        name: name.trim(),
        settings: {
          ...settings,
          mode: "rule",
        },
        ruleSetId,
      };
      saved = profile
        ? (
            await apiPut<{ profile: Profile }>(
              `${API}/profiles/${profile.id}`,
              payload,
            )
          ).profile
        : (await apiPost<{ profile: Profile }>(`${API}/profiles`, payload))
            .profile;
      await apiPost(`${API}/profiles/${saved.id}/publish`, {});
      await onSaved(profile ? "聚合配置已更新并发布" : "聚合配置已创建并发布");
    } catch (caught) {
      const failureMessage = message(caught);
      setError(failureMessage);
      if (
        saved &&
        caught instanceof ApiError &&
        caught.code === "PUBLISH_VALIDATION_FAILED"
      ) {
        try {
          setDiagnostic(
            await apiGet<ProfilePreview>(`${API}/profiles/${saved.id}/preview`),
          );
        } catch {
          // Keep the original publish error visible if diagnostics cannot be
          // fetched.
        }
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={profile ? "编辑聚合配置" : "新建聚合配置"}
      width={640}
      onClose={() => !saving && onClose()}
      foot={
        <>
          <button
            className="csm-btn csm-btn-secondary"
            type="button"
            disabled={saving}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            disabled={saving}
            onClick={() => void save()}
          >
            {saving ? (
              <>
                <Spin size={14} />
                保存中
              </>
            ) : (
              "保存并发布"
            )}
          </button>
        </>
      }
    >
      <div className="csm-form-grid">
        {error && (
          <div className="csm-full-col">
            <Alert type="error">{error}</Alert>
          </div>
        )}
        <Field label="配置名称">
          <input
            className="csm-input"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label="规则组">
          <select
            className="csm-select"
            value={ruleSetId}
            onChange={(event) => setRuleSetId(event.target.value)}
          >
            <option value="">请选择规则组</option>
            {ruleSets.map((rule) => (
              <option value={rule.id} key={rule.id}>
                {rule.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Mixed Port">
          <input
            className="csm-input"
            type="number"
            min={1}
            max={65535}
            value={settings.mixedPort ?? 7890}
            onChange={(event) =>
              setSettings({
                ...settings,
                mixedPort: Number(event.target.value),
              })
            }
          />
        </Field>
        <Field label="Rule Provider 输出">
          <select
            className="csm-select"
            value={settings.ruleProviderOutputMode ?? "inline"}
            onChange={(event) =>
              setSettings({
                ...settings,
                ruleProviderOutputMode:
                  event.target.value === "url" ? "url" : "inline",
              })
            }
          >
            <option value="inline">URL 拉取后优先</option>
            <option value="url">URL 优先</option>
          </select>
          <small className="csm-muted">
            URL 拉取后优先会在服务端下载规则并直接写入最终 YAML；URL
            优先则由客户端按 rule-providers 中的 URL 拉取。
          </small>
        </Field>
        <Field label="网络选项">
          <div className="csm-inline-checks">
            <label className="csm-check">
              <input
                type="checkbox"
                checked={Boolean(settings.allowLan)}
                onChange={(event) =>
                  setSettings({ ...settings, allowLan: event.target.checked })
                }
              />
              允许局域网
            </label>
            <label className="csm-check">
              <input
                type="checkbox"
                checked={Boolean(settings.ipv6)}
                onChange={(event) =>
                  setSettings({ ...settings, ipv6: event.target.checked })
                }
              />
              启用 IPv6
            </label>
          </div>
        </Field>
        <div className="csm-full-col">
          <small className="csm-muted">
            输出固定为 Clash Meta YAML，运行模式固定为
            Rule；节点与策略组完全由所选规则组决定。
          </small>
        </div>
      </div>
      {diagnostic && (
        <ProfileDiagnosticModal
          profileName={name || "未命名配置"}
          preview={diagnostic}
          onClose={() => setDiagnostic(null)}
        />
      )}
    </Modal>
  );
}

function ProfileDiagnosticModal({
  profileName,
  preview,
  onClose,
}: {
  profileName: string;
  preview: ProfilePreview;
  onClose: () => void;
}) {
  const [copyState, setCopyState] = useState<"idle" | "success" | "failed">(
    "idle",
  );
  const validationText = preview.messages
    .map(
      (item) =>
        `${item.level.toUpperCase()} ${item.code ?? "UNKNOWN"}：${item.message}`,
    )
    .join("\n");
  const diagnosticText = [
    "Clash Meta 配置诊断",
    `配置名称：${profileName}`,
    `校验结果：${preview.valid ? "通过" : "未通过"}`,
    "",
    "===== 校验消息 =====",
    validationText || "无",
    "",
    "===== 生成过程日志 =====",
    preview.buildLog.join("\n"),
    "",
    "===== YAML 原文 =====",
    preview.yaml,
  ].join("\n");

  async function copyDiagnostic() {
    try {
      await navigator.clipboard.writeText(diagnosticText);
      setCopyState("success");
    } catch {
      setCopyState("failed");
    }
  }

  return (
    <Modal
      title="配置校验诊断"
      width={900}
      onClose={onClose}
      foot={
        <>
          <span className="csm-footnote">
            {copyState === "success"
              ? "诊断信息已复制。"
              : copyState === "failed"
                ? "复制失败，请手动选择内容复制。"
                : "复制内容包含校验消息、生成过程日志和 YAML 原文。"}
          </span>
          <button
            className="csm-btn csm-btn-secondary"
            type="button"
            onClick={onClose}
          >
            关闭
          </button>
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            onClick={() => void copyDiagnostic()}
          >
            <Clipboard size={14} />
            复制诊断信息
          </button>
        </>
      }
    >
      <div className="csm-stack">
        <Alert type="error">
          配置校验未通过，已发布版本未被覆盖。请结合下方过程日志与 YAML
          原文定位问题。
        </Alert>
        <div className="csm-preview">
          <strong>校验消息</strong>
          <pre>{validationText || "无"}</pre>
          <strong>生成过程日志</strong>
          <pre>{preview.buildLog.join("\n")}</pre>
          <strong>YAML 原文</strong>
          <pre>{preview.yaml}</pre>
        </div>
      </div>
    </Modal>
  );
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function providerKeyText(provider: RuleProviderLibraryItem): string {
  return /^rp-[0-9a-f]{32}$/.test(provider.providerKey)
    ? "自动生成"
    : provider.providerKey;
}

function providerBindings(
  value?: Record<string, unknown>,
): Record<string, string[]> {
  const raw = value?.providerBindings;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  return Object.fromEntries(
    Object.entries(raw as Record<string, unknown>).map(([name, ids]) => [
      name,
      stringList(ids),
    ]),
  );
}

function initialRuleGroups(
  ruleSet: RuleSet | undefined,
  library: RuleProviderLibraryItem[],
): { groups: RuleGroupDraft[]; fallback: RuleGroupDraft } {
  const rawGroups = ruleSet?.groups?.length
    ? ruleSet.groups
    : [{ name: "PROXY", type: "select", proxies: ["DIRECT"] }];
  const bindings = providerBindings(ruleSet?.importMeta);
  const providerByKey = new Map(
    library.map((provider) => [provider.providerKey, provider.id]),
  );
  const bindingsFromRules: Record<string, string[]> = {};
  for (const rawRule of ruleSet?.rules ?? []) {
    if (typeof rawRule !== "string") continue;
    const parts = rawRule.split(",").map((part) => part.trim());
    if (parts[0]?.toUpperCase() !== "RULE-SET" || !parts[1] || !parts[2])
      continue;
    const providerId = providerByKey.get(parts[1]);
    if (providerId)
      bindingsFromRules[parts[2]] = [
        ...(bindingsFromRules[parts[2]] ?? []),
        providerId,
      ];
  }
  const drafts = rawGroups.map((rawGroup, index) => {
    const {
      name,
      type,
      proxies,
      nodeGroups: rawNodeGroups,
      ...extra
    } = rawGroup;
    const groupName = String(name ?? `PROXY-${index + 1}`);
    return {
      id: `strategy-${index}-${groupName}`,
      name: groupName,
      type: String(type ?? "select"),
      proxies: stringList(proxies),
      nodeGroups: stringList(rawNodeGroups),
      providerIds: [
        ...new Set(bindings[groupName] ?? bindingsFromRules[groupName] ?? []),
      ],
      extra,
    };
  });
  const savedFallbackName =
    typeof ruleSet?.importMeta?.fallbackStrategyName === "string"
      ? ruleSet.importMeta.fallbackStrategyName
      : "";
  const savedFallbackIndex = savedFallbackName
    ? drafts.findIndex((group) => group.name === savedFallbackName)
    : -1;
  if (savedFallbackIndex >= 0) {
    const fallback = {
      ...drafts[savedFallbackIndex],
      providerIds: [],
      fallback: true,
    };
    return {
      groups: drafts.filter((_, index) => index !== savedFallbackIndex),
      fallback,
    };
  }

  const usedNames = new Set(drafts.map((group) => group.name));
  let fallbackName = "兜底策略";
  let suffix = 2;
  while (usedNames.has(fallbackName)) fallbackName = `兜底策略-${suffix++}`;
  const source = drafts[0];
  return {
    groups: drafts,
    fallback: {
      id: `strategy-fallback-${fallbackName}`,
      name: fallbackName,
      type: source?.type ?? "select",
      proxies: source?.proxies.length ? [...source.proxies] : ["DIRECT"],
      nodeGroups: [],
      providerIds: [],
      extra: { ...(source?.extra ?? {}) },
      fallback: true,
    },
  };
}

function RuleSetModal({
  mode,
  ruleSet,
  nodes,
  providerLibrary,
  nodeGroups,
  onClose,
  onSaved,
}: {
  mode: "edit" | "import" | "create";
  ruleSet?: RuleSet;
  nodes: Node[];
  providerLibrary: RuleProviderLibraryItem[];
  nodeGroups: NodeGroup[];
  onClose: () => void;
  onSaved: (message: string) => Promise<void>;
}) {
  const isEdit = mode === "edit";
  const isImport = mode === "import";
  const [name, setName] = useState(
    ruleSet?.name ?? (isImport ? "导入规则" : "新建规则组"),
  );
  const [subscriptionUrl, setSubscriptionUrl] = useState("");
  const [yamlContent, setYamlContent] = useState("");
  const [initialGroups] = useState(() =>
    initialRuleGroups(ruleSet, providerLibrary),
  );
  const [groups, setGroups] = useState<RuleGroupDraft[]>(initialGroups.groups);
  const [fallbackGroup, setFallbackGroup] = useState<RuleGroupDraft>(
    initialGroups.fallback,
  );
  const [editing, setEditing] = useState<{
    index: number | null;
    value: RuleGroupDraft;
    fallback?: boolean;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function newGroup(): RuleGroupDraft {
    const used = new Set([
      ...groups.map((group) => group.name),
      fallbackGroup.name,
    ]);
    let nextName = "PROXY";
    let suffix = 2;
    while (used.has(nextName)) nextName = `PROXY-${suffix++}`;
    return {
      id: `strategy-new-${Date.now()}`,
      name: nextName,
      type: "select",
      proxies: ["DIRECT"],
      nodeGroups: [],
      providerIds: [],
      extra: {},
    };
  }

  function saveGroup(value: RuleGroupDraft) {
    if (editing?.fallback) {
      setFallbackGroup({ ...value, providerIds: [], fallback: true });
      setEditing(null);
      setError("");
      return;
    }
    const duplicate =
      value.name === fallbackGroup.name ||
      groups.some(
        (group, index) => group.name === value.name && index !== editing?.index,
      );
    if (duplicate) {
      setError(`策略组名称“${value.name}”已存在。`);
      return;
    }
    setGroups((current) =>
      editing?.index === null
        ? [...current, value]
        : current.map((group, index) =>
            index === editing?.index ? value : group,
          ),
    );
    setEditing(null);
    setError("");
  }

  function moveGroup(index: number, offset: -1 | 1) {
    setGroups((current) => {
      const target = index + offset;
      if (
        index < 0 ||
        index >= current.length ||
        target < 0 ||
        target >= current.length
      )
        return current;
      const reordered = [...current];
      [reordered[index], reordered[target]] = [
        reordered[target],
        reordered[index],
      ];
      return reordered;
    });
  }

  async function save() {
    if (!name.trim()) {
      setError("请输入规则组名称。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (isImport) {
        if (!subscriptionUrl.trim() && !yamlContent.trim())
          throw new Error("请输入订阅链接或粘贴 Clash YAML 内容。");
        await apiPost(`${API}/rule-sets/import`, {
          name: name.trim(),
          url: subscriptionUrl.trim(),
          content: yamlContent.trim(),
        });
      } else {
        const cleanGroups = [...groups, fallbackGroup].map(
          ({
            id: _id,
            providerIds: _providerIds,
            fallback: _fallback,
            nodeGroups: groupNodeGroups,
            name: groupName,
            type,
            proxies,
            extra,
          }) => ({
            ...extra,
            name: groupName,
            type,
            proxies,
            nodeGroups: [...new Set(groupNodeGroups)],
          }),
        );
        const bindings = Object.fromEntries(
          groups.map((group) => [group.name, [...new Set(group.providerIds)]]),
        );
        const rules = groups.flatMap((group) =>
          group.providerIds
            .map((providerId) => {
              const provider = providerLibrary.find(
                (item) => item.id === providerId,
              );
              return provider
                ? `RULE-SET,${provider.providerKey},${group.name}`
                : "";
            })
            .filter(Boolean),
        );
        rules.push(`MATCH,${fallbackGroup.name}`);
        const payload = {
          name: name.trim(),
          groups: cleanGroups,
          rules,
          providers: {},
          importMeta: {
            ...(ruleSet?.importMeta ?? {}),
            providerBindings: bindings,
            fallbackStrategyName: fallbackGroup.name,
            strategyGroupEditor: true,
          },
        };
        if (isEdit && ruleSet)
          await apiPut(`${API}/rule-sets/${ruleSet.id}`, payload);
        else await apiPost(`${API}/rule-sets`, payload);
      }
      await onSaved(
        isEdit
          ? "规则组已更新"
          : isImport
            ? "规则已导入，可点击编辑调整策略组"
            : "规则组已创建",
      );
    } catch (caught) {
      setError(message(caught));
    } finally {
      setSaving(false);
    }
  }

  const title = isEdit
    ? "编辑规则组"
    : isImport
      ? "导入 Clash 规则"
      : "手动新增规则组";
  const saveLabel = isEdit
    ? "保存规则组"
    : isImport
      ? "仅导入规则"
      : "创建规则组";
  return (
    <>
      <Modal
        title={title}
        width={860}
        onClose={() => !saving && !editing && onClose()}
        foot={
          <>
            <button
              className="csm-btn csm-btn-secondary"
              type="button"
              disabled={saving}
              onClick={onClose}
            >
              取消
            </button>
            <button
              className="csm-btn csm-btn-primary"
              type="button"
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? (
                <>
                  <Spin size={14} />
                  保存中
                </>
              ) : (
                saveLabel
              )}
            </button>
          </>
        }
      >
        <div className="csm-form-grid">
          {error && (
            <div className="csm-full-col">
              <Alert type="error">{error}</Alert>
            </div>
          )}
          <Field label="名称" full>
            <input
              className="csm-input"
              value={name}
              maxLength={120}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          {isImport ? (
            <>
              <div className="csm-full-col">
                <Alert type="info">
                  只导入订阅中的规则、策略组和 Rule
                  Provider，节点不会进入节点池。导入的 Provider 会进入独立的
                  Rule Provider 库。
                </Alert>
              </div>
              <Field label="订阅链接（与 YAML 二选一）" full>
                <input
                  className="csm-input"
                  type="url"
                  value={subscriptionUrl}
                  onChange={(event) => setSubscriptionUrl(event.target.value)}
                  placeholder="https://example.com/subscription?format=clash"
                />
              </Field>
              <Field label="Clash YAML（与订阅链接二选一）" full>
                <textarea
                  className="csm-textarea csm-code-editor"
                  rows={20}
                  spellCheck={false}
                  value={yamlContent}
                  onChange={(event) => setYamlContent(event.target.value)}
                  placeholder={
                    "proxy-groups:\n  - name: PROXY\n    type: select\n    proxies: [DIRECT]\nrule-providers:\n  example:\n    type: http\n    behavior: domain\n    url: https://example.com/rules.yaml\nrules:\n  - RULE-SET,example,PROXY\n  - MATCH,PROXY"
                  }
                />
              </Field>
            </>
          ) : (
            <div className="csm-full-col csm-strategy-section">
              <div className="csm-strategy-section-head">
                <div>
                  <strong>策略组</strong>
                  <span>
                    越靠上的策略组规则越先匹配；兜底策略在下方单独配置
                  </span>
                </div>
                <button
                  className="csm-btn csm-btn-sm csm-btn-primary"
                  type="button"
                  onClick={() => setEditing({ index: null, value: newGroup() })}
                >
                  <Plus size={13} />
                  新增策略组
                </button>
              </div>
              {!groups.length ? (
                <EmptyState
                  title="暂无策略组"
                  hint="新增策略组后，配置节点和使用的 Rule Provider"
                />
              ) : (
                <div className="csm-strategy-group-cards">
                  {groups.map((group, index) => (
                    <article className="csm-strategy-group-card" key={group.id}>
                      <div className="csm-strategy-card-main">
                        <div className="csm-strategy-card-title">
                          <strong>{group.name}</strong>
                          <Badge>顺序 {index + 1}</Badge>
                          <Badge color="blue">{group.type}</Badge>
                        </div>
                        <div className="csm-strategy-card-stats">
                          <span>
                            节点 <b>{group.proxies.length}</b>
                          </span>
                          <span>
                            代理规则 <b>{group.providerIds.length}</b>
                          </span>
                        </div>
                      </div>
                      <Actions>
                        <button
                          className="csm-btn csm-btn-sm csm-btn-ghost"
                          type="button"
                          title="上移策略组"
                          aria-label={`上移策略组 ${group.name}`}
                          disabled={index === 0}
                          onClick={() => moveGroup(index, -1)}
                        >
                          <ArrowUp size={13} />
                        </button>
                        <button
                          className="csm-btn csm-btn-sm csm-btn-ghost"
                          type="button"
                          title="下移策略组"
                          aria-label={`下移策略组 ${group.name}`}
                          disabled={index === groups.length - 1}
                          onClick={() => moveGroup(index, 1)}
                        >
                          <ArrowDown size={13} />
                        </button>
                        <button
                          className="csm-btn csm-btn-sm csm-btn-secondary"
                          type="button"
                          onClick={() => setEditing({ index, value: group })}
                        >
                          <Pencil size={13} />
                          编辑
                        </button>
                        <button
                          className="csm-btn csm-btn-sm csm-btn-ghost csm-danger-text"
                          type="button"
                          title="删除策略组"
                          onClick={() =>
                            setGroups((current) =>
                              current.filter(
                                (_, itemIndex) => itemIndex !== index,
                              ),
                            )
                          }
                        >
                          <Trash2 size={13} />
                        </button>
                      </Actions>
                    </article>
                  ))}
                </div>
              )}
              <div className="csm-fallback-strategy">
                <div className="csm-fallback-strategy-head">
                  <div>
                    <strong>默认兜底策略</strong>
                    <span>
                      固定用于 MATCH，不参与规则排序，不能删除或设置代理规则
                    </span>
                  </div>
                </div>
                <article className="csm-strategy-group-card csm-fallback-strategy-card">
                  <div className="csm-strategy-card-main">
                    <div className="csm-strategy-card-title">
                      <strong>兜底策略</strong>
                      <Badge color="blue">不可删除</Badge>
                      <Badge>{fallbackGroup.type}</Badge>
                    </div>
                    <div className="csm-strategy-card-stats">
                      <span>
                        节点 / 内置策略 <b>{fallbackGroup.proxies.length}</b>
                      </span>
                      <span>
                        代理规则 <b>0</b>
                      </span>
                    </div>
                  </div>
                  <Actions>
                    <button
                      className="csm-btn csm-btn-sm csm-btn-secondary"
                      type="button"
                      onClick={() =>
                        setEditing({
                          index: null,
                          value: fallbackGroup,
                          fallback: true,
                        })
                      }
                    >
                      <Pencil size={13} />
                      编辑节点
                    </button>
                  </Actions>
                </article>
              </div>
            </div>
          )}
        </div>
      </Modal>
      {editing && (
        <StrategyGroupModal
          key={`${editing.index ?? "new"}-${editing.value.id}`}
          group={editing.value}
          nodes={nodes}
          nodeGroups={nodeGroups}
          providers={providerLibrary}
          fallback={Boolean(editing.fallback)}
          onClose={() => setEditing(null)}
          onSave={saveGroup}
        />
      )}
    </>
  );
}

function StrategyGroupModal({
  group,
  nodes,
  nodeGroups,
  providers,
  fallback = false,
  onClose,
  onSave,
}: {
  group: RuleGroupDraft;
  nodes: Node[];
  nodeGroups: NodeGroup[];
  providers: RuleProviderLibraryItem[];
  fallback?: boolean;
  onClose: () => void;
  onSave: (value: RuleGroupDraft) => void;
}) {
  const [name, setName] = useState(group.name);
  const [type, setType] = useState(group.type);
  const [proxies, setProxies] = useState(group.proxies);
  const [providerIds, setProviderIds] = useState(group.providerIds);
  const [selectedNodeGroups, setSelectedNodeGroups] = useState<string[]>(
    group.nodeGroups,
  );
  const [nodeSearch, setNodeSearch] = useState("");
  const [providerSearch, setProviderSearch] = useState("");
  const [error, setError] = useState("");
  const visibleNodes = useMemo(() => {
    const keyword = nodeSearch.trim().toLowerCase();
    return nodes.filter(
      (node) =>
        !keyword ||
        `${node.name} ${node.alias} ${node.displayName} ${node.protocol} ${node.server}`
          .toLowerCase()
          .includes(keyword),
    );
  }, [nodes, nodeSearch]);
  const visibleProviders = useMemo(() => {
    const keyword = providerSearch.trim().toLowerCase();
    return providers.filter(
      (provider) =>
        !keyword ||
        `${provider.name} ${provider.providerKey} ${provider.description}`
          .toLowerCase()
          .includes(keyword),
    );
  }, [providers, providerSearch]);
  const nodeReferences = new Set(
    nodes.flatMap((node) =>
      [node.name, node.alias, node.displayName].filter(Boolean),
    ),
  );
  const groupSelectedMembers = useMemo(() => {
    const selected = new Set(selectedNodeGroups);
    const members = new Set<string>();
    for (const item of nodeGroups) {
      if (selected.has(item.id)) {
        for (const member of item.members) members.add(member);
      }
    }
    return members;
  }, [nodeGroups, selectedNodeGroups]);
  const effectiveProxies = useMemo(
    () => [...new Set([...proxies, ...groupSelectedMembers])],
    [proxies, groupSelectedMembers],
  );
  function toggleNodeGroup(groupId: string, checked: boolean) {
    setSelectedNodeGroups((current) =>
      checked
        ? [...new Set([...current, groupId])]
        : current.filter((id) => id !== groupId),
    );
  }

  function toggleProxy(value: string, checked: boolean) {
    setProxies((current) =>
      checked
        ? [...new Set([...current, value])]
        : current.filter((item) => item !== value),
    );
  }
  function moveProvider(index: number, offset: -1 | 1) {
    setProviderIds((current) => {
      const target = index + offset;
      if (
        index < 0 ||
        index >= current.length ||
        target < 0 ||
        target >= current.length
      )
        return current;
      const reordered = [...current];
      [reordered[index], reordered[target]] = [
        reordered[target],
        reordered[index],
      ];
      return reordered;
    });
  }
  function submit() {
    const cleanName = fallback ? group.name : name.trim();
    if (!cleanName || /[,\r\n]/.test(cleanName)) {
      setError("请输入有效的策略组名称，不能包含逗号或换行。");
      return;
    }
    if (!effectiveProxies.length) {
      setError("请至少选择一个节点、节点分组或 DIRECT / REJECT。");
      return;
    }
    const extra = { ...group.extra };
    if (type !== "select") {
      if (!extra.url) extra.url = "http://www.gstatic.com/generate_204";
      if (!extra.interval) extra.interval = 300;
    }
    onSave({
      ...group,
      name: cleanName,
      type,
      proxies: [...new Set(proxies)],
      nodeGroups: [...new Set(selectedNodeGroups)],
      providerIds: fallback ? [] : [...new Set(providerIds)],
      extra,
    });
  }

  return (
    <Modal
      title={fallback ? "编辑默认兜底策略" : "编辑策略组"}
      width={820}
      onClose={onClose}
      foot={
        <>
          <button
            className="csm-btn csm-btn-secondary"
            type="button"
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="csm-btn csm-btn-primary"
            type="button"
            onClick={submit}
          >
            保存策略组
          </button>
        </>
      }
    >
      <div className="csm-form-grid csm-strategy-editor">
        {error && (
          <div className="csm-full-col">
            <Alert type="error">{error}</Alert>
          </div>
        )}
        {fallback && (
          <div className="csm-full-col">
            <Alert type="info">
              兜底策略只能选择节点、DIRECT 或 REJECT，不能绑定 Rule Provider。
            </Alert>
          </div>
        )}
        <Field label="策略组名称">
          <input
            className="csm-input"
            value={fallback ? "兜底策略" : name}
            maxLength={120}
            disabled={fallback}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如：AI平台"
          />
          {fallback && (
            <small className="csm-muted">
              固定用于最终 MATCH，名称不可修改。
            </small>
          )}
        </Field>
        <Field label="节点选择方法">
          <select
            className="csm-select"
            value={type}
            onChange={(event) => setType(event.target.value)}
          >
            <option value="select">手动选择（select）</option>
            <option value="url-test">自动测速（url-test）</option>
            <option value="fallback">故障转移（fallback）</option>
            <option value="load-balance">负载均衡（load-balance）</option>
          </select>
        </Field>
        <Field label={`节点（已选 ${effectiveProxies.length}）`} full>
          <div className="csm-strategy-picker">
            <label className="csm-search">
              <Search size={15} />
              <input
                value={nodeSearch}
                onChange={(event) => setNodeSearch(event.target.value)}
                placeholder="按节点原名、别名、协议或地址搜索"
              />
            </label>
            <div className="csm-strategy-builtins">
              {["DIRECT", "REJECT"].map((value) => (
                <label key={value}>
                  <input
                    type="checkbox"
                    checked={proxies.includes(value)}
                    onChange={(event) =>
                      toggleProxy(value, event.target.checked)
                    }
                  />
                  <strong>{value}</strong>
                </label>
              ))}
            </div>
            {nodeGroups.length > 0 && (
              <div className="csm-strategy-group-picker">
                <div className="csm-strategy-group-picker-head">
                  <strong>按节点分组选择</strong>
                  <small>勾选后，分组内的节点会自动并入该策略组</small>
                </div>
                {nodeGroups.map((item) => {
                  const checked = selectedNodeGroups.includes(item.id);
                  return (
                    <label key={item.id} className={checked ? "checked" : ""}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) =>
                          toggleNodeGroup(item.id, event.target.checked)
                        }
                      />
                      <span>
                        <strong>{item.name}</strong>
                        {nodeGroupKindBadge(item.kind)}
                        <small>{nodeGroupSummary(item, nodes)}</small>
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
            <div className="csm-strategy-node-list">
              {visibleNodes.map((node) => {
                const value = node.alias || node.name;
                const disabled = !node.supportedOutput;
                const inGroup = groupSelectedMembers.has(value);
                return (
                  <label
                    key={node.id}
                    className={disabled || inGroup ? "disabled" : ""}
                  >
                    <input
                      type="checkbox"
                      disabled={
                        (disabled || inGroup) && !proxies.includes(value)
                      }
                      checked={effectiveProxies.includes(value)}
                      onChange={(event) =>
                        toggleProxy(value, event.target.checked)
                      }
                    />
                    <span>
                      <strong>{node.displayName || value}</strong>
                      {node.alias && <small>原名：{node.name}</small>}
                      <small>
                        {node.protocol} · {node.server}:{node.port ?? "—"}
                        {disabled ? " · Clash Meta 不支持" : ""}
                        {inGroup ? " · 由节点分组带入" : ""}
                      </small>
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        </Field>
        {!fallback && (
          <Field label={`代理规则（已选 ${providerIds.length}）`} full>
            <div className="csm-strategy-picker">
              {providerIds.length > 0 && (
                <div className="csm-provider-order">
                  <div className="csm-provider-order-heading">
                    <strong>规则匹配顺序</strong>
                    <small>越靠上的规则越先输出和匹配</small>
                  </div>
                  {providerIds.map((providerId, index) => {
                    const provider = providers.find(
                      (item) => item.id === providerId,
                    );
                    return (
                      <div className="csm-provider-order-row" key={providerId}>
                        <span className="csm-provider-order-index">
                          {index + 1}
                        </span>
                        <span className="csm-provider-order-name">
                          <strong>
                            {provider?.name ?? "已失效的 Rule Provider"}
                          </strong>
                          {provider && (
                            <small>{providerKeyText(provider)}</small>
                          )}
                        </span>
                        <span className="csm-provider-order-actions">
                          <button
                            className="csm-btn csm-btn-sm csm-btn-ghost"
                            type="button"
                            title="上移"
                            aria-label={`上移 ${provider?.name ?? "规则"}`}
                            disabled={index === 0}
                            onClick={() => moveProvider(index, -1)}
                          >
                            <ArrowUp size={14} />
                          </button>
                          <button
                            className="csm-btn csm-btn-sm csm-btn-ghost"
                            type="button"
                            title="下移"
                            aria-label={`下移 ${provider?.name ?? "规则"}`}
                            disabled={index === providerIds.length - 1}
                            onClick={() => moveProvider(index, 1)}
                          >
                            <ArrowDown size={14} />
                          </button>
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
              <label className="csm-search">
                <Search size={15} />
                <input
                  value={providerSearch}
                  onChange={(event) => setProviderSearch(event.target.value)}
                  placeholder="搜索 Rule Provider 名称或 Key"
                />
              </label>
              {!providers.length ? (
                <div className="csm-inline-empty">
                  Rule Provider 库为空，请先到独立 Tab 新增。
                </div>
              ) : (
                <div className="csm-provider-picker-list">
                  {visibleProviders.map((provider) => (
                    <label key={provider.id}>
                      <input
                        type="checkbox"
                        checked={providerIds.includes(provider.id)}
                        onChange={(event) =>
                          setProviderIds((current) =>
                            event.target.checked
                              ? [...new Set([...current, provider.id])]
                              : current.filter((id) => id !== provider.id),
                          )
                        }
                      />
                      <span>
                        <strong>{provider.name}</strong>
                        <code>
                          {providerKeyText(provider)} ·{" "}
                          {String(provider.config.type ?? "") === "manual"
                            ? "自定义"
                            : "规则订阅"}
                        </code>
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>
            <small className="csm-muted">
              勾选后会自动生成该策略组的 RULE-SET 规则，Provider
              内容始终读取独立库中的最新版本；保存的规则内容会在输出时展开为普通规则。
            </small>
          </Field>
        )}
      </div>
    </Modal>
  );
}

function Toolbar({
  title,
  icon,
  actions,
}: {
  title: string;
  icon: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="csm-toolbar">
      <div>
        <h2>
          {icon}
          {title}
        </h2>
      </div>
      {actions && <div className="csm-toolbar-actions">{actions}</div>}
    </div>
  );
}
function PanelTitle({
  title,
  icon,
  action,
}: {
  title: string;
  icon: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="csm-panel-title">
      <h2>
        {icon}
        {title}
      </h2>
      {action && <div className="csm-panel-title-action">{action}</div>}
    </div>
  );
}
function Table({ children }: { children: React.ReactNode }) {
  return (
    <div className="csm-table-wrap">
      <table className="csm-table">{children}</table>
    </div>
  );
}
function Actions({ children }: { children: React.ReactNode }) {
  return <div className="csm-actions">{children}</div>;
}
function Loading({ text }: { text: string }) {
  return (
    <div className="csm-loading">
      <Spin />
      {text}
    </div>
  );
}
function StatusDot({ status }: { status: string }) {
  return <span className={`csm-status-dot ${status}`} />;
}
function SourceBadge({
  status,
  enabled,
}: {
  status: string;
  enabled: boolean;
}) {
  if (!enabled) return <Badge>已停用</Badge>;
  return status === "healthy" ? (
    <Badge color="green">正常</Badge>
  ) : status === "error" ? (
    <Badge color="red">异常</Badge>
  ) : (
    <Badge color="amber">未刷新</Badge>
  );
}
function profileState(profile: Profile): {
  label: string;
  color: "default" | "green" | "amber" | "red";
} {
  const hasError = profile.validation.some((item) => item.level === "error");
  if (profile.publishedStatus === "degraded" || hasError) {
    return { label: "异常", color: "red" };
  }
  if (!profile.publishedAt) {
    return { label: "未发布", color: "default" };
  }
  if (!profile.ruleSetId) {
    return { label: "规则组缺失", color: "red" };
  }
  if (
    profile.ruleSetUpdatedAt &&
    new Date(profile.ruleSetUpdatedAt).getTime() >
      new Date(profile.publishedAt).getTime()
  ) {
    return { label: "待发布", color: "amber" };
  }
  return { label: "已发布", color: "green" };
}

function PublishBadge({ profile }: { profile: Profile }) {
  const state = profileState(profile);
  return <Badge color={state.color}>{state.label}</Badge>;
}
function publishLabel(profile: Profile) {
  return profileState(profile).label;
}
function stamp(value?: string | null) {
  if (!value) return "从未";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
function relativeStamp(value?: string | null) {
  if (!value) return "从未";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  const seconds = Math.max(0, Math.floor((Date.now() - date.valueOf()) / 1000));
  if (seconds < 10) return "刚刚";
  if (seconds < 60) return `${seconds} 秒钟前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days <= 7) return `${days} 天前`;
  return date.toLocaleDateString();
}
function duration(seconds: number) {
  if (seconds % 86400 === 0) return `${seconds / 86400} 天`;
  if (seconds % 3600 === 0) return `${seconds / 3600} 小时`;
  return `${seconds} 秒`;
}
function message(error: unknown) {
  return error instanceof ApiError
    ? error.message
    : error instanceof Error
      ? error.message
      : "请求失败";
}
