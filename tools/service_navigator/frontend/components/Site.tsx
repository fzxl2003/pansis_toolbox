import { type FormEvent, useEffect, useState } from "react";
import { KeyRound, ShieldCheck, UserPlus, Users } from "lucide-react";
import { apiDelete, apiGet, apiPost, apiPut } from "../../../../frontend/src/api/client";
import { API, type AccessSettings } from "../types";
import { Field, Modal } from "./shared";

export function AccessManager({ onChanged }: { onChanged?: () => void }) {
  const [access, setAccess] = useState<AccessSettings | null>(null);
  const [username, setUsername] = useState("");
  const [label, setLabel] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const load = async () => setAccess(await apiGet<AccessSettings>(`${API}/access`));
  useEffect(() => { void load().catch((reason: Error) => setError(reason.message)); }, []);
  const run = async (action: () => Promise<unknown>) => {
    try { setError(""); await action(); await load(); onChanged?.(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "访问设置保存失败"); }
  };
  if (!access) return <p className="sn-muted">正在读取访问设置…</p>;
  const isPrivate = access.visibility === "private";
  const enabledPasswordCount = access.passwords.filter((item) => item.enabled).length;
  return <section className="sn-access-manager">
    <section className={`sn-access-hero${isPrivate ? " is-private" : ""}`}>
      <div className="sn-access-hero-icon"><ShieldCheck size={22} /></div>
      <div className="sn-access-hero-copy">
        <span className={`sn-access-visibility${isPrivate ? " private" : ""}`}>{isPrivate ? "私密站点" : "公开站点"}</span>
        <strong>{isPrivate ? "仅限已授权的访客访问" : "任何人都可以访问此导航站"}</strong>
        <p>{isPrivate ? "站点所有者、受邀用户和持有效访问密码的访客均可进入。" : "开启私密访问后，可通过受邀用户或访客密码控制进入权限。"}</p>
      </div>
      <label className="sn-access-switch">
        <input type="checkbox" checked={isPrivate} onChange={(event) => void run(() => apiPut(`${API}/access`, { visibility: event.target.checked ? "private" : "public" }))} />
        <span aria-hidden="true" />
        <em>{isPrivate ? "私密" : "公开"}</em>
      </label>
      <div className="sn-access-summary" aria-label="访问控制摘要">
        <span><Users size={14} /> {access.users.length} 位受邀用户</span>
        <span><KeyRound size={14} /> {enabledPasswordCount} 个生效密码</span>
      </div>
    </section>
    {error && <p className="sn-error-text">{error}</p>}
    <section className="sn-access-card">
      <header className="sn-access-card-heading"><span><UserPlus size={18} /></span><div><h3>受邀用户</h3><p>受邀的已有平台账号无需输入访客密码。</p></div></header>
      <div className="sn-access-add-user"><input value={username} placeholder="输入平台用户名" aria-label="平台用户名" onChange={(event) => setUsername(event.target.value)} /><button className="primary-button" type="button" disabled={!username.trim()} onClick={() => void run(async () => { await apiPost(`${API}/access/users`, { username }); setUsername(""); })}><UserPlus size={15} />添加用户</button></div>
      <div className="sn-access-rows sn-access-user-rows">{access.users.length ? access.users.map((user) => <div key={user.userId}><span className="sn-access-avatar">{user.displayName.slice(0, 1).toUpperCase()}</span><span><strong>{user.displayName}</strong><small>@{user.username}</small></span><button className="sn-danger-text" type="button" onClick={() => void run(() => apiDelete(`${API}/access/users/${user.userId}`))}>移除</button></div>) : <div className="sn-access-empty"><Users size={18} /><span>尚未邀请其他用户</span></div>}</div>
    </section>
    <section className="sn-access-card">
      <header className="sn-access-card-heading"><span><KeyRound size={18} /></span><div><h3>访客密码</h3><p>密码不会回显；停用或重置会撤销相应访客会话。</p></div></header>
      <div className="sn-access-add-password"><input value={label} placeholder="密码名称，例如：客户演示" aria-label="密码名称" onChange={(event) => setLabel(event.target.value)} /><input value={password} type="password" placeholder="设置访问密码" aria-label="访问密码" onChange={(event) => setPassword(event.target.value)} /><button className="primary-button" type="button" disabled={!label.trim() || !password} onClick={() => void run(async () => { await apiPost(`${API}/access/passwords`, { label, password }); setLabel(""); setPassword(""); })}><KeyRound size={15} />创建密码</button></div>
      <div className="sn-access-rows sn-access-password-rows">{access.passwords.length ? access.passwords.map((item) => <div key={item.id}><span className="sn-access-key"><KeyRound size={16} /></span><span><strong>{item.label}</strong><small><i className={item.enabled ? "enabled" : ""} />{item.enabled ? "已启用，可用于访问" : "已停用"}</small></span><div className="sn-access-actions"><button type="button" className="sn-btn-link" onClick={() => { const value = prompt("输入新的密码名称", item.label); if (value?.trim()) void run(() => apiPut(`${API}/access/passwords/${item.id}`, { label: value, password: "", enabled: item.enabled })); }}>重命名</button><button type="button" className="sn-btn-link" onClick={() => { const value = prompt(`为「${item.label}」设置新密码`); if (value) void run(() => apiPut(`${API}/access/passwords/${item.id}`, { label: item.label, password: value, enabled: item.enabled })); }}>重置</button><label className="sn-access-enabled"><input type="checkbox" checked={item.enabled} onChange={(event) => void run(() => apiPut(`${API}/access/passwords/${item.id}`, { label: item.label, password: "", enabled: event.target.checked }))} /><span />启用</label><button className="sn-danger-text" type="button" onClick={() => { if (confirm(`删除访问密码「${item.label}」？`)) void run(() => apiDelete(`${API}/access/passwords/${item.id}`)); }}>删除</button></div></div>) : <div className="sn-access-empty"><KeyRound size={18} /><span>尚未配置访客密码</span></div>}</div>
    </section>
  </section>;
}

export function SiteModal({
  form,
  setForm,
  onClose,
  onSubmit,
  create = false,
}: {
  form: { title: string; slug: string; description: string };
  setForm: (value: {
    title: string;
    slug: string;
    description: string;
  }) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
  create?: boolean;
}) {
  return (
    <Modal title={create ? "创建服务导航站" : "站点设置"} onClose={onClose}>
      <form className="sn-form-grid" onSubmit={onSubmit}>
        <Field label="站点标题">
          <input
            required
            value={form.title}
            onChange={(event) =>
              setForm({ ...form, title: event.target.value })
            }
          />
        </Field>
        <Field label="公开地址 slug">
          <input
            required
            value={form.slug}
            onChange={(event) => setForm({ ...form, slug: event.target.value })}
          />
        </Field>
        <Field label="简介" full>
          <textarea
            value={form.description}
            onChange={(event) =>
              setForm({ ...form, description: event.target.value })
            }
          />
        </Field>
        <div className="sn-form-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button">保存</button>
        </div>
      </form>
    </Modal>
  );
}

export function SiteSettingsPanel({
  form,
  setForm,
  onSubmit,
}: {
  form: { title: string; slug: string; description: string };
  setForm: (value: { title: string; slug: string; description: string }) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <section className="sn-panel">
      <div className="sn-panel-heading"><div><h2>站点设置</h2><p>配置访客页的标题、地址和简介。</p></div></div>
      <form className="sn-form-grid" onSubmit={onSubmit}>
        <Field label="站点标题"><input required value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></Field>
        <Field label="公开地址 slug"><input required value={form.slug} onChange={(event) => setForm({ ...form, slug: event.target.value })} /></Field>
        <Field label="简介" full><textarea value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></Field>
        <div className="sn-form-actions"><button className="primary-button">保存站点设置</button></div>
      </form>
    </section>
  );
}

export function AccessModal({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged?: () => void;
}) {
  return <Modal title="访问控制" onClose={onClose} className="sn-access-modal"><AccessManager onChanged={onChanged} /></Modal>;
}
