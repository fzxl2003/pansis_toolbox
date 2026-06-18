// ============================================================
// Containers Panel — Docker Manager
// ============================================================

import { FormEvent, useCallback, useEffect, useState } from 'react';
import {
  Box,
  CheckCircle,
  ClipboardList,
  Copy,
  FileText,
  Globe,
  HardDrive,
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
import { Alert, Field, Modal, ServerSelector, Spin, TruncText } from './components';
import { API, containerStateClass, parseContainerStatus, renderMarkdown, useErrorMsg } from './utils';
import type {
  ContainerDetail,
  CreateMode,
  DmServer,
  DockerContainer,
  Template,
  TemplateDetail,
  UserQuota,
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

  const protoColor: Record<string, { bg: string; color: string; border: string }> = {
    tcp:  { bg: '#dbeafe', color: '#1d4ed8', border: '#bfdbfe' },
    udp:  { bg: '#fef3c7', color: '#92400e', border: '#fde68a' },
    sctp: { bg: '#f3e8ff', color: '#7c3aed', border: '#e9d5ff' },
  };

  const visible = entries.slice(0, 3);
  const rest = entries.length - visible.length;

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

// ---- RunCreateModal ----

export function RunCreateModal({ serverId, quota, onClose, onSuccess }: { serverId: string; quota: UserQuota | null; onClose: () => void; onSuccess: () => void }) {
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

export function TemplateDeployModal({ serverId, onClose, onSuccess }: { serverId: string; onClose: () => void; onSuccess: () => void }) {
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

// ---- ContainersPanel ----

export function ContainersPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
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
