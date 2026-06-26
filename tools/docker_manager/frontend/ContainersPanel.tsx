// ============================================================
// Containers Panel — Docker Manager
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import {
  Box,
  CheckCircle,
  ClipboardList,
  Copy,
  Cpu,
  Database,
  FileText,
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
import { API, containerStateClass, formatSize, parseContainerStatus, renderMarkdown, useErrorMsg } from './utils';
import type {
  ContainerDetail,
  CreateMode,
  DmServer,
  DockerContainer,
  DockerImage,
  DockerVolume,
  ServerResourceOverview,
  Template,
  TemplateDetail,
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
  const unlimitedVol = vol.quotaGb === 0;

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

// 将界面表单组装成 docker run 命令字符串（单向，仅用于预览/命令行模式）
function buildDockerCmd(p: {
  image: string; name: string; restart: string; network: string; command: string;
  ports: PortEntry[]; mounts: MountEntry[]; envs: EnvEntry[];
  gpus: string; extra_args: string;
}): string {
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
      const volName = m.source || m.newVolName;
      if (volName && m.target) parts.push('-v', `${volName}:${m.target}${m.ro ? ':ro' : ''}`);
    }
  }
  for (const e of p.envs) {
    if (e.key) parts.push('-e', `${e.key}=${e.value}`);
  }
  if (p.gpus) parts.push('--gpus', p.gpus);
  if (p.extra_args) parts.push(...p.extra_args.trim().split(/\s+/));
  if (p.image) parts.push(p.image);
  if (p.command) parts.push(...p.command.trim().split(/\s+/));
  return parts.join(' ');
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

  // 镜像配额是否已用满（remainingGb=null 表示不限）
  const imgQuotaExhausted =
    serverOverview?.image?.remainingGb != null && serverOverview.image.remainingGb <= 0;

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
          const v = m.source || m.newVolName;
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

  const canSubmitGui = imageMode === 'input' ? !!imageInput.trim() : !!image;

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
            disabled={loading || (mode === 'gui' && !canSubmitGui)}
          >
            {loading ? <Spin /> : <Play size={14} />} 创建
          </button>
        </>
      }>
      {error && <Alert type="error">{error}</Alert>}
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
                      <input
                        value={m.source} placeholder="/data/myapp"
                        onChange={e => setMounts(prev => prev.map((x, j) => j === i ? { ...x, source: e.target.value } : x))}
                      />
                    </Field>
                  ) : (
                    <Field label="卷名称 *" style={{ width: 160, flexShrink: 0 }}>
                      <select
                        value={m.source}
                        onChange={e => setMounts(prev => prev.map((x, j) => j === i ? { ...x, source: e.target.value, newVolName: '' } : x))}
                        style={{ width: '100%' }}
                      >
                        <option value="">— 选择已有卷 —</option>
                        {availVolumes.map(v => (
                          <option key={v.name} value={v.name}>{v.name}</option>
                        ))}
                        <option value="__new__">+ 新建卷…</option>
                      </select>
                      {m.source === '__new__' && (
                        <input
                          style={{ marginTop: 4, width: '100%' }}
                          value={m.newVolName} placeholder="new-volume-name"
                          onChange={e => setMounts(prev => prev.map((x, j) => j === i ? { ...x, newVolName: e.target.value } : x))}
                        />
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
                        <input type="checkbox" checked={m.ro}
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
    </>
  );
}

// ---- ComposeCreateModal ----

export function ComposeCreateModal({ serverId, onClose, onSuccess }: { serverId: string; onClose: () => void; onSuccess: () => void }) {
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

// ---- TemplateDeployModal ----

export function TemplateDeployModal({ serverId, serverOverview, onClose, onSuccess }: {
  serverId: string;
  serverOverview: ServerResourceOverview | null;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState<TemplateDetail | null>(null);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  // CUDA 挂载选项
  const [selectedGpuIndices, setSelectedGpuIndices] = useState<number[]>([]);
  const [cudaMode, setCudaMode] = useState<'none' | 'all' | 'custom'>('none');

  const availableGpus = serverOverview?.cuda?.availableGpus ?? [];
  const hasCuda = (serverOverview?.cuda?.serverHasCuda ?? false) && availableGpus.length > 0;

  function buildGpusArg(): string {
    if (cudaMode === 'none') return '';
    if (cudaMode === 'all') return 'all';
    if (selectedGpuIndices.length === 0) return '';
    return `"device=${selectedGpuIndices.join(',')}"`;
  }

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
        gpus: buildGpusArg(),
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
      {/* 资源概览提示条 */}
      {serverOverview && <ResourceOverviewStrip overview={serverOverview} />}

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

          {/* CUDA 挂载区域 */}
          {hasCuda ? (
            <div className="dm-perm-section">
              <div className="dm-perm-section-title"><Cpu size={13} /> CUDA / GPU 挂载</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                <label className="dm-form-check">
                  <input type="radio" name="tplCudaMode" checked={cudaMode === 'none'} onChange={() => setCudaMode('none')} />
                  不使用 GPU
                </label>
                <label className="dm-form-check">
                  <input type="radio" name="tplCudaMode" checked={cudaMode === 'all'} onChange={() => { setCudaMode('all'); setSelectedGpuIndices(availableGpus.map(g => g.index)); }} />
                  使用全部可用 GPU
                </label>
                <label className="dm-form-check">
                  <input type="radio" name="tplCudaMode" checked={cudaMode === 'custom'} onChange={() => setCudaMode('custom')} />
                  自定义选择
                </label>
              </div>
              {cudaMode === 'custom' && (
                <div className="dm-roles-checklist">
                  {availableGpus.map((gpu) => (
                    <label key={gpu.index} className="dm-form-check">
                      <input
                        type="checkbox"
                        checked={selectedGpuIndices.includes(gpu.index)}
                        onChange={(e) => {
                          setSelectedGpuIndices((prev) =>
                            e.target.checked
                              ? [...prev, gpu.index].sort((a, b) => a - b)
                              : prev.filter((i) => i !== gpu.index)
                          );
                        }}
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
            <div className="dm-perm-section">
              <div className="dm-perm-section-title"><Cpu size={13} /> CUDA / GPU 挂载</div>
              <div style={{ fontSize: 12, color: '#94a3b8' }}>您暂无 GPU 使用权限，请联系管理员分配。</div>
            </div>
          ) : null}
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

  const canManage = (sid: string | null) => {
    if (!sid) return false;
    const s = servers.find((x) => x.id === sid);
    return me.role === 'admin' || !!s?.perms?.ctr_manage_all || !!quota?.ctr_manage_all;
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
          <button className="btn" onClick={() => serverId && load(serverId)} disabled={loading}><RefreshCw size={14} /> 刷新</button>
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
                <span style={{ fontWeight: 600, minWidth: 0 }}>
                  <TruncText text={cname(c)} />
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
                        {canManage(serverId) && (
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
        <RunCreateModal serverId={serverId} servers={servers} me={me} quota={quota} serverOverview={serverOverview} onClose={() => setCreateMode(null)} onSuccess={() => { setCreateMode(null); void load(serverId!); }} />
      )}
      {createMode === 'compose' && serverId && (
        <ComposeCreateModal serverId={serverId} onClose={() => setCreateMode(null)} onSuccess={() => { setCreateMode(null); void load(serverId!); }} />
      )}
      {createMode === 'template' && serverId && (
        <TemplateDeployModal serverId={serverId} serverOverview={serverOverview} onClose={() => setCreateMode(null)} onSuccess={() => { setCreateMode(null); void load(serverId!); }} />
      )}
    </div>
  );
}