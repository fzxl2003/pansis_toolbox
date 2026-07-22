// ============================================================
// TensorBoard Dashboard Tool — Servers Management Panel
// ============================================================

import { useEffect, useState } from 'react';
import { CheckCircle, Loader2, Pencil, Play, Plus, Server, Trash2, X, XCircle } from 'lucide-react';

import { apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { Alert, Badge, EmptyState, Field, Modal, Spin, useConfirm } from './components';
import { API, formatRelativeTime, messageFromError } from './utils';
import type { ServerForm, TbServer } from './types';
import { EMPTY_SERVER_FORM } from './types';

export type ServersPanelProps = {
  servers: TbServer[];
  loading: boolean;
  onRefresh: () => void;
};

export function ServersPanel({ servers, loading, onRefresh }: ServersPanelProps) {
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [testResult, setTestResult] = useState<{ serverName: string; parts: string[] } | null>(null);
  const { confirm, dialog } = useConfirm();

  function handleAdd() {
    setEditingId(null);
    setShowForm(true);
  }

  function handleEdit(srv: TbServer) {
    setEditingId(srv.id);
    setShowForm(true);
  }

  function handleDelete(srv: TbServer) {
    confirm({
      title: '删除服务器',
      message: `确认删除「${srv.name}」？关联的 TensorBoard 会话将一并停止。`,
      onConfirm: async () => {
        try {
          await apiDelete(`${API}/servers/${srv.id}`);
          onRefresh();
        } catch (exc) {
          setError(messageFromError(exc));
        }
      },
    });
  }

  async function handleTest(srv: TbServer) {
    try {
      const r = await apiPost<{
        ssh?: { connected: boolean; user?: string; error?: string };
        anaconda?: { ok: boolean; version?: string; error?: string };
      }>(`${API}/servers/${srv.id}/test`, {});
      const parts: string[] = [];
      if (r.ssh) {
        parts.push(r.ssh.connected ? `SSH ✓ (${r.ssh.user || ''})` : `SSH ✕ ${r.ssh.error || ''}`);
      }
      if (r.anaconda) {
        parts.push(r.anaconda.ok ? `Anaconda ✓ (${r.anaconda.version || ''})` : `Anaconda ✕ ${r.anaconda.error || ''}`);
      }
      setTestResult({ serverName: srv.name, parts });
      onRefresh();
    } catch (exc) {
      setError(messageFromError(exc));
    }
  }

  return (
    <div className="tb-panel">
      <div className="tb-toolbar">
        <div className="tb-toolbar-left">
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}><Server size={18} /> 服务器管理</h2>
        </div>
        <div className="tb-toolbar-right">
          <button className="tb-btn tb-btn-primary" onClick={handleAdd} type="button">
            <Plus size={14} /> 添加服务器
          </button>
        </div>
      </div>

      {error && <Alert type="error">{error}</Alert>}

      {testResult && (
        <Alert type="info">
          <strong>{testResult.serverName}</strong> 检测结果：
          {testResult.parts.map((p, i) => (
            <span key={i} style={{ marginLeft: 8 }}>{p}</span>
          ))}
          <button className="tb-btn-icon" type="button" onClick={() => setTestResult(null)} style={{ marginLeft: 8 }}><X size={12} /></button>
        </Alert>
      )}

      {loading ? (
        <div className="tb-loading-overlay"><Spin /> 加载服务器列表…</div>
      ) : servers.length === 0 ? (
        <EmptyState
          icon={<Server size={32} />}
          title="暂无服务器"
          hint="点击「添加服务器」开始"
        />
      ) : (
        <div className="tb-table-wrap">
          <table className="tb-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>主机</th>
                <th>用户名</th>
                <th>认证</th>
                <th>测试</th>
                <th>测试时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {servers.map((srv) => (
                <ServerRow
                  key={srv.id}
                  server={srv}
                  onEdit={() => handleEdit(srv)}
                  onDelete={() => handleDelete(srv)}
                  onTest={() => void handleTest(srv)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showForm && (
        <ServerFormModal
          serverId={editingId}
          onClose={() => { setShowForm(false); setEditingId(null); }}
          onSaved={() => { setShowForm(false); setEditingId(null); onRefresh(); }}
        />
      )}
      {dialog}
    </div>
  );
}

// ============================================================
// Server Row
// ============================================================

type ServerRowProps = {
  server: TbServer;
  onEdit: () => void;
  onDelete: () => void;
  onTest: () => void;
};

function ServerRow({ server, onEdit, onDelete, onTest }: ServerRowProps) {
  const [testing, setTesting] = useState(false);

  async function handleTest() {
    setTesting(true);
    try {
      await onTest();
    } finally {
      setTesting(false);
    }
  }

  return (
    <tr>
      <td style={{ fontWeight: 600 }}>{server.name}</td>
      <td><span className="tb-code">{server.sshUsername}@{server.host}:{server.port}</span></td>
      <td>{server.sshUsername}</td>
      <td>{server.authType === 'password' ? '密码' : '私钥'}</td>
      <td>
        {testing ? (
          <Loader2 size={13} className="spin" />
        ) : server.lastTestStatus === 'ok' ? (
          <Badge color="green"><CheckCircle size={10} /> 正常</Badge>
        ) : server.lastTestStatus === 'failed' ? (
          <Badge color="red"><XCircle size={10} /> 失败</Badge>
        ) : (
          <Badge>未测试</Badge>
        )}
      </td>
      <td style={{ fontSize: 12, color: '#94a3b8' }}>{formatRelativeTime(server.lastTestedAt)}</td>
      <td>
        <div className="tb-table-actions">
          <button className="tb-btn tb-btn-sm tb-btn-ghost" onClick={handleTest} type="button" disabled={testing} title="测试连接">
            <Play size={12} />
          </button>
          <button className="tb-btn tb-btn-sm tb-btn-ghost" onClick={onEdit} type="button" title="编辑">
            <Pencil size={12} />
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
// Server Form Modal
// ============================================================

function ServerFormModal({
  serverId,
  onClose,
  onSaved,
}: {
  serverId: string | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState<ServerForm>({ ...EMPTY_SERVER_FORM });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(!!serverId);

  useEffect(() => {
    if (!serverId) return;
    void (async () => {
      try {
        const r = await apiGet<{ servers: TbServer[] }>(`${API}/servers`);
        const srv = r.servers.find((s) => s.id === serverId);
        if (srv) {
          setForm({
            name: srv.name,
            host: srv.host,
            port: srv.port,
            sshUsername: srv.sshUsername,
            authType: srv.authType,
            sshPassword: '',
            privateKey: '',
            privateKeyPassphrase: '',
            condaBasePath: srv.condaBasePath || '',
          });
        }
      } catch (exc) {
        setError(messageFromError(exc));
      } finally {
        setLoading(false);
      }
    })();
  }, [serverId]);

  async function handleSave() {
    setSaving(true);
    setError('');
    try {
      if (serverId) {
        const payload: Record<string, unknown> = {
          name: form.name,
          host: form.host,
          port: form.port,
          sshUsername: form.sshUsername,
          authType: form.authType,
          condaBasePath: form.condaBasePath,
        };
        if (form.sshPassword) payload.sshPassword = form.sshPassword;
        if (form.privateKey) payload.privateKey = form.privateKey;
        if (form.privateKeyPassphrase) payload.privateKeyPassphrase = form.privateKeyPassphrase;
        await apiPut(`${API}/servers/${serverId}`, payload);
      } else {
        await apiPost(`${API}/servers`, form);
      }
      onSaved();
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <Modal title="加载中…" onClose={onClose}>
        <div className="tb-loading-overlay"><Spin /> 加载中…</div>
      </Modal>
    );
  }

  return (
    <Modal
      title={serverId ? '编辑服务器' : '添加服务器'}
      onClose={onClose}
      foot={
        <>
          <button className="tb-btn tb-btn-secondary" onClick={onClose} type="button" disabled={saving}>取消</button>
          <button className="tb-btn tb-btn-primary" onClick={() => void handleSave()} type="button" disabled={saving}>
            {saving ? <><Loader2 size={14} className="spin" /> 保存中</> : '保存'}
          </button>
        </>
      }
    >
      <div className="tb-form-grid">
        {error && <Alert type="error">{error}</Alert>}
        <Field label="名称"><input className="tb-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如：GPU 服务器" /></Field>
        <Field label="主机地址"><input className="tb-input" value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="例如：192.168.1.100" /></Field>
        <Field label="端口"><input className="tb-input" type="number" value={form.port} onChange={(e) => setForm({ ...form, port: parseInt(e.target.value) || 22 })} /></Field>
        <Field label="SSH 用户名"><input className="tb-input" value={form.sshUsername} onChange={(e) => setForm({ ...form, sshUsername: e.target.value })} /></Field>
        <Field label="Anaconda 安装路径（可选）" full>
          <input className="tb-input" value={form.condaBasePath} onChange={(e) => setForm({ ...form, condaBasePath: e.target.value })} placeholder="例如：/home/user/anaconda3 或 /opt/conda（不填则仅支持直接指定 Python 路径）" />
        </Field>
        <Field label="认证方式" full>
          <div className="tb-radio-group">
            <label className="tb-radio-label">
              <input type="radio" checked={form.authType === 'password'} onChange={() => setForm({ ...form, authType: 'password' })} />
              密码
            </label>
            <label className="tb-radio-label">
              <input type="radio" checked={form.authType === 'private_key'} onChange={() => setForm({ ...form, authType: 'private_key' })} />
              私钥
            </label>
          </div>
        </Field>
        {form.authType === 'password' && (
          <Field label={serverId ? 'SSH 密码（留空不修改）' : 'SSH 密码'} full>
            <input className="tb-input" type="password" value={form.sshPassword} onChange={(e) => setForm({ ...form, sshPassword: e.target.value })} />
          </Field>
        )}
        {form.authType === 'private_key' && (
          <>
            <Field label={serverId ? '私钥内容（留空不修改）' : '私钥内容'} full>
              <textarea className="tb-textarea" rows={5} value={form.privateKey} onChange={(e) => setForm({ ...form, privateKey: e.target.value })} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
            </Field>
            <Field label="私钥 passphrase（可选）" full>
              <input className="tb-input" type="password" value={form.privateKeyPassphrase} onChange={(e) => setForm({ ...form, privateKeyPassphrase: e.target.value })} />
            </Field>
          </>
        )}
      </div>
    </Modal>
  );
}
