// ============================================================
// TensorBoard Dashboard Tool — Sessions Management Panel
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import {
  CheckCircle,
  ChevronRight,
  ExternalLink,
  Folder,
  Loader2,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Square,
  Trash2,
  BarChart3,
} from 'lucide-react';

import { apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { Alert, Badge, EmptyState, Field, Modal, Spin, useConfirm } from './components';
import { API, formatRelativeTime, messageFromError, statusColor, statusLabel } from './utils';
import type { SessionForm, TbServer, TbSession } from './types';
import { EMPTY_SESSION_FORM } from './types';

export type SessionsPanelProps = {
  servers: TbServer[];
  serversLoading: boolean;
};

export function SessionsPanel({ servers, serversLoading }: SessionsPanelProps) {
  const [sessions, setSessions] = useState<TbSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editingSession, setEditingSession] = useState<TbSession | null>(null);
  const { confirm, dialog } = useConfirm();

  async function loadSessions() {
    setLoading(true);
    setError('');
    try {
      const r = await apiGet<{ sessions: TbSession[] }>(`${API}/sessions`);
      setSessions(r.sessions);
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadSessions(); }, []);

  function serverName(serverId: string): string {
    return servers.find((s) => s.id === serverId)?.name || '未知服务器';
  }

  async function handleStop(session: TbSession) {
    try {
      await apiPost(`${API}/sessions/${session.id}/stop`, {});
      await loadSessions();
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  async function handleRestart(session: TbSession) {
    try {
      await apiPost(`${API}/sessions/${session.id}/restart`, {});
      await loadSessions();
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  async function handleCheck(session: TbSession) {
    try {
      await apiPost(`${API}/sessions/${session.id}/check`, {});
      await loadSessions();
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  function handleDelete(session: TbSession) {
    confirm({
      title: '删除会话',
      message: `确认删除会话「${session.name}」的记录？`,
      onConfirm: async () => {
        try {
          await apiDelete(`${API}/sessions/${session.id}`);
          await loadSessions();
        } catch (exc) {
          setError(messageFromError(exc));
        }
      },
    });
  }

  function handleOpen(session: TbSession) {
    window.open(session.url, '_blank');
  }

  return (
    <div className="tb-panel">
      <div className="tb-toolbar">
        <div className="tb-toolbar-left">
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}><BarChart3 size={18} /> TensorBoard 会话</h2>
        </div>
        <div className="tb-toolbar-right">
          <button className="tb-btn tb-btn-secondary" onClick={() => void loadSessions()} type="button" disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} /> 刷新
          </button>
          <button className="tb-btn tb-btn-primary" onClick={() => setShowForm(true)} type="button" disabled={serversLoading || servers.length === 0}>
            <Plus size={14} /> 启动 TensorBoard
          </button>
        </div>
      </div>

      {error && <Alert type="error">{error}</Alert>}

      {loading ? (
        <div className="tb-loading-overlay"><Spin /> 加载会话列表…</div>
      ) : sessions.length === 0 ? (
        <EmptyState
          icon={<BarChart3 size={32} />}
          title="暂无 TensorBoard 会话"
          hint={servers.length === 0 ? '请先在「服务器」页添加 SSH 服务器' : '点击「启动 TensorBoard」开始'}
        />
      ) : (
        <div className="tb-table-wrap">
          <table className="tb-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>服务器</th>
                <th>日志路径</th>
                <th>状态</th>
                <th>启动时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((session) => (
                <SessionRow
                  key={session.id}
                  session={session}
                  serverName={serverName(session.serverId)}
                  onOpen={() => handleOpen(session)}
                  onStop={() => void handleStop(session)}
                  onRestart={() => void handleRestart(session)}
                  onCheck={() => void handleCheck(session)}
                  onDelete={() => handleDelete(session)}
                  onEdit={() => setEditingSession(session)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showForm && (
        <SessionFormModal
          servers={servers}
          onClose={() => setShowForm(false)}
          onSaved={() => { setShowForm(false); void loadSessions(); }}
        />
      )}
      {editingSession && (
        <SessionEditModal
          session={editingSession}
          servers={servers}
          onClose={() => setEditingSession(null)}
          onSaved={() => { setEditingSession(null); void loadSessions(); }}
          onSavedAndRestart={async () => {
            setEditingSession(null);
            try {
              await apiPost(`${API}/sessions/${editingSession.id}/restart`, {});
              await loadSessions();
            } catch (exc) {
              setError(messageFromError(exc));
            }
          }}
        />
      )}
      {dialog}
    </div>
  );
}

// ============================================================
// Session Row
// ============================================================

type SessionRowProps = {
  session: TbSession;
  serverName: string;
  onOpen: () => void;
  onStop: () => void;
  onRestart: () => void;
  onCheck: () => void;
  onDelete: () => void;
  onEdit: () => void;
};

function SessionRow({ session, serverName, onOpen, onStop, onRestart, onCheck, onDelete, onEdit }: SessionRowProps) {
  const [stopping, setStopping] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const isActive = session.status === 'running' || session.status === 'starting';

  async function handleStop() {
    setStopping(true);
    try { await onStop(); } finally { setStopping(false); }
  }

  async function handleRestart() {
    setRestarting(true);
    try { await onRestart(); } finally { setRestarting(false); }
  }

  return (
    <tr>
      <td style={{ fontWeight: 600 }}>{session.name}</td>
      <td>{serverName}</td>
      <td><span className="tb-code" title={session.logdir}>{session.logdir}</span></td>
      <td>
        <Badge color={statusColor(session.status)}>{statusLabel(session.status)}</Badge>
        {session.error && (
          <div className="tb-session-error">{session.error}</div>
        )}
      </td>
      <td style={{ fontSize: 12, color: '#94a3b8' }}>{formatRelativeTime(session.startedAt)}</td>
      <td>
        <div className="tb-table-actions">
          {isActive && (
            <button className="tb-btn tb-btn-sm tb-btn-primary" onClick={onOpen} type="button" title="打开 TensorBoard">
              <ExternalLink size={12} /> 打开
            </button>
          )}
          {isActive && (
            <button className="tb-btn tb-btn-sm tb-btn-ghost" onClick={handleStop} type="button" disabled={stopping} title="停止">
              {stopping ? <Loader2 size={12} className="spin" /> : <Square size={12} />}
            </button>
          )}
          {!isActive && (
            <>
              <button className="tb-btn tb-btn-sm tb-btn-primary" onClick={handleRestart} type="button" disabled={restarting} title="重新启动">
                {restarting ? <Loader2 size={12} className="spin" /> : <Play size={12} />} 重启
              </button>
              <button className="tb-btn tb-btn-sm tb-btn-ghost" onClick={onEdit} type="button" title="编辑">
                <Pencil size={12} /> 编辑
              </button>
            </>
          )}
          <button className="tb-btn tb-btn-sm tb-btn-ghost" onClick={onCheck} type="button" title="检查状态">
            <RefreshCw size={12} />
          </button>
          <button className="tb-btn tb-btn-sm tb-btn-ghost" onClick={onDelete} type="button" title="删除" style={{ color: '#dc2626' }}>
            <Trash2 size={12} />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ============================================================
// Shared: Conda env dropdown hook
// ============================================================

function useCondaEnvs(serverId: string, pythonMode: string, servers: TbServer[]) {
  const [condaEnvs, setCondaEnvs] = useState<string[]>([]);
  const [condaLoading, setCondaLoading] = useState(false);
  const [condaError, setCondaError] = useState('');
  const selectedServer = servers.find((s) => s.id === serverId);
  const hasCondaBase = !!selectedServer?.condaBasePath;

  useEffect(() => {
    if (pythonMode !== 'conda' || !serverId || !hasCondaBase) {
      setCondaEnvs([]);
      setCondaError('');
      return;
    }
    setCondaLoading(true);
    setCondaError('');
    apiGet<{ envs: string[]; error?: string }>(`${API}/servers/${serverId}/conda-envs`)
      .then((r) => {
        setCondaEnvs(r.envs || []);
        if (r.error) setCondaError(r.error);
      })
      .catch((exc) => setCondaError(messageFromError(exc)))
      .finally(() => setCondaLoading(false));
  }, [serverId, pythonMode, hasCondaBase]);

  return { condaEnvs, condaLoading, condaError, hasCondaBase };
}

// ============================================================
// Shared: Host path picker modal
// ============================================================

function HostPathPickerModal({
  serverId,
  initialPath,
  onSelect,
  onClose,
}: {
  serverId: string;
  initialPath: string;
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

  const crumbs = cwd.split('/').filter(Boolean);

  return (
    <Modal title="选择日志路径" onClose={onClose} width={560}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {/* 面包屑导航 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap', fontSize: 12, color: '#526071' }}>
          <Folder size={13} />
          <span style={{ cursor: 'pointer', color: '#4f46e5' }} onClick={() => loadDir('/')}>/</span>
          {crumbs.map((c, i) => {
            const p = '/' + crumbs.slice(0, i + 1).join('/');
            return (
              <span key={p} style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                <ChevronRight size={11} style={{ color: '#94a3b8' }} />
                <span style={{ cursor: 'pointer', color: '#4f46e5' }} onClick={() => loadDir(p)}>{c}</span>
              </span>
            );
          })}
        </div>

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
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            className="tb-input"
            value={cwd}
            onChange={(e) => setCwd(e.target.value)}
            style={{ flex: 1, minWidth: 200 }}
            placeholder="/path/to/dir"
          />
          <button
            className="tb-btn tb-btn-secondary"
            onClick={() => loadDir(cwd)}
            type="button"
          >
            前往
          </button>
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
          <button className="tb-btn tb-btn-secondary" onClick={onClose} type="button">取消</button>
          <button
            className="tb-btn tb-btn-primary"
            disabled={!cwd.startsWith('/')}
            onClick={() => onSelect(cwd)}
            type="button"
          >
            <CheckCircle size={14} /> 选择此路径
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ============================================================
// Shared: Session form fields
// ============================================================

function SessionFormFields({
  form, setForm, servers, condaState,
}: {
  form: SessionForm;
  setForm: (f: SessionForm) => void;
  servers: TbServer[];
  condaState: ReturnType<typeof useCondaEnvs>;
}) {
  const { condaEnvs, condaLoading, condaError, hasCondaBase } = condaState;
  const [showPathPicker, setShowPathPicker] = useState(false);
  const [envChecking, setEnvChecking] = useState(false);
  const [envResult, setEnvResult] = useState<{
    ok: boolean;
    hasPython?: boolean;
    hasTensorboard?: boolean;
    pythonVersion?: string;
    tensorboardVersion?: string;
    error?: string;
  } | null>(null);

  async function handleCheckEnv() {
    if (!form.serverId) return;
    setEnvChecking(true);
    setEnvResult(null);
    try {
      const r = await apiPost<{
        ok: boolean;
        hasPython?: boolean;
        hasTensorboard?: boolean;
        pythonVersion?: string;
        tensorboardVersion?: string;
        error?: string;
      }>(`${API}/servers/${form.serverId}/check-python-env`, {
        pythonMode: form.pythonMode,
        condaEnv: form.condaEnv,
        pythonPath: form.pythonPath,
      });
      setEnvResult(r);
    } catch (exc) {
      setEnvResult({ ok: false, error: messageFromError(exc) });
    } finally {
      setEnvChecking(false);
    }
  }

  // Auto-select first env if current selection is not in the list
  useEffect(() => {
    if (form.pythonMode === 'conda' && condaEnvs.length > 0 && !condaEnvs.includes(form.condaEnv)) {
      setForm({ ...form, condaEnv: condaEnvs[0] });
    }
  }, [condaEnvs]);

  // If the selected server has no conda base path, force 'path' mode
  useEffect(() => {
    if (form.pythonMode === 'conda' && !hasCondaBase) {
      setForm({ ...form, pythonMode: 'path' });
    }
  }, [hasCondaBase]);

  return (
    <div className="tb-form-grid">
      <Field label="服务器" full>
        <select className="tb-select" value={form.serverId} onChange={(e) => setForm({ ...form, serverId: e.target.value, condaEnv: '' })}>
          {servers.map((srv) => (
            <option key={srv.id} value={srv.id}>{srv.name} ({srv.host})</option>
          ))}
        </select>
      </Field>
      <Field label="会话名称" full>
        <input className="tb-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如：训练监控" />
      </Field>
      <Field label="日志路径 (logdir)" full>
        <div style={{ display: 'flex', gap: 4 }}>
          <input className="tb-input" value={form.logdir} onChange={(e) => setForm({ ...form, logdir: e.target.value })} placeholder="例如：/home/user/logs/experiment_1" style={{ flex: 1, minWidth: 0 }} />
          <button className="tb-btn tb-btn-secondary" type="button" onClick={() => setShowPathPicker(true)} disabled={!form.serverId} title="浏览服务器目录" style={{ flexShrink: 0 }}>
            <Folder size={13} /> 浏览
          </button>
        </div>
        {showPathPicker && form.serverId && (
          <HostPathPickerModal
            serverId={form.serverId}
            initialPath={form.logdir || '/'}
            onSelect={(p) => { setForm({ ...form, logdir: p }); setShowPathPicker(false); }}
            onClose={() => setShowPathPicker(false)}
          />
        )}
      </Field>
        <Field label="Python 模式" full>
          <div className="tb-radio-group">
            {hasCondaBase && (
              <label className="tb-radio-label">
                <input type="radio" checked={form.pythonMode === 'conda'} onChange={() => setForm({ ...form, pythonMode: 'conda' })} />
                Conda 环境
              </label>
            )}
            <label className="tb-radio-label">
              <input type="radio" checked={form.pythonMode === 'path'} onChange={() => setForm({ ...form, pythonMode: 'path' })} />
              直接指定 Python 路径
            </label>
          </div>
          {!hasCondaBase && (
            <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
              该服务器未配置 Anaconda 路径，仅支持直接指定 Python 路径
            </div>
          )}
        </Field>
      {form.pythonMode === 'conda' ? (
        <Field label="Conda 环境" full>
          {condaLoading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 0' }}><Spin size={14} /> 加载环境列表…</div>
          ) : condaError ? (
            <>
              <input className="tb-input" value={form.condaEnv} onChange={(e) => setForm({ ...form, condaEnv: e.target.value })} placeholder="手动输入环境名" />
              <div className="tb-session-error" style={{ marginTop: 4 }}>{condaError}</div>
            </>
          ) : condaEnvs.length > 0 ? (
            <select className="tb-select" value={form.condaEnv} onChange={(e) => setForm({ ...form, condaEnv: e.target.value })}>
              {condaEnvs.map((env) => (
                <option key={env} value={env}>{env}</option>
              ))}
            </select>
          ) : (
            <input className="tb-input" value={form.condaEnv} onChange={(e) => setForm({ ...form, condaEnv: e.target.value })} placeholder={hasCondaBase ? '未找到环境，手动输入' : '请先在服务器设置中配置 Anaconda 路径'} />
          )}
        </Field>
      ) : (
        <Field label="Python 路径" full>
          <input className="tb-input" value={form.pythonPath} onChange={(e) => setForm({ ...form, pythonPath: e.target.value })} placeholder="例如：/home/user/anaconda3/envs/pytorch/bin/python" />
        </Field>
      )}
      <Field label="环境检测" full>
        <button
          className="tb-btn tb-btn-secondary"
          type="button"
          onClick={() => void handleCheckEnv()}
          disabled={envChecking || !form.serverId || (form.pythonMode === 'conda' ? !form.condaEnv : !form.pythonPath)}
        >
          {envChecking ? <><Loader2 size={13} className="spin" /> 检测中…</> : <><CheckCircle size={13} /> 检测 Python 环境</>}
        </button>
        {envResult && (
          <div className="tb-session-error" style={{
            marginTop: 6,
            background: envResult.ok ? '#f0fdf4' : '#fef2f2',
            borderColor: envResult.ok ? '#bbf7d0' : '#fecaca',
            color: envResult.ok ? '#166534' : '#991b1b',
          }}>
            {envResult.ok ? (
              <>
                ✓ 环境检测通过
                {envResult.pythonVersion && <div style={{ marginTop: 2 }}>Python: {envResult.pythonVersion}</div>}
                {envResult.tensorboardVersion && <div>TensorBoard: {envResult.tensorboardVersion}</div>}
              </>
            ) : (
              <>
                ✕ {envResult.error || '环境检测失败'}
                {envResult.hasPython === false && <div style={{ marginTop: 2 }}>未检测到 Python</div>}
                {envResult.hasTensorboard === false && <div>未检测到 TensorBoard（请安装：pip install tensorboard）</div>}
              </>
            )}
          </div>
        )}
      </Field>
      <Field label="TensorBoard URL 参数（可选）" full>
        <input className="tb-input" value={form.extraParams} onChange={(e) => setForm({ ...form, extraParams: e.target.value })} placeholder="例如：?smoothing=0.79&runFilter=exp1#timeseries" />
      </Field>
    </div>
  );
}

// ============================================================
// Session Form Modal (Start TensorBoard)
// ============================================================

function SessionFormModal({
  servers,
  onClose,
  onSaved,
}: {
  servers: TbServer[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<SessionForm>({ ...EMPTY_SESSION_FORM, serverId: servers[0]?.id || '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const condaState = useCondaEnvs(form.serverId, form.pythonMode, servers);

  async function handleSave() {
    setSaving(true);
    setError('');
    try {
      await apiPost(`${API}/sessions`, form);
      onSaved();
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title="启动 TensorBoard"
      onClose={onClose}
      width={560}
      foot={
        <>
          <button className="tb-btn tb-btn-secondary" onClick={onClose} type="button" disabled={saving}>取消</button>
          <button className="tb-btn tb-btn-primary" onClick={() => void handleSave()} type="button" disabled={saving || !form.serverId}>
            {saving ? <><Loader2 size={14} className="spin" /> 启动中…</> : '启动'}
          </button>
        </>
      }
    >
      {error && <Alert type="error">{error}</Alert>}
      <SessionFormFields form={form} setForm={setForm} servers={servers} condaState={condaState} />
    </Modal>
  );
}

// ============================================================
// Session Edit Modal (same fields as create)
// ============================================================

function SessionEditModal({
  session,
  servers,
  onClose,
  onSaved,
  onSavedAndRestart,
}: {
  session: TbSession;
  servers: TbServer[];
  onClose: () => void;
  onSaved: () => void;
  onSavedAndRestart: () => void;
}) {
  const [form, setForm] = useState<SessionForm>({
    serverId: session.serverId,
    name: session.name,
    logdir: session.logdir,
    pythonMode: session.pythonMode,
    condaEnv: session.condaEnv,
    pythonPath: session.pythonPath,
    extraParams: session.extraParams || '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const condaState = useCondaEnvs(form.serverId, form.pythonMode, servers);

  async function handleSave(andRestart: boolean) {
    setSaving(true);
    setError('');
    try {
      await apiPut(`${API}/sessions/${session.id}`, form);
      if (andRestart) {
        onSavedAndRestart();
      } else {
        onSaved();
      }
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title="编辑会话"
      onClose={onClose}
      width={560}
      foot={
        <>
          <button className="tb-btn tb-btn-secondary" onClick={onClose} type="button" disabled={saving}>取消</button>
          <button className="tb-btn tb-btn-secondary" onClick={() => void handleSave(false)} type="button" disabled={saving}>
            {saving ? <><Loader2 size={14} className="spin" /> 保存中</> : '保存'}
          </button>
          <button className="tb-btn tb-btn-primary" onClick={() => void handleSave(true)} type="button" disabled={saving}>
            {saving ? <><Loader2 size={14} className="spin" /> 保存中</> : '保存并重启'}
          </button>
        </>
      }
    >
      {error && <Alert type="error">{error}</Alert>}
      <SessionFormFields form={form} setForm={setForm} servers={servers} condaState={condaState} />
    </Modal>
  );
}
