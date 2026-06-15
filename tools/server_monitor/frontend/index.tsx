import { FormEvent, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Activity,
  ArrowDown,
  ArrowUp,
  Columns2,
  Cpu,
  Database,
  LayoutDashboard,
  FolderPlus,
  Gauge,
  HardDrive,
  MemoryStick,
  Plus,
  RefreshCw,
  Rows3,
  Server,
  Settings,
  SquareActivity,
  Thermometer,
  Trash2,
  X,
  Zap,
} from 'lucide-react';

import { ApiError, apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

type MonitorServer = {
  id: string;
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  isDefault: boolean;
  ownerUserId: string | null;
  directoryWhitelist: string[];
  directoryRefreshSeconds: number;
};

type Disk = { filesystem: string; totalBytes: number; usedBytes: number; freeBytes: number; mountPath: string };
type GpuProcess = {
  pid: number;
  name: string;
  username?: string;
  command?: string;
  usedMemoryMiB: number | null;
  cpuPercent: number | null;
  gpuPercent: number | null;
  memoryPercent: number | null;
};
type Gpu = {
  index: number;
  uuid?: string;
  name: string;
  utilizationPercent: number | null;
  memoryTotalMiB: number | null;
  memoryUsedMiB: number | null;
  temperatureC: number | null;
  powerW: number | null;
  processCount: number;
  processes: GpuProcess[];
};
type Sample = {
  id: string;
  collectedAt: string;
  cpuPercent: number | null;
  memoryTotalBytes: number | null;
  memoryUsedBytes: number | null;
  disks: Disk[];
  gpus: Gpu[];
  error: string | null;
};
type DirectoryUsage = {
  path: string;
  totalBytes: number | null;
  usedBytes: number | null;
  freeBytes: number | null;
  refreshedAt: string | null;
  error: string | null;
};

type ServerFormState = {
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  sshPassword: string;
  isDefault: boolean;
  directoryWhitelist: string;
  directoryRefreshSeconds: number;
  trackedDirectory: string;
};

type ModuleId = 'summary' | 'trends' | 'disks' | 'directories' | 'gpu';
type ModulePreference = { id: ModuleId; label: string; visible: boolean };

const emptyServer: ServerFormState = {
  name: '',
  host: '',
  port: 22,
  sshUsername: '',
  sshPassword: '',
  isDefault: false,
  directoryWhitelist: '/data',
  directoryRefreshSeconds: 300,
  trackedDirectory: '',
};

const defaultModules: ModulePreference[] = [
  { id: 'summary', label: '资源概览', visible: true },
  { id: 'trends', label: '24 小时趋势', visible: true },
  { id: 'disks', label: '硬盘挂载', visible: true },
  { id: 'directories', label: '固定文件夹空间', visible: true },
  { id: 'gpu', label: 'CUDA GPU', visible: true },
];

export default function ServerMonitorTool() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [servers, setServers] = useState<MonitorServer[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [sample, setSample] = useState<Sample | null>(null);
  const [history, setHistory] = useState<Sample[]>([]);
  const [directories, setDirectories] = useState<DirectoryUsage[]>([]);
  const [visibleMounts, setVisibleMounts] = useState<Record<string, string[]>>({});
  const [modulePreferences, setModulePreferences] = useState<Record<string, ModulePreference[]>>({});
  const [gpuLayout, setGpuLayout] = useState<GpuLayout>('stacked');
  const [activeGpuIndex, setActiveGpuIndex] = useState<number | null>(null);
  const [modal, setModal] = useState<'create' | 'edit' | null>(null);
  const [form, setForm] = useState<ServerFormState>(emptyServer);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const selected = servers.find((serverItem) => serverItem.id === selectedId) ?? servers[0];
  const isAdmin = me?.role === 'admin';
  const canEditSelected = Boolean(me && selected && (!selected.isDefault || isAdmin) && (!selected.ownerUserId || selected.ownerUserId === me.id || isAdmin));
  const visibleDiskKeys = selected ? visibleMounts[selected.id] : undefined;
  const displayedDisks = sample?.disks.filter((disk) => !visibleDiskKeys || visibleDiskKeys.includes(disk.mountPath)) ?? [];
  const activeGpu = sample?.gpus.find((gpu) => gpu.index === activeGpuIndex) ?? null;
  const selectedModules = selected ? normalizeModules(modulePreferences[selected.id]) : defaultModules;

  useEffect(() => {
    fetchMe().then((state) => setMe(state.user)).catch(() => setMe(null));
    void loadServers();
  }, []);

  useEffect(() => {
    const key = preferenceKey(me, 'gpu-layout');
    const saved = window.localStorage.getItem(key);
    if (saved === 'split' || saved === 'stacked' || saved === 'overview') setGpuLayout(saved);
    const savedMounts = window.localStorage.getItem(preferenceKey(me, 'disk-mounts'));
    if (savedMounts) {
      try {
        setVisibleMounts(JSON.parse(savedMounts));
      } catch {
        setVisibleMounts({});
      }
    }
    const savedModules = window.localStorage.getItem(preferenceKey(me, 'modules'));
    if (savedModules) {
      try {
        setModulePreferences(JSON.parse(savedModules));
      } catch {
        setModulePreferences({});
      }
    }
  }, [me?.id]);

  useEffect(() => {
    if (!selected) return;
    setSelectedId(selected.id);
    void refreshSelected(selected.id);
    const gpuTimer = window.setInterval(() => void loadSnapshot(selected.id, true), 5000);
    const historyTimer = window.setInterval(() => {
      void loadHistory(selected.id);
      void loadDirectories(selected.id);
    }, 30000);
    return () => {
      window.clearInterval(gpuTimer);
      window.clearInterval(historyTimer);
    };
  }, [selected?.id]);

  async function refreshSelected(serverId: string, force = false) {
    await Promise.all([loadSnapshot(serverId, force), loadHistory(serverId), loadDirectories(serverId)]);
  }

  async function loadServers() {
    try {
      const payload = await apiGet<{ servers: MonitorServer[] }>('/api/tools/server-monitor/servers');
      setServers(payload.servers);
      if (payload.servers.length && !selectedId) setSelectedId(payload.servers[0].id);
    } catch (err) {
      handleError(err);
    }
  }

  async function loadSnapshot(serverId: string, force = false) {
    try {
      const payload = await apiGet<{ sample: Sample }>(`/api/tools/server-monitor/servers/${serverId}/snapshot${force ? '?force=true' : ''}`);
      setSample(payload.sample);
      setActiveGpuIndex((current) => current ?? payload.sample.gpus[0]?.index ?? null);
    } catch (err) {
      handleError(err);
    }
  }

  async function loadHistory(serverId: string) {
    try {
      const payload = await apiGet<{ samples: Sample[] }>(`/api/tools/server-monitor/servers/${serverId}/history?hours=24`);
      setHistory(payload.samples);
    } catch (err) {
      handleError(err);
    }
  }

  async function loadDirectories(serverId: string) {
    try {
      const payload = await apiGet<{ directories: DirectoryUsage[] }>(`/api/tools/server-monitor/servers/${serverId}/directories`);
      setDirectories(payload.directories);
    } catch (err) {
      handleError(err);
    }
  }

  function openCreate() {
    setForm(emptyServer);
    setModal('create');
  }

  function openEdit() {
    if (!selected) return;
    setForm({
      name: selected.name,
      host: selected.host,
      port: selected.port,
      sshUsername: selected.sshUsername,
      sshPassword: '',
      isDefault: selected.isDefault,
      directoryWhitelist: selected.directoryWhitelist.join('\n'),
      directoryRefreshSeconds: selected.directoryRefreshSeconds,
      trackedDirectory: '',
    });
    setModal('edit');
  }

  function setGpuLayoutPreference(layout: GpuLayout) {
    setGpuLayout(layout);
    window.localStorage.setItem(preferenceKey(me, 'gpu-layout'), layout);
  }

  async function saveServer(event: FormEvent) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    const payload = {
      name: form.name,
      host: form.host,
      port: form.port,
      sshUsername: form.sshUsername,
      sshPassword: form.sshPassword || undefined,
      isDefault: form.isDefault,
      directoryWhitelist: splitLines(form.directoryWhitelist),
      directoryRefreshSeconds: form.directoryRefreshSeconds,
    };
    try {
      if (modal === 'edit' && selected) {
        await apiPut(`/api/tools/server-monitor/servers/${selected.id}`, payload);
      } else {
        await apiPost('/api/tools/server-monitor/servers', { ...payload, sshPassword: form.sshPassword });
      }
      setModal(null);
      await loadServers();
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function addTrackedDirectory(event: FormEvent) {
    event.preventDefault();
    if (!selected || !form.trackedDirectory.trim()) return;
    setIsLoading(true);
    try {
      await apiPost(`/api/tools/server-monitor/servers/${selected.id}/directories`, { path: form.trackedDirectory.trim() });
      setForm({ ...form, trackedDirectory: '' });
      await loadDirectories(selected.id);
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function removeTrackedDirectory(path: string) {
    if (!selected || !window.confirm(`确认删除固定文件夹「${path}」？`)) return;
    try {
      const response = await fetch(`/api/tools/server-monitor/servers/${selected.id}/directories`, {
        method: 'DELETE',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error?.message ?? '删除固定文件夹失败');
      }
      await loadDirectories(selected.id);
    } catch (err) {
      handleError(err);
    }
  }

  async function removeServer() {
    if (!selected || !window.confirm(`确认删除服务器「${selected.name}」？`)) return;
    try {
      await apiDelete(`/api/tools/server-monitor/servers/${selected.id}`);
      setSelectedId('');
      setSample(null);
      setHistory([]);
      setDirectories([]);
      await loadServers();
    } catch (err) {
      handleError(err);
    }
  }

  async function killProcess(pid: number) {
    if (!selected) return;
    if (!window.confirm(`确认终止进程 ${pid}？这可能中断正在运行的任务。`)) return;
    try {
      await apiPost(`/api/tools/server-monitor/servers/${selected.id}/processes/kill`, { pid });
      await loadSnapshot(selected.id, true);
    } catch (err) {
      handleError(err);
    }
  }

  function toggleMount(mountPath: string) {
    if (!selected || !sample) return;
    const current = visibleMounts[selected.id] ?? sample.disks.map((disk) => disk.mountPath);
    const next = current.includes(mountPath) ? current.filter((item) => item !== mountPath) : [...current, mountPath];
    const nextVisibleMounts = { ...visibleMounts, [selected.id]: next };
    setVisibleMounts(nextVisibleMounts);
    window.localStorage.setItem(preferenceKey(me, 'disk-mounts'), JSON.stringify(nextVisibleMounts));
  }

  function updateModulePreferences(next: ModulePreference[]) {
    if (!selected) return;
    const nextPreferences = { ...modulePreferences, [selected.id]: normalizeModules(next) };
    setModulePreferences(nextPreferences);
    window.localStorage.setItem(preferenceKey(me, 'modules'), JSON.stringify(nextPreferences));
  }

  function toggleModule(moduleId: ModuleId) {
    updateModulePreferences(selectedModules.map((item) => (item.id === moduleId ? { ...item, visible: !item.visible } : item)));
  }

  function moveModule(moduleId: ModuleId, direction: -1 | 1) {
    const index = selectedModules.findIndex((item) => item.id === moduleId);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= selectedModules.length) return;
    const next = [...selectedModules];
    const [item] = next.splice(index, 1);
    next.splice(nextIndex, 0, item);
    updateModulePreferences(next);
  }

  function handleError(err: unknown) {
    if (err instanceof ApiError && err.code === 'LOGIN_REQUIRED') {
      setError('请先登录后再执行该操作');
      return;
    }
    setError(err instanceof Error ? err.message : '操作失败');
  }

  return (
    <div className="tool-surface monitor-tool">
      <div className="tool-header monitor-titlebar">
        <div>
          <p className="eyebrow">SSH Monitor</p>
          <h1>服务器监控看板</h1>
        </div>
        <div className="toolbar">
          {me && <button className="primary-button" type="button" onClick={openCreate}><Plus size={16} />添加服务器</button>}
          <button className="chip" type="button" disabled={!selected} onClick={() => selected && void refreshSelected(selected.id, true)}>
            <RefreshCw size={15} />刷新
          </button>
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}

      <ServerStrip servers={servers} selectedId={selected?.id ?? ''} onSelect={setSelectedId} onEdit={openEdit} canEdit={canEditSelected} />

      {selected && sample ? (
        <>
          {sample.error && <div className="error-box">采集失败：{sample.error}</div>}
          {selectedModules.filter((item) => item.visible).map((module) => {
            if (module.id === 'summary') return <MetricGrid key={module.id} sample={sample} disks={displayedDisks} />;
            if (module.id === 'trends') {
              return (
                <section className="monitor-section" key={module.id}>
                  <div className="result-header">
                    <span>24 小时趋势</span>
                    <span className="muted">{history.length} 个采样点</span>
                  </div>
                  <div className="chart-grid">
                    <LineChart title="CPU %" samples={history} getValue={(item) => item.cpuPercent} />
                    <LineChart title="内存 %" samples={history} getValue={(item) => percent(item.memoryUsedBytes, item.memoryTotalBytes)} />
                  </div>
                </section>
              );
            }
            if (module.id === 'disks') return <DiskPanel key={module.id} displayedDisks={displayedDisks} />;
            if (module.id === 'directories') return <DirectoryCards key={module.id} directories={directories} />;
            return (
              <GpuPanel
                key={module.id}
                gpus={sample.gpus}
                activeGpu={activeGpu}
                history={history}
                layout={gpuLayout}
                serverUsername={selected.sshUsername}
                canKill={canEditSelected}
                onLayout={setGpuLayoutPreference}
                onSelect={setActiveGpuIndex}
                onKill={(pid) => void killProcess(pid)}
              />
            );
          })}
        </>
      ) : (
        <div className="empty-state">选择服务器后查看实时数据。</div>
      )}

      {!me && <LoginPanel onSuccess={() => window.location.reload()} />}

      {modal && (
        <Modal title={modal === 'edit' ? '编辑服务器' : '添加服务器'} onClose={() => setModal(null)}>
          <ServerForm form={form} isAdmin={isAdmin} isEdit={modal === 'edit'} isLoading={isLoading} onChange={setForm} onSubmit={saveServer} />
          {modal === 'edit' && selected && (
            <section className="modal-subsection">
              <div className="result-header">
                <span>模块显示与顺序</span>
                <span className="muted">影响当前服务器看板</span>
              </div>
              <div className="module-preference-list">
                {selectedModules.map((module, index) => (
                  <div className="module-preference-row" key={module.id}>
                    <label className="check-row">
                      <input type="checkbox" checked={module.visible} onChange={() => toggleModule(module.id)} />
                      {module.label}
                    </label>
                    <div className="module-order-buttons">
                      <button className="icon-button tiny" type="button" disabled={index === 0} onClick={() => moveModule(module.id, -1)} title="上移"><ArrowUp size={13} /></button>
                      <button className="icon-button tiny" type="button" disabled={index === selectedModules.length - 1} onClick={() => moveModule(module.id, 1)} title="下移"><ArrowDown size={13} /></button>
                    </div>
                  </div>
                ))}
              </div>
              {sample && (
                <>
                  <div className="result-header">
                    <span>硬盘挂载显示</span>
                    <span className="muted">新服务器默认全部显示</span>
                  </div>
                  <div className="mount-filter modal-mount-filter">
                    {sample.disks.map((disk) => {
                      const checked = !visibleDiskKeys || visibleDiskKeys.includes(disk.mountPath);
                      return (
                        <label className={checked ? 'mount-chip active' : 'mount-chip'} key={disk.mountPath}>
                          <input type="checkbox" checked={checked} onChange={() => toggleMount(disk.mountPath)} />
                          {disk.mountPath}
                        </label>
                      );
                    })}
                  </div>
                </>
              )}
              <div className="result-header">
                <span>固定展示的文件夹</span>
                <span className="muted">符合白名单后会定期刷新</span>
              </div>
              <form className="directory-query" onSubmit={addTrackedDirectory}>
                <input className="text-input" placeholder="/data/project" value={form.trackedDirectory} onChange={(event) => setForm({ ...form, trackedDirectory: event.target.value })} />
                <button className="primary-button" type="submit" disabled={isLoading}><FolderPlus size={16} />添加</button>
              </form>
              <div className="compact-list">
                {directories.length ? directories.map((directory) => (
                  <span className="path-pill removable" key={directory.path}>
                    <span>{directory.path}</span>
                    <button className="icon-button tiny danger" type="button" onClick={() => void removeTrackedDirectory(directory.path)} title="删除固定文件夹">
                      <Trash2 size={13} />
                    </button>
                  </span>
                )) : <p className="muted">尚未添加固定展示目录。</p>}
              </div>
            </section>
          )}
          {modal === 'edit' && canEditSelected && (
            <button className="danger-row modal-danger" type="button" onClick={() => void removeServer()}>
              <Trash2 size={15} />删除当前服务器
            </button>
          )}
        </Modal>
      )}
    </div>
  );
}

function ServerStrip(props: {
  servers: MonitorServer[];
  selectedId: string;
  canEdit: boolean;
  onSelect: (id: string) => void;
  onEdit: () => void;
}) {
  return (
    <section className="server-strip">
      <div className="server-tabs">
        {props.servers.length === 0 ? (
          <span className="muted">暂无可查看服务器</span>
        ) : (
          props.servers.map((serverItem) => (
            <button
              key={serverItem.id}
              className={props.selectedId === serverItem.id ? 'server-tab active' : 'server-tab'}
              type="button"
              onClick={() => props.onSelect(serverItem.id)}
            >
              <Server size={15} />
              <strong>{serverItem.name}</strong>
              <small>{serverItem.host}</small>
              {serverItem.isDefault && <em>默认</em>}
            </button>
          ))
        )}
      </div>
      <button className="chip" type="button" disabled={!props.canEdit} onClick={props.onEdit}>
        <Settings size={15} />编辑
      </button>
    </section>
  );
}

function MetricGrid({ sample, disks }: { sample: Sample; disks: Disk[] }) {
  const memoryPercent = percent(sample.memoryUsedBytes, sample.memoryTotalBytes);
  const diskTotals = useMemo(() => disks.reduce(
    (acc, disk) => ({ total: acc.total + disk.totalBytes, used: acc.used + disk.usedBytes }),
    { total: 0, used: 0 },
  ), [disks]);
  return (
    <div className="metric-grid">
      <MetricCard icon={<Cpu size={18} />} label="CPU" value={formatPercent(sample.cpuPercent)} />
      <MetricCard icon={<MemoryStick size={18} />} label="内存" value={formatPercent(memoryPercent)} detail={`${formatBytes(sample.memoryUsedBytes)} / ${formatBytes(sample.memoryTotalBytes)}`} />
      <MetricCard icon={<HardDrive size={18} />} label="硬盘" value={formatPercent(percent(diskTotals.used, diskTotals.total))} detail={`${disks.length} 个展示挂载点`} />
      <MetricCard icon={<SquareActivity size={18} />} label="CUDA GPU" value={`${sample.gpus.length} 张`} detail={`${sample.gpus.reduce((sum, gpu) => sum + (gpu.processCount ?? 0), 0)} 个进程`} />
    </div>
  );
}

function MetricCard({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string; detail?: string }) {
  return (
    <div className="metric-card">
      <span>{icon}{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function LineChart({ title, samples, getValue }: { title: string; samples: Sample[]; getValue: (sample: Sample) => number | null }) {
  const points = samples.map((sampleItem, index) => ({ index, value: getValue(sampleItem) })).filter((point) => point.value !== null) as { index: number; value: number }[];
  const path = points.map((point, pointIndex) => {
    const x = points.length === 1 ? 150 : (pointIndex / (points.length - 1)) * 300;
    const y = 100 - Math.max(0, Math.min(100, point.value));
    return `${pointIndex === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
  return (
    <div className="mini-chart">
      <div className="result-header"><span>{title}</span><small>{points.length ? formatPercent(points.at(-1)?.value ?? null) : '--'}</small></div>
      <svg viewBox="0 0 300 100" role="img" aria-label={title}>
        <path d="M 0 100 L 300 100" />
        {path && <path className="chart-line" d={path} />}
      </svg>
    </div>
  );
}

function DiskPanel(props: { displayedDisks: Disk[] }) {
  return (
    <section className="monitor-section">
      <div className="result-header">
        <span>硬盘挂载</span>
        <span className="muted">显示项在服务器编辑页配置</span>
      </div>
      <div className="disk-grid">
        {props.displayedDisks.map((disk) => (
          <div className="disk-card" key={`${disk.filesystem}-${disk.mountPath}`}>
            <div className="result-header">
              <strong>{disk.mountPath}</strong>
              <small>{formatPercent(percent(disk.usedBytes, disk.totalBytes))}</small>
            </div>
            <span className="muted">{disk.filesystem}</span>
            <div className="progress-track"><span style={{ width: formatProgress(percent(disk.usedBytes, disk.totalBytes)) }} /></div>
            <div className="usage-legend">
              <span><i className="current" />已用 {formatBytes(disk.usedBytes)}</span>
              <span><i className="free" />剩余 {formatBytes(disk.freeBytes)}</span>
            </div>
            <small>{formatBytes(disk.totalBytes)} 总量</small>
          </div>
        ))}
      </div>
    </section>
  );
}

function DirectoryCards({ directories }: { directories: DirectoryUsage[] }) {
  if (!directories.length) return null;
  return (
    <section className="monitor-section">
      <div className="result-header">
        <span>固定文件夹空间</span>
        <span className="muted">由服务器编辑页添加，后台定期刷新</span>
      </div>
      <div className="directory-grid">
        {directories.map((directory) => {
          const usage = directoryUsageParts(directory);
          return (
            <div className="directory-result" key={directory.path}>
              <span>{directory.path}</span>
              <strong>{formatBytes(usage.currentBytes)} 当前目录</strong>
              <div className="stacked-usage-bar" aria-label="目录所在挂载点空间占用">
                <span className="current" style={{ width: formatProgress(usage.currentPercent) }} />
                <span className="other" style={{ width: formatProgress(usage.otherPercent) }} />
                <span className="free" style={{ width: formatProgress(usage.freePercent) }} />
              </div>
              <div className="usage-legend">
                <span><i className="current" />当前 {formatBytes(usage.currentBytes)}</span>
                <span><i className="other" />其他 {formatBytes(usage.otherBytes)}</span>
                <span><i className="free" />剩余 {formatBytes(usage.freeBytes)}</span>
              </div>
              <small>{formatBytes(directory.totalBytes)} 总量</small>
              <small>{directory.refreshedAt ? new Date(directory.refreshedAt).toLocaleString() : '等待刷新'}</small>
              {directory.error && <small className="danger-text">{directory.error}</small>}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function GpuPanel(props: {
  gpus: Gpu[];
  activeGpu: Gpu | null;
  history: Sample[];
  layout: GpuLayout;
  serverUsername: string;
  canKill: boolean;
  onLayout: (layout: GpuLayout) => void;
  onSelect: (index: number) => void;
  onKill: (pid: number) => void;
}) {
  const cards = (
    <div className="gpu-grid">
      {props.gpus.map((gpu) => {
        const memoryPercent = percent(gpu.memoryUsedMiB, gpu.memoryTotalMiB);
        return (
          <button className={props.activeGpu?.index === gpu.index ? 'gpu-card active' : 'gpu-card'} key={gpu.index} type="button" onClick={() => props.onSelect(gpu.index)}>
            <div className="gpu-card-top">
              <span><SquareActivity size={18} />#{gpu.index}</span>
              <em>{gpu.processCount ?? 0} 进程</em>
            </div>
            <strong>{gpu.name}</strong>
            <div className="gpu-stats">
              <span><Gauge size={15} />负载 {formatPercent(gpu.utilizationPercent)}</span>
              <span><Database size={15} />显存 {formatGpuMemory(gpu.memoryUsedMiB)} / {formatGpuMemory(gpu.memoryTotalMiB)}</span>
              <span><Thermometer size={15} />{gpu.temperatureC ?? '--'} C</span>
              <span><Zap size={15} />{gpu.powerW ?? '--'} W</span>
            </div>
            <ProgressLine label="显存" value={memoryPercent} />
            <ProgressLine label="负载" value={gpu.utilizationPercent} />
          </button>
        );
      })}
    </div>
  );
  const detail = props.activeGpu ? <GpuDetail activeGpu={props.activeGpu} history={props.history} canKill={props.canKill} serverUsername={props.serverUsername} onKill={props.onKill} /> : null;
  const overview = <GpuOverview gpus={props.gpus} canKill={props.canKill} serverUsername={props.serverUsername} onKill={props.onKill} />;

  return (
    <section className="monitor-section">
      <div className="result-header">
        <span>CUDA GPU</span>
        <div className="segmented-control" aria-label="GPU 布局">
          <button className={props.layout === 'stacked' ? 'active' : ''} type="button" onClick={() => props.onLayout('stacked')} title="上下布局"><Rows3 size={15} /></button>
          <button className={props.layout === 'split' ? 'active' : ''} type="button" onClick={() => props.onLayout('split')} title="左右布局"><Columns2 size={15} /></button>
          <button className={props.layout === 'overview' ? 'active' : ''} type="button" onClick={() => props.onLayout('overview')} title="总览布局"><LayoutDashboard size={15} /></button>
        </div>
      </div>
      {props.gpus.length && props.layout === 'overview' ? overview : props.gpus.length ? (
        <div className={props.layout === 'split' ? 'gpu-layout split' : 'gpu-layout stacked'}>
          {cards}
          {detail}
        </div>
      ) : <p className="muted">未检测到 CUDA GPU。</p>}
    </section>
  );
}

function GpuOverview(props: { gpus: Gpu[]; canKill: boolean; serverUsername: string; onKill: (pid: number) => void }) {
  const processes = props.gpus.flatMap((gpu) => gpu.processes.map((process) => ({ ...process, gpuIndex: gpu.index })));
  return (
    <div className="gpu-overview">
      <div className="gpu-overview-grid">
        {props.gpus.map((gpu) => (
          <div className="gpu-overview-row" key={gpu.index}>
            <span><strong>GPU {gpu.index}</strong><small>{gpu.name}</small></span>
            <ProgressLine label="显存" value={percent(gpu.memoryUsedMiB, gpu.memoryTotalMiB)} />
            <ProgressLine label="负载" value={gpu.utilizationPercent} />
            <span>{formatGpuMemory(gpu.memoryUsedMiB)} / {formatGpuMemory(gpu.memoryTotalMiB)}</span>
            <span>{gpu.processCount ?? 0} 进程</span>
          </div>
        ))}
      </div>
      <div className="result-header"><span>全部 GPU 进程</span><span className="muted">{processes.length} 个</span></div>
      <ProcessTable processes={processes} canKill={props.canKill} serverUsername={props.serverUsername} onKill={props.onKill} showGpu />
    </div>
  );
}

function GpuDetail(props: { activeGpu: Gpu; history: Sample[]; canKill: boolean; serverUsername: string; onKill: (pid: number) => void }) {
  return (
    <div className="gpu-detail">
      <div className="chart-grid">
        <LineChart title={`GPU #${props.activeGpu.index} 利用率`} samples={props.history} getValue={(item) => item.gpus.find((gpu) => gpu.index === props.activeGpu.index)?.utilizationPercent ?? null} />
        <LineChart title={`GPU #${props.activeGpu.index} 显存`} samples={props.history} getValue={(item) => {
          const gpu = item.gpus.find((entry) => entry.index === props.activeGpu.index);
          return gpu ? percent(gpu.memoryUsedMiB, gpu.memoryTotalMiB) : null;
        }} />
      </div>
      <div className="result-header"><span>当前挂载进程</span><span className="muted">{props.activeGpu.processes.length} 个</span></div>
      <ProcessTable processes={props.activeGpu.processes} canKill={props.canKill} serverUsername={props.serverUsername} onKill={props.onKill} />
    </div>
  );
}

type GpuLayout = 'stacked' | 'split' | 'overview';
type ProcessWithGpu = GpuProcess & { gpuIndex?: number };

function ProcessTable(props: { processes: ProcessWithGpu[]; canKill: boolean; serverUsername: string; showGpu?: boolean; onKill: (pid: number) => void }) {
  return (
    <div className="process-table">
      {props.processes.length ? props.processes.map((process) => {
          const canKillProcess = props.canKill && process.username === props.serverUsername;
          return (
            <div className={props.showGpu ? 'process-row with-gpu' : 'process-row'} key={`${process.gpuIndex ?? 'gpu'}-${process.pid}`}>
              {props.showGpu && <span>GPU {process.gpuIndex}</span>}
              <span className="process-name"><strong>{process.name}</strong><small>{process.command || `PID ${process.pid}`}</small></span>
              <span>{process.username ?? '--'}</span>
              <span>{formatGpuMemory(process.usedMemoryMiB)} 显存</span>
              <span>CPU {formatPercent(process.cpuPercent)}</span>
              <span>GPU {formatPercent(process.gpuPercent)}</span>
              <span>内存 {formatPercent(process.memoryPercent)}</span>
              {canKillProcess ? (
                <button className="icon-button danger" type="button" onClick={() => props.onKill(process.pid)} title="终止进程">
                  <Trash2 size={15} />
                </button>
              ) : <span className="muted">-</span>}
            </div>
          );
        }) : <p className="muted">当前没有采集到 GPU 进程。</p>}
    </div>
  );
}

function ProgressLine({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="progress-line">
      <span>{label}</span>
      <div className="progress-track"><span style={{ width: formatProgress(value) }} /></div>
      <small>{formatPercent(value)}</small>
    </div>
  );
}

function ServerForm(props: {
  form: ServerFormState;
  isAdmin: boolean;
  isEdit: boolean;
  isLoading: boolean;
  onChange: (form: ServerFormState) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const { form, isAdmin, isEdit, isLoading, onChange, onSubmit } = props;
  return (
    <form className="em-form" onSubmit={onSubmit}>
      <div className="form-group">
        <label>服务器名称 *</label>
        <input className="text-input" placeholder="如：实验服务器 A" value={form.name} onChange={(event) => onChange({ ...form, name: event.target.value })} />
      </div>

      <fieldset className="em-fieldset">
        <legend>SSH 连接信息</legend>
        <div className="em-form-grid2">
          <div className="form-group">
            <label>主机 / IP 地址 *</label>
            <input className="text-input" placeholder="192.168.1.100 或 example.com" value={form.host} onChange={(event) => onChange({ ...form, host: event.target.value })} />
          </div>
          <div className="form-group">
            <label>SSH 端口</label>
            <input className="text-input" type="number" min="1" max="65535" placeholder="22" value={form.port} onChange={(event) => onChange({ ...form, port: Number(event.target.value) })} />
          </div>
        </div>
        <div className="em-form-grid2">
          <div className="form-group">
            <label>SSH 用户名 *</label>
            <input className="text-input" placeholder="root 或 ubuntu" value={form.sshUsername} onChange={(event) => onChange({ ...form, sshUsername: event.target.value })} />
          </div>
          <div className="form-group">
            <label>SSH 密码{isEdit ? '（留空则不修改）' : ' *'}</label>
            <input className="text-input" type="password" placeholder={isEdit ? '留空则不修改' : '••••••••'} value={form.sshPassword} onChange={(event) => onChange({ ...form, sshPassword: event.target.value })} />
          </div>
        </div>
      </fieldset>

      <fieldset className="em-fieldset">
        <legend>目录监控配置</legend>
        <div className="form-group">
          <label>目录白名单</label>
          <textarea className="text-input" rows={3} placeholder={'/data\n/home/user/projects'} value={form.directoryWhitelist} onChange={(event) => onChange({ ...form, directoryWhitelist: event.target.value })} />
          <small className="form-hint">每行一个绝对路径，只有在白名单内的目录才会被追踪统计</small>
        </div>
        <div className="form-group">
          <label>目录空间刷新间隔（秒）</label>
          <input className="text-input" type="number" min="10" placeholder="300" value={form.directoryRefreshSeconds} onChange={(event) => onChange({ ...form, directoryRefreshSeconds: Number(event.target.value) })} />
          <small className="form-hint">后台定期重新统计固定文件夹占用空间的频率，建议 60～600 秒</small>
        </div>
      </fieldset>

      {isAdmin && (
        <label className="check-row">
          <input type="checkbox" checked={form.isDefault} onChange={(event) => onChange({ ...form, isDefault: event.target.checked })} />
          设为默认服务器（所有用户均可查看）
        </label>
      )}

      <button className="primary-button" type="submit" disabled={isLoading}>
        <Server size={16} />{isEdit ? '保存服务器' : '添加服务器'}
      </button>
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

function splitLines(value: string) {
  return value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean);
}

function preferenceKey(user: AuthUser | null, key: string) {
  return `server-monitor:${user?.id ?? 'anonymous'}:${key}`;
}

function normalizeModules(value: ModulePreference[] | undefined) {
  if (!value) return defaultModules;
  const byId = new Map(value.map((item) => [item.id, item]));
  const ordered = value
    .filter((item) => defaultModules.some((module) => module.id === item.id))
    .map((item) => ({ ...defaultModules.find((module) => module.id === item.id)!, visible: item.visible }));
  for (const module of defaultModules) {
    if (!byId.has(module.id)) ordered.push(module);
  }
  return ordered;
}

function percent(used: number | null, total: number | null) {
  if (used === null || total === null || total <= 0) return null;
  return Math.round((used / total) * 10000) / 100;
}

function directoryUsageParts(directory: DirectoryUsage) {
  const total = Math.max(0, directory.totalBytes ?? 0);
  const current = Math.max(0, directory.usedBytes ?? 0);
  const free = Math.max(0, directory.freeBytes ?? 0);
  const other = Math.max(0, total - free - current);
  const adjustedCurrent = total > 0 ? Math.min(current, total) : current;
  const adjustedOther = total > 0 ? Math.min(other, Math.max(0, total - adjustedCurrent)) : other;
  const adjustedFree = total > 0 ? Math.max(0, total - adjustedCurrent - adjustedOther) : free;
  return {
    currentBytes: adjustedCurrent,
    otherBytes: adjustedOther,
    freeBytes: adjustedFree,
    currentPercent: total ? (adjustedCurrent / total) * 100 : null,
    otherPercent: total ? (adjustedOther / total) * 100 : null,
    freePercent: total ? (adjustedFree / total) * 100 : null,
  };
}

function formatPercent(value: number | null) {
  return value === null || Number.isNaN(value) ? '--' : `${Math.round(value * 10) / 10}%`;
}

function formatProgress(value: number | null) {
  if (value === null || Number.isNaN(value)) return '0%';
  return `${Math.max(0, Math.min(100, value))}%`;
}

function formatGpuMemory(value: number | null) {
  if (value === null || value === undefined) return '--';
  return `${(value / 1024).toFixed(1)} GB`;
}

function formatBytes(value: number | null) {
  if (value === null || value === undefined) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}
