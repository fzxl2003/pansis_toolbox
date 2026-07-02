// ============================================================
// Containers Panel — Docker Manager
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import {
  Box,
  CheckCircle,
  ChevronRight,
  ClipboardList,
  Code,
  Copy,
  Cpu,
  Database,
  FileText,
  Folder,
  Globe,
  HardDrive,
  Image,
  Info,
  Layers,
  Network,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Settings,
  Square,
  Terminal,
  Trash2,
} from 'lucide-react';
import { apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import type { AuthUser } from '../../../frontend/src/api/auth';
import { Alert, Field, Modal, ResourceUsagePanel, ServerSelector, Spin, TruncText } from './components';
import {
  API,
  containerStateClass,
  docReferencedVariables,
  extractPathPrefix,
  filterMatchesValue,
  filterSummary,
  formatSize,
  parseContainerStatus,
  parseNumberRange,
  renderMarkdownInline,
  splitDocByVariables,
  splitDocIntoBlocks,
  useErrorMsg,
  validateVariableValue,
} from './utils';
import type {
  ContainerDetail,
  CreateMode,
  DmServer,
  DockerContainer,
  DockerImage,
  DockerVolume,
  GpuInfo,
  ServerResourceOverview,
  Template,
  TemplateDetail,
  TemplateVariable,
  UserPerms,
} from './types';

// ---- 容器端口标签渲染 ----

function formatPortTags(ports: string | undefined): React.ReactNode {
  if (!ports) return <span style={{ color: '#cbd5e1', fontSize: 12 }}>—</span>;
  const parts = ports.split(', ').filter(Boolean);
  if (parts.length === 0) return <span style={{ color: '#cbd5e1', fontSize: 12 }}>—</span>;

  type PortEntry = { host: string; container: string; proto: string };
  const entries: PortEntry[] = [];
  for (const p of parts) {
    const m = p.match(/(?:[^:]+:)?(\d+)->(\d+)\/(\w+)/);
    if (m) {
      entries.push({ host: m[1], container: m[2], proto: m[3].toLowerCase() });
    }
  }
  if (entries.length === 0) return <span style={{ color: '#cbd5e1', fontSize: 12 }}>—</span>;

  // Docker 同一端口会同时生成 0.0.0.0:xxx 和 :::xxx 两条记录，去重保留唯一映射
  const seen = new Set<string>();
  const unique = entries.filter((e) => {
    const key = `${e.host}:${e.container}/${e.proto}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  const protoColor: Record<string, { bg: string; color: string; border: string }> = {
    tcp:  { bg: '#dbeafe', color: '#1d4ed8', border: '#bfdbfe' },
    udp:  { bg: '#fef3c7', color: '#92400e', border: '#fde68a' },
    sctp: { bg: '#f3e8ff', color: '#7c3aed', border: '#e9d5ff' },
  };

  const visible = unique.slice(0, 3);
  const rest = unique.length - visible.length;

  return (
    <span style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
      {visible.map((e, i) => {
        const c = protoColor[e.proto] ?? { bg: '#f1f5f9', color: '#475569', border: '#e2e8f0' };
        return (
          <span key={i} style={{
            display: 'inline-flex', alignItems: 'center', gap: 2,
            padding: '1px 7px', borderRadius: 999,
            background: c.bg, color: c.color, border: `1px solid ${c.border}`,
            fontSize: 11, fontFamily: 'monospace', fontWeight: 600, whiteSpace: 'nowrap',
          }}>
            {e.host}:{e.container}
            <span style={{ opacity: 0.65, fontSize: 10, fontWeight: 400 }}>{e.proto}</span>
          </span>
        );
      })}
      {rest > 0 && (
        <span style={{
          padding: '1px 7px', borderRadius: 999,
          background: '#f1f5f9', color: '#64748b', border: '1px solid #e2e8f0',
          fontSize: 11, fontWeight: 600,
        }}>+{rest}</span>
      )}
    </span>
  );
}

// 解析容器 Ports 字段，提取宿主机侧 SSH 端口（映射到容器 22 端口的）
function parseSshPort(ports: string | undefined): string | null {
  if (!ports) return null;
  const match = ports.match(/(?:\d+\.\d+\.\d+\.\d+:|:::)?(\d+)->22\/tcp/);
  return match ? match[1] : null;
}

// ---- ResourceOverviewStrip（在创建容器弹窗顶部显示用户当前额度）----

function ResourceOverviewStrip({ overview }: { overview: ServerResourceOverview }) {
  const vol = overview.volume;
  const cuda = overview.cuda;
  const ctr = overview.container;
  const unlimitedVol = vol.quotaGb === 0;
  const unlimitedCtr = ctr.quotaNum === 0;

  return (
    <div className="dm-resource-strip">
      {/* 卷配额 */}
      <div className="dm-resource-chip">
        <Database size={11} />
        <span>卷</span>
        {unlimitedVol
          ? <span style={{ color: '#22c55e', fontWeight: 600 }}>不限</span>
          : (
            <>
              <span style={{ color: (vol.remainingGb ?? 0) < vol.quotaGb * 0.1 ? '#ef4444' : '#1e293b', fontWeight: 600 }}>
                {vol.remainingGb !== null ? formatSize(vol.remainingGb) : '不限'}
              </span>
              <span style={{ color: '#94a3b8' }}>剩余</span>
            </>
          )
        }
      </div>
      {/* 容器配额 */}
      {!unlimitedCtr && (
        <div className="dm-resource-chip">
          <Box size={11} />
          <span>容器</span>
          <span style={{ color: (ctr.remaining ?? 0) <= 0 ? '#ef4444' : '#1e293b', fontWeight: 600 }}>
            {ctr.remaining ?? '不限'}
          </span>
          <span style={{ color: '#94a3b8' }}>剩余</span>
        </div>
      )}
      {/* 路径磁盘 */}
      {overview.paths.map((p) => (
        p.availGb !== null && (
          <div key={p.path} className="dm-resource-chip">
            <HardDrive size={11} />
            <span style={{ maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.path}>
              {p.path.split('/').pop() || p.path}
            </span>
            <span style={{ fontWeight: 600 }}>{formatSize(p.availGb)}</span>
            <span style={{ color: '#94a3b8' }}>可用</span>
          </div>
        )
      ))}
      {/* CUDA */}
      {cuda.serverHasCuda && (
        cuda.availableGpus.length > 0 ? (
          <div className="dm-resource-chip" style={{ gap: 4 }}>
            <Cpu size={11} />
            <span>GPU</span>
            {cuda.availableGpus.map((gpu) => (
              <span key={gpu.index} style={{
                background: '#ede9fe', color: '#5b21b6', border: '1px solid #c4b5fd',
                borderRadius: 999, padding: '0 5px', fontSize: 11, fontWeight: 700,
                lineHeight: '16px',
              }}>#{gpu.index}</span>
            ))}
            <span style={{ color: '#94a3b8' }}>可用</span>
          </div>
        ) : (
          <div className="dm-resource-chip">
            <Cpu size={11} />
            <span style={{ color: '#94a3b8' }}>无 GPU 权限</span>
          </div>
        )
      )}
    </div>
  );
}

// ---- RunCreateModal 相关子类型 ----

type PortEntry  = { id: number; host: string; container: string; proto: 'tcp' | 'udp' };
type MountEntry = { id: number; type: 'bind' | 'volume'; source: string; target: string; ro: boolean; newVolName: string };
type EnvEntry   = { id: number; key: string; value: string };

let _uid = 0;
const uid = () => ++_uid;

function mkPort(): PortEntry  { return { id: uid(), host: '', container: '', proto: 'tcp' }; }
function mkMount(): MountEntry { return { id: uid(), type: 'bind', source: '', target: '', ro: false, newVolName: '' }; }
function mkEnv(): EnvEntry    { return { id: uid(), key: '', value: '' }; }

// 判断卷是否为 viewer-only（仅有查看权、非所有者），此时挂载必须只读
function isViewerOnlyVolume(volName: string, volumes: DockerVolume[]): boolean {
  if (!volName || volName.startsWith('__')) return false;
  const vol = volumes.find(v => v.name === volName);
  return !!vol && vol.canManage === false;
}

// 将路径白名单（前缀列表）转为结构化筛选 JSON，供 HostPathPickerModal 使用
function buildWhitelistFilter(whitelist: string[] | undefined): string {
  if (!whitelist || whitelist.length === 0) return '';
  const groups = whitelist
    .map(w => w.trim().replace(/\/+$/, ''))
    .filter(Boolean)
    .map(w => ({ conditions: [{ op: 'match', pattern: w + '/*' }] }));
  return groups.length > 0 ? JSON.stringify({ groups }) : '';
}

// 将界面表单组装成 docker run 命令字符串（单向，仅用于预览/命令行模式）
function buildDockerCmd(p: {
  image: string; name: string; restart: string; network: string; command: string;
  ports: PortEntry[]; mounts: MountEntry[]; envs: EnvEntry[];
  gpus: string; extra_args: string;
  volumes: DockerVolume[];
}): string {
  // 先收集需要显式创建的新卷
  const createCmds: string[] = [];
  const parts: string[] = ['docker', 'run', '-d'];
  if (p.name)    parts.push('--name', p.name);
  if (p.restart) parts.push('--restart', p.restart);
  if (p.network) parts.push('--network', p.network);
  for (const pt of p.ports) {
    if (pt.host || pt.container) {
      const mapping = pt.host
        ? `${pt.host}:${pt.container}/${pt.proto}`
        : `${pt.container}/${pt.proto}`;
      parts.push('-p', mapping);
    }
  }
  for (const m of p.mounts) {
    if (m.type === 'bind') {
      if (m.source && m.target) parts.push('-v', `${m.source}:${m.target}${m.ro ? ':ro' : ''}`);
    } else {
      const volName = m.source === '__new__' ? m.newVolName : m.source;
      if (volName.startsWith('__') && volName.endsWith('__')) continue;
      if (volName && m.target) {
        // 新卷需先创建
        if (m.source === '__new__' && volName.trim()) {
          createCmds.push(`docker volume create ${volName.trim()}`);
        }
        parts.push('-v', `${volName}:${m.target}${m.ro ? ':ro' : ''}`);
      }
    }
  }
  for (const e of p.envs) {
    if (e.key) parts.push('-e', `${e.key}=${e.value}`);
  }
  if (p.gpus) parts.push('--gpus', p.gpus);
  if (p.extra_args) parts.push(...p.extra_args.trim().split(/\s+/));
  if (p.image) parts.push(p.image);
  if (p.command) parts.push(...p.command.trim().split(/\s+/));
  const runCmd = parts.join(' ');
  // 显示时合并为一条命令（用 && 连接）
  return createCmds.length > 0 ? [...createCmds, runCmd].join(' && ') : runCmd;
}

// ---- CrossServerImageModal ----
// 容器创建时，从其他服务器复制镜像到当前服务器

function CrossServerImageModal({
  servers,
  me,
  currentServerId,
  currentServerName,
  imgQuotaExhausted,
  onClose,
  onCopied,
}: {
  servers: DmServer[];
  me: AuthUser;
  currentServerId: string;
  currentServerName: string;
  imgQuotaExhausted: boolean;
  onClose: () => void;
  onCopied: (imageRef: string) => void;
}) {
  const [selectedSrcId, setSelectedSrcId] = useState<string | null>(null);
  const [images, setImages] = useState<DockerImage[]>([]);
  const [loading, setLoading] = useState(false);
  const [copying, setCopying] = useState<string | null>(null);
  const [error, setError, clearError] = useErrorMsg();

  const canCopyServer = (sid: string) => {
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || !!s?.perms?.img_copy;
  };

  // 可作为源服务器的列表（用户有 img_copy 权限，且不是当前服务器）
  const srcServers = servers.filter((s) => s.id !== currentServerId && canCopyServer(s.id));

  async function selectSrc(sid: string) {
    setSelectedSrcId(sid);
    setImages([]);
    clearError();
    setLoading(true);
    try {
      const r = await apiGet<{ images: DockerImage[] }>(`${API}/servers/${sid}/images`);
      setImages(r.images);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }

  async function doCopy(imageRef: string) {
    if (!selectedSrcId) return;
    setCopying(imageRef);
    clearError();
    try {
      await apiPost(`${API}/images/copy`, {
        srcServerId: selectedSrcId,
        dstServerId: currentServerId,
        imageRef,
      });
      onCopied(imageRef);
    } catch (e) {
      setError(e);
    } finally {
      setCopying(null);
    }
  }

  const srcServer = servers.find((s) => s.id === selectedSrcId);

  return (
    <Modal title="从其他服务器复制镜像" onClose={onClose} wide
      foot={
        selectedSrcId ? (
          <>
            <button className="btn" onClick={() => { setSelectedSrcId(null); setImages([]); clearError(); }}>← 返回服务器列表</button>
            <button className="btn btn-primary" onClick={onClose}>关闭</button>
          </>
        ) : (
          <button className="btn btn-primary" onClick={onClose}>关闭</button>
        )
      }>
      {error && <Alert type="error">{error}</Alert>}
      {imgQuotaExhausted && (
        <Alert type="error">当前服务器镜像空间配额已用满，无法接收复制的镜像。请删除不再使用的镜像或联系管理员调整配额。</Alert>
      )}

      {!selectedSrcId ? (
        <div>
          <Alert type="info">选择一个源服务器，将其中的镜像复制到当前服务器（{currentServerName}），复制完成后将自动选中该镜像。</Alert>
          {srcServers.length === 0 ? (
            <div className="dm-empty"><Image size={32} /> 暂无其他可复制镜像的服务器（需要在其他服务器也拥有「跨服务器复制镜像」权限）</div>
          ) : (
            <div className="dm-table">
              <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1.5fr auto' }}>
                <span>服务器</span><span>地址</span><span>操作</span>
              </div>
              {srcServers.map((s) => (
                <div key={s.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1.5fr auto' }}>
                  <span style={{ fontWeight: 600 }}>{s.name}</span>
                  <span style={{ color: '#526071', fontFamily: 'monospace', fontSize: 13 }}>{s.host}:{s.port}</span>
                  <span>
                    <button className="btn" style={{ fontSize: 12 }} onClick={() => selectSrc(s.id)}>选择</button>
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div>
          <div style={{ marginBottom: 12, fontSize: 13, color: '#526071' }}>
            源服务器：<strong>{srcServer?.name}</strong>（{srcServer?.host}:{srcServer?.port}） → 目标：<strong>{currentServerName}</strong>
          </div>
          {loading ? (
            <div className="dm-empty"><Spin /> 加载镜像列表中…</div>
          ) : images.length === 0 ? (
            <div className="dm-empty"><Image size={32} /> 该服务器暂无可复制的镜像</div>
          ) : (
            <div className="dm-table">
              <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 1fr auto' }}>
                <span>镜像</span><span>标签</span><span>大小</span><span>操作</span>
              </div>
              {images.map((img) => {
                const ref = `${img.repo}:${img.tag}`;
                return (
                  <div key={img.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1fr 1fr auto' }}>
                    <span style={{ fontFamily: 'monospace', fontSize: 13, minWidth: 0 }}><TruncText text={img.repo} /></span>
                    <span><code style={{ background: '#f1f5f9', padding: '2px 6px', borderRadius: 4 }}>{img.tag}</code></span>
                    <span style={{ color: '#526071' }}>{img.size}</span>
                    <span>
                      <button className="btn btn-primary" style={{ fontSize: 12 }}
                        disabled={!!copying || imgQuotaExhausted}
                        onClick={() => doCopy(ref)}>
                        {copying === ref ? <Spin /> : <Copy size={12} />} 复制
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function CrossServerVolumeModal({
  servers,
  me,
  currentServerId,
  currentServerName,
  volQuotaExhausted,
  onClose,
  onCopied,
}: {
  servers: DmServer[];
  me: AuthUser;
  currentServerId: string;
  currentServerName: string;
  volQuotaExhausted: boolean;
  onClose: () => void;
  onCopied: (volumeName: string) => void;
}) {
  const [selectedSrcId, setSelectedSrcId] = useState<string | null>(null);
  const [volumes, setVolumes] = useState<DockerVolume[]>([]);
  const [selectedVolume, setSelectedVolume] = useState('');
  const [dstName, setDstName] = useState('');
  const [loading, setLoading] = useState(false);
  const [copying, setCopying] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  const canCopyServer = (sid: string) => {
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || (!!s?.perms?.vol_copy && !!s?.perms?.vol_create);
  };

  const srcServers = servers.filter((s) => canCopyServer(s.id));

  async function selectSrc(sid: string) {
    setSelectedSrcId(sid);
    setSelectedVolume('');
    setDstName('');
    setVolumes([]);
    clearError();
    setLoading(true);
    try {
      const r = await apiGet<{ volumes: DockerVolume[] }>(`${API}/servers/${sid}/volumes`);
      setVolumes(r.volumes ?? []);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }

  async function doCopy() {
    if (!selectedSrcId || !selectedVolume || !dstName.trim()) return;
    setCopying(true);
    clearError();
    try {
      await apiPost(`${API}/volumes/copy`, {
        srcServerId: selectedSrcId,
        srcVolumeName: selectedVolume,
        dstServerId: currentServerId,
        dstVolumeName: dstName.trim(),
      });
      onCopied(dstName.trim());
    } catch (e) {
      setError(e);
    } finally {
      setCopying(false);
    }
  }

  const srcServer = servers.find((s) => s.id === selectedSrcId);
  const sameNameOnSameServer = selectedSrcId === currentServerId && selectedVolume === dstName.trim();

  return (
    <Modal title="复制卷到当前服务器" onClose={onClose} wide
      foot={
        selectedSrcId ? (
          <>
            <button className="btn" onClick={() => { setSelectedSrcId(null); setVolumes([]); clearError(); }}>← 返回服务器列表</button>
            <button className="btn" onClick={onClose}>取消</button>
            <button className="btn btn-primary" onClick={doCopy}
              disabled={copying || volQuotaExhausted || !selectedVolume || !dstName.trim() || sameNameOnSameServer}>
              {copying ? <Spin /> : <Copy size={14} />} 复制并使用
            </button>
          </>
        ) : (
          <button className="btn btn-primary" onClick={onClose}>关闭</button>
        )
      }>
      {error && <Alert type="error">{error}</Alert>}
      {volQuotaExhausted && <Alert type="error">当前服务器卷空间配额已用满，无法接收复制卷。</Alert>}
      {!selectedSrcId ? (
        <div>
          <Alert type="info">选择源服务器，可以本地复制当前服务器上的卷，也可以从其他服务器复制到当前服务器（{currentServerName}）。</Alert>
          {srcServers.length === 0 ? (
            <div className="dm-empty"><HardDrive size={32} /> 暂无可复制卷的服务器（需要卷复制和创建卷权限）</div>
          ) : (
            <div className="dm-table">
              <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1.5fr auto' }}>
                <span>服务器</span><span>地址</span><span>操作</span>
              </div>
              {srcServers.map((s) => (
                <div key={s.id} className="dm-table-row" style={{ gridTemplateColumns: '2fr 1.5fr auto' }}>
                  <span style={{ fontWeight: 600 }}>{s.name}{s.id === currentServerId ? '（本地）' : ''}</span>
                  <span style={{ color: '#526071', fontFamily: 'monospace', fontSize: 13 }}>{s.host}:{s.port}</span>
                  <span><button className="btn" style={{ fontSize: 12 }} onClick={() => selectSrc(s.id)}>选择</button></span>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 12 }}>
          <div style={{ fontSize: 13, color: '#526071' }}>
            源服务器：<strong>{srcServer?.name}</strong>（{srcServer?.host}:{srcServer?.port}） → 目标：<strong>{currentServerName}</strong>
          </div>
          {loading ? (
            <div className="dm-empty"><Spin /> 加载卷列表中…</div>
          ) : volumes.length === 0 ? (
            <div className="dm-empty"><HardDrive size={32} /> 该服务器暂无可复制的卷</div>
          ) : (
            <>
              <Field label="源卷">
                <select value={selectedVolume} onChange={(e) => {
                  setSelectedVolume(e.target.value);
                  setDstName(e.target.value ? `${e.target.value}-copy` : '');
                }}>
                  <option value="">— 选择源卷 —</option>
                  {volumes.map((v) => <option key={v.name} value={v.name}>{v.name}</option>)}
                </select>
              </Field>
              <Field label="目标卷名称">
                <input value={dstName} onChange={(e) => setDstName(e.target.value)} placeholder="new-volume-name" />
              </Field>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}

// ---- RunCreateModal ----

export function RunCreateModal({ serverId, servers, me, quota, serverOverview, onClose, onSuccess }: {
  serverId: string;
  servers: DmServer[];
  me: AuthUser;
  quota: UserPerms | null;
  serverOverview: ServerResourceOverview | null;
  onClose: () => void;
  onSuccess: () => void;
}) {
  // ---------- 模式切换 ----------
  const [mode, setMode] = useState<'gui' | 'cli'>('gui');

  // ---------- 界面模式表单 ----------
  const [name, setName]       = useState('');
  const [image, setImage]     = useState('');
  const [imageInput, setImageInput] = useState(''); // 手动输入部分
  const [imageMode, setImageMode] = useState<'select' | 'input'>('select');
  const [availImages, setAvailImages] = useState<DockerImage[]>([]);
  const [imagesLoaded, setImagesLoaded] = useState(false);
  const [restart, setRestart] = useState('unless-stopped');
  const [network, setNetwork] = useState('');
  const [command, setCommand] = useState('');
  const [extraArgs, setExtraArgs] = useState('');
  const [ports, setPorts]   = useState<PortEntry[]>([mkPort()]);
  const [mounts, setMounts] = useState<MountEntry[]>([mkMount()]);
  const [envs, setEnvs]     = useState<EnvEntry[]>([mkEnv()]);
  const [availVolumes, setAvailVolumes] = useState<DockerVolume[]>([]);
  const [volumeCopyMountId, setVolumeCopyMountId] = useState<number | null>(null);
  const [pathPickerMountId, setPathPickerMountId] = useState<number | null>(null);

  // ---------- CUDA ----------
  const [selectedGpuIndices, setSelectedGpuIndices] = useState<number[]>([]);
  const [cudaMode, setCudaMode] = useState<'none' | 'all' | 'custom'>('none');

  // ---------- CLI 模式 ----------
  const [cliCmd, setCliCmd] = useState('docker run -d ');

  // ---------- 公共 ----------
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  // ---------- 跨服务器镜像复制 ----------
  const [showCrossServer, setShowCrossServer] = useState(false);

  // 权限判断
  const canPull = me.role === 'admin' || !!quota?.img_pull;
  const canCopyCurrent = me.role === 'admin' || !!quota?.img_copy;
  const canCreateVolume = me.role === 'admin' || !!quota?.vol_create;
  const canCopyVolume = me.role === 'admin' || (!!quota?.vol_copy && !!quota?.vol_create);

  // 镜像配额是否已用满（remainingGb=null 表示不限）
  const imgQuotaExhausted =
    serverOverview?.image?.remainingGb != null && serverOverview.image.remainingGb <= 0;

  // 容器配额是否已用满（remaining=null 表示不限）
  const ctrQuotaExhausted =
    serverOverview?.container?.remaining != null && serverOverview.container.remaining <= 0;
  const volQuotaExhausted =
    serverOverview?.volume?.remainingGb != null && serverOverview.volume.remainingGb <= 0;

  const availableGpus = serverOverview?.cuda?.availableGpus ?? [];
  const hasCuda = (serverOverview?.cuda?.serverHasCuda ?? false) && availableGpus.length > 0;

  function buildGpusArg(): string {
    if (cudaMode === 'none') return '';
    if (cudaMode === 'all') return 'all';
    if (selectedGpuIndices.length === 0) return '';
    return `device=${selectedGpuIndices.join(',')}`;
  }

  // 加载镜像和卷列表（懒加载）
  const reloadImages = () => {
    apiGet<{ images: DockerImage[] }>(`${API}/servers/${serverId}/images`)
      .then(r => setAvailImages(r.images))
      .catch(() => {/* 静默 */});
  };

  useEffect(() => {
    if (imagesLoaded) return;
    setImagesLoaded(true);
    reloadImages();
    apiGet<{ volumes: DockerVolume[] }>(`${API}/servers/${serverId}/volumes`)
      .then(r => setAvailVolumes(r.volumes ?? []))
      .catch(() => {/* 静默 */});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverId, imagesLoaded]);

  // 当切换到 CLI 模式时，将界面表单同步为命令字符串
  function switchToCli() {
    const gpus = buildGpusArg();
    const cmd = buildDockerCmd({
      image: imageMode === 'input' ? imageInput : image,
      name, restart, network, command,
      ports, mounts, envs, gpus, extra_args: extraArgs,
      volumes: availVolumes,
    });
    setCliCmd(cmd);
    setMode('cli');
  }

  // ---------- 提交 ----------
  async function submitGui() {
    const finalImage = imageMode === 'input' ? imageInput.trim() : image;
    if (!finalImage) return;
    clearError();
    setLoading(true);
    try {
      const portList = ports
        .filter(p => p.container)
        .map(p => p.host ? `${p.host}:${p.container}/${p.proto}` : `${p.container}/${p.proto}`);
      const volList = mounts.flatMap(m => {
        if (m.type === 'bind') {
          return m.source && m.target ? [`${m.source}:${m.target}${m.ro ? ':ro' : ''}`] : [];
        } else {
          const v = m.source === '__new__' ? m.newVolName : m.source;
          if (v.startsWith('__') && v.endsWith('__')) return [];
          // viewer-only 卷不强制只读，由后端校验是否已设只读
          return v && m.target ? [`${v}:${m.target}${m.ro ? ':ro' : ''}`] : [];
        }
      });
      const envList = envs.filter(e => e.key).map(e => `${e.key}=${e.value}`);
      await apiPost(`${API}/servers/${serverId}/containers/run`, {
        name, image: finalImage, command, network, restart,
        ports: portList, volumes: volList, envs: envList,
        gpus: buildGpusArg(), extra_args: extraArgs,
      });
      onSuccess();
    } catch (e) { setError(e); }
    finally { setLoading(false); }
  }

  async function submitCli() {
    clearError();
    setLoading(true);
    try {
      await apiPost(`${API}/servers/${serverId}/containers/run-raw`, { command: cliCmd });
      onSuccess();
    } catch (e) { setError(e); }
    finally { setLoading(false); }
  }

  // 跨服务器复制成功后：关闭弹窗、刷新镜像列表、自动选中
  function handleCrossServerCopied(imageRef: string) {
    setShowCrossServer(false);
    reloadImages();
    setImage(imageRef);
    setImageMode('select');
  }

  function handleVolumeCopied(volumeName: string) {
    setVolumeCopyMountId((mountId) => {
      if (mountId !== null) {
        setMounts((prev) => prev.map((m) => m.id === mountId ? { ...m, type: 'volume', source: volumeName, newVolName: '' } : m));
      }
      return null;
    });
    setAvailVolumes((prev) => prev.some((v) => v.name === volumeName)
      ? prev
      : [...prev, { name: volumeName, driver: 'local', mountpoint: '', platformManaged: true }]);
  }

  const hasInvalidMount = mounts.some((m) => {
    if (!m.target && (m.source || m.newVolName)) return true;
    if (m.type === 'volume' && m.source === '__new__') return !m.newVolName.trim() || !canCreateVolume || volQuotaExhausted;
    if (m.type === 'volume' && m.source === '__copy__') return true;
    // viewer-only 卷必须勾选只读，否则后端会拒绝创建
    if (m.type === 'volume' && isViewerOnlyVolume(m.source, availVolumes) && !m.ro) return true;
    return false;
  });
  const canSubmitGui = (imageMode === 'input' ? !!imageInput.trim() : !!image) && !hasInvalidMount;

  // ---------- 渲染 ----------
  return (
    <>
    <Modal title="docker run 创建容器" onClose={onClose} wide
      foot={
        <>
          {/* 模式切换 */}
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4 }}>
            <button
              type="button"
              className={`btn${mode === 'gui' ? ' btn-primary' : ''}`}
              style={{ fontSize: 12 }}
              onClick={() => setMode('gui')}
            >界面参数</button>
            <button
              type="button"
              className={`btn${mode === 'cli' ? ' btn-primary' : ''}`}
              style={{ fontSize: 12 }}
              onClick={switchToCli}
            >命令行</button>
          </div>
          <button className="btn" onClick={onClose}>取消</button>
          <button
            className="btn btn-primary"
            onClick={mode === 'gui' ? submitGui : submitCli}
            disabled={loading || ctrQuotaExhausted || (mode === 'gui' && !canSubmitGui)}
          >
            {loading ? <Spin /> : <Play size={14} />} 创建
          </button>
        </>
      }>
      {error && <Alert type="error">{error}</Alert>}
      {ctrQuotaExhausted && (
        <Alert type="error">容器数量配额已用满，无法创建新容器。请删除不再使用的容器或联系管理员调整配额。</Alert>
      )}
      {serverOverview && <ResourceOverviewStrip overview={serverOverview} />}
{quota?.ctr_path_whitelist && quota.ctr_path_whitelist.length > 0 && (
<Alert type="info">挂载路径白名单：{quota.ctr_path_whitelist.join('、')}</Alert>
      )}

      {/* ===== CLI 模式 ===== */}
      {mode === 'cli' && (
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ fontSize: 12, color: '#64748b' }}>
            直接输入完整 <code>docker run</code> 命令（从界面模式同步而来，可手动修改）：
          </div>
          {availVolumes.some(v => v.canManage === false) && (
            <Alert type="info">
              提示：若命令中挂载了您仅有查看权限的卷（非所有者），必须手动在对应 <code>-v</code> / <code>--mount</code> 项添加 <code>:ro</code> / <code>readonly=true</code> 设置只读，否则将拒绝创建。
            </Alert>
          )}
          <textarea
            className="mono"
            value={cliCmd}
            onChange={e => setCliCmd(e.target.value)}
            style={{ minHeight: 120, fontSize: 13 }}
          />
        </div>
      )}

      {/* ===== 界面模式 ===== */}
      {mode === 'gui' && (
        <div style={{ display: 'grid', gap: 16 }}>

          {/* ── 基本信息 ── */}
            <div className="dm-run-section">
              <div className="dm-run-section-title">基本信息</div>
              <div className="dm-form-grid">
              {/* 镜像 */}
              <Field label="镜像 *" full>
                <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    {imageMode === 'select' ? (
                      <select
                        value={image}
                        onChange={e => {
                          if (e.target.value === '__cross_server__') {
                            setShowCrossServer(true);
                            setImage('');
                          } else {
                            setImage(e.target.value);
                          }
                        }}
                        style={{ width: '100%' }}
                      >
                        <option value="">— 选择已有镜像 —</option>
                        {canCopyCurrent && (
                          <option value="__cross_server__">⟳ 从其他服务器复制镜像…</option>
                        )}
                        {availImages.map(img => (
                          <option key={`${img.repo}:${img.tag}`} value={`${img.repo}:${img.tag}`}>
                            {img.repo}:{img.tag}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        value={imageInput}
                        onChange={e => setImageInput(e.target.value)}
                        placeholder="nginx:latest（不存在则自动拉取）"
                      />
                    )}
                  </div>
                  {canPull && (
                    <button
                      type="button"
                      className="btn"
                      style={{ flexShrink: 0, fontSize: 12 }}
                      onClick={() => { setImageMode(m => m === 'select' ? 'input' : 'select'); }}
                    >
                      {imageMode === 'select' ? '手动输入' : '选择已有'}
                    </button>
                  )}
                </div>
                {!canPull && imageMode === 'select' && (
                  <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
                    您没有拉取镜像权限，只能选择已有镜像或从其他服务器复制。
                  </div>
                )}
              </Field>
              <Field label="容器名称">
                <input value={name} onChange={e => setName(e.target.value)} placeholder="my-nginx（可选）" />
              </Field>
              <Field label="重启策略">
                <select value={restart} onChange={e => setRestart(e.target.value)}>
                  <option value="">不重启</option>
                  <option value="always">always</option>
                  <option value="unless-stopped">unless-stopped</option>
                  <option value="on-failure">on-failure</option>
                </select>
              </Field>
              <Field label="网络">
                <input value={network} onChange={e => setNetwork(e.target.value)} placeholder="bridge（可选）" />
              </Field>
              <Field label="启动命令">
                <input value={command} onChange={e => setCommand(e.target.value)} placeholder="可选，覆盖默认 CMD" />
              </Field>
            </div>
          </div>

          {/* ── 端口映射 ── */}
          <div className="dm-run-section">
            <div className="dm-run-section-title" style={{ justifyContent: 'space-between' }}>
              <span><Globe size={13} /> 端口映射</span>
              <button type="button" className="btn" style={{ fontSize: 12 }} onClick={() => setPorts(p => [...p, mkPort()])}>
                <Plus size={12} /> 添加
              </button>
            </div>
            {ports.length === 0 && (
              <div style={{ fontSize: 12, color: '#94a3b8', padding: '4px 0' }}>暂无端口映射</div>
            )}
            {ports.map((pt, i) => (
              <div key={pt.id} className="dm-run-list-row">
                <div className="dm-run-list-row-inputs">
                  <Field label="宿主机端口">
                    <input
                      type="number" min={1} max={65535}
                      value={pt.host} placeholder="8080（可选）"
                      onChange={e => setPorts(prev => prev.map((p, j) => j === i ? { ...p, host: e.target.value } : p))}
                    />
                  </Field>
                  <span style={{ alignSelf: 'flex-end', paddingBottom: 10, color: '#94a3b8', flexShrink: 0 }}>→</span>
                  <Field label="容器端口 *">
                    <input
                      type="number" min={1} max={65535}
                      value={pt.container} placeholder="80"
                      onChange={e => setPorts(prev => prev.map((p, j) => j === i ? { ...p, container: e.target.value } : p))}
                    />
                  </Field>
                  <Field label="协议">
                    <select
                      value={pt.proto}
                      onChange={e => setPorts(prev => prev.map((p, j) => j === i ? { ...p, proto: e.target.value as 'tcp' | 'udp' } : p))}
                    >
                      <option value="tcp">TCP</option>
                      <option value="udp">UDP</option>
                    </select>
                  </Field>
                </div>
                <button type="button" className="dm-btn-icon danger" title="删除" style={{ flexShrink: 0, alignSelf: 'flex-end', marginBottom: 6 }}
                  onClick={() => setPorts(prev => prev.filter((_, j) => j !== i))}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>

          {/* ── 卷 / 路径挂载 ── */}
          <div className="dm-run-section">
            <div className="dm-run-section-title" style={{ justifyContent: 'space-between' }}>
              <span><HardDrive size={13} /> 卷 / 路径挂载</span>
              <button type="button" className="btn" style={{ fontSize: 12 }} onClick={() => setMounts(m => [...m, mkMount()])}>
                <Plus size={12} /> 添加
              </button>
            </div>
            {mounts.length === 0 && (
              <div style={{ fontSize: 12, color: '#94a3b8', padding: '4px 0' }}>暂无挂载</div>
            )}
            {mounts.map((m, i) => (
              <div key={m.id} className="dm-run-list-row">
                {/* 不换行：类型固定宽，源/目标弹性，只读紧凑 */}
                <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', gap: 8, flexWrap: 'nowrap', minWidth: 0 }}>
                  <Field label="类型" style={{ width: 110, flexShrink: 0 }}>
                    <select
                      value={m.type}
                      onChange={e => setMounts(prev => prev.map((x, j) => j === i ? { ...x, type: e.target.value as 'bind' | 'volume', source: '', newVolName: '' } : x))}
                    >
                      <option value="bind">路径 (bind)</option>
                      <option value="volume">卷 (volume)</option>
                    </select>
                  </Field>
                  {m.type === 'bind' ? (
                    <Field label="宿主机路径 *" style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <input
                          value={m.source} placeholder="/data/myapp"
                          onChange={e => setMounts(prev => prev.map((x, j) => j === i ? { ...x, source: e.target.value } : x))}
                          style={{ flex: 1, minWidth: 0 }}
                        />
                        <button type="button" className="btn" style={{ flexShrink: 0, fontSize: 12 }}
                          onClick={() => setPathPickerMountId(m.id)}
                          title="点选服务器目录"
                        >
                          <Folder size={12} /> 点选
                        </button>
                      </div>
                    </Field>
                  ) : (
                    <Field label="卷名称 *" style={{ width: 220, flexShrink: 0 }}>
                      <select
                        value={m.source}
                        onChange={e => {
                          if (e.target.value === '__copy__') {
                            setVolumeCopyMountId(m.id);
                            return;
                          }
                          const viewerOnly = isViewerOnlyVolume(e.target.value, availVolumes);
                          setMounts(prev => prev.map((x, j) => j === i ? { ...x, source: e.target.value, newVolName: '', ro: viewerOnly ? true : x.ro } : x));
                        }}
                        style={{ width: '100%' }}
                      >
                        <option value="">— 选择已有卷 —</option>
                        {availVolumes.map(v => (
                          <option key={v.name} value={v.name}>{v.name}{v.canManage === false ? ' (只读)' : ''}</option>
                        ))}
                        {canCopyVolume && (
                          <option value="__copy__" disabled={volQuotaExhausted}>⟳ 复制卷到当前服务器…</option>
                        )}
                        {canCreateVolume && (
                          <option value="__new__" disabled={volQuotaExhausted}>+ 新建卷…</option>
                        )}
                      </select>
                      {m.source === '__new__' && (
                        <input
                          style={{ marginTop: 4, width: '100%' }}
                          value={m.newVolName} placeholder="new-volume-name"
                          onChange={e => setMounts(prev => prev.map((x, j) => j === i ? { ...x, newVolName: e.target.value } : x))}
                        />
                      )}
                      {isViewerOnlyVolume(m.source, availVolumes) && !m.ro && (
                        <div style={{ fontSize: 11, color: '#dc2626', marginTop: 4 }}>您仅有此卷的查看权限，必须勾选「只读」才能创建</div>
                      )}
                      {isViewerOnlyVolume(m.source, availVolumes) && m.ro && (
                        <div style={{ fontSize: 11, color: '#92400e', marginTop: 4 }}>您仅有此卷的查看权限，已设为只读挂载</div>
                      )}
                      {!canCreateVolume && availVolumes.length === 0 && (
                        <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>您没有创建卷权限，只能使用已有可见卷。</div>
                      )}
                    </Field>
                  )}
                  <Field label="容器路径 *" style={{ flex: 1, minWidth: 0 }}>
                    <input
                      value={m.target} placeholder="/app/data"
                      onChange={e => setMounts(prev => prev.map((x, j) => j === i ? { ...x, target: e.target.value } : x))}
                    />
                  </Field>
                  <Field label="只读" style={{ width: 64, flexShrink: 0 }}>
                    <div style={{ paddingTop: 8 }}>
                      <label className="dm-form-check">
                        <input type="checkbox"
                          checked={m.ro}
                          onChange={e => setMounts(prev => prev.map((x, j) => j === i ? { ...x, ro: e.target.checked } : x))}
                        />
                        <span>ro</span>
                      </label>
                    </div>
                  </Field>
                </div>
                <button type="button" className="dm-btn-icon danger" title="删除" style={{ flexShrink: 0, alignSelf: 'flex-end', marginBottom: 6 }}
                  onClick={() => setMounts(prev => prev.filter((_, j) => j !== i))}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>

          {/* ── 环境变量 ── */}
          <div className="dm-run-section">
            <div className="dm-run-section-title" style={{ justifyContent: 'space-between' }}>
              <span><Settings size={13} /> 环境变量</span>
              <button type="button" className="btn" style={{ fontSize: 12 }} onClick={() => setEnvs(e => [...e, mkEnv()])}>
                <Plus size={12} /> 添加
              </button>
            </div>
            {envs.length === 0 && (
              <div style={{ fontSize: 12, color: '#94a3b8', padding: '4px 0' }}>暂无环境变量</div>
            )}
            {envs.map((ev, i) => (
              <div key={ev.id} className="dm-run-list-row">
                <div className="dm-run-list-row-inputs">
                  <Field label="Key">
                    <input
                      value={ev.key} placeholder="MY_VAR" className="mono"
                      onChange={e => setEnvs(prev => prev.map((x, j) => j === i ? { ...x, key: e.target.value } : x))}
                    />
                  </Field>
                  <span style={{ alignSelf: 'flex-end', paddingBottom: 10, color: '#94a3b8', flexShrink: 0 }}>=</span>
                  <Field label="Value">
                    <input
                      value={ev.value} placeholder="value" className="mono"
                      onChange={e => setEnvs(prev => prev.map((x, j) => j === i ? { ...x, value: e.target.value } : x))}
                    />
                  </Field>
                </div>
                <button type="button" className="dm-btn-icon danger" title="删除" style={{ flexShrink: 0, alignSelf: 'flex-end', marginBottom: 6 }}
                  onClick={() => setEnvs(prev => prev.filter((_, j) => j !== i))}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>

          {/* ── CUDA / GPU 挂载 ── */}
          {hasCuda ? (
            <div className="dm-run-section">
              <div className="dm-run-section-title"><Cpu size={13} /> CUDA / GPU 挂载</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                <label className="dm-form-check">
                  <input type="radio" name="rcudaMode" checked={cudaMode === 'none'} onChange={() => setCudaMode('none')} />
                  不使用 GPU
                </label>
                <label className="dm-form-check">
                  <input type="radio" name="rcudaMode" checked={cudaMode === 'all'}
                    onChange={() => { setCudaMode('all'); setSelectedGpuIndices(availableGpus.map(g => g.index)); }} />
                  使用全部可用 GPU
                </label>
                <label className="dm-form-check">
                  <input type="radio" name="rcudaMode" checked={cudaMode === 'custom'} onChange={() => setCudaMode('custom')} />
                  自定义选择
                </label>
              </div>
              {cudaMode === 'custom' && (
                <div className="dm-roles-checklist">
                  {availableGpus.map((gpu) => (
                    <label key={gpu.index} className="dm-form-check">
                      <input type="checkbox"
                        checked={selectedGpuIndices.includes(gpu.index)}
                        onChange={(e) => setSelectedGpuIndices(prev =>
                          e.target.checked ? [...prev, gpu.index].sort((a, b) => a - b) : prev.filter(x => x !== gpu.index)
                        )}
                      />
                      <span className="dm-cuda-gpu-label">
                        <strong>GPU {gpu.index}</strong>
                        <span style={{ color: '#64748b', marginLeft: 4 }}>{gpu.name}</span>
                        <span style={{ color: '#94a3b8', fontSize: 11, marginLeft: 4 }}>{gpu.memoryTotal}</span>
                      </span>
                    </label>
                  ))}
                </div>
              )}
              {cudaMode !== 'none' && (
                <div style={{ marginTop: 6, fontSize: 12, color: '#64748b' }}>
                  将使用参数：<code style={{ background: '#f1f5f9', padding: '1px 5px', borderRadius: 3 }}>--gpus {buildGpusArg() || '（未选择）'}</code>
                </div>
              )}
            </div>
          ) : serverOverview?.cuda?.serverHasCuda && availableGpus.length === 0 ? (
            <div className="dm-run-section">
              <div className="dm-run-section-title"><Cpu size={13} /> CUDA / GPU 挂载</div>
              <div style={{ fontSize: 12, color: '#94a3b8' }}>您暂无 GPU 使用权限，请联系管理员分配。</div>
            </div>
          ) : null}

          {/* ── 额外参数 ── */}
          <div className="dm-run-section">
            <div className="dm-run-section-title">额外参数</div>
            <Field label="--flags（原样追加）" full>
              <input value={extraArgs} onChange={e => setExtraArgs(e.target.value)} placeholder="--memory=2g --cpus=2（可选）" />
            </Field>
          </div>

        </div>
      )}
    </Modal>
    {showCrossServer && (
      <CrossServerImageModal
        servers={servers}
        me={me}
        currentServerId={serverId}
        currentServerName={servers.find(s => s.id === serverId)?.name ?? serverId}
        imgQuotaExhausted={imgQuotaExhausted}
        onClose={() => setShowCrossServer(false)}
        onCopied={handleCrossServerCopied}
      />
    )}
    {volumeCopyMountId !== null && (
      <CrossServerVolumeModal
        servers={servers}
        me={me}
        currentServerId={serverId}
        currentServerName={servers.find(s => s.id === serverId)?.name ?? serverId}
        volQuotaExhausted={volQuotaExhausted}
        onClose={() => setVolumeCopyMountId(null)}
        onCopied={handleVolumeCopied}
      />
    )}
    {pathPickerMountId !== null && (
      <HostPathPickerModal
        serverId={serverId}
        initialPath={mounts.find(m => m.id === pathPickerMountId)?.source || '/'}
        filterStr={buildWhitelistFilter(quota?.ctr_path_whitelist)}
        onSelect={(p) => {
          setMounts(prev => prev.map(m => m.id === pathPickerMountId ? { ...m, source: p } : m));
          setPathPickerMountId(null);
        }}
        onClose={() => setPathPickerMountId(null)}
      />
    )}
    </>
  );
}

// ---- ComposeCreateModal ----

export function ComposeCreateModal({ serverId, serverOverview, onClose, onSuccess }: { serverId: string; serverOverview: ServerResourceOverview | null; onClose: () => void; onSuccess: () => void }) {
  const [yaml, setYaml] = useState('');
  const [project, setProject] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  // 容器配额是否已用满
  const ctrQuotaExhausted =
    serverOverview?.container?.remaining != null && serverOverview.container.remaining <= 0;

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
          <button className="btn btn-primary" onClick={submit} disabled={loading || ctrQuotaExhausted || !yaml.trim()}>
            {loading ? <Spin /> : <Layers size={14} />} 部署
          </button>
        </>
      }>
      {error && <Alert type="error">{error}</Alert>}
      {ctrQuotaExhausted && (
        <Alert type="error">容器数量配额已用满，无法创建新容器。请删除不再使用的容器或联系管理员调整配额。</Alert>
      )}
      <div className="dm-form-grid">
        <Field label="项目名称（可选）">
          <input value={project} onChange={(e) => setProject(e.target.value)} placeholder="自动生成" />
        </Field>
      </div>
      <Field label="docker-compose.yml 内容" full>
        <textarea className="mono" value={yaml} onChange={(e) => setYaml(e.target.value)}
          placeholder={"services:\n  web:\n    image: nginx:latest\n    ports:\n      - \"8080:80\""} style={{ minHeight: 280 }} />
      </Field>
      <Alert type="info">
        提示：若 YAML 中挂载了您仅有查看权限的卷（非所有者），系统将自动在对应挂载项后追加 <code>:ro</code> 强制只读。
      </Alert>
    </Modal>
  );
}

// ---- TemplateDeployModal ----

// 宿主机路径选择器弹窗：通过后端 browse-dirs API 逐层浏览服务器目录，类似 VSCode 选择文件夹
function HostPathPickerModal({
  serverId,
  initialPath,
  filterStr,
  onSelect,
  onClose,
}: {
  serverId: string;
  initialPath: string;
  filterStr: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [cwd, setCwd] = useState(initialPath || '/');
  const [dirs, setDirs] = useState<{ name: string; path: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const loadDir = useCallback((path: string) => {
    setLoading(true);
    setError('');
    apiGet<{ path: string; dirs: { name: string; path: string }[] }>(
      `${API}/servers/${serverId}/browse-dirs?path=${encodeURIComponent(path)}`,
    )
      .then((r) => {
        setCwd(r.path);
        setDirs(r.dirs || []);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : '加载目录失败');
        setDirs([]);
      })
      .finally(() => setLoading(false));
  }, [serverId]);

  useEffect(() => {
    loadDir(initialPath || '/');
  }, [loadDir, initialPath]);

  // 路径面包屑：将 cwd 拆分为可点击的层级
  const crumbs = cwd.split('/').filter(Boolean);

  // 判断某路径是否符合筛选条件（支持结构化筛选条件和旧文本格式）
  const pathAllowed = (p: string) => !filterStr || filterMatchesValue(filterStr, p, 'host_path') || filterMatchesValue(filterStr, p + '/', 'host_path') || filterMatchesValue(filterStr, p + '/*', 'host_path');

  return (
    <Modal title="选择宿主机路径" onClose={onClose} width={560}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {/* 面包屑导航 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap', fontSize: 12, color: '#526071' }}>
          <Folder size={13} />
          <span
            style={{ cursor: 'pointer', color: '#2563eb' }}
            onClick={() => loadDir('/')}
          >
            /
          </span>
          {crumbs.map((c, i) => {
            const p = '/' + crumbs.slice(0, i + 1).join('/');
            return (
              <span key={p} style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                <ChevronRight size={11} style={{ color: '#94a3b8' }} />
                <span
                  style={{ cursor: 'pointer', color: '#2563eb' }}
                  onClick={() => loadDir(p)}
                >
                  {c}
                </span>
              </span>
            );
          })}
        </div>

        {filterStr && (
          <div style={{ fontSize: 11, color: '#92400e', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 4, padding: '4px 8px' }}>
            筛选条件：{filterSummary(filterStr, 'host_path')}（仅允许选择匹配的路径）
          </div>
        )}

        {error && <Alert type="error">{error}</Alert>}

        {/* 目录列表 */}
        <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, maxHeight: 320, overflowY: 'auto', background: '#fff' }}>
          {loading ? (
            <div style={{ padding: 20, textAlign: 'center' }}><Spin /></div>
          ) : dirs.length === 0 ? (
            <div style={{ padding: 20, textAlign: 'center', color: '#94a3b8', fontSize: 12 }}>没有子目录</div>
          ) : (
            dirs.map((d) => (
              <div
                key={d.path}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '8px 12px',
                  borderBottom: '1px solid #f1f5f9',
                  cursor: 'pointer',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = '#f8fafc'; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = ''; }}
                onClick={() => loadDir(d.path)}
              >
                <Folder size={14} style={{ color: '#eab308', flexShrink: 0 }} />
                <span style={{ fontSize: 13, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                <span style={{ fontSize: 10, color: '#94a3b8', fontFamily: 'monospace' }}>{d.path}</span>
              </div>
            ))
          )}
        </div>

        {/* 当前路径与操作 */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            className="mono"
            value={cwd}
            onChange={(e) => setCwd(e.target.value)}
            style={{ flex: 1, minWidth: 200 }}
            placeholder="/path/to/dir"
          />
          <button
            className="btn"
            onClick={() => loadDir(cwd)}
          >
            前往
          </button>
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
          <button className="btn" onClick={onClose}>取消</button>
          <button
            className="btn btn-primary"
            disabled={!cwd.startsWith('/') || Boolean(filterStr && !pathAllowed(cwd))}
            onClick={() => onSelect(cwd)}
          >
            <CheckCircle size={14} /> 选择此路径
          </button>
        </div>
        {filterStr && !pathAllowed(cwd) && cwd.startsWith('/') && (
          <div style={{ fontSize: 11, color: '#dc2626' }}>当前路径「{cwd}」不在筛选范围「{filterSummary(filterStr, 'host_path')}」内，无法选择。</div>
        )}
      </div>
    </Modal>
  );
}

// 根据变量类型渲染对应的输入控件（筛选条件语义随类型变化）
function VariableInput({
  variable,
  value,
  onChange,
  images,
  volumes,
  gpus,
  serverId,
}: {
  variable: TemplateVariable;
  value: string;
  onChange: (v: string) => void;
  images: DockerImage[];
  volumes: DockerVolume[];
  gpus: GpuInfo[];
  serverId: string;
}) {
  const { type, filter, name, defaultValue } = variable;
  const filterStr = (filter || '').trim();
  const [showPathPicker, setShowPathPicker] = useState(false);

  // 镜像选择器：按筛选条件（通配符）过滤下拉选项
  if (type === 'image') {
    const filtered = filterStr
      ? images.filter((img) => filterMatchesValue(filter, `${img.repo}:${img.tag}`, 'image') || filterMatchesValue(filter, img.repo, 'image'))
      : images;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <select value={value} onChange={(e) => onChange(e.target.value)} style={{ width: '100%' }}>
          <option value="">— 选择镜像 —</option>
          {filtered.map((img) => (
            <option key={`${img.repo}:${img.tag}`} value={`${img.repo}:${img.tag}`}>
              {img.repo}:{img.tag} ({img.size})
            </option>
          ))}
        </select>
        {filterStr && (
          <span style={{ fontSize: 11, color: '#94a3b8' }}>筛选条件：{filterSummary(filter, 'image')}{filtered.length === 0 ? '（暂无匹配镜像）' : `（匹配 ${filtered.length} 个）`}</span>
        )}
        {!filterStr && filtered.length === 0 && (
          <span style={{ fontSize: 11, color: '#94a3b8' }}>暂无可用镜像</span>
        )}
      </div>
    );
  }

  // 卷选择器：按筛选条件（通配符）过滤下拉选项
  if (type === 'volume') {
    const selectedVolViewerOnly = isViewerOnlyVolume(value, volumes);
    const filtered = filterStr ? volumes.filter((vol) => filterMatchesValue(filter, vol.name, 'volume')) : volumes;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <select value={value} onChange={(e) => onChange(e.target.value)} style={{ width: '100%' }}>
          <option value="">— 选择卷 —</option>
          {filtered.map((vol) => (
            <option key={vol.name} value={vol.name}>{vol.name}{vol.canManage === false ? ' (只读)' : ''}</option>
          ))}
        </select>
        {selectedVolViewerOnly && (
          <span style={{ fontSize: 11, color: '#92400e' }}>您仅有此卷的查看权限，将强制只读挂载 (:ro)</span>
        )}
        {filterStr && (
          <span style={{ fontSize: 11, color: '#94a3b8' }}>筛选条件：{filterSummary(filter, 'volume')}{filtered.length === 0 ? '（暂无匹配卷）' : `（匹配 ${filtered.length} 个）`}</span>
        )}
        {!filterStr && filtered.length === 0 && (
          <span style={{ fontSize: 11, color: '#94a3b8' }}>暂无可用的卷</span>
        )}
      </div>
    );
  }

  // GPU 选择器：按筛选条件（通配符匹配 GPU 名称）过滤可选 GPU，复选框多选
  if (type === 'gpu') {
    const filtered = filterStr ? gpus.filter((g) => filterMatchesValue(filter, g.name, 'gpu')) : gpus;
    // 当前选中的索引集合：value 为 "all" 或逗号分隔索引
    let selectedIdx: number[] = [];
    if (value.trim().toLowerCase() === 'all') {
      selectedIdx = filtered.map((g) => g.index);
    } else if (value) {
      selectedIdx = value.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => Number.isInteger(n) && !Number.isNaN(n));
    }
    function toggleGpu(idx: number, checked: boolean) {
      const next = checked
        ? [...new Set([...selectedIdx, idx])].sort((a, b) => a - b)
        : selectedIdx.filter((x) => x !== idx);
      onChange(next.map(String).join(','));
    }
    // 全选：输出所有匹配 GPU 的索引（逗号分隔），便于配合 --gpus device={{name}} 使用
    const selectAll = () => onChange(filtered.map((g) => g.index).join(','));
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {filtered.length > 0 ? (
          <>
            <div className="dm-roles-checklist" style={{ maxHeight: 160, overflowY: 'auto' }}>
              {filtered.map((gpu) => (
                <label key={gpu.index} className="dm-form-check">
                  <input
                    type="checkbox"
                    checked={selectedIdx.includes(gpu.index)}
                    onChange={(e) => toggleGpu(gpu.index, e.target.checked)}
                  />
                  <span className="dm-cuda-gpu-label">
                    <strong>GPU {gpu.index}</strong>
                    <span style={{ color: '#64748b', marginLeft: 4 }}>{gpu.name}</span>
                    <span style={{ color: '#94a3b8', fontSize: 11, marginLeft: 4 }}>{gpu.memoryTotal}</span>
                  </span>
                </label>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 11, color: '#94a3b8', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="btn"
                style={{ fontSize: 11, padding: '2px 8px' }}
                onClick={selectAll}
              >
                全选
              </button>
              <button
                type="button"
                className="btn"
                style={{ fontSize: 11, padding: '2px 8px' }}
                onClick={() => onChange('')}
              >
                清空
              </button>
              <span>
                当前值：<code style={{ background: '#f1f5f9', padding: '1px 5px', borderRadius: 3 }}>{value || '（未选择）'}</code>
                （部署时替换到 <code style={{ background: '#f1f5f9', padding: '1px 5px', borderRadius: 3 }}>{`{{${name}}}`}</code>，建议配合 <code style={{ background: '#f1f5f9', padding: '1px 5px', borderRadius: 3 }}>{`--gpus device={{${name}}}`}</code> 使用）
              </span>
            </div>
          </>
        ) : (
          <span style={{ fontSize: 11, color: '#92400e' }}>
            {filterStr ? `筛选条件「${filterSummary(filter, 'gpu')}」下暂无匹配的 GPU` : '该服务器暂无可用 GPU'}
          </span>
        )}
        {filterStr && filtered.length > 0 && (
          <span style={{ fontSize: 11, color: '#94a3b8' }}>筛选条件：{filterSummary(filter, 'gpu')}（匹配 {filtered.length} 个）</span>
        )}
      </div>
    );
  }

  // 宿主路径选择器：文本框 + 浏览按钮（点选服务器目录），受筛选条件前缀约束
  if (type === 'host_path') {
    const err = validateVariableValue(variable, value);
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ display: 'flex', gap: 4 }}>
          <input
            className="mono"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={defaultValue || '/host/path'}
            style={{ flex: 1, minWidth: 0 }}
          />
          <button
            type="button"
            className="btn"
            style={{ flexShrink: 0 }}
            onClick={() => setShowPathPicker(true)}
          >
            <Folder size={13} /> 浏览
          </button>
        </div>
        {filterStr && !err && <span style={{ fontSize: 11, color: '#94a3b8' }}>允许范围：{filterSummary(filter, 'host_path')}（受您挂载白名单限制）</span>}
        {err && <span style={{ fontSize: 11, color: '#dc2626' }}>{err}</span>}
        {showPathPicker && (
          <HostPathPickerModal
            serverId={serverId}
            initialPath={value || extractPathPrefix(filter)}
            filterStr={filterStr}
            onSelect={(p) => { onChange(p); setShowPathPicker(false); }}
            onClose={() => setShowPathPicker(false)}
          />
        )}
      </div>
    );
  }

  // 容器内路径：纯文本输入，筛选条件为通配符
  if (type === 'docker_path') {
    const err = validateVariableValue(variable, value);
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <input
          className="mono"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={defaultValue || '/container/path'}
          style={{ width: '100%' }}
        />
        {filterStr && !err && <span style={{ fontSize: 11, color: '#94a3b8' }}>须匹配：{filterSummary(filter, 'docker_path')}</span>}
        {err && <span style={{ fontSize: 11, color: '#dc2626' }}>{err}</span>}
      </div>
    );
  }

  // 下拉选择：筛选条件解析为逗号分隔的允许选项
  if (type === 'select') {
    const options = filterStr.split(',').map((s) => s.trim()).filter(Boolean);
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <select value={value} onChange={(e) => onChange(e.target.value)} style={{ width: '100%' }}>
          <option value="">— 选择 —</option>
          {options.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        {options.length === 0 && (
          <span style={{ fontSize: 11, color: '#92400e' }}>未配置允许选项，请在模板中填写筛选条件</span>
        )}
      </div>
    );
  }

  // 多行文本：筛选条件为通配符，输入须匹配
  if (type === 'text') {
    const err = validateVariableValue(variable, value);
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <textarea
          className="mono"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={defaultValue || `请输入 ${name}`}
          style={{ minHeight: 60, width: '100%' }}
        />
        {filterStr && !err && <span style={{ fontSize: 11, color: '#94a3b8' }}>须匹配：{filterSummary(filter, 'text')}</span>}
        {err && <span style={{ fontSize: 11, color: '#dc2626' }}>{err}</span>}
      </div>
    );
  }

  // 端口号 / 数字：筛选条件为范围约束
  if (type === 'port' || type === 'number') {
    const rng = parseNumberRange(filterStr);
    const isPort = type === 'port';
    const baseMin = isPort ? 1 : undefined;
    const baseMax = isPort ? 65535 : undefined;
    const min = rng?.min ?? baseMin;
    const max = rng?.max ?? baseMax;
    const err = validateVariableValue(variable, value);
    const constraintLabel = filterStr
      ? `范围：${filterSummary(filter, type)}${isPort ? '（且 1-65535）' : ''}`
      : isPort ? '范围：1-65535' : '';
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <input
          type="number"
          min={min}
          max={max}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={defaultValue || (isPort ? '如 8080' : '0')}
          style={{ width: '100%' }}
        />
        {constraintLabel && !err && <span style={{ fontSize: 11, color: '#94a3b8' }}>{constraintLabel}</span>}
        {err && <span style={{ fontSize: 11, color: '#dc2626' }}>{err}</span>}
      </div>
    );
  }

  // 默认：单行文本（筛选条件为通配符，输入须匹配）
  const err = validateVariableValue(variable, value);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={defaultValue || `请输入 ${name}`}
        style={{ width: '100%' }}
      />
      {filterStr && !err && <span style={{ fontSize: 11, color: '#94a3b8' }}>须匹配：{filterSummary(filter, 'string')}</span>}
      {err && <span style={{ fontSize: 11, color: '#dc2626' }}>{err}</span>}
    </div>
  );
}

// 统一的变量字段：左上角变量名（蓝色徽标）+ 灰色说明，下方为占满一行的输入控件。
// 用于说明文档内联渲染与下方变量汇总区，保证视觉一致。
function VariableField({
  variable,
  value,
  onChange,
  images,
  volumes,
  gpus,
  serverId,
}: {
  variable: TemplateVariable;
  value: string;
  onChange: (v: string) => void;
  images: DockerImage[];
  volumes: DockerVolume[];
  gpus: GpuInfo[];
  serverId: string;
}) {
  return (
    <div className="dm-var-field">
      <div className="dm-var-field-head">
        <code className="dm-var-badge">{`{{${variable.name}}}`}</code>
        {variable.description && <span className="dm-var-desc">{variable.description}</span>}
      </div>
      <VariableInput
        variable={variable}
        value={value}
        onChange={onChange}
        images={images}
        volumes={volumes}
        gpus={gpus}
        serverId={serverId}
      />
    </div>
  );
}

export function TemplateDeployModal({ serverId, serverOverview, onClose, onSuccess }: {
  serverId: string;
  serverOverview: ServerResourceOverview | null;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState<TemplateDetail | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [projectName, setProjectName] = useState('');
  const [loading, setLoading] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  // 服务器上的镜像和卷列表（用于 image/volume 类型变量选择）
  const [images, setImages] = useState<DockerImage[]>([]);
  const [volumes, setVolumes] = useState<DockerVolume[]>([]);

  const availableGpus = serverOverview?.cuda?.availableGpus ?? [];
  const ctrQuotaExhausted =
    serverOverview?.container?.remaining != null && serverOverview.container.remaining <= 0;

  useEffect(() => {
    setLoading(true);
    apiGet<{ templates: Template[] }>(`${API}/templates`)
      .then((r) => setTemplates(r.templates))
      .catch(setError)
      .finally(() => setLoading(false));
  }, []);

  // 选中模板后加载详情、初始化变量值、拉取镜像/卷列表
  async function selectTemplate(id: string) {
    clearError();
    try {
      const r = await apiGet<{ template: TemplateDetail }>(`${API}/templates/${id}`);
      const tpl = r.template;
      setSelected(tpl);
      // 用变量默认值初始化 overrides
      const initOverrides: Record<string, string> = {};
      for (const v of tpl.variables ?? []) {
        initOverrides[v.name] = v.defaultValue || '';
      }
      setOverrides(initOverrides);
      setProjectName('');

      // 如果模板包含 image 或 volume 类型变量，预加载镜像/卷列表
      const hasImageVar = (tpl.variables ?? []).some((v) => v.type === 'image');
      const hasVolumeVar = (tpl.variables ?? []).some((v) => v.type === 'volume');
      if (hasImageVar) {
        apiGet<{ images: DockerImage[] }>(`${API}/servers/${serverId}/images`)
          .then((r) => setImages(r.images))
          .catch(() => {});
      }
      if (hasVolumeVar) {
        apiGet<{ volumes: DockerVolume[] }>(`${API}/servers/${serverId}/volumes`)
          .then((r) => setVolumes(r.volumes ?? []))
          .catch(() => {});
      }
    } catch (e) {
      setError(e);
    }
  }

  async function deploy() {
    if (!selected) return;
    if (hasInvalidVar) return;
    setDeploying(true);
    clearError();
    try {
      const payload: Record<string, unknown> = { ...overrides };
      if (selected.deployType === 'compose' && projectName.trim()) {
        payload._projectName = projectName.trim();
      }
      await apiPost(`${API}/servers/${serverId}/containers/from-template`, {
        templateId: selected.id,
        overrides: payload,
      });
      onSuccess();
    } catch (e) {
      setError(e);
    } finally {
      setDeploying(false);
    }
  }

  // 预览替换后的命令（仅用于显示）
  function previewCommand(): string {
    if (!selected) return '';
    let content = selected.rawContent;
    for (const v of selected.variables ?? []) {
      const val = overrides[v.name] ?? '';
      content = content.replace(new RegExp(`\\{\\{${v.name}\\}\\}`, 'g'), val || `{{${v.name}}}`);
    }
    return content;
  }

  const variables = selected?.variables ?? [];
  // 变量筛选条件校验：拦截不合规输入
  const invalidVarErrors = variables
    .map((v) => ({ name: v.name, msg: validateVariableValue(v, overrides[v.name] ?? '') }))
    .filter((x) => x.msg);
  const hasInvalidVar = invalidVarErrors.length > 0;
  // 说明文档中已内联引用的变量名集合（这些变量在文档中直接渲染为控件）
  const docInlineVarNames = new Set(selected?.docContent ? docReferencedVariables(selected.docContent) : []);
  const varMap = new Map(variables.map((v) => [v.name, v]));
  // 文档中引用但未在变量表中声明的占位符（原样提示）
  const docUnknownVars = selected?.docContent
    ? splitDocByVariables(selected.docContent).filter((s) => s.type === 'var' && !varMap.has(s.value)).map((s) => (s.type === 'var' ? s.value : ''))
    : [];
  // 未在文档中内联的变量 —— 用于决定变量汇总修改区是否默认展开
  const remainingVars = variables.filter((v) => !docInlineVarNames.has(v.name));

  return (
    <Modal title="从模板创建容器" onClose={onClose} wide
      foot={
        <>
          {selected && <button className="btn" onClick={() => setSelected(null)}>← 返回列表</button>}
          <button className="btn" onClick={onClose}>取消</button>
          {selected && (
            <button className="btn btn-primary" onClick={deploy} disabled={deploying || ctrQuotaExhausted || hasInvalidVar}>
              {deploying ? <Spin /> : <Play size={14} />} 部署
            </button>
          )}
        </>
      }>
      {error && <Alert type="error">{error}</Alert>}
      {hasInvalidVar && (
        <Alert type="error">
          以下变量输入不符合筛选条件，请修正后部署：
          <ul style={{ margin: '6px 0 0 18px' }}>
            {invalidVarErrors.map((x) => (
              <li key={x.name}><code>{`{{${x.name}}}`}</code> — {x.msg}</li>
            ))}
          </ul>
        </Alert>
      )}
      {ctrQuotaExhausted && (
        <Alert type="error">容器数量配额已用满，无法创建新容器。请删除不再使用的容器或联系管理员调整配额。</Alert>
      )}
      {serverOverview && <ResourceOverviewStrip overview={serverOverview} />}

      {!selected ? (
        loading ? <div className="dm-empty"><Spin /> 加载中…</div> :
        templates.length === 0 ? <div className="dm-empty"><ClipboardList size={32} /> 暂无模板</div> :
        <div style={{ display: 'grid', gap: 10 }}>
          {templates.map((t) => (
            <button key={t.id} className="dm-card" style={{ cursor: 'pointer', textAlign: 'left' }} onClick={() => selectTemplate(t.id)}>
              <div className="dm-card-header">
                <span className="dm-card-title">{t.name}</span>
                <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  <span className="dm-category-tag">{t.category}</span>
                  {t.deployType === 'compose'
                    ? <span className="dm-deploy-tag compose"><Layers size={10} /> compose</span>
                    : <span className="dm-deploy-tag run"><Code size={10} /> run</span>}
                  {t.hasDoc && <FileText size={14} style={{ color: '#94a3b8' }} />}
                </div>
              </div>
              {t.description && <span style={{ color: '#526071', fontSize: 13 }}>{t.description}</span>}
              {(t.variables?.length ?? 0) > 0 && (
                <span style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
                  <Code size={10} style={{ verticalAlign: 'middle' }} /> {t.variables.length} 个可配置变量
                </span>
              )}
            </button>
          ))}
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10, flexWrap: 'wrap' }}>
              <strong style={{ fontSize: 16 }}>{selected.name}</strong>
              <span className="dm-category-tag">{selected.category}</span>
              {selected.deployType === 'compose'
                ? <span className="dm-deploy-tag compose"><Layers size={11} /> compose</span>
                : <span className="dm-deploy-tag run"><Code size={11} /> run</span>}
            </div>
            {selected.description && <p style={{ color: '#526071', margin: '0 0 10px 0' }}>{selected.description}</p>}
            {selected.docContent && (
              <div className="dm-md-preview">
                {splitDocIntoBlocks(selected.docContent).map((blk, bi) => {
                  if (blk.kind === 'block') {
                    return <div key={bi} dangerouslySetInnerHTML={{ __html: blk.html }} />;
                  }
                  // 行内段落：文本段渲染为段落，变量段渲染为占满一行的统一控件块
                  const parts: React.ReactNode[] = [];
                  let textBuf = '';
                  let keyIdx = 0;
                  const flushText = () => {
                    if (textBuf.trim()) {
                      parts.push(
                        <p key={`t-${keyIdx++}`} dangerouslySetInnerHTML={{ __html: renderMarkdownInline(textBuf) }} />,
                      );
                    }
                    textBuf = '';
                  };
                  blk.segments.forEach((seg) => {
                    if (seg.type === 'text') {
                      textBuf += seg.value;
                    } else {
                      flushText();
                      const v = varMap.get(seg.value);
                      if (!v) {
                        parts.push(
                          <code key={`u-${keyIdx++}`} className="dm-md-unknown-var">{`{{${seg.value}}}`}</code>,
                        );
                      } else {
                        parts.push(
                          <VariableField
                            key={`v-${keyIdx++}`}
                            variable={v}
                            value={overrides[v.name] ?? ''}
                            onChange={(val) => setOverrides((p) => ({ ...p, [v.name]: val }))}
                            images={images}
                            volumes={volumes}
                            gpus={availableGpus}
                            serverId={serverId}
                          />,
                        );
                      }
                    }
                  });
                  flushText();
                  return <div key={bi}>{parts}</div>;
                })}
              </div>
            )}
            {docUnknownVars.length > 0 && (
              <Alert type="info">
                文档中包含未定义的变量占位符：<code>{docUnknownVars.map((n) => `{{${n}}}`).join('、')}</code>，将以原样文本展示，部署时不会替换。
              </Alert>
            )}
          </div>

          {/* 变量汇总修改区（折叠）：包含所有变量（文档中出现的 + 未在文档中出现的） */}
          {variables.length > 0 && (
            <details className="dm-var-summary" open={remainingVars.length > 0}>
              <summary>
                <Settings size={13} /> 变量汇总修改（共 {variables.length} 个{remainingVars.length > 0 ? `，含 ${remainingVars.length} 个未在文档中展示` : ''}）
              </summary>
              <div className="dm-var-summary-body">
                {variables.map((v) => (
                  <VariableField
                    key={v.name}
                    variable={v}
                    value={overrides[v.name] ?? ''}
                    onChange={(val) => setOverrides((p) => ({ ...p, [v.name]: val }))}
                    images={images}
                    volumes={volumes}
                    gpus={availableGpus}
                    serverId={serverId}
                  />
                ))}
              </div>
            </details>
          )}

          {/* compose 项目名称 */}
          {selected.deployType === 'compose' && (
            <Field label="项目名称（可选）">
              <input
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder="自动生成（默认使用模板名）"
              />
            </Field>
          )}

          {/* 最终命令/配置预览（run 与 compose 均展示） */}
          {selected.rawContent && (
            <details className="dm-cmd-preview">
              <summary>
                <Code size={12} style={{ verticalAlign: 'middle' }} /> {selected.deployType === 'compose' ? '预览最终 compose 配置' : '预览最终命令'}
              </summary>
              <pre className="dm-raw-content-preview" style={{ marginTop: 8 }}>{previewCommand()}</pre>
            </details>
          )}
        </div>
      )}
    </Modal>
  );
}

// ---- ContainersPanel ----

export function ContainersPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  const [serverId, setServerId] = useState<string | null>(servers[0]?.id ?? null);
  const [containers, setContainers] = useState<DockerContainer[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [logs, setLogs] = useState<{ id: string; name: string; text: string } | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [createMode, setCreateMode] = useState<CreateMode | null>(null);
  const [quota, setQuota] = useState<UserPerms | null>(null);
  const [serverOverview, setServerOverview] = useState<ServerResourceOverview | null>(null);
  // 容器详情弹窗
  const [detailTarget, setDetailTarget] = useState<DockerContainer | null>(null);
  const [detail, setDetail] = useState<ContainerDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  // 重启策略修改
  const [restartEditMode, setRestartEditMode] = useState(false);
  const [restartPolicy, setRestartPolicy] = useState('');
  const [savingRestart, setSavingRestart] = useState(false);
  const [restartMsg, setRestartMsg] = useState<string | null>(null);
  // 复制成功提示
  const [copyHint, setCopyHint] = useState<string | null>(null);

  const canCreate = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || !!s?.perms?.ctr_create || !!quota?.ctr_create;
  };

  // 服务器级别的全局管理权限（ctr_manage_all）
  const canManageAll = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || !!s?.perms?.ctr_manage_all || !!quota?.ctr_manage_all;
  };

  // 容器级别的管理权限：ctr_manage_all 或该容器的所有者
  const canManageContainer = (sid: string | null, c: DockerContainer) => {
    if (!sid) return false;
    if (canManageAll(sid)) return true;
    // 检查当前用户是否是该容器的所有者
    return !!c.ownerUserId && c.ownerUserId === me.id;
  };

  const load = useCallback(async (sid: string) => {
    setLoading(true);
    clearError();
    try {
      const [cr, qr, ovr] = await Promise.all([
        apiGet<{ containers: DockerContainer[] }>(`${API}/servers/${sid}/containers`),
        apiGet<UserPerms>(`${API}/servers/${sid}/my-quota`),
        apiGet<ServerResourceOverview>(`${API}/servers/${sid}/resource-overview`).catch(() => null),
      ]);
      setContainers(cr.containers);
      setQuota(qr);
      setServerOverview(ovr);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { if (serverId) void load(serverId); }, [serverId, load]);

  async function doRefresh() {
    if (!serverId) return;
    setLoading(true);
    clearError();
    try {
      await apiPost(`${API}/servers/${serverId}/df-cache/refresh`, {});
      await load(serverId);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }

  async function doAction(containerId: string, containerName: string, action: string) {
    if (!serverId) return;
    if (action === 'remove' && !confirm(`确定删除容器 ${containerName}？`)) return;
    clearError();
    try {
      await apiPost(`${API}/servers/${serverId}/containers/${containerId}/action`, { action });
      void load(serverId);
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
    setRestartEditMode(false);
    setRestartMsg(null);
  }

  async function saveRestartPolicy() {
    if (!serverId || !detail) return;
    setSavingRestart(true);
    setRestartMsg(null);
    try {
      await apiPut(`${API}/servers/${serverId}/containers/${encodeURIComponent(detail.shortId)}/restart-policy`, { policy: restartPolicy });
      setRestartMsg('重启策略已更新');
      setRestartEditMode(false);
      setDetail((d) => d ? { ...d, restartPolicy } : d);
      setTimeout(() => setRestartMsg(null), 2500);
    } catch (e) {
      setRestartMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingRestart(false);
    }
  }

  function copyText(text: string, hint: string) {
    navigator.clipboard.writeText(text).then(() => {
      setCopyHint(hint);
      setTimeout(() => setCopyHint(null), 2000);
    }).catch(() => {
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

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <ServerSelector servers={servers} selected={serverId} onSelect={(id) => { setServerId(id); setContainers([]); setQuota(null); setServerOverview(null); }} />
      {serverId && (
        <ResourceUsagePanel overview={serverOverview} resourceType="container" loading={loading && !serverOverview} />
      )}
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
          <button className="btn" onClick={doRefresh} disabled={loading}><RefreshCw size={14} /> 刷新</button>
        </div>
      )}

      {loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : containers.length === 0 ? (
        <div className="dm-empty"><Box size={32} /> 暂无容器</div>
      ) : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '0.7fr 1.5fr 1.5fr 1fr 1.2fr' }}>
            <span>状态</span><span>名称</span><span>端口</span><span>SSH 端口</span><span>操作</span>
          </div>
          {containers.map((c) => {
            const state = (c.State ?? c.Status ?? '').toLowerCase();
            const isRunning = state.includes('running') || state.startsWith('up');
            const { label: stateLabel } = parseContainerStatus(c.Status ?? c.State ?? '');
            const sshPort = parseSshPort(c.Ports);
            const server = servers.find((s) => s.id === serverId);
            const sshCmd = sshPort && server
              ? `ssh -p ${sshPort} root@${server.host}`
              : null;
            return (
              <div key={cid(c)} className="dm-table-row" style={{ gridTemplateColumns: '0.7fr 1.5fr 1.5fr 1fr 1.2fr' }}>
                {/* 状态 */}
                <span>
                  <span className={`dm-status ${containerStateClass(state)}`}>
                    <span className="dm-status-dot" />
                    {stateLabel}
                  </span>
                </span>
                {/* 名称 */}
                <span style={{ fontWeight: 600, minWidth: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <TruncText text={cname(c)} />
                  {c.isPublic && <span style={{ fontSize: 10, color: '#059669', border: '1px solid #a7f3d0', background: '#ecfdf5', padding: '1px 5px', borderRadius: 4, whiteSpace: 'nowrap' }}>公开</span>}
                </span>
                {/* 端口 */}
                <span style={{ minWidth: 0 }}>{formatPortTags(c.Ports)}</span>
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
                  {canManageContainer(serverId, c) && (
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
                  {(canManageAll(serverId) || (detail.platformMeta.ownerUserId && detail.platformMeta.ownerUserId === me.id)) && (
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
                    {detail.platformMeta.canSeeImage
                      ? <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{detail.image || '—'}</span>
                      : <span style={{ fontSize: 12, color: '#94a3b8', fontStyle: 'italic' }}>（无镜像查看权限）</span>}</div>
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
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <span style={{ color: '#94a3b8' }}>重启策略：</span>
                    {restartEditMode ? (
                      <>
                        <select
                          value={restartPolicy}
                          onChange={(e) => setRestartPolicy(e.target.value)}
                          style={{ fontSize: 12, padding: '2px 6px', borderRadius: 5, border: '1px solid #d0d9e4', background: '#f8fafc', minWidth: 140 }}
                        >
                          <option value="no">不重启 (no)</option>
                          <option value="always">always（始终重启）</option>
                          <option value="unless-stopped">unless-stopped</option>
                          <option value="on-failure">on-failure</option>
                        </select>
                        <button className="btn btn-primary" style={{ padding: '2px 10px', fontSize: 12, minHeight: 26 }} onClick={saveRestartPolicy} disabled={savingRestart}>
                          {savingRestart ? <Spin /> : '保存'}
                        </button>
                        <button className="btn" style={{ padding: '2px 8px', fontSize: 12, minHeight: 26 }} onClick={() => { setRestartEditMode(false); setRestartMsg(null); }}>取消</button>
                      </>
                    ) : (
                      <>
                        <span>{detail.restartPolicy || '不重启'}</span>
                        {(canManageAll(serverId) || (detail.platformMeta.ownerUserId && detail.platformMeta.ownerUserId === me.id)) && (
                          <button
                            className="dm-btn-icon"
                            style={{ width: 22, height: 22 }}
                            title="修改重启策略"
                            onClick={() => { setRestartPolicy(detail.restartPolicy || 'no'); setRestartEditMode(true); setRestartMsg(null); }}
                          >
                            <Pencil size={11} />
                          </button>
                        )}
                      </>
                    )}
                    {restartMsg && (
                      <span style={{ fontSize: 12, color: restartMsg.includes('已更新') ? '#22c55e' : '#ef4444' }}>{restartMsg}</span>
                    )}
                  </div>
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

              {/* ── SSH 连接 ── */}
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
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 80px', gap: '2px 0', marginTop: 6, fontSize: 12 }}>
                    {/* 表头 */}
                    <div style={{ padding: '4px 8px', background: '#f1f5f9', borderRadius: '6px 0 0 0', color: '#526071', fontWeight: 700, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.04em' }}>主机端口</div>
                    <div style={{ padding: '4px 8px', background: '#f1f5f9', color: '#526071', fontWeight: 700, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.04em' }}>容器端口</div>
                    <div style={{ padding: '4px 8px', background: '#f1f5f9', borderRadius: '0 6px 0 0', color: '#526071', fontWeight: 700, fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.04em' }}>类型</div>
                    {/* 数据行 */}
                    {detail.ports.map((p, i) => {
                      const isLast = i === detail.ports.length - 1;
                      const hostDisplay = p.hostPort
                        ? (p.hostIp && p.hostIp !== '0.0.0.0' ? `${p.hostIp}:${p.hostPort}` : p.hostPort)
                        : '未绑定';
                      const protocol = p.containerPort?.includes('/') ? p.containerPort.split('/')[1] : 'tcp';
                      const containerPort = p.containerPort?.includes('/') ? p.containerPort.split('/')[0] : p.containerPort;
                      return (
                        <>
                          <div key={`h-${i}`} style={{ padding: '5px 8px', background: i % 2 === 0 ? '#ffffff' : '#f8fafc', borderBottom: isLast ? 'none' : '1px solid #f1f5f9', borderBottomLeftRadius: isLast ? 6 : 0, fontFamily: 'monospace', color: p.hostPort ? '#1e293b' : '#94a3b8' }}>{hostDisplay}</div>
                          <div key={`c-${i}`} style={{ padding: '5px 8px', background: i % 2 === 0 ? '#ffffff' : '#f8fafc', borderBottom: isLast ? 'none' : '1px solid #f1f5f9', fontFamily: 'monospace', fontWeight: 600, color: '#1e293b' }}>{containerPort}</div>
                          <div key={`t-${i}`} style={{ padding: '5px 8px', background: i % 2 === 0 ? '#ffffff' : '#f8fafc', borderBottom: isLast ? 'none' : '1px solid #f1f5f9', borderBottomRightRadius: isLast ? 6 : 0 }}>
                            <span style={{ background: protocol === 'udp' ? '#fef3c7' : '#dbeafe', color: protocol === 'udp' ? '#92400e' : '#1d4ed8', padding: '1px 6px', borderRadius: 4, fontSize: 11, fontWeight: 600 }}>{protocol.toUpperCase()}</span>
                          </div>
                        </>
                      );
                    })}
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
                        {m.type === 'volume' && !m.canSeeVolume ? (
                          <div style={{ color: '#94a3b8', marginTop: 3, fontStyle: 'italic', fontSize: 11 }}>（无卷查看权限）</div>
                        ) : (m.source || m.name) ? (
                          <div style={{ color: '#94a3b8', marginTop: 3, fontFamily: 'monospace', fontSize: 11 }}>
                            {m.name ? `卷: ${m.name}` : `主机: ${m.source}`}
                          </div>
                        ) : null}
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
        <RunCreateModal serverId={serverId} servers={servers} me={me} quota={quota} serverOverview={serverOverview} onClose={() => setCreateMode(null)} onSuccess={() => { setCreateMode(null); void load(serverId!); }} />
      )}
      {createMode === 'compose' && serverId && (
        <ComposeCreateModal serverId={serverId} serverOverview={serverOverview} onClose={() => setCreateMode(null)} onSuccess={() => { setCreateMode(null); void load(serverId!); }} />
      )}
      {createMode === 'template' && serverId && (
        <TemplateDeployModal serverId={serverId} serverOverview={serverOverview} onClose={() => setCreateMode(null)} onSuccess={() => { setCreateMode(null); void load(serverId!); }} />
      )}
    </div>
  );
}
