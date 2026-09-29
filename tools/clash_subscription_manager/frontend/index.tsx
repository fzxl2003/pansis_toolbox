import './style.css';

import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Check,
  Clipboard,
  Eye,
  FileCode2,
  Gauge,
  Pencil,
  Plus,
  RefreshCw,
  RotateCw,
  Search,
  Send,
  Server,
  ShieldAlert,
  Trash2,
  Upload,
} from 'lucide-react';

import { ApiError, apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { Alert, Badge, EmptyState, Field, Modal, Spin, useConfirm } from './components';

const API = '/api/tools/clash-subscription-manager';
type View = 'dashboard' | 'sources' | 'nodes' | 'profiles' | 'rules';
type Kernel = 'mihomo' | 'clash';
type ValidationMessage = { level: string; code?: string; message: string };
type Source = {
  id: string; name: string; url: string; userAgent: string; refreshSeconds: number; enabled: boolean;
  status: string; lastSuccessAt?: string; lastAttemptAt?: string; nextRefreshAt?: string; lastError?: string;
};
type Node = {
  id: string; stableIdentity: string; name: string; protocol: string; server: string; port?: number;
  sources: string[]; supportedOutput: boolean; lastSeenAt: string;
  tcp?: { reachable: boolean; latencyMs?: number; error?: string; checkedAt?: string };
};
type ProfileSettings = { mixedPort?: number; allowLan?: boolean; mode?: string; ipv6?: boolean; dns?: unknown };
type Profile = {
  id: string; name: string; targetKernel: Kernel; settings: ProfileSettings; ruleSetId?: string;
  selectedStableIdentities?: string[]; publishedAt?: string; publishedStatus: string;
  validation: ValidationMessage[]; subscriptionToken?: string;
};
type RuleSet = {
  id: string; name: string; rules: unknown[]; providers: Record<string, unknown>;
  groups: Record<string, unknown>[]; importMeta?: Record<string, unknown>; updatedAt?: string;
};
type RefreshRun = {
  id: string; sourceId: string; sourceName: string; status: string; startedAt: string; finishedAt?: string;
  durationMs?: number; nodesBefore: number; nodesAfter: number; error?: string;
};
type Dashboard = {
  metrics: Record<string, number>; protocolDistribution: { name: string; value: number }[];
  sources: Source[]; profiles: Profile[]; alerts: { kind?: string; message: string }[];
};
type PreviewState = { profile: Profile; yaml: string; valid: boolean; messages: ValidationMessage[] };

export default function ClashSubscriptionManager() {
  const [view, setView] = useState<View>('dashboard');
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [ruleSets, setRuleSets] = useState<RuleSet[]>([]);
  const [runs, setRuns] = useState<RefreshRun[]>([]);
  const [sourceModal, setSourceModal] = useState<Source | null | false>(false);
  const [profileModal, setProfileModal] = useState<Profile | null | false>(false);
  const [ruleModal, setRuleModal] = useState<RuleSet | 'import' | null>(null);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const { confirm, dialog } = useConfirm();

  async function loadAll() {
    setLoading(true);
    try {
      const [dashboardData, sourceData, nodeData, profileData, ruleData, runData] = await Promise.all([
        apiGet<Dashboard>(`${API}/dashboard`),
        apiGet<{ sources: Source[] }>(`${API}/sources`),
        apiGet<{ nodes: Node[] }>(`${API}/nodes`),
        apiGet<{ profiles: Profile[] }>(`${API}/profiles`),
        apiGet<{ ruleSets: RuleSet[] }>(`${API}/rule-sets`),
        apiGet<{ runs: RefreshRun[] }>(`${API}/refresh-runs?limit=100`),
      ]);
      setDashboard(dashboardData); setSources(sourceData.sources); setNodes(nodeData.nodes);
      setProfiles(profileData.profiles); setRuleSets(ruleData.ruleSets); setRuns(runData.runs); setError('');
    } catch (caught) { setError(message(caught)); }
    finally { setLoading(false); }
  }

  useEffect(() => { void loadAll(); }, []);

  async function action(key: string, work: () => Promise<unknown>, success: string, reload = true) {
    setPending(key); setError('');
    try {
      await work(); setNotice(success);
      if (reload) await loadAll();
      return true;
    } catch (caught) { setError(message(caught)); return false; }
    finally { setPending(''); }
  }

  function removeSource(source: Source) {
    confirm({ title: '删除订阅源', message: <>确认删除「{source.name}」及其刷新历史？已发布订阅不会因此被立即覆盖。</>, onConfirm: async () => { await action(`source-delete-${source.id}`, () => apiDelete(`${API}/sources/${source.id}`), '订阅源已删除'); } });
  }
  function removeProfile(profile: Profile) {
    confirm({ title: '删除聚合配置', message: <>确认删除「{profile.name}」？对应公开订阅链接将立即失效。</>, onConfirm: async () => { await action(`profile-delete-${profile.id}`, () => apiDelete(`${API}/profiles/${profile.id}`), '聚合配置已删除'); } });
  }
  function removeRuleSet(ruleSet: RuleSet) {
    confirm({ title: '删除规则库', message: <>确认删除「{ruleSet.name}」？引用它的聚合配置需要重新选择规则库。</>, onConfirm: async () => { await action(`rule-delete-${ruleSet.id}`, () => apiDelete(`${API}/rule-sets/${ruleSet.id}`), '规则库已删除'); } });
  }
  async function openPreview(profile: Profile) {
    setPending(`preview-${profile.id}`); setError('');
    try {
      const result = await apiPost<{ yaml: string; valid: boolean; messages: ValidationMessage[] }>(`${API}/profiles/${profile.id}/preview`, {});
      setPreview({ profile, ...result });
    } catch (caught) { setError(message(caught)); }
    finally { setPending(''); }
  }
  async function copy(value: string, success: string) {
    try { await navigator.clipboard.writeText(value); setNotice(success); }
    catch { setError('浏览器拒绝了剪贴板访问，请手动复制。'); }
  }
  const subscriptionUrl = (profile: Profile) => profile.subscriptionToken ? `${window.location.origin}/sub/clash/${profile.subscriptionToken}` : '';

  return <div className="tool-page csm-tool">
    <header className="tool-header">
      <div><h1 className="tool-title">Clash 订阅聚合器</h1><p className="tool-subtitle">订阅解析、节点去重、规则校验与双内核安全发布</p></div>
      <button className="csm-btn csm-btn-secondary" type="button" disabled={loading} onClick={() => void loadAll()}><RefreshCw size={14} className={loading ? 'csm-spin' : ''} />刷新</button>
    </header>
    <nav className="csm-topnav" aria-label="工具视图">
      <Tab active={view === 'dashboard'} icon={<Gauge size={14} />} onClick={() => setView('dashboard')}>运营总览</Tab>
      <Tab active={view === 'sources'} icon={<Server size={14} />} onClick={() => setView('sources')}>订阅源</Tab>
      <Tab active={view === 'nodes'} icon={<Activity size={14} />} onClick={() => setView('nodes')}>节点池</Tab>
      <Tab active={view === 'profiles'} icon={<Send size={14} />} onClick={() => setView('profiles')}>聚合配置</Tab>
      <Tab active={view === 'rules'} icon={<FileCode2 size={14} />} onClick={() => setView('rules')}>规则库与刷新记录</Tab>
    </nav>
    <div className="csm-body">
      {error && <Alert type="error">{error}</Alert>}
      {notice && <Alert type="success">{notice}</Alert>}
      {view === 'dashboard' && <DashboardView data={dashboard} loading={loading} onNavigate={setView} />}
      {view === 'sources' && <SourcesView sources={sources} loading={loading} pending={pending} onReload={() => void loadAll()} onAdd={() => setSourceModal(null)} onEdit={setSourceModal} onRemove={removeSource} onRefresh={(source) => void action(`source-refresh-${source.id}`, () => apiPost(`${API}/sources/${source.id}/refresh`, {}), '订阅源刷新完成')} />}
      {view === 'nodes' && <NodesView nodes={nodes} loading={loading} pending={pending} onProbe={(ids) => void action('nodes-probe', () => apiPost(`${API}/nodes/probe`, { nodeIds: ids }), 'TCP 探测完成')} />}
      {view === 'profiles' && <ProfilesView profiles={profiles} loading={loading} pending={pending} subscriptionUrl={subscriptionUrl} onAdd={() => setProfileModal(null)} onEdit={setProfileModal} onRemove={removeProfile} onValidate={(profile) => void action(`validate-${profile.id}`, () => apiPost(`${API}/profiles/${profile.id}/validate`, {}), '配置校验完成')} onPreview={(profile) => void openPreview(profile)} onCopy={(profile) => void copy(subscriptionUrl(profile), '订阅链接已复制')} onRotate={(profile) => confirm({ title: '轮换订阅令牌', message: <>轮换后「{profile.name}」的旧链接将立即失效，确认继续？</>, onConfirm: async () => { await action(`rotate-${profile.id}`, () => apiPost(`${API}/profiles/${profile.id}/rotate-token`, {}), '订阅令牌已轮换'); } })} />}
      {view === 'rules' && <RulesAndRunsView ruleSets={ruleSets} runs={runs} loading={loading} onImport={() => setRuleModal('import')} onEdit={setRuleModal} onRemove={removeRuleSet} />}
    </div>
    {sourceModal !== false && <SourceModal source={sourceModal} onClose={() => setSourceModal(false)} onSaved={async (text) => { setSourceModal(false); setNotice(text); await loadAll(); }} />}
    {profileModal !== false && <ProfileModal profile={profileModal} nodes={nodes} ruleSets={ruleSets} onClose={() => setProfileModal(false)} onSaved={async (text) => { setProfileModal(false); setNotice(text); await loadAll(); }} />}
    {ruleModal && <RuleSetModal ruleSet={ruleModal === 'import' ? null : ruleModal} onClose={() => setRuleModal(null)} onSaved={async (text) => { setRuleModal(null); setNotice(text); await loadAll(); }} />}
    {preview && <PreviewModal state={preview} publishing={pending === `publish-${preview.profile.id}`} onClose={() => setPreview(null)} onCopy={() => void copy(preview.yaml, 'YAML 已复制')} onPublish={() => void action(`publish-${preview.profile.id}`, () => apiPost(`${API}/profiles/${preview.profile.id}/publish`, {}), '配置已发布').then((ok) => { if (ok) setPreview(null); })} />}
    {dialog}
  </div>;
}

function Tab({ active, icon, onClick, children }: { active: boolean; icon: React.ReactNode; onClick: () => void; children: React.ReactNode }) {
  return <button className={active ? 'active' : ''} type="button" onClick={onClick}>{icon}{children}</button>;
}

function DashboardView({ data, loading, onNavigate }: { data: Dashboard | null; loading: boolean; onNavigate: (view: View) => void }) {
  if (loading && !data) return <Loading text="加载运营总览…" />;
  const metrics = data?.metrics ?? {};
  const cards: Array<[string, number, string, View]> = [
    ['订阅源', metrics.sources ?? 0, `${metrics.healthySources ?? 0} 个正常`, 'sources'],
    ['去重后节点', metrics.nodes ?? 0, `去重前 ${metrics.nodesBeforeDedupe ?? 0}`, 'nodes'],
    ['TCP 可达', metrics.tcpReachable ?? 0, '仅表示端口连通', 'nodes'],
    ['已发布配置', metrics.publishedProfiles ?? 0, '最后有效版本持续可用', 'profiles'],
    ['待处理告警', metrics.alerts ?? 0, '来源与配置异常', 'profiles'],
  ];
  return <div className="csm-stack">
    <section className="csm-metrics">{cards.map(([label, value, note, target]) => <button type="button" className="csm-metric" key={label} onClick={() => onNavigate(target)}><span>{label}</span><strong>{value}</strong><small>{note}</small></button>)}</section>
    <section className="csm-grid-two">
      <div className="csm-panel"><PanelTitle icon={<BarChart3 size={18} />} title="节点协议分布" />{data?.protocolDistribution.length ? <div className="csm-bars">{data.protocolDistribution.map((item) => <div key={item.name}><code>{item.name}</code><span><i style={{ width: `${Math.max(5, item.value / Math.max(...data.protocolDistribution.map((entry) => entry.value)) * 100)}%` }} /></span><b>{item.value}</b></div>)}</div> : <EmptyState title="暂无节点数据" hint="成功刷新订阅源后显示协议分布" />}</div>
      <div className="csm-panel"><PanelTitle icon={<ShieldAlert size={18} />} title="告警中心" />{data?.alerts.length ? <div className="csm-alert-list">{data.alerts.map((alert, index) => <div key={`${alert.message}-${index}`}><AlertTriangle size={15} /><span>{alert.message}</span></div>)}</div> : <EmptyState icon={<Check size={28} />} title="没有待处理告警" hint="订阅源和已发布配置状态正常" />}</div>
    </section>
    <section className="csm-grid-two">
      <div className="csm-panel"><PanelTitle icon={<Server size={18} />} title="订阅源健康" />{data?.sources.length ? <div className="csm-summary-list">{data.sources.map((source) => <div key={source.id}><StatusDot status={source.status} /><strong>{source.name}</strong><span>{source.lastError || stamp(source.lastSuccessAt)}</span></div>)}</div> : <EmptyState title="暂无订阅源" />}</div>
      <div className="csm-panel"><PanelTitle icon={<Send size={18} />} title="聚合配置" />{data?.profiles.length ? <div className="csm-summary-list">{data.profiles.map((profile) => <div key={profile.id}><Send size={14} /><strong>{profile.name}</strong><span>{kernelName(profile.targetKernel)} · {publishLabel(profile)}</span></div>)}</div> : <EmptyState title="暂无聚合配置" />}</div>
    </section>
  </div>;
}

function SourcesView({ sources, loading, pending, onReload, onAdd, onEdit, onRemove, onRefresh }: { sources: Source[]; loading: boolean; pending: string; onReload: () => void; onAdd: () => void; onEdit: (source: Source) => void; onRemove: (source: Source) => void; onRefresh: (source: Source) => void }) {
  return <div className="csm-panel"><Toolbar title="订阅源" icon={<Server size={18} />} actions={<><button className="csm-btn csm-btn-secondary" type="button" disabled={loading} onClick={onReload}><RefreshCw size={14} />刷新列表</button><button className="csm-btn csm-btn-primary" type="button" onClick={onAdd}><Plus size={14} />添加订阅源</button></>} />
    {loading ? <Loading text="加载订阅源…" /> : !sources.length ? <EmptyState icon={<Server size={32} />} title="暂无订阅源" hint="点击“添加订阅源”开始导入节点" /> : <Table><thead><tr><th>名称</th><th>状态</th><th>刷新周期</th><th>上次成功</th><th>下次刷新</th><th>操作</th></tr></thead><tbody>{sources.map((source) => <tr key={source.id}><td><strong>{source.name}</strong><small className="csm-cell-note">{source.url}</small>{source.lastError && <small className="csm-cell-error">{source.lastError}</small>}</td><td><SourceBadge status={source.status} enabled={source.enabled} /></td><td>{duration(source.refreshSeconds)}</td><td>{stamp(source.lastSuccessAt)}</td><td>{source.enabled ? stamp(source.nextRefreshAt) : '已停用'}</td><td><Actions><button className="csm-btn csm-btn-sm csm-btn-ghost" type="button" title="立即刷新" disabled={pending === `source-refresh-${source.id}`} onClick={() => onRefresh(source)}>{pending === `source-refresh-${source.id}` ? <Spin size={13} /> : <RefreshCw size={13} />}</button><button className="csm-btn csm-btn-sm csm-btn-ghost" type="button" title="编辑" onClick={() => onEdit(source)}><Pencil size={13} /></button><button className="csm-btn csm-btn-sm csm-btn-ghost csm-danger-text" type="button" title="删除" onClick={() => onRemove(source)}><Trash2 size={13} /></button></Actions></td></tr>)}</tbody></Table>}
  </div>;
}

function NodesView({ nodes, loading, pending, onProbe }: { nodes: Node[]; loading: boolean; pending: string; onProbe: (ids: string[]) => void }) {
  const [search, setSearch] = useState(''); const [protocol, setProtocol] = useState(''); const [selected, setSelected] = useState<string[]>([]);
  const protocols = useMemo(() => Array.from(new Set(nodes.map((node) => node.protocol))).sort(), [nodes]);
  const filtered = useMemo(() => nodes.filter((node) => (!protocol || node.protocol === protocol) && `${node.name} ${node.server} ${node.sources.join(' ')}`.toLowerCase().includes(search.trim().toLowerCase())), [nodes, protocol, search]);
  const allSelected = filtered.length > 0 && filtered.every((node) => selected.includes(node.id));
  return <div className="csm-panel"><Toolbar title="节点池" icon={<Activity size={18} />} actions={<button className="csm-btn csm-btn-primary" type="button" disabled={pending === 'nodes-probe' || (!selected.length && !filtered.length)} onClick={() => onProbe(selected.length ? selected : filtered.map((node) => node.id))}>{pending === 'nodes-probe' ? <Spin size={14} /> : <Activity size={14} />}探测{selected.length ? `（${selected.length}）` : '当前结果'}</button>} />
    <div className="csm-filterbar"><label className="csm-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="按名称、地址或来源搜索" /></label><select className="csm-select csm-compact" value={protocol} onChange={(event) => setProtocol(event.target.value)}><option value="">全部协议</option>{protocols.map((item) => <option key={item}>{item}</option>)}</select><span className="csm-muted">共 {filtered.length} 个节点</span></div>
    {loading ? <Loading text="加载节点…" /> : !filtered.length ? <EmptyState icon={<Activity size={32} />} title="没有符合条件的节点" hint="调整筛选条件或刷新订阅源" /> : <Table><thead><tr><th><input aria-label="选择当前结果" type="checkbox" checked={allSelected} onChange={(event) => setSelected(event.target.checked ? Array.from(new Set([...selected, ...filtered.map((node) => node.id)])) : selected.filter((id) => !filtered.some((node) => node.id === id)))} /></th><th>节点</th><th>协议</th><th>端点</th><th>来源</th><th>兼容性</th><th>TCP 状态</th></tr></thead><tbody>{filtered.map((node) => <tr key={node.id}><td><input type="checkbox" checked={selected.includes(node.id)} onChange={(event) => setSelected(event.target.checked ? [...selected, node.id] : selected.filter((id) => id !== node.id))} /></td><td><strong>{node.name}</strong><small className="csm-cell-note">最后出现：{stamp(node.lastSeenAt)}</small></td><td><code className="csm-code">{node.protocol}</code></td><td><code className="csm-code">{node.server}:{node.port ?? '—'}</code></td><td>{node.sources.join('、') || '未知'}</td><td>{node.supportedOutput ? <Badge color="green">可输出</Badge> : <Badge color="red">不支持输出</Badge>}</td><td>{!node.tcp ? <Badge>未探测</Badge> : node.tcp.reachable ? <><Badge color="green">可达</Badge><small className="csm-cell-note">{node.tcp.latencyMs ?? '—'} ms</small></> : <><Badge color="red">失败</Badge><small className="csm-cell-error">{node.tcp.error}</small></>}</td></tr>)}</tbody></Table>}
    <p className="csm-footnote">TCP 可达仅表示目标地址和端口能够建立连接，不代表代理真实可用或实际延迟。</p>
  </div>;
}

function ProfilesView({ profiles, loading, pending, subscriptionUrl, onAdd, onEdit, onRemove, onValidate, onPreview, onCopy, onRotate }: { profiles: Profile[]; loading: boolean; pending: string; subscriptionUrl: (profile: Profile) => string; onAdd: () => void; onEdit: (profile: Profile) => void; onRemove: (profile: Profile) => void; onValidate: (profile: Profile) => void; onPreview: (profile: Profile) => void; onCopy: (profile: Profile) => void; onRotate: (profile: Profile) => void }) {
  return <div className="csm-panel"><Toolbar title="聚合配置" icon={<Send size={18} />} actions={<button className="csm-btn csm-btn-primary" type="button" onClick={onAdd}><Plus size={14} />新建聚合配置</button>} />
    {loading ? <Loading text="加载聚合配置…" /> : !profiles.length ? <EmptyState icon={<Send size={32} />} title="暂无聚合配置" hint="创建配置后选择节点、关联规则并校验发布" /> : <Table><thead><tr><th>名称</th><th>目标内核</th><th>节点</th><th>规则库</th><th>发布状态</th><th>订阅链接</th><th>操作</th></tr></thead><tbody>{profiles.map((profile) => { const errors = profile.validation.filter((item) => item.level === 'error').length; return <tr key={profile.id}><td><strong>{profile.name}</strong>{errors > 0 && <small className="csm-cell-error">{errors} 个校验问题</small>}</td><td><Badge color={profile.targetKernel === 'mihomo' ? 'blue' : 'default'}>{kernelName(profile.targetKernel)}</Badge></td><td>{profile.selectedStableIdentities?.length ?? 0}</td><td>{profile.ruleSetId ? '已关联' : '未关联'}</td><td><PublishBadge profile={profile} /></td><td>{profile.subscriptionToken ? <button className="csm-link-button" type="button" title={subscriptionUrl(profile)} onClick={() => onCopy(profile)}><Clipboard size={13} />复制链接</button> : '—'}</td><td><Actions><button className="csm-btn csm-btn-sm csm-btn-ghost" type="button" title="编辑" onClick={() => onEdit(profile)}><Pencil size={13} /></button><button className="csm-btn csm-btn-sm csm-btn-ghost" type="button" title="校验" disabled={pending === `validate-${profile.id}`} onClick={() => onValidate(profile)}>{pending === `validate-${profile.id}` ? <Spin size={13} /> : <Check size={13} />}</button><button className="csm-btn csm-btn-sm csm-btn-primary" type="button" disabled={pending === `preview-${profile.id}`} onClick={() => onPreview(profile)}>{pending === `preview-${profile.id}` ? <Spin size={13} /> : <Eye size={13} />}预览 / 发布</button><button className="csm-btn csm-btn-sm csm-btn-ghost" type="button" title="轮换令牌" onClick={() => onRotate(profile)}><RotateCw size={13} /></button><button className="csm-btn csm-btn-sm csm-btn-ghost csm-danger-text" type="button" title="删除" onClick={() => onRemove(profile)}><Trash2 size={13} /></button></Actions></td></tr>; })}</tbody></Table>}
  </div>;
}

function RulesAndRunsView({ ruleSets, runs, loading, onImport, onEdit, onRemove }: { ruleSets: RuleSet[]; runs: RefreshRun[]; loading: boolean; onImport: () => void; onEdit: (rule: RuleSet) => void; onRemove: (rule: RuleSet) => void }) {
  return <div className="csm-stack"><div className="csm-panel"><Toolbar title="规则库" icon={<FileCode2 size={18} />} actions={<button className="csm-btn csm-btn-primary" type="button" onClick={onImport}><Upload size={14} />导入规则</button>} />
    {loading ? <Loading text="加载规则库…" /> : !ruleSets.length ? <EmptyState icon={<FileCode2 size={32} />} title="暂无规则库" hint="规则不会随节点订阅自动导入" /> : <Table><thead><tr><th>名称</th><th>规则</th><th>策略组</th><th>Rule Provider</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{ruleSets.map((rule) => <tr key={rule.id}><td><strong>{rule.name}</strong></td><td>{rule.rules.length}</td><td>{rule.groups.length}</td><td>{Object.keys(rule.providers).length}</td><td>{stamp(rule.updatedAt)}</td><td><Actions><button className="csm-btn csm-btn-sm csm-btn-ghost" type="button" title="编辑" onClick={() => onEdit(rule)}><Pencil size={13} /></button><button className="csm-btn csm-btn-sm csm-btn-ghost csm-danger-text" type="button" title="删除" onClick={() => onRemove(rule)}><Trash2 size={13} /></button></Actions></td></tr>)}</tbody></Table>}
  </div><div className="csm-panel"><Toolbar title="刷新记录" icon={<RefreshCw size={18} />} />{loading ? <Loading text="加载刷新记录…" /> : !runs.length ? <EmptyState title="暂无刷新记录" /> : <Table><thead><tr><th>订阅源</th><th>状态</th><th>开始时间</th><th>耗时</th><th>节点变化</th><th>错误</th></tr></thead><tbody>{runs.map((run) => <tr key={run.id}><td><strong>{run.sourceName}</strong></td><td><Badge color={run.status === 'success' ? 'green' : run.status === 'failed' ? 'red' : 'blue'}>{run.status === 'success' ? '成功' : run.status === 'failed' ? '失败' : '进行中'}</Badge></td><td>{stamp(run.startedAt)}</td><td>{run.durationMs == null ? '—' : `${run.durationMs} ms`}</td><td>{run.nodesBefore} → {run.nodesAfter}</td><td><span className="csm-error-text">{run.error || '—'}</span></td></tr>)}</tbody></Table>}</div></div>;
}

function SourceModal({ source, onClose, onSaved }: { source: Source | null; onClose: () => void; onSaved: (message: string) => Promise<void> }) {
  const [form, setForm] = useState({ name: source?.name ?? '', url: '', userAgent: source?.userAgent ?? '', refreshSeconds: source?.refreshSeconds ?? 21600, enabled: source?.enabled ?? true });
  const [saving, setSaving] = useState(false); const [error, setError] = useState('');
  async function save() { if (!source && !form.url.trim()) { setError('请输入订阅地址。'); return; } setSaving(true); setError(''); try { if (source) await apiPut(`${API}/sources/${source.id}`, { ...form, url: form.url.trim() || undefined }); else await apiPost(`${API}/sources`, form); await onSaved(source ? '订阅源已更新' : '订阅源已添加'); } catch (caught) { setError(message(caught)); } finally { setSaving(false); } }
  return <Modal title={source ? '编辑订阅源' : '添加订阅源'} width={620} onClose={() => !saving && onClose()} foot={<><button className="csm-btn csm-btn-secondary" type="button" disabled={saving} onClick={onClose}>取消</button><button className="csm-btn csm-btn-primary" type="button" disabled={saving} onClick={() => void save()}>{saving ? <><Spin size={14} />保存中</> : '保存'}</button></>}><div className="csm-form-grid">{error && <div className="csm-full-col"><Alert type="error">{error}</Alert></div>}<Field label="名称"><input className="csm-input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：主订阅" /></Field><Field label="刷新周期"><select className="csm-select" value={form.refreshSeconds} onChange={(event) => setForm({ ...form, refreshSeconds: Number(event.target.value) })}><option value={3600}>每小时</option><option value={21600}>每 6 小时</option><option value={43200}>每 12 小时</option><option value={86400}>每天</option></select></Field><Field label={source ? '订阅地址（留空保持不变）' : '订阅地址'} full><input className="csm-input" type="url" value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} placeholder={source ? source.url : 'https://example.com/subscription'} /></Field><Field label="User-Agent" full><input className="csm-input" value={form.userAgent} onChange={(event) => setForm({ ...form, userAgent: event.target.value })} placeholder="留空使用工具默认值" /></Field><Field label="状态" full><label className="csm-check"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用定时刷新</label></Field></div></Modal>;
}

function ProfileModal({ profile, nodes, ruleSets, onClose, onSaved }: { profile: Profile | null; nodes: Node[]; ruleSets: RuleSet[]; onClose: () => void; onSaved: (message: string) => Promise<void> }) {
  const [name, setName] = useState(profile?.name ?? ''); const [kernel, setKernel] = useState<Kernel>(profile?.targetKernel ?? 'mihomo');
  const [ruleSetId, setRuleSetId] = useState(profile?.ruleSetId ?? ''); const [selected, setSelected] = useState(profile?.selectedStableIdentities ?? []);
  const [settings, setSettings] = useState<ProfileSettings>({ mixedPort: 7890, allowLan: false, mode: 'rule', ipv6: false, ...(profile?.settings ?? {}) });
  const [search, setSearch] = useState(''); const [saving, setSaving] = useState(false); const [error, setError] = useState('');
  const visibleNodes = useMemo(() => nodes.filter((node) => `${node.name} ${node.protocol} ${node.server}`.toLowerCase().includes(search.toLowerCase())), [nodes, search]);
  const compatible = (node: Node) => node.supportedOutput && (kernel === 'mihomo' || !['vless', 'hysteria', 'hysteria2', 'tuic', 'wireguard'].includes(node.protocol));
  async function save() { if (!name.trim()) { setError('请输入配置名称。'); return; } setSaving(true); setError(''); try { const payload = { name: name.trim(), targetKernel: kernel, settings, ruleSetId: ruleSetId || null }; const saved = profile ? (await apiPut<{ profile: Profile }>(`${API}/profiles/${profile.id}`, payload)).profile : (await apiPost<{ profile: Profile }>(`${API}/profiles`, payload)).profile; await apiPut(`${API}/profiles/${saved.id}/selections`, { stableIdentities: selected }); await onSaved(profile ? '聚合配置已更新' : '聚合配置已创建'); } catch (caught) { setError(message(caught)); } finally { setSaving(false); } }
  return <Modal title={profile ? '编辑聚合配置' : '新建聚合配置'} width={900} onClose={() => !saving && onClose()} foot={<><button className="csm-btn csm-btn-secondary" type="button" disabled={saving} onClick={onClose}>取消</button><button className="csm-btn csm-btn-primary" type="button" disabled={saving} onClick={() => void save()}>{saving ? <><Spin size={14} />保存中</> : '保存配置'}</button></>}><div className="csm-form-grid">{error && <div className="csm-full-col"><Alert type="error">{error}</Alert></div>}<Field label="配置名称"><input className="csm-input" value={name} onChange={(event) => setName(event.target.value)} /></Field><Field label="目标内核"><select className="csm-select" value={kernel} onChange={(event) => setKernel(event.target.value as Kernel)}><option value="mihomo">Mihomo / Clash Meta</option><option value="clash">传统 Clash Premium</option></select></Field><Field label="规则库"><select className="csm-select" value={ruleSetId} onChange={(event) => setRuleSetId(event.target.value)}><option value="">不使用规则库</option>{ruleSets.map((rule) => <option value={rule.id} key={rule.id}>{rule.name}</option>)}</select></Field><Field label="运行模式"><select className="csm-select" value={settings.mode ?? 'rule'} onChange={(event) => setSettings({ ...settings, mode: event.target.value })}><option value="rule">Rule</option><option value="global">Global</option><option value="direct">Direct</option></select></Field><Field label="Mixed Port"><input className="csm-input" type="number" min={1} max={65535} value={settings.mixedPort ?? 7890} onChange={(event) => setSettings({ ...settings, mixedPort: Number(event.target.value) })} /></Field><Field label="网络选项"><div className="csm-inline-checks"><label className="csm-check"><input type="checkbox" checked={Boolean(settings.allowLan)} onChange={(event) => setSettings({ ...settings, allowLan: event.target.checked })} />允许局域网</label><label className="csm-check"><input type="checkbox" checked={Boolean(settings.ipv6)} onChange={(event) => setSettings({ ...settings, ipv6: event.target.checked })} />启用 IPv6</label></div></Field><Field label={`选择节点（已选 ${selected.length}）`} full><div className="csm-node-picker"><label className="csm-search"><Search size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索节点" /></label><div className="csm-node-options">{visibleNodes.map((node) => { const disabled = !compatible(node); return <label key={node.id} className={disabled ? 'disabled' : ''}><input type="checkbox" disabled={disabled} checked={selected.includes(node.stableIdentity)} onChange={(event) => setSelected(event.target.checked ? [...selected, node.stableIdentity] : selected.filter((identity) => identity !== node.stableIdentity))} /><span><strong>{node.name}</strong><small>{node.protocol} · {node.server}:{node.port ?? '—'}{disabled ? ' · 与目标内核不兼容' : ''}</small></span></label>; })}</div></div><small className="csm-muted">新出现的节点不会自动加入；已选节点消失时发布校验会给出告警。</small></Field></div></Modal>;
}

function RuleSetModal({ ruleSet, onClose, onSaved }: { ruleSet: RuleSet | null; onClose: () => void; onSaved: (message: string) => Promise<void> }) {
  const [name, setName] = useState(ruleSet?.name ?? ''); const [yamlContent, setYamlContent] = useState('');
  const [rules, setRules] = useState(JSON.stringify(ruleSet?.rules ?? [], null, 2)); const [groups, setGroups] = useState(JSON.stringify(ruleSet?.groups ?? [], null, 2)); const [providers, setProviders] = useState(JSON.stringify(ruleSet?.providers ?? {}, null, 2));
  const [saving, setSaving] = useState(false); const [error, setError] = useState('');
  async function save() { if (!name.trim()) { setError('请输入规则库名称。'); return; } setSaving(true); setError(''); try { if (!ruleSet) { if (!yamlContent.trim()) throw new Error('请粘贴 Clash YAML 内容。'); await apiPost(`${API}/rule-sets/import`, { name: name.trim(), content: yamlContent }); } else { await apiPut(`${API}/rule-sets/${ruleSet.id}`, { name: name.trim(), rules: JSON.parse(rules), groups: JSON.parse(groups), providers: JSON.parse(providers), importMeta: ruleSet.importMeta ?? {} }); } await onSaved(ruleSet ? '规则库已更新' : '规则库已导入'); } catch (caught) { setError(caught instanceof SyntaxError ? `JSON 格式错误：${caught.message}` : message(caught)); } finally { setSaving(false); } }
  return <Modal title={ruleSet ? '编辑规则库' : '导入 Clash 规则'} width={880} onClose={() => !saving && onClose()} foot={<><button className="csm-btn csm-btn-secondary" type="button" disabled={saving} onClick={onClose}>取消</button><button className="csm-btn csm-btn-primary" type="button" disabled={saving} onClick={() => void save()}>{saving ? <><Spin size={14} />保存中</> : ruleSet ? '保存规则库' : '导入为独立副本'}</button></>}><div className="csm-form-grid">{error && <div className="csm-full-col"><Alert type="error">{error}</Alert></div>}<Field label="名称" full><input className="csm-input" value={name} onChange={(event) => setName(event.target.value)} /></Field>{ruleSet ? <><Field label="规则（JSON 数组）" full><textarea className="csm-textarea csm-code-editor" rows={12} spellCheck={false} value={rules} onChange={(event) => setRules(event.target.value)} /></Field><Field label="策略组（JSON 数组）" full><textarea className="csm-textarea csm-code-editor" rows={12} spellCheck={false} value={groups} onChange={(event) => setGroups(event.target.value)} /></Field><Field label="Rule Provider（JSON 对象）" full><textarea className="csm-textarea csm-code-editor" rows={10} spellCheck={false} value={providers} onChange={(event) => setProviders(event.target.value)} /></Field></> : <Field label="Clash YAML" full><textarea className="csm-textarea csm-code-editor" rows={24} spellCheck={false} value={yamlContent} onChange={(event) => setYamlContent(event.target.value)} placeholder={'proxy-groups:\n  - name: PROXY\n    type: select\n    proxies: [DIRECT]\nrules:\n  - MATCH,PROXY'} /><small className="csm-muted">仅导入 rules、proxy-groups 和 rule-providers，导入后成为独立可编辑副本。</small></Field>}</div></Modal>;
}

function PreviewModal({ state, publishing, onClose, onCopy, onPublish }: { state: PreviewState; publishing: boolean; onClose: () => void; onCopy: () => void; onPublish: () => void }) {
  const errors = state.messages.filter((item) => item.level === 'error');
  return <Modal title={`配置预览：${state.profile.name}`} width={920} onClose={() => !publishing && onClose()} foot={<><button className="csm-btn csm-btn-secondary" type="button" disabled={publishing} onClick={onCopy}><Clipboard size={14} />复制 YAML</button><button className="csm-btn csm-btn-secondary" type="button" disabled={publishing} onClick={onClose}>关闭</button><button className="csm-btn csm-btn-primary" type="button" disabled={!state.valid || publishing} onClick={onPublish}>{publishing ? <><Spin size={14} />发布中</> : <><Upload size={14} />发布有效配置</>}</button></>}><div className="csm-preview">{state.valid ? <Alert type="success">校验通过，可以发布并替换当前有效版本。</Alert> : <Alert type="error">存在 {errors.length} 个错误，当前已发布版本不会被覆盖。</Alert>}{state.messages.length > 0 && <div className="csm-validation-list">{state.messages.map((item, index) => <div className={item.level === 'error' ? 'error' : 'info'} key={`${item.code}-${index}`}><AlertTriangle size={14} /><span>{item.message}</span></div>)}</div>}<pre>{state.yaml}</pre></div></Modal>;
}

function Toolbar({ title, icon, actions }: { title: string; icon: React.ReactNode; actions?: React.ReactNode }) { return <div className="csm-toolbar"><div><h2>{icon}{title}</h2></div>{actions && <div className="csm-toolbar-actions">{actions}</div>}</div>; }
function PanelTitle({ title, icon }: { title: string; icon: React.ReactNode }) { return <div className="csm-panel-title"><h2>{icon}{title}</h2></div>; }
function Table({ children }: { children: React.ReactNode }) { return <div className="csm-table-wrap"><table className="csm-table">{children}</table></div>; }
function Actions({ children }: { children: React.ReactNode }) { return <div className="csm-actions">{children}</div>; }
function Loading({ text }: { text: string }) { return <div className="csm-loading"><Spin />{text}</div>; }
function StatusDot({ status }: { status: string }) { return <span className={`csm-status-dot ${status}`} />; }
function SourceBadge({ status, enabled }: { status: string; enabled: boolean }) { if (!enabled) return <Badge>已停用</Badge>; return status === 'healthy' ? <Badge color="green">正常</Badge> : status === 'error' ? <Badge color="red">异常</Badge> : <Badge color="amber">未刷新</Badge>; }
function PublishBadge({ profile }: { profile: Profile }) { return profile.publishedStatus === 'degraded' ? <Badge color="amber">已降级</Badge> : profile.publishedAt ? <Badge color="green">已发布</Badge> : <Badge>草稿</Badge>; }
function publishLabel(profile: Profile) { return profile.publishedStatus === 'degraded' ? '已降级' : profile.publishedAt ? '已发布' : '草稿'; }
function kernelName(kernel: Kernel) { return kernel === 'mihomo' ? 'Mihomo / Meta' : '传统 Clash'; }
function stamp(value?: string | null) { if (!value) return '从未'; const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleString(); }
function duration(seconds: number) { if (seconds % 86400 === 0) return `${seconds / 86400} 天`; if (seconds % 3600 === 0) return `${seconds / 3600} 小时`; return `${seconds} 秒`; }
function message(error: unknown) { return error instanceof ApiError ? error.message : error instanceof Error ? error.message : '请求失败'; }
