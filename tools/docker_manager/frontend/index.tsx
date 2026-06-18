import './style.css';
import { FormEvent, useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  AlertCircle,
  Box,
  CheckCircle,
  ClipboardList,
  Copy,
  Database,
  Download,
  ExternalLink,
  FileText,
  Globe,
  HardDrive,
  Image,
  Info,
  Layers,
  Loader2,
  Network,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Server,
  Settings,
  Shield,
  Square,
  Terminal,
  Trash2,
  Users,
  X,
} from 'lucide-react';

import { ApiError, apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

// ============================================================
// Types
// ============================================================

type PermLevel = 'manage' | 'use' | 'view' | 'none';

type DmServer = {
  id: string;
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  permissionLevel: PermLevel;
  createdAt: string;
};

// 细粒度权限结构
type UserPerms = {
  server_visible: boolean;
  // 镜像
  img_pull: boolean;
  img_delete: boolean;
  img_copy: boolean;
  // 容器
  ctr_view_own: boolean;
  ctr_view_all: boolean;
  ctr_create_run: boolean;
  ctr_create_compose: boolean;
  ctr_create_template: boolean;
  ctr_manage_own: boolean;
  ctr_manage_all: boolean;
  ctr_path_whitelist: string[];
  // 卷
  vol_create: boolean;
  vol_delete_own: boolean;
  vol_delete_all: boolean;
  vol_copy: boolean;
  vol_quota_gb: number;
  // 模板
  tpl_use: boolean;
  tpl_create: boolean;
  tpl_edit: boolean;
};

const DEFAULT_PERMS: UserPerms = {
  server_visible: false,
  img_pull: false, img_delete: false, img_copy: false,
  ctr_view_own: false, ctr_view_all: false,
  ctr_create_run: false, ctr_create_compose: false, ctr_create_template: false,
  ctr_manage_own: false, ctr_manage_all: false, ctr_path_whitelist: [],
  vol_create: false, vol_delete_own: false, vol_delete_all: false, vol_copy: false, vol_quota_gb: 0,
  tpl_use: false, tpl_create: false, tpl_edit: false,
};

type ServerPermEntry = {
  userId: string;
  username: string;
  displayName: string;
  role: string;
  level: PermLevel;
  perms: UserPerms;
};

// 兼容旧配额类型（内部使用）
type UserQuota = {
  volumeTotalGb: number;
  volumeUsedGb?: number;
  pathWhitelist: string[];
  canCreateContainer: boolean;
  canManageContainer: boolean;
};

type DockerImage = {
  id: string;
  repo: string;
  tag: string;
  size: string;
  created: string;
};

type DockerContainer = {
  ID?: string;
  Names?: string;
  Image?: string;
  Status?: string;
  State?: string;
  Ports?: string;
  CreatedAt?: string;
};

// 容器详情类型（来自 docker inspect）
type ContainerPortBinding = {
  containerPort: string;
  hostIp: string;
  hostPort: string;
};

type ContainerMount = {
  type: string;
  source: string;
  destination: string;
  mode: string;
  rw: boolean;
  name: string;
};

type ContainerNetwork = {
  name: string;
  ipAddress: string;
  gateway: string;
  macAddress: string;
};

type ContainerDetail = {
  id: string;
  shortId: string;
  name: string;
  image: string;
  imageId: string;
  status: string;
  running: boolean;
  paused: boolean;
  restarting: boolean;
  startedAt: string;
  finishedAt: string;
  exitCode: number;
  created: string;
  restartPolicy: string;
  platform: string;
  hostname: string;
  cmd: string[];
  entrypoint: string[];
  workingDir: string;
  user: string;
  envs: string[];
  ports: ContainerPortBinding[];
  mounts: ContainerMount[];
  networks: ContainerNetwork[];
  sshHostPort: string | null;
  serverHost: string;
  serverSshUsername: string;
  platformMeta: {
    ownerUserId: string | null;
    assignedAt: string | null;
    displayPorts: string[] | null;
  };
};

type DockerVolume = {
  name: string;
  driver: string;
  mountpoint: string;
  ownerUserId?: string;
  sizeGb?: number;
  createdAt?: string;
  platformManaged: boolean;
};

type VolumeDetailUser = {
  userId: string;
  username: string;
  displayName: string;
};

type MountedContainer = {
  id: string;
  name: string;
  image: string;
  status: string;
  state: string;
};

type VolumeDetail = {
  serverId: string;
  name: string;
  sizeGb: number | null;
  createdAt: string | null;
  platformManaged: boolean;
  roles: {
    creatorUserId: string | null;
    creator: VolumeDetailUser | null;
    ownerUserIds: string[];
    owners: VolumeDetailUser[];
    viewerUserIds: string[];
    viewers: VolumeDetailUser[];
  };
  mountedContainers: MountedContainer[];
  hiddenContainerCount: number;
};

type Template = {
  id: string;
  name: string;
  description: string;
  category: string;
  creatorId: string;
  hasDoc: boolean;
  config: Record<string, unknown>;
  isPublic: boolean;
  createdAt: string;
  updatedAt: string;
};

type TemplateDetail = Template & { docContent: string };

// 资源多角色管理相关类型
type ResourceRoles = {
  ownerUserIds: string[];       // 多所有者
  viewerUserIds: string[];      // 多查看者
  creatorUserId: string | null; // 创建者（唯一）
  platformManaged: boolean;
  ownerUserId?: string | null;  // 兼容旧字段
};

type ResourceItem = ResourceRoles;

type ContainerResource = DockerContainer & ResourceItem;
type ImageResource = DockerImage & ResourceItem;
type VolumeResource = { name: string } & ResourceItem;

type ServerResources = {
  serverId: string;
  containers: ContainerResource[];
  images: ImageResource[];
  volumes: VolumeResource[];
};

// 我的资源（非管理员用于查看和管理 viewer）
type ViewerDetail = {
  userId: string;
  username: string;
  displayName: string;
};

type MyOwnedResource = {
  serverId: string;
  serverName: string;
  resourceType: 'container' | 'image' | 'volume';
  resourceRef: string;
  creatorUserId: string | null;
  viewerUserIds: string[];
  viewers: ViewerDetail[];
};

// ============================================================
// Tiny Markdown Renderer (no external deps)
// ============================================================

function renderMarkdown(md: string): string {
  let html = md
    // Code blocks
    .replace(/```[\s\S]*?```/g, (m) => {
      const inner = m.replace(/^```[^\n]*\n?/, '').replace(/```$/, '');
      return `<pre><code>${inner.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`;
    })
    // Inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // Headers
    .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // Bold & italic
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Blockquote
    .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
    // Unordered list
    .replace(/^[\-\*] (.+)$/gm, '<li>$1</li>')
    // Ordered list
    .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
    // Horizontal rule
    .replace(/^---$/gm, '<hr>')
    // Links
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    // Paragraphs (double newline)
    .replace(/\n\n+/g, '</p><p>')
    // Line breaks
    .replace(/\n/g, '<br>');

  // Wrap loose li in ul
  html = html.replace(/(<li>.*?<\/li>)+/gs, (m) => `<ul>${m}</ul>`);
  return `<p>${html}</p>`;
}

// ============================================================
// Helpers
// ============================================================

const API = '/api/tools/docker-manager';

function permColor(level: PermLevel): string {
  if (level === 'manage') return 'manage';
  if (level === 'use') return 'use';
  return 'view';
}

function permLabel(level: PermLevel): string {
  const m: Record<PermLevel, string> = { manage: '管理', use: '使用', view: '查看', none: '无权限' };
  return m[level] ?? level;
}

function containerStateClass(state?: string): string {
  const s = (state ?? '').toLowerCase();
  if (s.includes('running') || s === 'up') return 'running';
  if (s.includes('exited')) return 'exited';
  if (s.includes('paused')) return 'paused';
  if (s.includes('created')) return 'created';
  return 'unknown';
}

function useErrorMsg(): [string | null, (e: unknown) => void, () => void] {
  const [msg, setMsg] = useState<string | null>(null);
  const set = useCallback((e: unknown) => {
    if (e instanceof ApiError) setMsg(e.message);
    else if (e instanceof Error) setMsg(e.message);
    else setMsg(String(e));
  }, []);
  const clear = useCallback(() => setMsg(null), []);
  return [msg, set, clear];
}

// ============================================================
// Shared UI Components
// ============================================================

function Alert({ type, children }: { type: 'error' | 'success' | 'info'; children: ReactNode }) {
  const icon = type === 'error' ? <AlertCircle size={16} /> : type === 'success' ? <CheckCircle size={16} /> : <AlertCircle size={16} />;
  return <div className={`dm-alert ${type}`}>{icon}<span>{children}</span></div>;
}

function Spin() {
  return <Loader2 size={16} className="spin" style={{ display: 'inline-block' }} />;
}

// 骨架屏行：cols 是每列宽度比例数组，传几个就画几格占位
function SkeletonRows({ cols, rows = 5 }: { cols: string[]; rows?: number }) {
  return (
    <div className="dm-table">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="dm-skeleton-row" style={{ gridTemplateColumns: cols.join(' ') }}>
          {cols.map((_, j) => (
            <div key={j} className={`dm-skeleton-cell ${j === 0 ? 'medium' : j % 2 === 0 ? 'short' : 'long'}`} />
          ))}
        </div>
      ))}
    </div>
  );
}

// 带加载遮罩的容器
function ResourceLoadingWrapper({ loading, children }: { loading: boolean; children: ReactNode }) {
  return (
    <div className="dm-resource-loading">
      {children}
      {loading && (
        <div className="dm-resource-loading-overlay">
          <div className="dm-resource-loading-spinner" />
          <span>正在从服务器获取数据…</span>
        </div>
      )}
    </div>
  );
}

function Modal({ title, onClose, children, foot, wide }: { title: string; onClose: () => void; children: ReactNode; foot?: ReactNode; wide?: boolean }) {
  return (
    <div className="dm-modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`dm-modal${wide ? ' wide' : ''}`}>
        <div className="dm-modal-head">
          <h3>{title}</h3>
          <button className="dm-btn-icon" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="dm-modal-body">{children}</div>
        {foot && <div className="dm-modal-foot">{foot}</div>}
      </div>
    </div>
  );
}

function Field({ label, children, full }: { label: string; children: ReactNode; full?: boolean }) {
  return (
    <div className={`dm-form-field${full ? ' dm-full-col' : ''}`}>
      <label>{label}</label>
      {children}
    </div>
  );
}

function ServerSelector({ servers, selected, onSelect }: { servers: DmServer[]; selected: string | null; onSelect: (id: string) => void }) {
  if (servers.length === 0) return <Alert type="info">暂无可访问的服务器，请联系管理员添加并授权</Alert>;
  return (
    <div className="dm-server-selector">
      <span style={{ fontSize: 13, color: '#526071', marginRight: 4 }}>选择服务器：</span>
      {servers.map((s) => (
        <button key={s.id} className={`dm-server-chip${selected === s.id ? ' active' : ''}`} onClick={() => onSelect(s.id)}>
          <Server size={13} />
          {s.name}
          <span className={`dm-perm-badge ${permColor(s.permissionLevel)}`}>{permLabel(s.permissionLevel)}</span>
        </button>
      ))}
    </div>
  );
}

// ============================================================
// Images Panel
// ============================================================

function ImagesPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  const [serverId, setServerId] = useState<string | null>(servers[0]?.id ?? null);
  const [images, setImages] = useState<DockerImage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [pullRef, setPullRef] = useState('');
  const [pulling, setPulling] = useState(false);
  const [pullMsg, setPullMsg] = useState('');
  const [showCopy, setShowCopy] = useState(false);
  const [copyDst, setCopyDst] = useState('');
  const [copyRef, setCopyRef] = useState('');
  const [copying, setCopying] = useState(false);

  const canManage = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || s?.permissionLevel === 'manage';
  };

  const canUse = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || s?.permissionLevel === 'manage' || s?.permissionLevel === 'use';
  };

  const load = useCallback(async (sid: string) => {
    setLoading(true);
    clearError();
    try {
      const r = await apiGet<{ images: DockerImage[] }>(`${API}/servers/${sid}/images`);
      setImages(r.images);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { if (serverId) void load(serverId); }, [serverId, load]);

  async function doPull() {
    if (!serverId || !pullRef.trim()) return;
    setPulling(true);
    setPullMsg('');
    clearError();
    try {
      const r = await apiPost<{ output: string }>(`${API}/servers/${serverId}/images/pull`, { imageRef: pullRef.trim() });
      setPullMsg(r.output.slice(-400));
      void load(serverId);
      setPullRef('');
    } catch (e) {
      setError(e);
    } finally {
      setPulling(false);
    }
  }

  async function doDelete(imageRef: string) {
    if (!serverId) return;
    if (!confirm(`确定要删除镜像 ${imageRef} 吗？`)) return;
    clearError();
    try {
      await apiDelete(`${API}/servers/${serverId}/images/${encodeURIComponent(imageRef)}`);
      void load(serverId);
    } catch (e) {
      setError(e);
    }
  }

  async function doCopy() {
    if (!serverId || !copyDst || !copyRef) return;
    setCopying(true);
    clearError();
    try {
      await apiPost(`${API}/images/copy`, { srcServerId: serverId, dstServerId: copyDst, imageRef: copyRef });
      alert('镜像复制成功');
      setShowCopy(false);
    } catch (e) {
      setError(e);
    } finally {
      setCopying(false);
    }
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <ServerSelector servers={servers} selected={serverId} onSelect={(id) => { setServerId(id); setImages([]); }} />
      {error && <Alert type="error">{error}</Alert>}

      {serverId && canUse(serverId) && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="dm-form-field" style={{ flex: '1 1 260px' }}>
            <label>拉取镜像</label>
            <input value={pullRef} onChange={(e) => setPullRef(e.target.value)} placeholder="nginx:latest 或 registry/image:tag"
              onKeyDown={(e) => e.key === 'Enter' && doPull()} />
          </div>
          <button className="btn btn-primary" onClick={doPull} disabled={pulling || !pullRef.trim()}>
            {pulling ? <Spin /> : <Download size={14} />} 拉取
          </button>
          {canManage(serverId) && servers.length > 1 && (
            <button className="btn" onClick={() => setShowCopy(true)}><Copy size={14} /> 跨服务器复制</button>
          )}
          <button className="btn" onClick={() => serverId && load(serverId)} disabled={loading}>
            <RefreshCw size={14} /> 刷新
          </button>
        </div>
      )}

      {pullMsg && <div className="dm-logs-box" style={{ maxHeight: 180 }}>{pullMsg}</div>}

      {loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : images.length === 0 ? (
        <div className="dm-empty"><Image size={32} /> 暂无镜像</div>
      ) : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 1fr 1fr auto' }}>
            <span>镜像</span><span>标签</span><span>大小</span><span>创建时间</span><span></span>
          </div>
          {images.map((img) => (
            <div key={img.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1fr 1fr 1fr auto' }}>
              <span style={{ fontFamily: 'monospace', fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{img.repo}</span>
              <span><code style={{ background: '#f1f5f9', padding: '2px 6px', borderRadius: 4 }}>{img.tag}</code></span>
              <span style={{ color: '#526071' }}>{img.size}</span>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>{img.created}</span>
              <span style={{ display: 'flex', gap: 4 }}>
                {canManage(serverId) && (
                  <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(`${img.repo}:${img.tag}`)}>
                    <Trash2 size={13} />
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {showCopy && (
        <Modal title="跨服务器复制镜像" onClose={() => setShowCopy(false)}
          foot={
            <>
              <button className="btn" onClick={() => setShowCopy(false)}>取消</button>
              <button className="btn btn-primary" onClick={doCopy} disabled={copying || !copyDst || !copyRef}>
                {copying ? <Spin /> : <Copy size={14} />} 开始复制
              </button>
            </>
          }>
          {error && <Alert type="error">{error}</Alert>}
          <Alert type="info">将从当前服务器（{servers.find(s => s.id === serverId)?.name}）通过 SSH 管道传输到目标服务器，大镜像可能耗时较长。</Alert>
          <div className="dm-form-grid">
            <Field label="源镜像（repo:tag）">
              <input value={copyRef} onChange={(e) => setCopyRef(e.target.value)} placeholder="nginx:latest" />
            </Field>
            <Field label="目标服务器">
              <select value={copyDst} onChange={(e) => setCopyDst(e.target.value)}>
                <option value="">请选择…</option>
                {servers.filter(s => s.id !== serverId).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </Field>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ============================================================
// Containers Panel
// ============================================================

type CreateMode = 'run' | 'compose' | 'template';

// 解析状态时间，如 "Up 2 hours" → 提取 "Up" 和 "2 hours" 两部分
function parseContainerStatus(status: string): { label: string; time: string } {
  const s = (status ?? '').trim();
  // "Up X hours/minutes/seconds" → label=Up, time=X hours
  const upMatch = s.match(/^(Up)\s+(.+?)(\s*\(.*\))?$/i);
  if (upMatch) return { label: 'Up', time: upMatch[2].trim() };
  // "Exited (N) X hours ago" → label=Exited, time=X hours ago
  const exitMatch = s.match(/^(Exited\s*(?:\(\d+\))?)\s+(.+)$/i);
  if (exitMatch) return { label: exitMatch[1].trim(), time: exitMatch[2].trim() };
  // "Created", "Paused", "Restarting"…
  const wordMatch = s.match(/^(\w+)\s*(.*)$/);
  if (wordMatch) return { label: wordMatch[1], time: wordMatch[2].trim() };
  return { label: s, time: '' };
}

function ContainersPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  const [serverId, setServerId] = useState<string | null>(servers[0]?.id ?? null);
  const [containers, setContainers] = useState<DockerContainer[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [logs, setLogs] = useState<{ id: string; name: string; text: string } | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [createMode, setCreateMode] = useState<CreateMode | null>(null);
  const [quota, setQuota] = useState<UserQuota | null>(null);
  // 容器详情弹窗
  const [detailTarget, setDetailTarget] = useState<DockerContainer | null>(null);
  const [detail, setDetail] = useState<ContainerDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  // 复制成功提示
  const [copyHint, setCopyHint] = useState<string | null>(null);

  const canCreate = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || s?.permissionLevel === 'manage' || quota?.canCreateContainer;
  };

  const canManage = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || s?.permissionLevel === 'manage' || quota?.canManageContainer;
  };

  const load = useCallback(async (sid: string) => {
    setLoading(true);
    clearError();
    try {
      const [cr, qr] = await Promise.all([
        apiGet<{ containers: DockerContainer[] }>(`${API}/servers/${sid}/containers`),
        apiGet<{ quota: UserQuota }>(`${API}/servers/${sid}/my-quota`),
      ]);
      setContainers(cr.containers);
      setQuota(qr.quota);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { if (serverId) void load(serverId); }, [serverId, load]);

  async function doAction(containerId: string, containerName: string, action: string) {
    if (!serverId) return;
    if (action === 'remove' && !confirm(`确定删除容器 ${containerName}？`)) return;
    clearError();
    try {
      await apiPost(`${API}/servers/${serverId}/containers/${containerId}/action`, { action });
      void load(serverId);
      // 如果详情弹窗打开则刷新详情
      if (detail && (detail.shortId === containerId.slice(0, 12) || detail.name === containerName)) {
        void openDetail(detailTarget!);
      }
    } catch (e) {
      setError(e);
    }
  }

  async function showLogs(containerId: string, containerName: string) {
    if (!serverId) return;
    clearError();
    setLogsLoading(true);
    setLogs({ id: containerId, name: containerName, text: '' });
    try {
      const r = await apiGet<{ logs: string }>(`${API}/servers/${serverId}/containers/${containerId}/logs?tail=300`);
      setLogs({ id: containerId, name: containerName, text: r.logs });
    } catch (e) {
      setError(e);
      setLogs(null);
    } finally {
      setLogsLoading(false);
    }
  }

  async function openDetail(c: DockerContainer) {
    if (!serverId) return;
    setDetailTarget(c);
    setDetail(null);
    setDetailLoading(true);
    setDetailError(null);
    const id = c.ID ?? '';
    try {
      const r = await apiGet<ContainerDetail>(`${API}/servers/${serverId}/containers/${encodeURIComponent(id)}/detail`);
      setDetail(r);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : String(e));
    } finally {
      setDetailLoading(false);
    }
  }

  function closeDetail() {
    setDetailTarget(null);
    setDetail(null);
    setDetailError(null);
  }

  function copyText(text: string, hint: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopyHint(hint);
      setTimeout(() => setCopyHint(null), 2000);
    }).catch(() => {
      // fallback
      const el = document.createElement('textarea');
      el.value = text;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);
      setCopyHint(hint);
      setTimeout(() => setCopyHint(null), 2000);
    });
  }

  const cid = (c: DockerContainer) => c.ID ?? '';
  const cname = (c: DockerContainer) => (c.Names ?? '').replace(/^\//, '');

  // 解析容器 Ports 字段，提取宿主机侧 SSH 端口（映射到容器 22 端口的）
  function parseSshPort(ports: string | undefined): string | null {
    if (!ports) return null;
    // 格式如：0.0.0.0:2222->22/tcp, :::2222->22/tcp
    const match = ports.match(/(?:\d+\.\d+\.\d+\.\d+:|:::)?(\d+)->22\/tcp/);
    return match ? match[1] : null;
  }

  // 解析端口字符串，过滤仅显示用户标记的端口（此版本直接展示全部端口，精简显示）
  function formatPorts(ports: string | undefined): string {
    if (!ports) return '—';
    // 每段格式：0.0.0.0:8080->80/tcp
    const parts = ports.split(', ').filter(Boolean);
    if (parts.length === 0) return '—';
    // 提取宿主机端口
    const formatted = parts
      .map((p) => {
        const m = p.match(/(?:\S+:)?(\d+)->(\d+\/\w+)/);
        if (m) return `${m[1]}→${m[2]}`;
        return p;
      })
      .filter(Boolean);
    if (formatted.length === 0) return '—';
    // 最多显示 2 条，其余用 +N
    if (formatted.length <= 2) return formatted.join(', ');
    return `${formatted.slice(0, 2).join(', ')} +${formatted.length - 2}`;
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <ServerSelector servers={servers} selected={serverId} onSelect={(id) => { setServerId(id); setContainers([]); setQuota(null); }} />
      {error && <Alert type="error">{error}</Alert>}

      {/* 复制成功浮动提示 */}
      {copyHint && (
        <div style={{
          position: 'fixed', top: 20, left: '50%', transform: 'translateX(-50%)',
          background: '#1e293b', color: '#fff', borderRadius: 8, padding: '8px 18px',
          fontSize: 13, zIndex: 9999, pointerEvents: 'none',
          boxShadow: '0 4px 16px rgba(0,0,0,0.2)',
        }}>
          <CheckCircle size={13} style={{ verticalAlign: 'middle', marginRight: 6, color: '#22c55e' }} />
          {copyHint}
        </div>
      )}

      {serverId && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {canCreate(serverId) && (
            <>
              <button className="btn btn-primary" onClick={() => setCreateMode('run')}><Plus size={14} /> docker run</button>
              <button className="btn btn-primary" onClick={() => setCreateMode('compose')}><Layers size={14} /> docker compose</button>
              <button className="btn btn-primary" onClick={() => setCreateMode('template')}><ClipboardList size={14} /> 从模板创建</button>
            </>
          )}
          <button className="btn" onClick={() => serverId && load(serverId)} disabled={loading}><RefreshCw size={14} /> 刷新</button>
        </div>
      )}

      {loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : containers.length === 0 ? (
        <div className="dm-empty"><Box size={32} /> 暂无容器</div>
      ) : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '1.8fr 1.2fr 1fr 1fr auto' }}>
            <span>名称</span><span>状态</span><span>端口</span><span>SSH 端口</span><span>操作</span>
          </div>
          {containers.map((c) => {
            const state = (c.State ?? c.Status ?? '').toLowerCase();
            const isRunning = state.includes('running') || state.startsWith('up');
            const { label: stateLabel, time: stateTime } = parseContainerStatus(c.Status ?? c.State ?? '');
            const sshPort = parseSshPort(c.Ports);
            const server = servers.find((s) => s.id === serverId);
            const sshCmd = sshPort && server
              ? `ssh -p ${sshPort} root@${server.host}`
              : null;
            return (
              <div key={cid(c)} className="dm-table-row" style={{ gridTemplateColumns: '1.8fr 1.2fr 1fr 1fr auto' }}>
                {/* 名称 */}
                <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {cname(c)}
                </span>
                {/* 状态（两行） */}
                <span>
                  <span className={`dm-status ${containerStateClass(state)}`}>
                    <span className="dm-status-dot" />
                    {stateLabel}
                  </span>
                  {stateTime && (
                    <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2, paddingLeft: 14 }}>{stateTime}</div>
                  )}
                </span>
                {/* 端口 */}
                <span style={{ color: '#64748b', fontSize: 12 }}>{formatPorts(c.Ports)}</span>
                {/* SSH 端口 */}
                <span>
                  {sshCmd ? (
                    <button
                      className="dm-ssh-port-btn"
                      title={`点击复制: ${sshCmd}`}
                      onClick={() => copyText(sshCmd, `已复制: ${sshCmd}`)}
                    >
                      <Terminal size={11} />
                      <span>:{sshPort}</span>
                    </button>
                  ) : (
                    <span style={{ color: '#cbd5e1', fontSize: 12 }}>无</span>
                  )}
                </span>
                {/* 操作 */}
                <span style={{ display: 'flex', gap: 4 }}>
                  <button className="dm-btn-icon" title="容器详情" onClick={() => void openDetail(c)}>
                    <Info size={13} />
                  </button>
                  <button className="dm-btn-icon" title="日志" onClick={() => void showLogs(cid(c), cname(c))}>
                    <FileText size={13} />
                  </button>
                  {canManage(serverId) && (
                    <>
                      {!isRunning && <button className="dm-btn-icon" title="启动" onClick={() => void doAction(cid(c), cname(c), 'start')}><Play size={13} /></button>}
                      {isRunning && <button className="dm-btn-icon" title="停止" onClick={() => void doAction(cid(c), cname(c), 'stop')}><Square size={13} /></button>}
                      <button className="dm-btn-icon" title="重启" onClick={() => void doAction(cid(c), cname(c), 'restart')}><RefreshCw size={13} /></button>
                      <button className="dm-btn-icon danger" title="删除" onClick={() => void doAction(cid(c), cname(c), 'remove')}><Trash2 size={13} /></button>
                    </>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* 容器详情弹窗 */}
      {detailTarget && (
        <Modal
          title={`容器详情 — ${cname(detailTarget)}`}
          onClose={closeDetail}
          wide
          foot={
            detail ? (
              <div style={{ display: 'flex', gap: 8, width: '100%' }}>
                <div style={{ display: 'flex', gap: 6, flex: 1 }}>
                  {canManage(serverId) && (
                    <>
                      {!detail.running && (
                        <button className="btn btn-primary" onClick={() => void doAction(detail.shortId, detail.name, 'start')}>
                          <Play size={13} /> 启动
                        </button>
                      )}
                      {detail.running && (
                        <button className="btn" onClick={() => void doAction(detail.shortId, detail.name, 'stop')}>
                          <Square size={13} /> 停止
                        </button>
                      )}
                      <button className="btn" onClick={() => void doAction(detail.shortId, detail.name, 'restart')}>
                        <RefreshCw size={13} /> 重启
                      </button>
                      <button className="btn danger" onClick={() => void doAction(detail.shortId, detail.name, 'remove')}>
                        <Trash2 size={13} /> 删除
                      </button>
                    </>
                  )}
                  <button className="btn" onClick={() => void showLogs(detail.shortId, detail.name)}>
                    <FileText size={13} /> 查看日志
                  </button>
                </div>
                <button className="btn btn-primary" onClick={closeDetail}>关闭</button>
              </div>
            ) : (
              <button className="btn btn-primary" onClick={closeDetail}>关闭</button>
            )
          }
        >
          {detailLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#526071', padding: '32px 0', justifyContent: 'center' }}>
              <Spin /> 加载容器详情中…
            </div>
          )}
          {detailError && <Alert type="error">{detailError}</Alert>}
          {detail && !detailLoading && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

              {/* ── 基本信息 ── */}
              <div className="dm-perm-section">
                <div className="dm-perm-section-title"><Box size={13} /> 基本信息</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 20px', fontSize: 13 }}>
                  <div><span style={{ color: '#94a3b8' }}>容器名称：</span>
                    <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{detail.name}</span></div>
                  <div><span style={{ color: '#94a3b8' }}>短 ID：</span>
                    <span style={{ fontFamily: 'monospace' }}>{detail.shortId}</span></div>
                  <div><span style={{ color: '#94a3b8' }}>镜像：</span>
                    <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{detail.image}</span></div>
                  <div><span style={{ color: '#94a3b8' }}>状态：</span>
                    <span className={`dm-status ${containerStateClass(detail.status.toLowerCase())}`} style={{ display: 'inline-flex' }}>
                      <span className="dm-status-dot" />{detail.status}
                    </span></div>
                  <div><span style={{ color: '#94a3b8' }}>创建时间：</span>
                    <span>{detail.created ? new Date(detail.created).toLocaleString('zh-CN') : '—'}</span></div>
                  <div><span style={{ color: '#94a3b8' }}>启动时间：</span>
                    <span>{detail.running && detail.startedAt ? new Date(detail.startedAt).toLocaleString('zh-CN') : (detail.startedAt ? new Date(detail.startedAt).toLocaleString('zh-CN') : '—')}</span></div>
                  {!detail.running && detail.finishedAt && detail.finishedAt !== '0001-01-01T00:00:00Z' && (
                    <div><span style={{ color: '#94a3b8' }}>停止时间：</span>
                      <span>{new Date(detail.finishedAt).toLocaleString('zh-CN')}</span></div>
                  )}
                  {!detail.running && (
                    <div><span style={{ color: '#94a3b8' }}>退出码：</span>
                      <span style={{ color: detail.exitCode !== 0 ? '#ef4444' : '#22c55e' }}>{detail.exitCode}</span></div>
                  )}
                  <div><span style={{ color: '#94a3b8' }}>重启策略：</span>
                    <span>{detail.restartPolicy || '不重启'}</span></div>
                  <div><span style={{ color: '#94a3b8' }}>主机名：</span>
                    <span style={{ fontFamily: 'monospace' }}>{detail.hostname || '—'}</span></div>
                  {detail.workingDir && (
                    <div><span style={{ color: '#94a3b8' }}>工作目录：</span>
                      <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{detail.workingDir}</span></div>
                  )}
                  {detail.user && (
                    <div><span style={{ color: '#94a3b8' }}>运行用户：</span>
                      <span style={{ fontFamily: 'monospace' }}>{detail.user}</span></div>
                  )}
                </div>
                {detail.cmd.length > 0 && (
                  <div style={{ marginTop: 8, fontSize: 12 }}>
                    <span style={{ color: '#94a3b8' }}>启动命令：</span>
                    <code style={{ background: '#f1f5f9', padding: '2px 8px', borderRadius: 4, fontSize: 12 }}>
                      {detail.cmd.join(' ')}
                    </code>
                  </div>
                )}
              </div>

              {/* ── SSH 连接（如果有映射 22 端口） ── */}
              {detail.sshHostPort && (
                <div className="dm-perm-section">
                  <div className="dm-perm-section-title"><Terminal size={13} /> SSH 连接</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <code style={{ background: '#0f172a', color: '#7dd3fc', padding: '6px 12px', borderRadius: 6, fontSize: 13, flex: 1 }}>
                      ssh -p {detail.sshHostPort} root@{detail.serverHost}
                    </code>
                    <button
                      className="btn btn-primary"
                      style={{ flexShrink: 0 }}
                      onClick={() => copyText(
                        `ssh -p ${detail.sshHostPort} root@${detail.serverHost}`,
                        '已复制 SSH 连接命令'
                      )}
                    >
                      <Copy size={13} /> 复制
                    </button>
                  </div>
                  <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
                    宿主机端口 {detail.sshHostPort} → 容器 22/tcp
                  </div>
                </div>
              )}

              {/* ── 端口映射 ── */}
              {detail.ports.length > 0 && (
                <div className="dm-perm-section">
                  <div className="dm-perm-section-title"><Globe size={13} /> 端口映射</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                    {detail.ports.map((p, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, background: '#f8fafc', borderRadius: 6, padding: '5px 10px' }}>
                        <span style={{ color: '#94a3b8', minWidth: 80 }}>
                          {p.hostIp ? `${p.hostIp}:${p.hostPort}` : p.hostPort || '未绑定'}
                        </span>
                        <span style={{ color: '#64748b' }}>→</span>
                        <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{p.containerPort}</span>
                        {p.hostPort && (
                          <button
                            className="dm-btn-icon"
                            style={{ marginLeft: 'auto' }}
                            title="复制映射信息"
                            onClick={() => copyText(
                              `${p.hostIp || '0.0.0.0'}:${p.hostPort}->${p.containerPort}`,
                              '已复制端口映射'
                            )}
                          >
                            <Copy size={11} />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ── 卷挂载 ── */}
              {detail.mounts.length > 0 && (
                <div className="dm-perm-section">
                  <div className="dm-perm-section-title"><HardDrive size={13} /> 卷挂载</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                    {detail.mounts.map((m, i) => (
                      <div key={i} style={{ fontSize: 12, background: '#f8fafc', borderRadius: 6, padding: '6px 10px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{
                            background: m.type === 'volume' ? '#dbeafe' : '#f0fdf4',
                            color: m.type === 'volume' ? '#1d4ed8' : '#166534',
                            padding: '1px 6px', borderRadius: 4, fontSize: 11, flexShrink: 0
                          }}>{m.type}</span>
                          <span style={{ fontFamily: 'monospace', color: '#1e293b', fontWeight: 600 }}>{m.destination}</span>
                          <span style={{ color: m.rw ? '#22c55e' : '#ef4444', fontSize: 11, marginLeft: 'auto' }}>
                            {m.rw ? 'rw' : 'ro'}
                          </span>
                        </div>
                        {(m.source || m.name) && (
                          <div style={{ color: '#94a3b8', marginTop: 3, fontFamily: 'monospace', fontSize: 11 }}>
                            {m.name ? `卷: ${m.name}` : `主机: ${m.source}`}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ── 网络 ── */}
              {detail.networks.length > 0 && (
                <div className="dm-perm-section">
                  <div className="dm-perm-section-title"><Network size={13} /> 网络</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                    {detail.networks.map((n, i) => (
                      <div key={i} style={{ fontSize: 12, background: '#f8fafc', borderRadius: 6, padding: '6px 10px' }}>
                        <div style={{ fontWeight: 600, color: '#1e293b', marginBottom: 2 }}>{n.name}</div>
                        <div style={{ display: 'flex', gap: 16, color: '#526071' }}>
                          {n.ipAddress && <span>IP: <code style={{ fontSize: 11 }}>{n.ipAddress}</code></span>}
                          {n.gateway && <span>网关: <code style={{ fontSize: 11 }}>{n.gateway}</code></span>}
                          {n.macAddress && <span>MAC: <code style={{ fontSize: 11 }}>{n.macAddress}</code></span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ── 环境变量 ── */}
              {detail.envs.length > 0 && (
                <div className="dm-perm-section">
                  <div className="dm-perm-section-title" style={{ justifyContent: 'space-between' }}>
                    <span><Settings size={13} /> 环境变量 ({detail.envs.length})</span>
                    <button className="dm-btn-icon" title="复制全部环境变量"
                      onClick={() => copyText(detail.envs.join('\n'), `已复制 ${detail.envs.length} 条环境变量`)}>
                      <Copy size={12} />
                    </button>
                  </div>
                  <div style={{ maxHeight: 180, overflowY: 'auto', marginTop: 4 }}>
                    {detail.envs.map((e, i) => {
                      const eq = e.indexOf('=');
                      const key = eq >= 0 ? e.slice(0, eq) : e;
                      const val = eq >= 0 ? e.slice(eq + 1) : '';
                      return (
                        <div key={i} style={{ display: 'flex', fontSize: 12, padding: '3px 0', borderBottom: '1px solid #f1f5f9' }}>
                          <span style={{ fontFamily: 'monospace', color: '#7c3aed', minWidth: '35%', fontWeight: 600 }}>{key}</span>
                          <span style={{ fontFamily: 'monospace', color: '#526071', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>={val}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

            </div>
          )}
        </Modal>
      )}

      {/* 日志弹窗 */}
      {logs && (
        <Modal title={`容器日志 — ${logs.name}`} onClose={() => setLogs(null)} wide
          foot={<button className="btn" onClick={() => setLogs(null)}>关闭</button>}>
          {logsLoading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '24px 0', justifyContent: 'center', color: '#526071' }}>
              <Spin /> 加载日志中…
            </div>
          ) : (
            <div className="dm-logs-box">{logs.text || '（无日志）'}</div>
          )}
        </Modal>
      )}

      {createMode === 'run' && serverId && (
        <RunCreateModal serverId={serverId} quota={quota} onClose={() => setCreateMode(null)} onSuccess={() => { setCreateMode(null); void load(serverId!); }} />
      )}
      {createMode === 'compose' && serverId && (
        <ComposeCreateModal serverId={serverId} onClose={() => setCreateMode(null)} onSuccess={() => { setCreateMode(null); void load(serverId!); }} />
      )}
      {createMode === 'template' && serverId && (
        <TemplateDeployModal serverId={serverId} onClose={() => setCreateMode(null)} onSuccess={() => { setCreateMode(null); void load(serverId!); }} />
      )}
    </div>
  );
}

function RunCreateModal({ serverId, quota, onClose, onSuccess }: { serverId: string; quota: UserQuota | null; onClose: () => void; onSuccess: () => void }) {
  const [form, setForm] = useState({
    name: '', image: '', command: '', ports: '', volumes: '', envs: '',
    network: '', restart: 'unless-stopped', gpus: '', extra_args: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  const f = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.value }));

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    if (!form.image.trim()) return;
    setLoading(true);
    clearError();
    try {
      await apiPost(`${API}/servers/${serverId}/containers/run`, {
        ...form,
        ports: form.ports.split('\n').map(s => s.trim()).filter(Boolean),
        volumes: form.volumes.split('\n').map(s => s.trim()).filter(Boolean),
        envs: form.envs.split('\n').map(s => s.trim()).filter(Boolean),
      });
      onSuccess();
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal title="docker run 创建容器" onClose={onClose} wide
      foot={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={submit} disabled={loading || !form.image.trim()}>
            {loading ? <Spin /> : <Play size={14} />} 创建
          </button>
        </>
      }>
      {error && <Alert type="error">{error}</Alert>}
      {quota?.pathWhitelist && quota.pathWhitelist.length > 0 && (
        <Alert type="info">挂载路径白名单：{quota.pathWhitelist.join('、')}</Alert>
      )}
      <form onSubmit={submit}>
        <div className="dm-form-grid">
          <Field label="镜像 *"><input value={form.image} onChange={f('image')} placeholder="nginx:latest" required /></Field>
          <Field label="容器名称"><input value={form.name} onChange={f('name')} placeholder="my-nginx（可选）" /></Field>
          <Field label="重启策略">
            <select value={form.restart} onChange={f('restart')}>
              <option value="">不重启</option>
              <option value="always">always</option>
              <option value="unless-stopped">unless-stopped</option>
              <option value="on-failure">on-failure</option>
            </select>
          </Field>
          <Field label="GPU（如：all）"><input value={form.gpus} onChange={f('gpus')} placeholder="all（可选）" /></Field>
          <Field label="网络"><input value={form.network} onChange={f('network')} placeholder="bridge（可选）" /></Field>
          <Field label="启动命令"><input value={form.command} onChange={f('command')} placeholder="可选，覆盖默认 CMD" /></Field>
          <Field label="端口映射（每行一条）" full>
            <textarea className="mono" value={form.ports} onChange={f('ports')} placeholder={"8080:80\n443:443"} style={{ minHeight: 80 }} />
          </Field>
          <Field label="卷挂载（每行一条）" full>
            <textarea className="mono" value={form.volumes} onChange={f('volumes')} placeholder={"/data/myapp:/app/data"} style={{ minHeight: 80 }} />
          </Field>
          <Field label="环境变量（每行一条 KEY=VAL）" full>
            <textarea className="mono" value={form.envs} onChange={f('envs')} placeholder={"ENV=prod\nDEBUG=0"} style={{ minHeight: 80 }} />
          </Field>
          <Field label="额外参数" full>
            <input value={form.extra_args} onChange={f('extra_args')} placeholder="--memory=2g --cpus=2（可选）" />
          </Field>
        </div>
      </form>
    </Modal>
  );
}

function ComposeCreateModal({ serverId, onClose, onSuccess }: { serverId: string; onClose: () => void; onSuccess: () => void }) {
  const [yaml, setYaml] = useState('');
  const [project, setProject] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  async function submit() {
    if (!yaml.trim()) return;
    setLoading(true);
    clearError();
    try {
      await apiPost(`${API}/servers/${serverId}/containers/compose`, { yamlContent: yaml, projectName: project });
      onSuccess();
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal title="docker compose 部署" onClose={onClose} wide
      foot={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={submit} disabled={loading || !yaml.trim()}>
            {loading ? <Spin /> : <Layers size={14} />} 部署
          </button>
        </>
      }>
      {error && <Alert type="error">{error}</Alert>}
      <div className="dm-form-grid">
        <Field label="项目名称（可选）">
          <input value={project} onChange={(e) => setProject(e.target.value)} placeholder="自动生成" />
        </Field>
      </div>
      <Field label="docker-compose.yml 内容" full>
        <textarea className="mono" value={yaml} onChange={(e) => setYaml(e.target.value)}
          placeholder={"services:\n  web:\n    image: nginx:latest\n    ports:\n      - \"8080:80\""} style={{ minHeight: 280 }} />
      </Field>
    </Modal>
  );
}

function TemplateDeployModal({ serverId, onClose, onSuccess }: { serverId: string; onClose: () => void; onSuccess: () => void }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState<TemplateDetail | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  useEffect(() => {
    setLoading(true);
    apiGet<{ templates: Template[] }>(`${API}/templates`)
      .then((r) => setTemplates(r.templates))
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  async function selectTemplate(id: string) {
    clearError();
    try {
      const r = await apiGet<{ template: TemplateDetail }>(`${API}/templates/${id}`);
      setSelected(r.template);
      // 初始化 overrides 从 config 中提取可覆盖字段
      const initOverrides: Record<string, string> = {};
      if (r.template.config && typeof r.template.config === 'object') {
        for (const [k, v] of Object.entries(r.template.config)) {
          if (typeof v === 'string' || typeof v === 'number') {
            initOverrides[k] = String(v);
          }
        }
      }
      setOverrides(initOverrides);
    } catch (e) {
      setError(e);
    }
  }

  async function deploy() {
    if (!selected) return;
    setDeploying(true);
    clearError();
    try {
      await apiPost(`${API}/servers/${serverId}/containers/from-template`, {
        templateId: selected.id,
        overrides,
      });
      onSuccess();
    } catch (e) {
      setError(e);
    } finally {
      setDeploying(false);
    }
  }

  return (
    <Modal title="从模板创建容器" onClose={onClose} wide
      foot={
        <>
          {selected && <button className="btn" onClick={() => setSelected(null)}>← 返回列表</button>}
          <button className="btn" onClick={onClose}>取消</button>
          {selected && (
            <button className="btn btn-primary" onClick={deploy} disabled={deploying}>
              {deploying ? <Spin /> : <Play size={14} />} 部署
            </button>
          )}
        </>
      }>
      {error && <Alert type="error">{error}</Alert>}

      {!selected ? (
        loading ? <div className="dm-empty"><Spin /> 加载中…</div> :
        templates.length === 0 ? <div className="dm-empty"><ClipboardList size={32} /> 暂无模板</div> :
        <div style={{ display: 'grid', gap: 10 }}>
          {templates.map((t) => (
            <button key={t.id} className="dm-card" style={{ cursor: 'pointer', textAlign: 'left' }} onClick={() => selectTemplate(t.id)}>
              <div className="dm-card-header">
                <span className="dm-card-title">{t.name}</span>
                <span className="dm-category-tag">{t.category}</span>
              </div>
              <span style={{ color: '#526071', fontSize: 13 }}>{t.description}</span>
            </button>
          ))}
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
              <strong style={{ fontSize: 16 }}>{selected.name}</strong>
              <span className="dm-category-tag">{selected.category}</span>
            </div>
            {selected.docContent && (
              <div className="dm-md-preview" dangerouslySetInnerHTML={{ __html: renderMarkdown(selected.docContent) }} />
            )}
          </div>
          {Object.keys(overrides).length > 0 && (
            <div>
              <div style={{ fontWeight: 600, fontSize: 13, color: '#526071', marginBottom: 8 }}>参数配置</div>
              <div className="dm-form-grid">
                {Object.entries(overrides).map(([k, v]) => (
                  <Field key={k} label={k}>
                    <input value={v} onChange={(e) => setOverrides((p) => ({ ...p, [k]: e.target.value }))} />
                  </Field>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

// ============================================================
// Volumes Panel
// ============================================================

function VolumesPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  const [serverId, setServerId] = useState<string | null>(servers[0]?.id ?? null);
  const [volumes, setVolumes] = useState<DockerVolume[]>([]);
  const [quota, setQuota] = useState<{ volumeTotalGb: number | null; volumeUsedGb: number | null }>({ volumeTotalGb: null, volumeUsedGb: null });
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newSizeGb, setNewSizeGb] = useState('0');
  const [creating, setCreating] = useState(false);
  // 卷复制状态
  const [copyTarget, setCopyTarget] = useState<DockerVolume | null>(null);
  const [copyDstServerId, setCopyDstServerId] = useState<string>('');
  const [copyDstName, setCopyDstName] = useState('');
  const [copying, setCopying] = useState(false);
  const [copyResult, setCopyResult] = useState<string>('');
  // 卷详情状态
  const [detailTarget, setDetailTarget] = useState<DockerVolume | null>(null);
  const [detail, setDetail] = useState<VolumeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const load = useCallback(async (sid: string) => {
    setLoading(true);
    clearError();
    try {
      const r = await apiGet<{ volumes: DockerVolume[]; quota: { volumeTotalGb: number | null; volumeUsedGb: number | null } }>(`${API}/servers/${sid}/volumes`);
      setVolumes(r.volumes);
      setQuota(r.quota);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { if (serverId) void load(serverId); }, [serverId, load]);

  async function doCreate() {
    if (!serverId || !newName.trim()) return;
    setCreating(true);
    clearError();
    try {
      await apiPost(`${API}/servers/${serverId}/volumes`, { name: newName.trim(), sizeGb: parseFloat(newSizeGb) || 0 });
      setShowCreate(false);
      setNewName('');
      setNewSizeGb('0');
      void load(serverId);
    } catch (e) {
      setError(e);
    } finally {
      setCreating(false);
    }
  }

  async function doDelete(name: string) {
    if (!serverId || !confirm(`确定删除卷 ${name}？此操作不可恢复！`)) return;
    clearError();
    try {
      await apiDelete(`${API}/servers/${serverId}/volumes/${encodeURIComponent(name)}`);
      void load(serverId);
    } catch (e) {
      setError(e);
    }
  }

  async function openDetail(vol: DockerVolume) {
    if (!serverId) return;
    setDetailTarget(vol);
    setDetail(null);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const r = await apiGet<VolumeDetail>(`${API}/servers/${serverId}/volumes/${encodeURIComponent(vol.name)}/detail`);
      setDetail(r);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : String(e));
    } finally {
      setDetailLoading(false);
    }
  }

  function closeDetail() {
    setDetailTarget(null);
    setDetail(null);
    setDetailError(null);
  }

  function openCopy(vol: DockerVolume) {
    setCopyTarget(vol);
    setCopyDstServerId(serverId ?? servers[0]?.id ?? '');
    setCopyDstName(`${vol.name}-copy`);
    setCopyResult('');
    clearError();
  }

  async function doCopy() {
    if (!serverId || !copyTarget || !copyDstServerId || !copyDstName.trim()) return;
    setCopying(true);
    setCopyResult('');
    clearError();
    try {
      const r = await apiPost<{
        success: boolean; transferredBytes: number; dstVolumeName: string; dstServerId: string;
      }>(`${API}/volumes/copy`, {
        srcServerId: serverId,
        srcVolumeName: copyTarget.name,
        dstServerId: copyDstServerId,
        dstVolumeName: copyDstName.trim(),
      });
      const mb = (r.transferredBytes / 1024 / 1024).toFixed(1);
      setCopyResult(`✓ 复制完成！传输 ${mb} MB，目标卷：${r.dstVolumeName}`);
      // 如果目标是当前服务器则刷新
      if (copyDstServerId === serverId) void load(serverId);
    } catch (e) {
      setError(e);
    } finally {
      setCopying(false);
    }
  }

  const usedPct = quota.volumeTotalGb && quota.volumeUsedGb != null
    ? Math.min(100, (quota.volumeUsedGb / quota.volumeTotalGb) * 100)
    : null;

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <ServerSelector servers={servers} selected={serverId} onSelect={(id) => { setServerId(id); setVolumes([]); }} />
      {error && <Alert type="error">{error}</Alert>}

      {quota.volumeTotalGb != null && quota.volumeTotalGb > 0 && (
        <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 8, padding: '12px 16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 8 }}>
            <span>卷空间配额</span>
            <span style={{ color: '#526071' }}>已用 {quota.volumeUsedGb?.toFixed(2)} GB / {quota.volumeTotalGb} GB</span>
          </div>
          <div className="dm-quota-track">
            <div className={`dm-quota-fill${usedPct && usedPct > 90 ? ' danger' : usedPct && usedPct > 70 ? ' warn' : ''}`}
              style={{ width: `${usedPct ?? 0}%` }} />
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}><Plus size={14} /> 创建卷</button>
        <button className="btn" onClick={() => serverId && load(serverId)} disabled={loading}><RefreshCw size={14} /> 刷新</button>
      </div>

      {loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : volumes.length === 0 ? (
        <div className="dm-empty"><HardDrive size={32} /> 暂无卷</div>
      ) : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 1fr 1fr auto' }}>
            <span>卷名称</span><span>驱动</span><span>大小</span><span>所有者</span><span>操作</span>
          </div>
          {volumes.map((v) => (
            <div key={v.name} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1fr 1fr 1fr auto' }}>
              <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{v.name}</span>
              <span style={{ color: '#526071' }}>{v.driver}</span>
              <span style={{ color: '#526071' }}>{v.sizeGb != null ? `${v.sizeGb} GB` : '—'}</span>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>
                {v.platformManaged ? (v.ownerUserId === me.id ? '本人' : (v.ownerUserId ?? '未知')) : '平台外'}
              </span>
              <span style={{ display: 'flex', gap: 4 }}>
                <button className="dm-btn-icon" title="卷详情" onClick={() => void openDetail(v)}>
                  <Info size={13} />
                </button>
                <button className="dm-btn-icon" title="复制卷（本地 / 跨服务器）" onClick={() => openCopy(v)}>
                  <Copy size={13} />
                </button>
                {(v.ownerUserId === me.id || me.role === 'admin') && (
                  <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(v.name)}>
                    <Trash2 size={13} />
                  </button>
                )}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 创建卷弹窗 */}
      {showCreate && (
        <Modal title="创建卷" onClose={() => setShowCreate(false)}
          foot={
            <>
              <button className="btn" onClick={() => setShowCreate(false)}>取消</button>
              <button className="btn btn-primary" onClick={doCreate} disabled={creating || !newName.trim()}>
                {creating ? <Spin /> : <Plus size={14} />} 创建
              </button>
            </>
          }>
          {error && <Alert type="error">{error}</Alert>}
          <div className="dm-form-grid">
            <Field label="卷名称"><input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="my-data-volume" /></Field>
            <Field label="预估大小 (GB)">
              <input type="number" min="0" step="0.5" value={newSizeGb} onChange={(e) => setNewSizeGb(e.target.value)} />
            </Field>
          </div>
          {quota.volumeTotalGb != null && quota.volumeTotalGb > 0 && (
            <Alert type="info">剩余配额：{((quota.volumeTotalGb ?? 0) - (quota.volumeUsedGb ?? 0)).toFixed(2)} GB</Alert>
          )}
        </Modal>
      )}

      {/* 卷详情弹窗 */}
      {detailTarget && (
        <Modal
          title={`卷详情 — ${detailTarget.name}`}
          onClose={closeDetail}
          foot={<button className="btn btn-primary" onClick={closeDetail}>关闭</button>}
        >
          {detailLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#526071', padding: '24px 0', justifyContent: 'center' }}>
              <Spin /> 加载详情中…
            </div>
          )}
          {detailError && <Alert type="error">{detailError}</Alert>}
          {detail && !detailLoading && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              {/* 基本信息 */}
              <div className="dm-perm-section">
                <div className="dm-perm-section-title"><HardDrive size={13} /> 基本信息</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px', fontSize: 13 }}>
                  <div><span style={{ color: '#94a3b8' }}>卷名称：</span><span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{detail.name}</span></div>
                  <div><span style={{ color: '#94a3b8' }}>大小：</span><span>{detail.sizeGb != null ? `${detail.sizeGb} GB` : '未知'}</span></div>
                  <div><span style={{ color: '#94a3b8' }}>创建时间：</span><span>{detail.createdAt ? new Date(detail.createdAt).toLocaleString('zh-CN') : '未知'}</span></div>
                  <div><span style={{ color: '#94a3b8' }}>平台管理：</span><span>{detail.platformManaged ? '是' : '否（平台外创建）'}</span></div>
                </div>
              </div>

              {/* 权限角色 */}
              <div className="dm-perm-section">
                <div className="dm-perm-section-title"><Shield size={13} /> 权限角色</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 13 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ color: '#94a3b8', minWidth: 60 }}>创建者：</span>
                    {detail.roles.creator ? (
                      <span className="dm-role-tag creator">
                        {detail.roles.creator.displayName}
                        <small style={{ color: '#64748b', marginLeft: 4 }}>@{detail.roles.creator.username}</small>
                      </span>
                    ) : <span style={{ color: '#94a3b8' }}>—</span>}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ color: '#94a3b8', minWidth: 60, paddingTop: 2 }}>所有者：</span>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {detail.roles.owners.length > 0 ? detail.roles.owners.map((o) => (
                        <span key={o.userId} className="dm-role-tag owner">
                          {o.displayName}
                          <small style={{ color: '#64748b', marginLeft: 4 }}>@{o.username}</small>
                        </span>
                      )) : <span style={{ color: '#94a3b8' }}>—</span>}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ color: '#94a3b8', minWidth: 60, paddingTop: 2 }}>查看者：</span>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {detail.roles.viewers.length > 0 ? detail.roles.viewers.map((v) => (
                        <span key={v.userId} className="dm-role-tag viewer">
                          {v.displayName}
                          <small style={{ color: '#64748b', marginLeft: 4 }}>@{v.username}</small>
                        </span>
                      )) : <span style={{ color: '#94a3b8' }}>—</span>}
                    </div>
                  </div>
                </div>
              </div>

              {/* 挂载容器 */}
              <div className="dm-perm-section">
                <div className="dm-perm-section-title">
                  <Box size={13} /> 挂载容器
                  {detail.hiddenContainerCount > 0 && (
                    <span style={{ marginLeft: 8, fontSize: 11, color: '#94a3b8', fontWeight: 400 }}>
                      （另有 {detail.hiddenContainerCount} 个无权查看的容器）
                    </span>
                  )}
                </div>
                {detail.mountedContainers.length === 0 && detail.hiddenContainerCount === 0 ? (
                  <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 4 }}>无容器挂载此卷</div>
                ) : detail.mountedContainers.length === 0 ? (
                  <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 4 }}>您无权查看任何挂载容器</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                    {detail.mountedContainers.map((c) => (
                      <div key={c.id} style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        background: '#f8fafc', borderRadius: 6, padding: '6px 10px', fontSize: 12
                      }}>
                        <span style={{
                          width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                          background: c.state === 'running' ? '#22c55e' : '#94a3b8'
                        }} />
                        <span style={{ fontFamily: 'monospace', fontWeight: 600, flex: '0 0 auto' }}>{c.name}</span>
                        <span style={{ color: '#94a3b8', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.image}</span>
                        <span style={{
                          fontSize: 11, padding: '1px 6px', borderRadius: 4,
                          background: c.state === 'running' ? '#dcfce7' : '#f1f5f9',
                          color: c.state === 'running' ? '#166534' : '#526071'
                        }}>{c.status}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </Modal>
      )}

      {/* 卷复制弹窗 */}
      {copyTarget && (
        <Modal
          title={`复制卷 — ${copyTarget.name}`}
          onClose={() => { setCopyTarget(null); setCopyResult(''); }}
          foot={
            copyResult ? (
              <button className="btn btn-primary" onClick={() => { setCopyTarget(null); setCopyResult(''); }}>关闭</button>
            ) : (
              <>
                <button className="btn" onClick={() => { setCopyTarget(null); setCopyResult(''); }}>取消</button>
                <button
                  className="btn btn-primary"
                  onClick={doCopy}
                  disabled={copying || !copyDstServerId || !copyDstName.trim()
                    || (copyDstServerId === serverId && copyDstName.trim() === copyTarget.name)}
                >
                  {copying ? <Spin /> : <Copy size={14} />}
                  {copying ? ' 复制中（数据流式传输）…' : (copyDstServerId === serverId ? ' 本地复制' : ' 跨服务器复制')}
                </button>
              </>
            )
          }
        >
          {error && <Alert type="error">{error}</Alert>}
          {copyResult ? (
            <Alert type="success">{copyResult}</Alert>
          ) : (
            <>
              {copyDstServerId === serverId ? (
                <Alert type="info">
                  <strong>本地复制</strong>：在同一服务器内新建一个卷并复制数据，通过 <code>tar</code> 管道完成，速度较快。
                </Alert>
              ) : (
                <Alert type="info">
                  <strong>跨服务器复制</strong>：通过 <code>tar | SSH 管道</code> 流式传输，数据不在任何中间节点落盘。
                  复制时间取决于卷数据量和网络速度。
                </Alert>
              )}
              <div className="dm-form-grid">
                <Field label="源服务器">
                  <input value={servers.find((s) => s.id === serverId)?.name ?? serverId ?? ''} disabled />
                </Field>
                <Field label="源卷名称">
                  <input value={copyTarget.name} disabled style={{ fontFamily: 'monospace' }} />
                </Field>
                <Field label="目标服务器">
                  <select value={copyDstServerId} onChange={(e) => setCopyDstServerId(e.target.value)}>
                    {servers.map((s) => (
                      <option key={s.id} value={s.id}>{s.name} ({s.host})</option>
                    ))}
                  </select>
                </Field>
                <Field label="目标卷名称">
                  <input
                    value={copyDstName}
                    onChange={(e) => setCopyDstName(e.target.value)}
                    placeholder={`${copyTarget.name}-copy`}
                    style={{ fontFamily: 'monospace' }}
                  />
                </Field>
              </div>
              {copyDstServerId === serverId && copyDstName.trim() === copyTarget.name && (
                <Alert type="error">目标卷名称不能与源卷名称相同（同一服务器内复制时）</Alert>
              )}
            </>
          )}
        </Modal>
      )}
    </div>
  );
}

// ============================================================
// Templates Panel (User View)
// ============================================================

function TemplatesPanel({ me }: { me: AuthUser }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState<TemplateDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  useEffect(() => {
    setLoading(true);
    apiGet<{ templates: Template[] }>(`${API}/templates`)
      .then((r) => setTemplates(r.templates))
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  async function selectTemplate(id: string) {
    clearError();
    try {
      const r = await apiGet<{ template: TemplateDetail }>(`${API}/templates/${id}`);
      setSelected(r.template);
    } catch (e) {
      setError(e);
    }
  }

  const categories = [...new Set(templates.map((t) => t.category))];

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      {error && <Alert type="error">{error}</Alert>}

      {selected ? (
        <div style={{ display: 'grid', gap: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <button className="btn" onClick={() => setSelected(null)}>← 返回</button>
            <strong style={{ fontSize: 16 }}>{selected.name}</strong>
            <span className="dm-category-tag">{selected.category}</span>
          </div>
          {selected.description && <p style={{ color: '#526071', margin: 0 }}>{selected.description}</p>}
          {selected.docContent ? (
            <div className="dm-md-preview" dangerouslySetInnerHTML={{ __html: renderMarkdown(selected.docContent) }} />
          ) : (
            <Alert type="info">该模板暂无说明文档</Alert>
          )}
        </div>
      ) : loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : templates.length === 0 ? (
        <div className="dm-empty"><ClipboardList size={32} /> 暂无可用模板</div>
      ) : (
        categories.map((cat) => (
          <div key={cat} style={{ display: 'grid', gap: 10 }}>
            <div style={{ fontWeight: 700, color: '#526071', fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              {cat}
            </div>
            <div className="dm-card-grid">
              {templates.filter((t) => t.category === cat).map((t) => (
                <div key={t.id} className="dm-card" style={{ cursor: 'pointer' }} onClick={() => selectTemplate(t.id)}>
                  <div className="dm-card-header">
                    <span className="dm-card-title">{t.name}</span>
                    {t.hasDoc && <FileText size={14} style={{ color: '#94a3b8', flexShrink: 0 }} />}
                  </div>
                  {t.description && <span style={{ color: '#64748b', fontSize: 13 }}>{t.description}</span>}
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ============================================================
// My Resources Panel（非管理员：查看并管理自己 owner 的资源 viewer）
// ============================================================

type BasicUser = { id: string; username: string; displayName: string };

function MyResourcesPanel({ me }: { me: AuthUser }) {
  const [resources, setResources] = useState<MyOwnedResource[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [success, setSuccess] = useState<string | null>(null);
  const [allUsers, setAllUsers] = useState<BasicUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);

  // 查看者编辑弹窗状态
  const [editTarget, setEditTarget] = useState<MyOwnedResource | null>(null);
  const [editViewerIds, setEditViewerIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError, clearEditError] = useErrorMsg();

  // 资源类型过滤
  const [filterType, setFilterType] = useState<'all' | 'container' | 'image' | 'volume'>('all');

  const load = useCallback(async () => {
    setLoading(true);
    clearError();
    setSuccess(null);
    try {
      const r = await apiGet<{ resources: MyOwnedResource[] }>(`${API}/my-owned-resources`);
      setResources(r.resources);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  const loadUsers = useCallback(async () => {
    setUsersLoading(true);
    try {
      const r = await apiGet<{ users: BasicUser[] }>('/api/auth/users-basic');
      setAllUsers(r.users);
    } catch {
      // 加载失败不影响主功能
    } finally {
      setUsersLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadUsers();
  }, [load, loadUsers]);

  function openEdit(res: MyOwnedResource) {
    setEditTarget(res);
    setEditViewerIds(res.viewerUserIds);
    clearEditError();
  }

  async function saveViewers() {
    if (!editTarget) return;
    setSaving(true);
    clearEditError();
    try {
      await apiPut(`${API}/servers/${editTarget.serverId}/resource-viewers`, {
        resourceType: editTarget.resourceType,
        resourceRef: editTarget.resourceRef,
        viewerUserIds: editViewerIds,
      });
      setSuccess(`「${editTarget.resourceRef}」的查看者已更新`);
      setEditTarget(null);
      void load();
    } catch (e) {
      setEditError(e);
    } finally {
      setSaving(false);
    }
  }

  const resourceTypeLabel: Record<string, string> = {
    container: '容器',
    image: '镜像',
    volume: '卷',
  };
  const resourceTypeIcon: Record<string, ReactNode> = {
    container: <Box size={13} />,
    image: <Image size={13} />,
    volume: <Database size={13} />,
  };

  const filtered = resources.filter((r) => filterType === 'all' || r.resourceType === filterType);

  // 非管理员可选择的 viewer（排除自己和已是 owner 的用户）
  // 我们不知道哪些用户是 owner，但可以排除当前用户自己
  const selectableViewers = allUsers.filter((u) => u.id !== me.id);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: '#526071', fontWeight: 600 }}>我管理的资源</span>
        <span style={{ fontSize: 12, color: '#94a3b8' }}>（您作为所有者的资源，可以为其分配查看者）</span>
        <button className="btn" style={{ marginLeft: 'auto', padding: '4px 12px' }} onClick={() => void load()} disabled={loading}>
          {loading ? <Spin /> : <RefreshCw size={13} />} 刷新
        </button>
      </div>

      {/* 类型筛选 */}
      <div style={{ display: 'flex', gap: 6 }}>
        {(['all', 'container', 'image', 'volume'] as const).map((t) => (
          <button
            key={t}
            className={`dm-server-chip${filterType === t ? ' active' : ''}`}
            onClick={() => setFilterType(t)}
          >
            {t === 'all' ? '全部' : resourceTypeLabel[t]}
          </button>
        ))}
      </div>

      {error && <Alert type="error">{error}</Alert>}
      {success && <Alert type="success">{success}</Alert>}

      {loading ? (
        <SkeletonRows cols={['2fr', '1fr', '1.5fr', '1.5fr', 'auto']} />
      ) : filtered.length === 0 ? (
        <div className="dm-empty">
          <Shield size={32} />
          {resources.length === 0 ? '您目前没有任何作为所有者的资源' : '当前筛选条件下无资源'}
        </div>
      ) : (
        <div className="dm-table">
          {/* 表头 */}
          <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 2fr 2fr auto' }}>
            <span>资源名称</span>
            <span>类型</span>
            <span>所属服务器</span>
            <span>查看者</span>
            <span>操作</span>
          </div>

          {filtered.map((res) => {
            return (
              <div key={`${res.serverId}-${res.resourceType}-${res.resourceRef}`}
                className="dm-table-row"
                style={{ gridTemplateColumns: '2fr 1fr 2fr 2fr auto' }}>
                <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {resourceTypeIcon[res.resourceType]} {res.resourceRef}
                </span>
                <span>
                  <span className="dm-role-tag" style={{ background: res.resourceType === 'container' ? '#dbeafe' : res.resourceType === 'image' ? '#fce7f3' : '#d1fae5', color: '#1e293b' }}>
                    {resourceTypeLabel[res.resourceType]}
                  </span>
                </span>
                <span style={{ color: '#526071', fontSize: 13 }}>{res.serverName}</span>
                <span>
                  {res.viewers.length === 0 ? (
                    <span style={{ color: '#94a3b8', fontSize: 12 }}>暂无查看者</span>
                  ) : (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {res.viewers.map((v) => (
                        <span key={v.userId} className="dm-role-tag viewer">
                          {v.displayName} <small style={{ color: '#64748b' }}>@{v.username}</small>
                        </span>
                      ))}
                    </div>
                  )}
                </span>
                <span>
                  <button className="dm-btn-icon" title="管理查看者" onClick={() => openEdit(res)}>
                    <Users size={13} />
                  </button>
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* 查看者编辑弹窗 */}
      {editTarget && (
        <Modal
          title={`管理查看者 — ${editTarget.resourceRef}`}
          onClose={() => setEditTarget(null)}
          foot={
            <>
              <button className="btn" onClick={() => setEditTarget(null)}>取消</button>
              <button className="btn btn-primary" onClick={saveViewers} disabled={saving}>
                {saving ? <Spin /> : <CheckCircle size={14} />} 保存
              </button>
            </>
          }
        >
          {editError && <Alert type="error">{editError}</Alert>}

          <div className="dm-perm-section">
            <div className="dm-perm-section-title" style={{ marginBottom: 4 }}>
              <Shield size={13} /> 资源信息
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 13, color: '#526071' }}>
              <span>类型：{resourceTypeLabel[editTarget.resourceType]}</span>
              <span>服务器：{editTarget.serverName}</span>
              {editTarget.creatorUserId && (
                <span>创建者 ID：{editTarget.creatorUserId}</span>
              )}
            </div>
          </div>

          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <FileText size={13} /> 查看者（勾选后可查看该资源）
            </div>
            {usersLoading ? (
              <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 6 }}><Spin /> 加载用户列表…</div>
            ) : (
              <div className="dm-roles-checklist">
                {selectableViewers.map((u) => (
                  <label key={u.id} className="dm-form-check">
                    <input
                      type="checkbox"
                      checked={editViewerIds.includes(u.id)}
                      onChange={(e) => {
                        setEditViewerIds((prev) =>
                          e.target.checked ? [...prev, u.id] : prev.filter((id) => id !== u.id)
                        );
                      }}
                    />
                    <span>{u.displayName}</span>
                    <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                  </label>
                ))}
                {selectableViewers.length === 0 && (
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无其他可选用户</div>
                )}
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}


// ============================================================
// Admin: Server Management Panel
// ============================================================

function AdminServersPanel({ onRefresh }: { onRefresh: () => void }) {
  const [servers, setServers] = useState<DmServer[]>([]);
  const [users, setUsers] = useState<ServerPermEntry[]>([]); // 全用户列表，用于资源分配
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [showAdd, setShowAdd] = useState(false);
  const [addForm, setAddForm] = useState({ name: '', host: '', port: '22', sshUsername: '', sshPassword: '' });
  const [adding, setAdding] = useState(false);
  const [addMsg, setAddMsg] = useState('');
  // 权限面板
  const [permServer, setPermServer] = useState<DmServer | null>(null);
  const [perms, setPerms] = useState<ServerPermEntry[]>([]);
  const [permsLoading, setPermsLoading] = useState(false);
  // 细粒度权限编辑弹窗
  const [permsEditTarget, setPermsEditTarget] = useState<ServerPermEntry | null>(null);
  const [permsForm, setPermsForm] = useState<UserPerms>(DEFAULT_PERMS);
  const [permsPathStr, setPermsPathStr] = useState('');
  const [savingPerms, setSavingPerms] = useState(false);
  // 资源多角色分配面板
  const [resourceServer, setResourceServer] = useState<DmServer | null>(null);
  const [resources, setResources] = useState<ServerResources | null>(null);
  const [resourcesLoading, setResourcesLoading] = useState(false);
  const [resourceTab, setResourceTab] = useState<'containers' | 'images' | 'volumes'>('containers');
  const [assignError, setAssignError, clearAssignError] = useErrorMsg();
  const [assignSuccess, setAssignSuccess] = useState<string | null>(null);
  // 角色分配弹窗状态
  type AssignRolesTarget = { resourceType: string; resourceRef: string; label: string; currentRoles: ResourceRoles };
  const [assignRolesTarget, setAssignRolesTarget] = useState<AssignRolesTarget | null>(null);
  const [assignOwnerIds, setAssignOwnerIds] = useState<string[]>([]);
  const [assignViewerIds, setAssignViewerIds] = useState<string[]>([]);
  const [savingRoles, setSavingRoles] = useState(false);

  // 加载服务器资源列表
  const loadResources = useCallback(async (serverId: string) => {
    setResourcesLoading(true);
    clearAssignError();
    try {
      const r = await apiGet<ServerResources>(`${API}/servers/${serverId}/resources`);
      setResources(r);
    } catch (e) {
      setAssignError(e);
    } finally {
      setResourcesLoading(false);
    }
  }, [clearAssignError, setAssignError]);

  async function openResourcePanel(s: DmServer) {
    // 重置所有面板状态，tab 归位到 containers
    setResourceServer(s);
    setResources(null);
    setAssignSuccess(null);
    clearAssignError();
    setResourceTab('containers');
    setResourcesLoading(true);
    // 并行加载用户列表 + 资源列表，减少等待时间
    await Promise.allSettled([
      apiGet<{ permissions: ServerPermEntry[] }>(`${API}/servers/${s.id}/permissions`)
        .then((r) => setUsers(r.permissions))
        .catch(() => { /* 用户列表加载失败不影响主功能 */ }),
      apiGet<ServerResources>(`${API}/servers/${s.id}/resources`)
        .then((r) => setResources(r))
        .catch((e) => setAssignError(e)),
    ]);
    setResourcesLoading(false);
  }

  // 打开角色分配弹窗
  function openAssignRoles(resourceType: string, resourceRef: string, label: string, currentRoles: ResourceRoles) {
    setAssignRolesTarget({ resourceType, resourceRef, label, currentRoles });
    setAssignOwnerIds(currentRoles.ownerUserIds ?? []);
    setAssignViewerIds(currentRoles.viewerUserIds ?? []);
    clearAssignError();
  }

  // 提交多角色分配
  async function doAssignRoles() {
    if (!resourceServer || !assignRolesTarget) return;
    setSavingRoles(true);
    clearAssignError();
    setAssignSuccess(null);
    try {
      await apiPut(`${API}/servers/${resourceServer.id}/resource-roles`, {
        resourceType: assignRolesTarget.resourceType,
        resourceRef: assignRolesTarget.resourceRef,
        ownerUserIds: assignOwnerIds,
        viewerUserIds: assignViewerIds,
      });
      setAssignSuccess(`「${assignRolesTarget.label}」角色分配已更新`);
      setAssignRolesTarget(null);
      void loadResources(resourceServer.id);
    } catch (e) {
      setAssignError(e);
    } finally {
      setSavingRoles(false);
    }
  }

  const load = useCallback(async () => {
    setLoading(true);
    clearError();
    try {
      const r = await apiGet<{ servers: DmServer[] }>(`${API}/servers`);
      setServers(r.servers);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { void load(); }, [load]);

  const af = (k: keyof typeof addForm) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setAddForm((p) => ({ ...p, [k]: e.target.value }));

  async function doAdd() {
    setAdding(true);
    setAddMsg('');
    clearError();
    try {
      await apiPost(`${API}/servers`, { ...addForm, port: parseInt(addForm.port) || 22 });
      setAddMsg('服务器添加成功！');
      setAddForm({ name: '', host: '', port: '22', sshUsername: '', sshPassword: '' });
      void load();
      onRefresh();
    } catch (e) {
      setError(e);
    } finally {
      setAdding(false);
    }
  }

  async function doDelete(id: string, name: string) {
    if (!confirm(`确定删除服务器 ${name}？此操作将同时删除该服务器的权限和配额记录。`)) return;
    clearError();
    try {
      await apiDelete(`${API}/servers/${id}`);
      void load();
      onRefresh();
    } catch (e) {
      setError(e);
    }
  }

  async function openPerms(s: DmServer) {
    setPermServer(s);
    setPermsLoading(true);
    clearError();
    try {
      const r = await apiGet<{ permissions: ServerPermEntry[] }>(`${API}/servers/${s.id}/permissions`);
      setPerms(r.permissions);
    } catch (e) {
      setError(e);
    } finally {
      setPermsLoading(false);
    }
  }

  async function reloadPerms() {
    if (!permServer) return;
    try {
      const r = await apiGet<{ permissions: ServerPermEntry[] }>(`${API}/servers/${permServer.id}/permissions`);
      setPerms(r.permissions);
    } catch (e) {
      setError(e);
    }
  }

  function openPermsEdit(entry: ServerPermEntry) {
    setPermsEditTarget(entry);
    const p = entry.perms ?? DEFAULT_PERMS;
    setPermsForm({ ...DEFAULT_PERMS, ...p });
    setPermsPathStr((p.ctr_path_whitelist ?? []).join('\n'));
    clearError();
  }

  function pf(k: keyof UserPerms) {
    return (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.type === 'checkbox' ? e.target.checked : parseFloat(e.target.value) || 0;
      setPermsForm((prev) => ({ ...prev, [k]: val }));
    };
  }

  async function savePerms() {
    if (!permsEditTarget || !permServer) return;
    setSavingPerms(true);
    clearError();
    try {
      const pathList = permsPathStr.split('\n').map((s) => s.trim()).filter(Boolean);
      await apiPut(`${API}/servers/${permServer.id}/user-perms`, {
        userId: permsEditTarget.userId,
        ...permsForm,
        ctr_path_whitelist: pathList,
      });
      setPermsEditTarget(null);
      await reloadPerms();
    } catch (e) {
      setError(e);
    } finally {
      setSavingPerms(false);
    }
  }

  function applyPreset(preset: 'none' | 'view' | 'use' | 'manage') {
    const none = { ...DEFAULT_PERMS };
    if (preset === 'none') { setPermsForm(none); return; }
    if (preset === 'view') {
      setPermsForm({ ...none, server_visible: true, ctr_view_own: true });
      return;
    }
    if (preset === 'use') {
      setPermsForm({
        ...none,
        server_visible: true,
        img_pull: true,
        ctr_view_own: true,
        ctr_create_run: true, ctr_create_compose: true, ctr_create_template: true,
        ctr_manage_own: true,
        vol_create: true, vol_delete_own: true, vol_copy: true,
        tpl_use: true,
      });
      return;
    }
    if (preset === 'manage') {
      setPermsForm({
        server_visible: true,
        img_pull: true, img_delete: true, img_copy: true,
        ctr_view_own: true, ctr_view_all: true,
        ctr_create_run: true, ctr_create_compose: true, ctr_create_template: true,
        ctr_manage_own: true, ctr_manage_all: true, ctr_path_whitelist: [],
        vol_create: true, vol_delete_own: true, vol_delete_all: true, vol_copy: true, vol_quota_gb: 0,
        tpl_use: true, tpl_create: true, tpl_edit: true,
      });
    }
  }

  return (
    <div style={{ display: 'grid', gap: 20 }}>
      {error && <Alert type="error">{error}</Alert>}

      {/* Add Server Form */}
      <div style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 10, padding: 20 }}>
        <div style={{ fontWeight: 700, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Plus size={16} /> 添加服务器
        </div>
        {addMsg && <Alert type="success">{addMsg}</Alert>}
        <div className="dm-form-grid">
          <Field label="显示名称"><input value={addForm.name} onChange={af('name')} placeholder="实验室服务器A" /></Field>
          <Field label="主机地址"><input value={addForm.host} onChange={af('host')} placeholder="192.168.1.100" /></Field>
          <Field label="SSH 端口"><input type="number" value={addForm.port} onChange={af('port')} /></Field>
          <Field label="SSH 用户名"><input value={addForm.sshUsername} onChange={af('sshUsername')} placeholder="labuser" /></Field>
          <Field label="SSH 密码" full><input type="password" value={addForm.sshPassword} onChange={af('sshPassword')} /></Field>
        </div>
        <div style={{ marginTop: 12 }}>
          <button className="btn btn-primary" onClick={doAdd} disabled={adding || !addForm.host || !addForm.sshUsername || !addForm.sshPassword || !addForm.name}>
            {adding ? <Spin /> : <Plus size={14} />} 连接并添加（自动验证 Docker 权限）
          </button>
        </div>
      </div>

      {/* Server List */}
      {loading ? <div className="dm-empty"><Spin /> 加载中…</div> :
       servers.length === 0 ? <div className="dm-empty"><Server size={32} /> 暂无服务器</div> : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr auto' }}>
            <span>服务器</span><span>地址</span><span>添加时间</span><span>操作</span>
          </div>
          {servers.map((s) => (
            <div key={s.id} className="dm-table-row" style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr auto' }}>
              <span style={{ fontWeight: 600 }}>{s.name}</span>
              <span style={{ color: '#526071', fontFamily: 'monospace', fontSize: 13 }}>{s.host}:{s.port}</span>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>{s.createdAt.slice(0, 10)}</span>
              <span style={{ display: 'flex', gap: 4 }}>
                <button className="dm-btn-icon" title="权限管理" onClick={() => openPerms(s)}><Users size={13} /></button>
                <button className="dm-btn-icon" title="资源分配" onClick={() => openResourcePanel(s)}><Database size={13} /></button>
                <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(s.id, s.name)}><Trash2 size={13} /></button>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Permissions List Modal */}
      {permServer && !permsEditTarget && (
        <Modal title={`权限管理 — ${permServer.name}`} onClose={() => setPermServer(null)} wide
          foot={<button className="btn" onClick={() => setPermServer(null)}>关闭</button>}>
          {error && <Alert type="error">{error}</Alert>}
          {permsLoading ? <div className="dm-empty"><Spin /></div> : (
            <div className="dm-perm-table">
              {perms.map((p) => {
                const lvlColor: Record<string, string> = {
                  manage: '#166534', use: '#1e40af', view: '#92400e', none: '#6b7280'
                };
                const lvlBg: Record<string, string> = {
                  manage: '#dcfce7', use: '#dbeafe', view: '#fef3c7', none: '#f3f4f6'
                };
                return (
                  <div key={p.userId} className="dm-perm-row" style={{ alignItems: 'center' }}>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <strong>{p.displayName}</strong>
                      <small style={{ color: '#94a3b8', marginLeft: 6 }}>@{p.username}</small>
                      {p.role === 'admin' && (
                        <span style={{ marginLeft: 6, fontSize: 11, background: '#fef3c7', color: '#92400e', padding: '1px 5px', borderRadius: 4 }}>系统管理员</span>
                      )}
                    </span>
                    <span style={{
                      fontSize: 11, padding: '2px 8px', borderRadius: 4, fontWeight: 600,
                      color: lvlColor[p.level] ?? '#6b7280',
                      background: lvlBg[p.level] ?? '#f3f4f6',
                    }}>
                      {p.role === 'admin' ? '全满权限' : { manage: '管理', use: '使用', view: '查看', none: '无权限' }[p.level] ?? p.level}
                    </span>
                    <button
                      className="btn"
                      style={{ fontSize: 12 }}
                      disabled={p.role === 'admin'}
                      onClick={() => openPermsEdit(p)}
                    >
                      <Shield size={12} /> 编辑权限
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </Modal>
      )}

      {/* Resource Roles Assignment Modal (大面板) */}
      {resourceServer && !permServer && (
        <Modal
          title={`资源角色管理 — ${resourceServer.name}`}
          onClose={() => { setResourceServer(null); setResources(null); }}
          wide
          foot={
            <button className="btn" onClick={() => { setResourceServer(null); setResources(null); }}>关闭</button>
          }
        >
          {assignError && <Alert type="error">{assignError}</Alert>}
          {assignSuccess && <Alert type="success">{assignSuccess}</Alert>}

          {/* 资源类型标签页 */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 14 }}>
            {(['containers', 'images', 'volumes'] as const).map((tab) => (
              <button
                key={tab}
                className={`btn${resourceTab === tab ? ' btn-primary' : ''}`}
                style={{ fontSize: 12 }}
                onClick={() => setResourceTab(tab)}
              >
                {tab === 'containers' ? <><Box size={12} /> 容器</> : tab === 'images' ? <><Image size={12} /> 镜像</> : <><HardDrive size={12} /> 卷</>}
              </button>
            ))}
            <button
              className="btn"
              style={{ fontSize: 12, marginLeft: 'auto' }}
              onClick={() => resourceServer && loadResources(resourceServer.id)}
              disabled={resourcesLoading}
            >
              <RefreshCw size={12} /> 刷新
            </button>
          </div>

          {/* 初次加载时显示骨架屏；刷新时用遮罩叠加 */}
          {resourcesLoading && !resources ? (
            <div style={{ display: 'grid', gap: 12 }}>
              <div style={{ display: 'flex', gap: 8 }}>
                {[70, 60, 50].map((w, i) => (
                  <div key={i} className="dm-skeleton-cell" style={{ width: w, height: 30, borderRadius: 6 }} />
                ))}
              </div>
              <SkeletonRows cols={['1.5fr', '2fr', '1fr', '1fr', '80px']} rows={6} />
            </div>
          ) : !resources ? (
            <div className="dm-empty"><Server size={24} /> 暂无数据</div>
          ) : resourceTab === 'containers' ? (
            <ResourceLoadingWrapper loading={resourcesLoading}>
            {resources.containers.length === 0 ? (
              <div className="dm-empty"><Box size={24} /> 暂无容器</div>
            ) : (
              <div className="dm-table">
                <div className="dm-table-header" style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr 1.5fr 1.5fr auto' }}>
                  <span>名称</span><span>镜像</span><span>状态</span><span>创建者</span><span>所有者</span><span>操作</span>
                </div>
                {resources.containers.map((ctr) => {
                  const cname = (ctr.Names ?? '').replace(/^\//, '');
                  const ref = (cname || ctr.ID) ?? '';
                  const creator = users.find((u) => u.userId === ctr.creatorUserId);
                  const ownerList = (ctr.ownerUserIds ?? []).map((id) => users.find((u) => u.userId === id)).filter(Boolean) as ServerPermEntry[];
                  return (
                    <div key={ref} className="dm-table-row" style={{ gridTemplateColumns: '1.5fr 1.5fr 1fr 1.5fr 1.5fr auto' }}>
                      <span style={{ fontFamily: 'monospace', fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ref}</span>
                      <span style={{ fontSize: 12, color: '#526071', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ctr.Image}</span>
                      <span>
                        <span className={`dm-status ${containerStateClass(ctr.State ?? ctr.Status)}`}>
                          <span className="dm-status-dot" />
                          {ctr.State ?? ctr.Status}
                        </span>
                      </span>
                      <span>
                        {creator
                          ? <span className="dm-role-tag creator">{creator.displayName}</span>
                          : <span style={{ fontSize: 12, color: '#94a3b8' }}>平台外</span>}
                      </span>
                      <span style={{ display: 'flex', flexWrap: 'wrap', gap: 3, alignItems: 'center' }}>
                        {ownerList.length > 0
                          ? ownerList.map((u) => <span key={u.userId} className="dm-role-tag owner">{u.displayName}</span>)
                          : <span style={{ fontSize: 12, color: '#94a3b8' }}>未分配</span>}
                      </span>
                      <span>
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: '3px 8px' }}
                          onClick={() => openAssignRoles('container', ref, ref, ctr)}
                        >
                          <Users size={11} /> 管理
                        </button>
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            </ResourceLoadingWrapper>
          ) : resourceTab === 'images' ? (
            <ResourceLoadingWrapper loading={resourcesLoading}>
            {resources.images.length === 0 ? (
              <div className="dm-empty"><Image size={24} /> 暂无镜像</div>
            ) : (
              <div className="dm-table">
                <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 1fr 1.5fr 1.5fr auto' }}>
                  <span>仓库</span><span>标签</span><span>大小</span><span>创建者</span><span>所有者</span><span>操作</span>
                </div>
                {resources.images.map((img) => {
                  const ref = `${img.repo}:${img.tag}`;
                  const creator = users.find((u) => u.userId === img.creatorUserId);
                  const ownerList = (img.ownerUserIds ?? []).map((id) => users.find((u) => u.userId === id)).filter(Boolean) as ServerPermEntry[];
                  return (
                    <div key={img.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1fr 1fr 1.5fr 1.5fr auto' }}>
                      <span style={{ fontFamily: 'monospace', fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{img.repo}</span>
                      <span><code style={{ background: '#f1f5f9', padding: '2px 5px', borderRadius: 3, fontSize: 12 }}>{img.tag}</code></span>
                      <span style={{ fontSize: 12, color: '#526071' }}>{img.size}</span>
                      <span>
                        {creator
                          ? <span className="dm-role-tag creator">{creator.displayName}</span>
                          : <span style={{ fontSize: 12, color: '#94a3b8' }}>平台外</span>}
                      </span>
                      <span style={{ display: 'flex', flexWrap: 'wrap', gap: 3, alignItems: 'center' }}>
                        {ownerList.length > 0
                          ? ownerList.map((u) => <span key={u.userId} className="dm-role-tag owner">{u.displayName}</span>)
                          : <span style={{ fontSize: 12, color: '#94a3b8' }}>未分配</span>}
                      </span>
                      <span>
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: '3px 8px' }}
                          onClick={() => openAssignRoles('image', ref, ref, img)}
                        >
                          <Users size={11} /> 管理
                        </button>
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            </ResourceLoadingWrapper>
          ) : (
            <ResourceLoadingWrapper loading={resourcesLoading}>
            {resources.volumes.length === 0 ? (
              <div className="dm-empty"><HardDrive size={24} /> 暂无卷</div>
            ) : (
              <div className="dm-table">
                <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1.5fr 1.5fr auto' }}>
                  <span>卷名称</span><span>创建者</span><span>所有者</span><span>操作</span>
                </div>
                {resources.volumes.map((vol) => {
                  const creator = users.find((u) => u.userId === vol.creatorUserId);
                  const ownerList = (vol.ownerUserIds ?? []).map((id) => users.find((u) => u.userId === id)).filter(Boolean) as ServerPermEntry[];
                  return (
                    <div key={vol.name} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1.5fr 1.5fr auto' }}>
                      <span style={{ fontFamily: 'monospace', fontSize: 13 }}>{vol.name}</span>
                      <span>
                        {creator
                          ? <span className="dm-role-tag creator">{creator.displayName}</span>
                          : <span style={{ fontSize: 12, color: '#94a3b8' }}>平台外</span>}
                      </span>
                      <span style={{ display: 'flex', flexWrap: 'wrap', gap: 3, alignItems: 'center' }}>
                        {ownerList.length > 0
                          ? ownerList.map((u) => <span key={u.userId} className="dm-role-tag owner">{u.displayName}</span>)
                          : <span style={{ fontSize: 12, color: '#94a3b8' }}>未分配</span>}
                      </span>
                      <span>
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: '3px 8px' }}
                          onClick={() => openAssignRoles('volume', vol.name, vol.name, vol)}
                        >
                          <Users size={11} /> 管理
                        </button>
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            </ResourceLoadingWrapper>
          )}
        </Modal>
      )}

      {/* 角色分配弹窗（二级弹窗） */}
      {assignRolesTarget && (
        <Modal
          title={`角色分配 — ${assignRolesTarget.label}`}
          onClose={() => setAssignRolesTarget(null)}
          foot={
            <>
              <button className="btn" onClick={() => setAssignRolesTarget(null)}>取消</button>
              <button className="btn btn-primary" onClick={doAssignRoles} disabled={savingRoles}>
                {savingRoles ? <Spin /> : <CheckCircle size={14} />} 保存
              </button>
            </>
          }
        >
          {assignError && <Alert type="error">{assignError}</Alert>}

          {/* 创建者（只读） */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Users size={13} /> 创建者（平台自动记录，不可修改）</div>
            {assignRolesTarget.currentRoles.creatorUserId ? (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6 }}>
                {(() => {
                  const u = users.find((x) => x.userId === assignRolesTarget.currentRoles.creatorUserId);
                  return u
                    ? <span className="dm-role-tag creator">{u.displayName} <small style={{ color: '#64748b' }}>@{u.username}</small></span>
                    : <span className="dm-role-tag creator">{assignRolesTarget.currentRoles.creatorUserId}</span>;
                })()}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 6 }}>无（平台外资源）</div>
            )}
          </div>

          {/* 所有者（多选） */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Shield size={13} /> 所有者（可管理该资源）</div>
            <div className="dm-roles-checklist">
              {users.filter((u) => u.role !== 'admin').map((u) => (
                <label key={u.userId} className="dm-form-check">
                  <input
                    type="checkbox"
                    checked={assignOwnerIds.includes(u.userId)}
                    onChange={(e) => {
                      setAssignOwnerIds((prev) =>
                        e.target.checked ? [...prev, u.userId] : prev.filter((id) => id !== u.userId)
                      );
                      // 所有者自动从查看者中移除（避免角色重叠）
                      if (e.target.checked) {
                        setAssignViewerIds((prev) => prev.filter((id) => id !== u.userId));
                      }
                    }}
                  />
                  <span>{u.displayName}</span>
                  <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                </label>
              ))}
              {users.filter((u) => u.role !== 'admin').length === 0 && (
                <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无普通用户</div>
              )}
            </div>
          </div>

          {/* 查看者（多选） */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><FileText size={13} /> 查看者（只可查看该资源）</div>
            <div className="dm-roles-checklist">
              {users.filter((u) => u.role !== 'admin').map((u) => (
                <label key={u.userId} className={`dm-form-check${assignOwnerIds.includes(u.userId) ? ' disabled' : ''}`}>
                  <input
                    type="checkbox"
                    checked={assignViewerIds.includes(u.userId)}
                    disabled={assignOwnerIds.includes(u.userId)}
                    onChange={(e) => {
                      setAssignViewerIds((prev) =>
                        e.target.checked ? [...prev, u.userId] : prev.filter((id) => id !== u.userId)
                      );
                    }}
                  />
                  <span>{u.displayName}</span>
                  <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                  {assignOwnerIds.includes(u.userId) && (
                    <span style={{ fontSize: 11, color: '#64748b', marginLeft: 4 }}>(已是所有者)</span>
                  )}
                </label>
              ))}
              {users.filter((u) => u.role !== 'admin').length === 0 && (
                <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无普通用户</div>
              )}
            </div>
          </div>
        </Modal>
      )}

      {/* Fine-grained Permissions Edit Modal */}
      {permsEditTarget && permServer && (
        <Modal
          title={`权限配置 — ${permsEditTarget.displayName} @ ${permServer.name}`}
          onClose={() => setPermsEditTarget(null)}
          wide
          foot={
            <>
              <button className="btn" onClick={() => setPermsEditTarget(null)}>取消</button>
              <button className="btn btn-primary" onClick={savePerms} disabled={savingPerms}>
                {savingPerms ? <Spin /> : <CheckCircle size={14} />} 保存权限
              </button>
            </>
          }
        >
          {error && <Alert type="error">{error}</Alert>}

          {/* 快速预设 */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>快速预设：</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {(['none', 'view', 'use', 'manage'] as const).map((preset) => (
                <button key={preset} className="btn" style={{ fontSize: 12 }} onClick={() => applyPreset(preset)}>
                  {{ none: '无权限', view: '查看', use: '使用', manage: '管理' }[preset]}
                </button>
              ))}
            </div>
          </div>

          {/* 服务器可见性 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Server size={13} /> 服务器访问</div>
            <label className="dm-form-check">
              <input type="checkbox" checked={permsForm.server_visible} onChange={pf('server_visible')} />
              可见该服务器（用户能在列表中看到此服务器）
            </label>
          </div>

          {/* 镜像权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Image size={13} /> 镜像权限</div>
            <div className="dm-perm-checks">
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.img_pull} onChange={pf('img_pull')} />
                拉取镜像（docker pull）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.img_delete} onChange={pf('img_delete')} />
                删除镜像（docker rmi）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.img_copy} onChange={pf('img_copy')} />
                跨服务器复制镜像（save | ssh | load）
              </label>
            </div>
          </div>

          {/* 容器权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Box size={13} /> 容器权限</div>
            <div className="dm-perm-checks">
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_view_own} onChange={pf('ctr_view_own')} />
                查看自己的容器（列表 / 日志）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_view_all} onChange={pf('ctr_view_all')} />
                查看所有人的容器
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_create_run} onChange={pf('ctr_create_run')} />
                创建容器（docker run 模式）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_create_compose} onChange={pf('ctr_create_compose')} />
                创建容器（docker compose 模式）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_create_template} onChange={pf('ctr_create_template')} />
                从模板创建容器
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_manage_own} onChange={pf('ctr_manage_own')} />
                管理自己的容器（启/停/重启/删除）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.ctr_manage_all} onChange={pf('ctr_manage_all')} />
                管理所有人的容器
              </label>
            </div>
            <Field label="宿主机路径挂载白名单（每行一个前缀，留空则禁止 -v 本地路径挂载）" full>
              <textarea
                className="mono"
                value={permsPathStr}
                onChange={(e) => setPermsPathStr(e.target.value)}
                placeholder={'/data/shared\n/mnt/datasets'}
                style={{ minHeight: 80 }}
              />
            </Field>
          </div>

          {/* 卷权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><HardDrive size={13} /> 卷权限</div>
            <div className="dm-perm-checks">
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.vol_create} onChange={pf('vol_create')} />
                创建卷（docker volume create）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.vol_delete_own} onChange={pf('vol_delete_own')} />
                删除自己创建的卷
              </label>
               <label className="dm-form-check">
                 <input type="checkbox" checked={permsForm.vol_delete_all} onChange={pf('vol_delete_all')} />
                 删除所有人的卷
               </label>
               <label className="dm-form-check">
                 <input type="checkbox" checked={permsForm.vol_copy} onChange={pf('vol_copy')} />
                 复制卷到其他服务器（tar | SSH 流式传输）
               </label>
             </div>
             <Field label="卷空间配额 (GB，0 = 不限制)" full={false}>
              <input
                type="number" min="0" step="10"
                value={permsForm.vol_quota_gb}
                onChange={pf('vol_quota_gb')}
                style={{ width: 120 }}
              />
            </Field>
          </div>

          {/* 模板权限 */}
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><ClipboardList size={13} /> 模板权限</div>
            <div className="dm-perm-checks">
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.tpl_use} onChange={pf('tpl_use')} />
                使用模板（从模板部署容器）
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.tpl_create} onChange={pf('tpl_create')} />
                创建/上传模板
              </label>
              <label className="dm-form-check">
                <input type="checkbox" checked={permsForm.tpl_edit} onChange={pf('tpl_edit')} />
                编辑/删除模板
              </label>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ============================================================
// Admin: Template Management Panel
// ============================================================

function AdminTemplatesPanel() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<TemplateDetail | null>(null);
  const [form, setForm] = useState({
    name: '', description: '', category: 'general',
    docContent: '', configStr: '{}', isPublic: true,
  });
  const [saving, setSaving] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    clearError();
    try {
      const r = await apiGet<{ templates: Template[] }>(`${API}/templates`);
      setTemplates(r.templates);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { void load(); }, [load]);

  function openCreate() {
    setForm({ name: '', description: '', category: 'general', docContent: '', configStr: '{}', isPublic: true });
    setEditing(null);
    setShowCreate(true);
    setSuccessMsg('');
  }

  async function openEdit(id: string) {
    clearError();
    try {
      const r = await apiGet<{ template: TemplateDetail }>(`${API}/templates/${id}`);
      const t = r.template;
      setForm({
        name: t.name, description: t.description, category: t.category,
        docContent: t.docContent, configStr: JSON.stringify(t.config, null, 2), isPublic: t.isPublic,
      });
      setEditing(t);
      setShowCreate(true);
      setSuccessMsg('');
    } catch (e) {
      setError(e);
    }
  }

  async function doSave() {
    setSaving(true);
    clearError();
    let config: Record<string, unknown> = {};
    try {
      config = JSON.parse(form.configStr || '{}');
    } catch {
      setError(new Error('配置 JSON 格式错误'));
      setSaving(false);
      return;
    }
    try {
      if (editing) {
        await apiPut(`${API}/templates/${editing.id}`, {
          name: form.name, description: form.description, category: form.category,
          docContent: form.docContent, config, isPublic: form.isPublic,
        });
        setSuccessMsg('模板更新成功！');
      } else {
        await apiPost(`${API}/templates`, {
          name: form.name, description: form.description, category: form.category,
          docContent: form.docContent, config, isPublic: form.isPublic,
        });
        setSuccessMsg('模板创建成功！');
      }
      void load();
      setShowCreate(false);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  async function doDelete(id: string, name: string) {
    if (!confirm(`确定删除模板 ${name}？`)) return;
    clearError();
    try {
      await apiDelete(`${API}/templates/${id}`);
      void load();
    } catch (e) {
      setError(e);
    }
  }

  const f = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.type === 'checkbox' ? (e.target as HTMLInputElement).checked : e.target.value }));

  const categories = [...new Set(templates.map((t) => t.category))];

  return (
    <div style={{ display: 'grid', gap: 20 }}>
      {error && <Alert type="error">{error}</Alert>}
      {successMsg && <Alert type="success">{successMsg}</Alert>}

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" onClick={openCreate}><Plus size={14} /> 创建模板</button>
        <button className="btn" onClick={load} disabled={loading}><RefreshCw size={14} /> 刷新</button>
      </div>

      {loading ? <div className="dm-empty"><Spin /> 加载中…</div> :
       templates.length === 0 ? <div className="dm-empty"><ClipboardList size={32} /> 暂无模板</div> : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1.5fr 1fr 1fr auto' }}>
            <span>模板名称</span><span>分类</span><span>可见性</span><span>更新时间</span><span>操作</span>
          </div>
          {templates.map((t) => (
            <div key={t.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1.5fr 1fr 1fr auto' }}>
              <span style={{ fontWeight: 600 }}>{t.name}</span>
              <span><span className="dm-category-tag">{t.category}</span></span>
              <span style={{ color: t.isPublic ? '#065f46' : '#91400e', fontSize: 12 }}>
                {t.isPublic ? '公开' : '私有'}
              </span>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>{t.updatedAt.slice(0, 10)}</span>
              <span style={{ display: 'flex', gap: 4 }}>
                <button className="dm-btn-icon" title="编辑" onClick={() => openEdit(t.id)}><Pencil size={13} /></button>
                <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(t.id, t.name)}><Trash2 size={13} /></button>
              </span>
            </div>
          ))}
        </div>
      )}

      {showCreate && (
        <Modal title={editing ? `编辑模板 — ${editing.name}` : '创建模板'} onClose={() => setShowCreate(false)} wide
          foot={
            <>
              <button className="btn" onClick={() => setShowCreate(false)}>取消</button>
              <button className="btn btn-primary" onClick={doSave} disabled={saving || !form.name.trim()}>
                {saving ? <Spin /> : <CheckCircle size={14} />} {editing ? '保存更改' : '创建'}
              </button>
            </>
          }>
          {error && <Alert type="error">{error}</Alert>}
          <div className="dm-form-grid">
            <Field label="模板名称 *"><input value={form.name} onChange={f('name')} placeholder="Jupyter Notebook" required /></Field>
            <Field label="分类"><input value={form.category} onChange={f('category')} placeholder="ml / web / database / general" /></Field>
            <Field label="描述" full><input value={form.description} onChange={f('description')} placeholder="简短说明" /></Field>
          </div>
          <div>
            <label className="dm-form-check">
              <input type="checkbox" checked={form.isPublic} onChange={f('isPublic')} />
              公开（所有用户可见）
            </label>
          </div>
          <Field label="说明文档（Markdown）" full>
            <textarea className="mono" value={form.docContent} onChange={f('docContent')}
              placeholder={"## Jupyter Notebook\n\n使用说明...\n\n### 端口\n\n- `8888` — Jupyter Web 界面"} style={{ minHeight: 200 }} />
          </Field>
          {form.docContent && (
            <details style={{ border: '1px solid #e2e8f0', borderRadius: 8, padding: '8px 12px' }}>
              <summary style={{ cursor: 'pointer', fontSize: 13, color: '#526071' }}>预览 Markdown</summary>
              <div className="dm-md-preview" style={{ marginTop: 10 }}
                dangerouslySetInnerHTML={{ __html: renderMarkdown(form.docContent) }} />
            </details>
          )}
          <Field label="容器配置（JSON）" full>
            <textarea className="mono" value={form.configStr} onChange={f('configStr')}
              placeholder={'{\n  "type": "run",\n  "image": "jupyter/base-notebook",\n  "ports": ["8888:8888"]\n}'}
              style={{ minHeight: 160 }} />
          </Field>
          <Alert type="info">
            配置支持 <code>type: "run"</code>（docker run）或 <code>type: "compose"</code>（docker compose，需 <code>composeYaml</code> 字段）。
            字符串和数值类型的配置项会在用户创建容器时作为可覆盖参数显示。
          </Alert>
        </Modal>
      )}
    </div>
  );
}

// ============================================================
// Main DockerManagerTool Component
// ============================================================

type TabId = 'servers' | 'images' | 'containers' | 'templates' | 'volumes' | 'my_resources' | 'admin_servers' | 'admin_templates';

const TAB_LABELS: { id: TabId; label: string; icon: ReactNode; adminOnly?: boolean; userOnly?: boolean }[] = [
  { id: 'servers',          label: '服务器',    icon: <Server size={14} /> },
  { id: 'images',           label: '镜像',      icon: <Image size={14} /> },
  { id: 'containers',       label: '容器',      icon: <Box size={14} /> },
  { id: 'templates',        label: '模板',      icon: <ClipboardList size={14} /> },
  { id: 'volumes',          label: '卷',        icon: <Database size={14} /> },
  { id: 'my_resources',     label: '资源管理',  icon: <HardDrive size={14} />, userOnly: true },
  { id: 'admin_servers',    label: '服务器管理',icon: <Shield size={14} />, adminOnly: true },
  { id: 'admin_templates',  label: '模板管理',  icon: <Layers size={14} />, adminOnly: true },
];

function ServersOverviewPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      {servers.length === 0 ? (
        <div className="dm-empty"><Server size={32} /> 暂无可访问的服务器</div>
      ) : (
        <div className="dm-card-grid">
          {servers.map((s) => (
            <div key={s.id} className="dm-card">
              <div className="dm-card-header">
                <span className="dm-card-title"><Server size={15} style={{ display: 'inline', marginRight: 6, verticalAlign: 'middle' }} />{s.name}</span>
                <span className={`dm-perm-badge ${permColor(s.permissionLevel)}`}>{permLabel(s.permissionLevel)}</span>
              </div>
              <div className="dm-card-meta">
                <span>🖥 {s.host}:{s.port}</span>
                <span>👤 {s.sshUsername}</span>
                <span style={{ color: '#94a3b8', fontSize: 12 }}>添加于 {s.createdAt.slice(0, 10)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function DockerManagerTool() {
  const [me, setMe] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TabId>('servers');
  const [servers, setServers] = useState<DmServer[]>([]);
  const [serversLoading, setServersLoading] = useState(false);

  async function loadMe() {
    try {
      const s = await fetchMe();
      setMe(s.user);
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }

  async function loadServers() {
    if (!me) return;
    setServersLoading(true);
    try {
      const r = await apiGet<{ servers: DmServer[] }>(`${API}/servers`);
      setServers(r.servers);
    } catch {
      setServers([]);
    } finally {
      setServersLoading(false);
    }
  }

  useEffect(() => { void loadMe(); }, []);
  useEffect(() => { if (me) void loadServers(); }, [me]);

  if (loading) {
    return (
      <div className="tool-page dm-tool">
        <div className="dm-empty"><Spin /> 正在初始化…</div>
      </div>
    );
  }

  if (!me) {
    return (
      <div className="tool-page dm-tool">
        <LoginPanel onSuccess={loadMe} />
      </div>
    );
  }

  const isAdmin = me.role === 'admin';
  // adminOnly: 仅管理员可见；userOnly: 仅非管理员可见
  const visibleTabs = TAB_LABELS.filter((t) => {
    if (t.adminOnly) return isAdmin;
    if (t.userOnly) return !isAdmin;
    return true;
  });

  return (
    <div className="tool-page dm-tool">
      <div className="tool-header">
        <div>
          <h1 className="tool-title">Docker 多租户管理</h1>
          <p className="tool-subtitle">实验室多服务器 Docker 资源统一管理平台</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {isAdmin && <span style={{ fontSize: 12, background: '#7c3aed', color: '#fff', padding: '3px 10px', borderRadius: 999, fontWeight: 700 }}>管理员</span>}
          <span style={{ fontSize: 13, color: '#526071' }}>{me.displayName}</span>
        </div>
      </div>

      {/* Navigation Tabs */}
      <nav className="dm-nav">
        {visibleTabs.map((t) => (
          <button
            key={t.id}
            className={`dm-nav-tab${activeTab === t.id ? ' active' : ''}${t.adminOnly ? ' admin-tab' : ''}`}
            onClick={() => setActiveTab(t.id)}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </nav>

      {/* Panel Content */}
      <div className="tool-body">
        {activeTab === 'servers' && (
          <ServersOverviewPanel servers={servers} me={me} />
        )}
        {activeTab === 'images' && (
          <ImagesPanel servers={servers} me={me} />
        )}
        {activeTab === 'containers' && (
          <ContainersPanel servers={servers} me={me} />
        )}
        {activeTab === 'templates' && (
          <TemplatesPanel me={me} />
        )}
        {activeTab === 'volumes' && (
          <VolumesPanel servers={servers} me={me} />
        )}
        {activeTab === 'my_resources' && !isAdmin && (
          <MyResourcesPanel me={me} />
        )}
        {activeTab === 'admin_servers' && isAdmin && (
          <AdminServersPanel onRefresh={() => void loadServers()} />
        )}
        {activeTab === 'admin_templates' && isAdmin && (
          <AdminTemplatesPanel />
        )}
      </div>
    </div>
  );
}