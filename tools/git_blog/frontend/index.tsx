import './style.css';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { BookOpen, CheckCircle2, CircleAlert, Copy, ExternalLink, Eye, FolderGit2, KeyRound, Link2, Loader2, Palette, Pencil, Plus, RefreshCw, ScrollText, Share2, Shield, Trash2, Upload, UserPlus } from 'lucide-react';
import { apiDelete, apiGet, apiPost, apiPostForm, apiPut } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { fetchGithubKeys, type GithubKey } from '../../../frontend/src/api/settings';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

const API = '/api/tools/git-blog';
type Blog = { id:string; name:string; slug:string; repoUrl:string; branch:string; contentRoot:string; githubKeyId:string; syncIntervalMinutes:number; autoSyncEnabled:boolean; config:any; syncStatus:string; lastError:string; currentCommit:string; visibility:'public'|'private'; shareEnabled:boolean };
type Theme = { id:string; name:string; filename:string; size:number; createdAt:string };
type Snapshot = { commit:string; createdAt:string; current:boolean };
type AccessSettings = { visibility:'public'|'private'; users:{ userId:string; username:string; displayName:string; canShare:boolean }[]; passwords:{ id:string; label:string; canShare:boolean; enabled:boolean; createdAt:string; updatedAt:string }[] };
type Share = { id:string; token:string; url:string; articleSlug:string; mode:'document'|'full'; passwordProtected:boolean; expiresAt:string|null; maxViews:number|null; views:number; enabled:boolean; createdByType:string; createdByLabel:string; createdAt:string; updatedAt:string };
type Form = ReturnType<typeof emptyForm> & { id?:string };
const ACCENT_COLORS = ['#42b983', '#0d9488', '#2563eb', '#7c3aed', '#db2777', '#dc2626', '#ea580c', '#475569'];
const emptyForm = () => ({ name:'', slug:'', repoUrl:'', branch:'main', contentRoot:'', githubKeyId:'', syncIntervalMinutes:15, autoSyncEnabled:true, config:{ site:{ theme:'auto', customThemeId:'', accentColor:'#42b983', accentColorEnabled:false }, defaults:{ published:false }, logs:{ accessEnabled:true, recordIp:false, recordUserAgent:true, recordReferrer:true, retentionDays:30 } } });

function Field({ label, children, full = false }: { label:string; children:ReactNode; full?:boolean }) {
  return <label className={`gb-field${full ? ' gb-full-col' : ''}`}><span>{label}</span>{children}</label>;
}

function datetimeLocalValue(value:string|null) {
  if (!value) return '';
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

function AccentColorPicker({ value, onChange }: { value:string; onChange:(value:string)=>void }) {
  const valid = /^#[0-9a-f]{6}$/i.test(value) ? value.toLowerCase() : '#42b983';
  return <div className="gb-accent-picker">
    <div className="gb-accent-presets" aria-label="主题色预设">{ACCENT_COLORS.map(color => <button type="button" key={color} className={`gb-accent-swatch${valid === color ? ' is-selected' : ''}`} style={{ backgroundColor:color }} title={color} aria-label={`选择主题色 ${color}`} aria-pressed={valid === color} onClick={() => onChange(color)}/>)}</div>
    <div className="gb-accent-custom"><input type="color" value={valid} aria-label="选择自定义主题色" onChange={event => onChange(event.target.value)}/><input className="gb-input gb-color-hex" value={value} maxLength={7} pattern="#[0-9a-fA-F]{6}" placeholder="#42b983" aria-label="十六进制主题色" onChange={event => onChange(event.target.value)}/></div>
  </div>;
}

function Status({ status }: { status:string }) {
  const value = status.toLowerCase();
  const kind = value.includes('success') || value === 'idle' ? 'success' : value.includes('fail') || value.includes('error') ? 'error' : value.includes('sync') || value.includes('running') ? 'working' : 'pending';
  const Icon = kind === 'success' ? CheckCircle2 : kind === 'error' ? CircleAlert : kind === 'working' ? Loader2 : RefreshCw;
  const labels:Record<string,string> = {
    success:'同步正常', idle:'同步正常', failed:'同步失败', error:'同步失败', paused:'同步暂停',
    pending:'等待首次同步', queued:'等待同步', running:'同步中', syncing:'同步中',
    syncing_checking:'检查远端版本', syncing_cloning:'正在拉取仓库', syncing_rendering:'解析并渲染文章',
    syncing_rebuilding:'应用配置更新', syncing_publishing:'更新发布索引',
  };
  const label = labels[value] || ({ success:'同步正常', error:'同步失败', working:'同步中', pending:'等待同步' }[kind]);
  return <span className={`gb-badge gb-badge-${kind}`} title={label}><Icon size={13} className={kind === 'working' ? 'gb-spin' : ''}/>{label}</span>;
}

function Logs({ blog, data, close }: { blog:Blog; data:{ runs:any[]; logs:any[] }|null; close:()=>void }) {
  return <div className="gb-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section className="gb-modal gb-log-modal">
      <header><div><p className="gb-eyebrow">BLOG LOGS</p><h3>{blog.name} · 日志</h3></div><button className="gb-close" onClick={close}>×</button></header>
      <div className="gb-modal-body">{!data ? <div className="gb-loading"><Loader2 className="gb-spin"/>正在读取日志…</div> : <>
        <h4>同步记录</h4>
        {data.runs.length ? <div className="gb-log-list">{data.runs.map(item => <div className="gb-log gb-sync-log" key={item.id}><Status status={item.status || ''}/><pre className="gb-log-message">{item.message || '—'}</pre><small>{item.started_at || item.startedAt || '—'}</small></div>)}</div> : <p className="gb-log-empty">暂无同步记录。</p>}
        <h4>访问记录</h4>
        {data.logs.length ? <div className="gb-log-list">{data.logs.map(item => <div className="gb-log gb-access-log" key={item.id}><strong>{item.status_code ?? item.statusCode ?? '—'}</strong><span>{item.method || 'GET'} {item.path || '—'}</span><small>{item.requested_at || item.requestedAt || '—'}</small></div>)}</div> : <p className="gb-log-empty">暂无访问记录。</p>}
      </>}</div>
    </section>
  </div>;
}

function ThemeManager({ themes, close, changed, notify }: { themes:Theme[]; close:()=>void; changed:()=>Promise<void>; notify:(text:string,error?:boolean)=>void }) {
  const [file, setFile] = useState<File|null>(null);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<Theme|null>(null);
  const upload = async (event:React.FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setUploading(true);
    try {
      const body = new FormData();
      body.append('css', file);
      const result = await apiPostForm<{ theme:Theme }>(`${API}/themes`, body);
      setFile(null);
      await changed();
      notify(`已添加主题「${result.theme.name}」。`);
    } catch (error:any) {
      notify(error.message, true);
    } finally {
      setUploading(false);
    }
  };
  const remove = async (theme:Theme) => {
    if (!confirm(`删除主题「${theme.name}」？使用该主题的博客将恢复为内置主题。`)) return;
    try {
      const result = await apiDelete<{ deleted:boolean; affectedBlogs:number }>(`${API}/themes/${theme.id}`);
      if (preview?.id === theme.id) setPreview(null);
      await changed();
      notify(result.affectedBlogs ? `已删除「${theme.name}」，${result.affectedBlogs} 个博客已恢复内置主题。` : `已删除主题「${theme.name}」。`);
    } catch (error:any) {
      notify(error.message, true);
    }
  };
  return <div className="gb-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section className="gb-modal gb-theme-modal">
      <header><div><p className="gb-eyebrow">TYPORA THEMES</p><h3><Palette size={18}/>主题管理</h3></div><button className="gb-close" onClick={close}>×</button></header>
      <div className="gb-modal-body">
        <form className="gb-theme-upload" onSubmit={upload}>
          <div><strong>添加 Typora 主题</strong><small>直接上传单个 CSS 文件，无需修改。可参考 <a href="https://github.com/etigerstudio/typora-misty-theme/releases/tag/v1.0-alpha.2" target="_blank" rel="noreferrer">Misty 主题</a>。</small></div>
          <label className="gb-theme-picker"><Upload size={15}/><span>{file?.name || '选择 CSS 文件'}</span><input type="file" accept=".css,text/css" onChange={event => setFile(event.target.files?.[0] || null)}/></label>
          <button className="gb-btn gb-btn-primary" disabled={!file || uploading}>{uploading ? <Loader2 size={14} className="gb-spin"/> : <Plus size={14}/>}添加主题</button>
        </form>
        <div className="gb-theme-list">
          {themes.length ? themes.map(theme => <article className="gb-theme-item" key={theme.id}>
            <div><Palette size={18}/><span><strong>{theme.name}</strong><small>{theme.filename} · {(theme.size / 1024).toFixed(1)} KiB</small></span></div>
            <div><button className="gb-btn gb-btn-secondary gb-btn-sm" onClick={() => setPreview(theme)}><Eye size={14}/>预览</button><button className="gb-icon-button gb-danger" title="删除主题" onClick={() => void remove(theme)}><Trash2 size={15}/></button></div>
          </article>) : <div className="gb-theme-empty"><Palette size={28}/><p>还没有自定义主题</p><small>上传 Typora CSS 后即可在博客设置中选择。</small></div>}
        </div>
        {preview && <section className="gb-theme-preview"><div><strong>{preview.name} · 预览</strong><button className="gb-close" onClick={() => setPreview(null)}>×</button></div><iframe title={`${preview.name} 主题预览`} sandbox="allow-same-origin" src={`${API}/themes/${preview.id}/preview`}/></section>}
      </div>
    </section>
  </div>;
}

function AccessManager({ blog, close, changed, notify }: { blog:Blog; close:()=>void; changed:()=>Promise<void>; notify:(text:string,error?:boolean)=>void }) {
  const [data, setData] = useState<AccessSettings|null>(null);
  const [username, setUsername] = useState('');
  const [userCanShare, setUserCanShare] = useState(false);
  const [label, setLabel] = useState('');
  const [password, setPassword] = useState('');
  const [passwordCanShare, setPasswordCanShare] = useState(false);
  const [addingUser, setAddingUser] = useState(false);
  const [addingPassword, setAddingPassword] = useState(false);
  const closeUserForm = () => { setAddingUser(false); setUsername(''); setUserCanShare(false); };
  const closePasswordForm = () => { setAddingPassword(false); setLabel(''); setPassword(''); setPasswordCanShare(false); };
  const loadAccess = async () => setData(await apiGet<AccessSettings>(`${API}/blogs/${blog.id}/access`));
  useEffect(() => { void loadAccess().catch((error:any) => notify(error.message, true)); }, [blog.id]);
  const run = async (action:()=>Promise<unknown>, success:string) => {
    try { await action(); await Promise.all([loadAccess(), changed()]); notify(success); return true; }
    catch (error:any) { notify(error.message, true); return false; }
  };
  const addUser = async (event:React.FormEvent) => {
    event.preventDefault();
    if (await run(() => apiPost(`${API}/blogs/${blog.id}/access/users`, { username, canShare:userCanShare }), `已邀请用户 ${username}。`)) {
      closeUserForm();
    }
  };
  const addPassword = async (event:React.FormEvent) => {
    event.preventDefault();
    if (await run(() => apiPost(`${API}/blogs/${blog.id}/access/passwords`, { label, password, canShare:passwordCanShare, enabled:true }), `已添加访问密码「${label}」。`)) {
      closePasswordForm();
    }
  };
  return <><div className="gb-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}><section className="gb-modal">
    <header><div><p className="gb-eyebrow">BLOG ACCESS</p><h3><Shield size={18}/>{blog.name} · 访问管理</h3></div><button className="gb-close" onClick={close}>×</button></header>
    <div className="gb-modal-body">{!data ? <div className="gb-loading"><Loader2 className="gb-spin"/>正在读取权限…</div> : <>
      <label className="gb-switch-setting"><span className="gb-switch-copy"><strong>私密访问</strong><small>{data.visibility === 'private' ? '仅博客管理者、受邀用户或持有访问密码的访客可进入。' : '当前为公开博客，所有人均可访问并创建分享链接。'}</small></span><span className="gb-switch-control"><span className="gb-switch-status">{data.visibility === 'private' ? '私密' : '公开'}</span><input type="checkbox" aria-label="切换博客私密访问" checked={data.visibility === 'private'} onChange={event => { const visibility=event.target.checked ? 'private' : 'public'; void run(() => apiPut(`${API}/blogs/${blog.id}/access`, { visibility }), visibility === 'private' ? '博客已设为私密。' : '博客已设为公开。'); }}/><span className="gb-switch-track" aria-hidden="true"/></span></label>
      <section className="gb-settings-section"><div className="gb-settings-heading"><div><h4><UserPlus size={15}/>受邀用户</h4><small>输入已有平台账号的准确用户名。</small></div><button className="gb-btn gb-btn-primary gb-btn-sm" onClick={() => setAddingUser(true)}><Plus size={14}/>添加用户</button></div>
        <div className="gb-settings-list">{data.users.length ? data.users.map(item => <div className="gb-settings-row" key={item.userId}><div><strong>{item.displayName}</strong><small>@{item.username}</small></div><label className="gb-check"><input type="checkbox" checked={item.canShare} onChange={event => void run(() => apiPut(`${API}/blogs/${blog.id}/access/users/${item.userId}`, { username:item.username, canShare:event.target.checked }), '用户分享权限已更新。')}/>允许分享</label><button className="gb-icon-button gb-danger" title="移除用户" onClick={() => void run(() => apiDelete(`${API}/blogs/${blog.id}/access/users/${item.userId}`), `已移除 ${item.username}。`)}><Trash2 size={15}/></button></div>) : <p className="gb-settings-empty">尚未邀请其他用户。</p>}</div>
      </section>
      <section className="gb-settings-section"><div className="gb-settings-heading"><div><h4><KeyRound size={15}/>访问密码</h4><small>可创建多组密码，并分别控制分享权限。</small></div><button className="gb-btn gb-btn-primary gb-btn-sm" onClick={() => setAddingPassword(true)}><Plus size={14}/>添加密码</button></div>
        <div className="gb-settings-list">{data.passwords.length ? data.passwords.map(item => <div className="gb-settings-row gb-password-row" key={item.id}><div><strong>{item.label}</strong><small>{item.enabled ? '已启用' : '已停用'} · 密码不会回显</small></div><label className="gb-check"><input type="checkbox" checked={item.canShare} onChange={event => void run(() => apiPut(`${API}/blogs/${blog.id}/access/passwords/${item.id}`, { label:item.label, password:'', canShare:event.target.checked, enabled:item.enabled }), '密码分享权限已更新。')}/>允许分享</label><label className="gb-check"><input type="checkbox" checked={item.enabled} onChange={event => void run(() => apiPut(`${API}/blogs/${blog.id}/access/passwords/${item.id}`, { label:item.label, password:'', canShare:item.canShare, enabled:event.target.checked }), '密码状态已更新。')}/>启用</label><div className="gb-row-actions"><button className="gb-btn gb-btn-secondary gb-btn-sm" onClick={() => { const value=prompt('输入新的密码名称', item.label); if (value && value !== item.label) void run(() => apiPut(`${API}/blogs/${blog.id}/access/passwords/${item.id}`, { label:value, password:'', canShare:item.canShare, enabled:item.enabled }), '密码名称已更新。'); }}>重命名</button><button className="gb-btn gb-btn-secondary gb-btn-sm" onClick={() => { const value=prompt(`为「${item.label}」设置新密码`); if (value) void run(() => apiPut(`${API}/blogs/${blog.id}/access/passwords/${item.id}`, { label:item.label, password:value, canShare:item.canShare, enabled:item.enabled }), '访问密码已重置，旧会话已撤销。'); }}>重置</button><button className="gb-icon-button gb-danger" title="删除密码" onClick={() => { if (confirm(`删除访问密码「${item.label}」？`)) void run(() => apiDelete(`${API}/blogs/${blog.id}/access/passwords/${item.id}`), `已删除「${item.label}」。`); }}><Trash2 size={15}/></button></div></div>) : <p className="gb-settings-empty">尚未配置访问密码。</p>}</div>
      </section>
    </>}</div><footer><span className="gb-footer-grow"/><button className="gb-btn gb-btn-secondary" onClick={close}>完成</button></footer>
  </section></div>
  {addingUser && <div className="gb-modal-backdrop gb-nested-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) closeUserForm(); }}><form className="gb-modal gb-modal-compact" onSubmit={addUser}><header><div><p className="gb-eyebrow">INVITE USER</p><h3><UserPlus size={18}/>添加受邀用户</h3></div><button type="button" className="gb-close" onClick={closeUserForm}>×</button></header><div className="gb-modal-body"><div className="gb-form-grid"><Field full label="平台用户名"><input className="gb-input" autoFocus required placeholder="例如：zhangsan" value={username} onChange={event => setUsername(event.target.value)}/></Field><label className="gb-check gb-full-col"><input type="checkbox" checked={userCanShare} onChange={event => setUserCanShare(event.target.checked)}/>允许该用户创建分享链接</label></div></div><footer><span className="gb-footer-grow"/><button type="button" className="gb-btn gb-btn-ghost" onClick={closeUserForm}>取消</button><button className="gb-btn gb-btn-primary"><Plus size={14}/>添加用户</button></footer></form></div>}
  {addingPassword && <div className="gb-modal-backdrop gb-nested-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) closePasswordForm(); }}><form className="gb-modal gb-modal-compact" onSubmit={addPassword}><header><div><p className="gb-eyebrow">ACCESS PASSWORD</p><h3><KeyRound size={18}/>添加访问密码</h3></div><button type="button" className="gb-close" onClick={closePasswordForm}>×</button></header><div className="gb-modal-body"><div className="gb-form-grid"><Field full label="密码名称"><input className="gb-input" autoFocus required placeholder="例如：项目组" value={label} onChange={event => setLabel(event.target.value)}/></Field><Field full label="访问密码"><input className="gb-input" required type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)}/></Field><label className="gb-check gb-full-col"><input type="checkbox" checked={passwordCanShare} onChange={event => setPasswordCanShare(event.target.checked)}/>使用此密码的访客可以创建分享链接</label></div></div><footer><span className="gb-footer-grow"/><button type="button" className="gb-btn gb-btn-ghost" onClick={closePasswordForm}>取消</button><button className="gb-btn gb-btn-primary"><Plus size={14}/>添加密码</button></footer></form></div>}
  </>;
}

function SharingManager({ blog, close, changed, notify }: { blog:Blog; close:()=>void; changed:()=>Promise<void>; notify:(text:string,error?:boolean)=>void }) {
  const [enabled, setEnabled] = useState(false);
  const [shares, setShares] = useState<Share[]|null>(null);
  const [drafts, setDrafts] = useState<Record<string,Share>>({});
  const [editingShareId, setEditingShareId] = useState<string|null>(null);
  const [sharePassword, setSharePassword] = useState('');
  const [removePassword, setRemovePassword] = useState(false);
  const [copiedShareId, setCopiedShareId] = useState<string|null>(null);
  const loadSharing = async () => {
    const result = await apiGet<{ enabled:boolean; shares:Share[] }>(`${API}/blogs/${blog.id}/sharing`);
    setEnabled(result.enabled); setShares(result.shares); setDrafts(Object.fromEntries(result.shares.map(item => [item.id, item])));
  };
  useEffect(() => { void loadSharing().catch((error:any) => notify(error.message, true)); }, [blog.id]);
  const run = async (action:()=>Promise<unknown>, success:string) => { try { await action(); await Promise.all([loadSharing(), changed()]); notify(success); return true; } catch (error:any) { notify(error.message, true); return false; } };
  const setDraft = (id:string, value:Partial<Share>) => setDrafts(current => ({ ...current, [id]:{ ...current[id], ...value } }));
  const closeEditor = () => { setEditingShareId(null); setSharePassword(''); setRemovePassword(false); };
  const openEditor = (item:Share) => { setDrafts(current => ({ ...current, [item.id]:item })); setEditingShareId(item.id); setSharePassword(''); setRemovePassword(false); };
  const copyShare = async (item:Share) => {
    try {
      await navigator.clipboard.writeText(`${location.origin}${item.url}`);
      setCopiedShareId(item.id);
      window.setTimeout(() => setCopiedShareId(current => current === item.id ? null : current), 1800);
    } catch {
      notify('复制分享链接失败，请手动复制。', true);
    }
  };
  const save = async (item:Share) => {
    const draft = drafts[item.id];
    const passwordAction = removePassword ? 'remove' : sharePassword ? 'replace' : 'keep';
    if (await run(() => apiPut(`${API}/blogs/${blog.id}/shares/${item.id}`, { mode:draft.mode, expiresAt:draft.expiresAt, maxViews:draft.maxViews, enabled:draft.enabled, passwordAction, password:sharePassword }), '分享设置已保存。')) closeEditor();
  };
  const editingShare = shares?.find(item => item.id === editingShareId) || null;
  const editingDraft = editingShare ? (drafts[editingShare.id] || editingShare) : null;
  return <><div className="gb-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}><section className="gb-modal">
    <header><div><p className="gb-eyebrow">DOCUMENT SHARING</p><h3><Share2 size={18}/>{blog.name} · 分享管理</h3></div><button className="gb-close" onClick={close}>×</button></header>
    <div className="gb-modal-body"><label className="gb-switch-setting"><span className="gb-switch-copy"><strong>文档分享</strong><small>{enabled ? '已允许从具体文档页面创建分享链接。' : '关闭后保留已有链接设置，但所有链接会立即停止访问。'}</small></span><span className="gb-switch-control"><span className="gb-switch-status">{enabled ? '已开启' : '已关闭'}</span><input type="checkbox" aria-label="切换文档分享" checked={enabled} onChange={event => { const next=event.target.checked; setEnabled(next); void run(() => apiPut(`${API}/blogs/${blog.id}/sharing`, { enabled:next }), next ? '已开启文档分享。' : '已关闭文档分享。'); }}/><span className="gb-switch-track" aria-hidden="true"/></span></label>
      <section className="gb-settings-section"><div className="gb-settings-heading"><div><h4><Link2 size={15}/>分享链接</h4><small>分享链接从具体文档页面创建，可在这里统一维护。</small></div></div>{shares === null ? <div className="gb-loading"><Loader2 className="gb-spin"/>正在读取分享链接…</div> : shares.length ? <div className="gb-share-list">{shares.map(item => <article className="gb-share-item" key={item.id}><div className="gb-share-item-head"><div className="gb-share-title"><div className="gb-share-title-line"><strong title={item.articleSlug}>{item.articleSlug}</strong><span className={`gb-badge ${item.enabled ? 'gb-badge-success' : 'gb-badge-pending'}`}>{item.enabled ? '有效' : '已停用'}</span></div><small>{item.createdByLabel} 创建 · {new Date(item.createdAt).toLocaleString()}</small></div><div className="gb-share-header-actions"><button className="gb-icon-button" title="编辑分享" aria-label="编辑分享" onClick={() => openEditor(item)}><Pencil size={15}/></button><button className="gb-icon-button gb-danger" title="删除分享" aria-label="删除分享" onClick={() => { if (confirm('永久删除此分享链接？')) void run(() => apiDelete(`${API}/blogs/${blog.id}/shares/${item.id}`), '分享链接已删除。'); }}><Trash2 size={15}/></button></div></div><div className="gb-share-url"><code title={`${location.origin}${item.url}`}>{location.origin}{item.url}</code><button className={`gb-icon-button${copiedShareId === item.id ? ' is-copied' : ''}`} title={copiedShareId === item.id ? '已复制' : '复制链接'} aria-label={copiedShareId === item.id ? '链接已复制' : '复制链接'} onClick={() => void copyShare(item)}>{copiedShareId === item.id ? <CheckCircle2 size={15}/> : <Copy size={15}/>}</button></div><div className="gb-share-summary"><span><strong>{item.mode === 'document' ? '仅当前文档' : '完整博客界面'}</strong><small>展示方式</small></span><span><strong>{item.maxViews ? `${item.views} / ${item.maxViews}` : `${item.views} / 不限`}</strong><small>浏览器打开</small></span><span><strong>{item.expiresAt ? new Date(item.expiresAt).toLocaleString() : '永久有效'}</strong><small>有效时间</small></span><span><strong>{item.passwordProtected ? '已设置' : '无密码'}</strong><small>访问密码</small></span></div></article>)}</div> : <div className="gb-settings-empty gb-settings-empty-large"><Link2 size={26}/><strong>还没有分享链接</strong><small>{enabled ? '请从具体文档页面点击分享按钮创建。' : '开启分享功能后，可从文档页创建链接。'}</small></div>}</section>
    </div><footer><span className="gb-footer-grow"/><button className="gb-btn gb-btn-secondary" onClick={close}>完成</button></footer>
  </section></div>
  {editingShare && editingDraft && <div className="gb-modal-backdrop gb-nested-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) closeEditor(); }}><form className="gb-modal gb-share-editor" onSubmit={event => { event.preventDefault(); void save(editingShare); }}><header><div><p className="gb-eyebrow">EDIT SHARE</p><h3><Pencil size={18}/>编辑分享链接</h3></div><button type="button" className="gb-close" onClick={closeEditor}>×</button></header><div className="gb-modal-body"><div className="gb-share-editor-source"><strong title={editingShare.articleSlug}>{editingShare.articleSlug}</strong><code title={`${location.origin}${editingShare.url}`}>{location.origin}{editingShare.url}</code></div><div className="gb-form-grid"><Field label="展示方式"><select className="gb-input" value={editingDraft.mode} onChange={event => setDraft(editingShare.id, { mode:event.target.value as Share['mode'] })}><option value="document">仅显示当前文档</option><option value="full">显示完整博客界面</option></select></Field><Field label="浏览器打开上限"><input className="gb-input" type="number" min="1" placeholder="留空表示不限" value={editingDraft.maxViews ?? ''} onChange={event => setDraft(editingShare.id, { maxViews:event.target.value ? Number(event.target.value) : null })}/></Field><Field full label="到期时间"><input className="gb-input" type="datetime-local" value={datetimeLocalValue(editingDraft.expiresAt)} onChange={event => setDraft(editingShare.id, { expiresAt:event.target.value ? new Date(event.target.value).toISOString() : null })}/></Field><Field full label={editingShare.passwordProtected ? '新分享密码（留空则不修改）' : '分享密码（可选）'}><input className="gb-input" type="password" autoComplete="new-password" disabled={removePassword} value={sharePassword} onChange={event => { setSharePassword(event.target.value); if (event.target.value) setRemovePassword(false); }}/></Field>{editingShare.passwordProtected && <label className="gb-check gb-full-col"><input type="checkbox" checked={removePassword} onChange={event => { setRemovePassword(event.target.checked); if (event.target.checked) setSharePassword(''); }}/>移除当前分享密码</label>}</div><label className="gb-switch-setting gb-share-enabled-switch"><span className="gb-switch-copy"><strong>启用此分享链接</strong><small>关闭后链接立即停止访问，配置仍会保留。</small></span><span className="gb-switch-control"><span className="gb-switch-status">{editingDraft.enabled ? '已启用' : '已停用'}</span><input type="checkbox" aria-label="启用此分享链接" checked={editingDraft.enabled} onChange={event => setDraft(editingShare.id, { enabled:event.target.checked })}/><span className="gb-switch-track" aria-hidden="true"/></span></label></div><footer><span className="gb-footer-grow"/><button type="button" className="gb-btn gb-btn-ghost" onClick={closeEditor}>取消</button><button className="gb-btn gb-btn-primary">保存修改</button></footer></form></div>}
  </>;
}

export default function GitBlogTool() {
  const [me, setMe] = useState<AuthUser|null>(null);
  const [blogs, setBlogs] = useState<Blog[]>([]);
  const [themes, setThemes] = useState<Theme[]>([]);
  const [keys, setKeys] = useState<GithubKey[]>([]);
  const [branches, setBranches] = useState<string[]>([]);
  const [form, setForm] = useState<Form|null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]|null>([]);
  const [rollingCommit, setRollingCommit] = useState('');
  const [message, setMessage] = useState<{ text:string; error?:boolean }|null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [logBlog, setLogBlog] = useState<Blog|null>(null);
  const [logData, setLogData] = useState<{ runs:any[]; logs:any[] }|null>(null);
  const [themeManagerOpen, setThemeManagerOpen] = useState(false);
  const [accessBlog, setAccessBlog] = useState<Blog|null>(null);
  const [sharingBlog, setSharingBlog] = useState<Blog|null>(null);

  const load = async () => {
    try {
      const [result, githubKeys, themeResult] = await Promise.all([apiGet<{ blogs:Blog[] }>(`${API}/blogs`), fetchGithubKeys(), apiGet<{ themes:Theme[] }>(`${API}/themes`)]);
      setBlogs(result.blogs);
      setKeys(githubKeys.keys);
      setThemes(themeResult.themes);
    } catch (error:any) {
      setMessage({ text:error.message, error:true });
    }
  };
  useEffect(() => { fetchMe().then(result => setMe(result.user)).catch(() => setMe(null)).finally(() => setLoading(false)); }, []);
  useEffect(() => { if (me) void load(); }, [me]);
  const hasActiveSync = blogs.some(blog => blog.syncStatus === 'queued' || blog.syncStatus === 'syncing' || blog.syncStatus.startsWith('syncing_') || (blog.syncStatus === 'pending' && blog.autoSyncEnabled));
  useEffect(() => {
    if (!me) return;
    const timer = window.setInterval(() => {
      apiGet<{ blogs:Blog[] }>(`${API}/blogs`).then(result => setBlogs(result.blogs)).catch((error:any) => setMessage({ text:error.message, error:true }));
    }, hasActiveSync ? 1200 : 5000);
    return () => window.clearInterval(timer);
  }, [me, hasActiveSync]);
  const stats = useMemo(() => ({
    total:blogs.length,
    healthy:blogs.filter(blog => ['success', 'idle'].some(value => blog.syncStatus.toLowerCase().includes(value))).length,
    pending:blogs.filter(blog => /sync|running|pending|queued/.test(blog.syncStatus.toLowerCase())).length,
  }), [blogs]);
  const set = (value:Partial<Form>) => setForm(current => current ? { ...current, ...value } : current);
  const selectedTheme = form?.config?.site?.customThemeId ? `custom:${form.config.site.customThemeId}` : (form?.config?.site?.theme || 'auto');
  const selectTheme = (value:string) => {
    if (!form) return;
    const customThemeId = value.startsWith('custom:') ? value.slice(7) : '';
    set({ config:{ ...form.config, site:{ ...form.config.site, theme:customThemeId ? 'auto' : value, customThemeId } } });
  };
  const accentColor = form?.config?.site?.accentColorEnabled ? (form.config.site.accentColor || '#42b983') : '#42b983';
  const selectAccentColor = (value:string) => {
    if (!form) return;
    set({ config:{ ...form.config, site:{ ...form.config.site, accentColor:value, accentColorEnabled:true } } });
  };

  const save = async (event:React.FormEvent) => {
    event.preventDefault();
    if (!form) return;
    setSaving(true);
    try {
      const result = form.id ? await apiPut<{ blog:Blog }>(`${API}/blogs/${form.id}`, form) : await apiPost<{ blog:Blog }>(`${API}/blogs`, form);
      const text = result.blog.syncStatus === 'queued'
        ? `已保存「${result.blog.name}」，正在应用配置更新。`
        : result.blog.autoSyncEnabled ? `已保存「${result.blog.name}」，正在等待同步。` : `已保存「${result.blog.name}」，自动同步已关闭。`;
      setMessage({ text });
      setForm(null);
      await load();
    } catch (error:any) {
      setMessage({ text:error.message, error:true });
    } finally {
      setSaving(false);
    }
  };
  const loadSnapshots = async (id:string) => {
    const result = await apiGet<{ snapshots:Snapshot[] }>(`${API}/blogs/${id}/snapshots`);
    setSnapshots(result.snapshots);
  };
  const openEdit = (blog:Blog) => {
    setBranches([]);
    setSnapshots(null);
    setForm({ ...blog, config:blog.config || emptyForm().config });
    void loadSnapshots(blog.id).catch((error:any) => { setSnapshots([]); setMessage({ text:error.message, error:true }); });
  };
  const rollback = async (snapshot:Snapshot) => {
    if (!form?.id || snapshot.current || !confirm(`确认回滚到提交 ${snapshot.commit.slice(0, 8)}？回滚后将自动关闭周期同步。`)) return;
    setRollingCommit(snapshot.commit);
    try {
      const result = await apiPost<{ blog:Blog }>(`${API}/blogs/${form.id}/snapshots/${snapshot.commit}/rollback`, {});
      setForm(current => current ? { ...current, ...result.blog, config:result.blog.config || current.config } : current);
      await Promise.all([load(), loadSnapshots(form.id)]);
      setMessage({ text:`已回滚到 ${snapshot.commit.slice(0, 8)}，自动同步已关闭。` });
    } catch (error:any) {
      setMessage({ text:error.message, error:true });
    } finally {
      setRollingCommit('');
    }
  };
  const probe = async () => {
    if (!form) return;
    setProbing(true);
    try {
      const result = await apiPost<{ branches:string[] }>(`${API}/repository/probe`, { repoUrl:form.repoUrl, githubKeyId:form.githubKeyId });
      setBranches(result.branches);
      setMessage({ text:`仓库连接成功，已读取 ${result.branches.length} 个分支。` });
    } catch (error:any) {
      setMessage({ text:error.message, error:true });
    } finally {
      setProbing(false);
    }
  };
  const sync = async (id:string) => {
    try { await apiPost(`${API}/blogs/${id}/sync`, {}); setMessage({ text:'已加入同步队列。' }); await load(); }
    catch (error:any) { setMessage({ text:error.message, error:true }); }
  };
  const remove = async (blog:Blog) => {
    if (!confirm(`删除博客「${blog.name}」及其本地快照？`)) return;
    try { await apiDelete(`${API}/blogs/${blog.id}`); setMessage({ text:`已删除「${blog.name}」。` }); await load(); }
    catch (error:any) { setMessage({ text:error.message, error:true }); }
  };
  const logs = async (blog:Blog) => {
    setLogBlog(blog);
    setLogData(null);
    try {
      const [runs, access] = await Promise.all([apiGet<{ runs:any[] }>(`${API}/blogs/${blog.id}/runs`), apiGet<{ logs:any[] }>(`${API}/blogs/${blog.id}/access-logs`)]);
      setLogData({ runs:runs.runs, logs:access.logs });
    } catch (error:any) {
      setMessage({ text:error.message, error:true });
      setLogBlog(null);
    }
  };

  if (loading) return <div className="gb-loading"><Loader2 className="gb-spin"/>正在加载…</div>;
  if (!me) return <LoginPanel onSuccess={() => fetchMe().then(result => setMe(result.user))}/>;
  return <div className="tool-page git-blog-tool">
    <header className="gb-header"><div><p className="gb-eyebrow">GITHUB PUBLISHING</p><h1><BookOpen size={24}/>Git 博客</h1><p>从 GitHub Markdown 仓库同步并发布为公开或私密站点</p></div><div className="gb-header-actions"><button className="gb-btn gb-btn-primary" onClick={() => { setBranches([]); setSnapshots([]); setForm(emptyForm()); }}><Plus size={16}/>创建博客</button><button className="gb-btn gb-btn-secondary" onClick={() => setThemeManagerOpen(true)}><Palette size={16}/>添加主题</button></div></header>
    {message && <div className={`gb-alert ${message.error ? 'error' : 'success'}`}><CircleAlert size={16}/><span>{message.text}</span><button onClick={() => setMessage(null)}>×</button></div>}
    <section className="gb-metrics"><div className="gb-metric"><span><BookOpen size={16}/>我的博客</span><strong>{stats.total}</strong><small>可独立配置的发布站点</small></div><div className="gb-metric"><span><CheckCircle2 size={16}/>同步正常</span><strong>{stats.healthy}</strong><small>每个博客最多保留 5 个快照</small></div><div className="gb-metric"><span><RefreshCw size={16}/>等待同步</span><strong>{stats.pending}</strong><small>新建、变更或手动请求后执行</small></div></section>
    <section className="gb-panel"><div className="gb-toolbar"><div><h2><FolderGit2 size={19}/>博客仓库</h2><p>管理博客来源、同步状态和访问地址。</p></div><button className="gb-btn gb-btn-secondary gb-btn-sm" onClick={() => void load()}><RefreshCw size={14}/>刷新</button></div>{blogs.length === 0 ? <div className="gb-empty"><FolderGit2 size={32}/><h3>还没有博客仓库</h3><p>连接一个 GitHub Markdown 仓库，即可开始发布。</p><button className="gb-btn gb-btn-primary" onClick={() => { setSnapshots([]); setForm(emptyForm()); }}><Plus size={16}/>创建第一个博客</button></div> : <div className="gb-table-wrap"><table className="gb-table"><thead><tr><th>博客</th><th>仓库与分支</th><th>访问地址</th><th>同步状态</th><th>最近提交</th><th/></tr></thead><tbody>{blogs.map(blog => <tr key={blog.id}><td><strong>{blog.name}</strong><code>{blog.slug}</code></td><td><div className="gb-repo">{blog.repoUrl.replace(/^https:\/\/github\.com\//, '')}</div><small>{blog.branch}{blog.contentRoot ? ` · ${blog.contentRoot}` : ''}{blog.autoSyncEnabled ? ' · 自动同步' : ' · 仅手动'}</small></td><td><a className="gb-public-link" target="_blank" rel="noreferrer" href={`/blog/${blog.slug}`}>/blog/{blog.slug}<ExternalLink size={13}/></a><small className="gb-access-summary">{blog.visibility === 'private' ? '私密' : '公开'} · 分享{blog.shareEnabled ? '开启' : '关闭'}</small></td><td><Status status={blog.syncStatus}/>{blog.lastError && <small className="gb-row-error" title={blog.lastError}>{blog.lastError}</small>}</td><td>{blog.currentCommit ? <code title={blog.currentCommit}>{blog.currentCommit.slice(0, 8)}</code> : <span className="gb-muted">尚未同步</span>}</td><td><div className="gb-table-actions"><button className="gb-icon-button" title="立即同步" onClick={() => void sync(blog.id)}><RefreshCw size={16}/></button><button className="gb-icon-button" title="编辑博客" onClick={() => openEdit(blog)}><Pencil size={16}/></button><button className="gb-icon-button" title="访问管理" onClick={() => setAccessBlog(blog)}><Shield size={16}/></button><button className="gb-icon-button" title="分享管理" onClick={() => setSharingBlog(blog)}><Share2 size={16}/></button><button className="gb-icon-button" title="查看日志" onClick={() => void logs(blog)}><ScrollText size={16}/></button><button className="gb-icon-button gb-danger" title="删除博客" onClick={() => void remove(blog)}><Trash2 size={16}/></button></div></td></tr>)}</tbody></table></div>}</section>
    {form && <div className="gb-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setForm(null); }}><form className="gb-modal" onSubmit={save}><header><div><p className="gb-eyebrow">{form.id ? 'BLOG SETTINGS' : 'NEW BLOG'}</p><h3>{form.id ? '编辑博客' : '创建博客'}</h3></div><button type="button" className="gb-close" onClick={() => setForm(null)}>×</button></header><div className="gb-modal-body"><div className="gb-form-grid"><Field label="博客名称（全局唯一）"><input className="gb-input" required placeholder="my-notes" value={form.slug} onChange={event => set({ slug:event.target.value })}/></Field><Field label="显示名称（可选）"><input className="gb-input" placeholder="留空则使用博客名称" value={form.name} onChange={event => set({ name:event.target.value })}/></Field><Field full label="GitHub 仓库 HTTPS 地址"><input className="gb-input" required placeholder="https://github.com/owner/repo" value={form.repoUrl} onChange={event => set({ repoUrl:event.target.value })}/></Field><Field label="GitHub Deploy Key · 私库需密钥"><select className="gb-input" value={form.githubKeyId} onChange={event => set({ githubKeyId:event.target.value })}><option value="">不使用（公开仓库）</option>{keys.map(key => <option value={key.id} key={key.id}>{key.name}</option>)}</select></Field><Field label="分支"><input className="gb-input" list="git-blog-branches" value={form.branch} onChange={event => set({ branch:event.target.value })}/><datalist id="git-blog-branches">{branches.map(branch => <option key={branch} value={branch}/>)}</datalist></Field><Field full label="内容目录"><input className="gb-input" placeholder="留空为仓库根目录" value={form.contentRoot} onChange={event => set({ contentRoot:event.target.value })}/></Field><Field label="文章主题"><select className="gb-input" value={selectedTheme} onChange={event => selectTheme(event.target.value)}><option value="auto">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option>{themes.length > 0 && <optgroup label="自定义 Typora 主题">{themes.map(theme => <option value={`custom:${theme.id}`} key={theme.id}>{theme.name}</option>)}</optgroup>}</select></Field><div className="gb-field gb-full-col"><span>主题色</span><AccentColorPicker value={accentColor} onChange={selectAccentColor}/></div>{form.id && <><Field label="同步周期（分钟）"><input className="gb-input" type="number" min="5" max="1440" value={form.syncIntervalMinutes} onChange={event => set({ syncIntervalMinutes:Number(event.target.value) })}/></Field><label className="gb-check gb-full-col"><input type="checkbox" checked={!!form.autoSyncEnabled} onChange={event => set({ autoSyncEnabled:event.target.checked })}/>开启周期自动同步（关闭后仍可手动同步）</label><label className="gb-check gb-full-col"><input type="checkbox" checked={!!form.config.defaults.published} onChange={event => set({ config:{ ...form.config, defaults:{ ...form.config.defaults, published:event.target.checked } } })}/>默认发布未标记的文章</label></>}</div>{form.id && <section className="gb-snapshots"><div><h4>版本快照</h4><small>自动保留最近 5 个不同提交。回滚后会关闭自动同步。</small></div>{snapshots === null ? <div className="gb-snapshot-empty"><Loader2 size={15} className="gb-spin"/>正在读取快照…</div> : snapshots.length ? <div className="gb-snapshot-list">{snapshots.map(snapshot => <div className="gb-snapshot" key={snapshot.commit}><div><code>{snapshot.commit.slice(0, 8)}</code>{snapshot.current && <span>当前</span>}<small>{new Date(snapshot.createdAt).toLocaleString()}</small></div><button type="button" className="gb-btn gb-btn-secondary gb-btn-sm" disabled={snapshot.current || !!rollingCommit} onClick={() => void rollback(snapshot)}>{rollingCommit === snapshot.commit ? <Loader2 size={14} className="gb-spin"/> : '回滚'}</button></div>)}</div> : <p className="gb-snapshot-empty">暂无可回滚快照。</p>}</section>}</div><footer><button type="button" className="gb-btn gb-btn-secondary" disabled={probing} onClick={() => void probe()}>{probing ? <Loader2 size={15} className="gb-spin"/> : <FolderGit2 size={15}/>} {probing ? '正在测试…' : '测试仓库并读取分支'}</button><span className="gb-footer-grow"/><button type="button" className="gb-btn gb-btn-ghost" onClick={() => setForm(null)}>取消</button><button className="gb-btn gb-btn-primary" disabled={saving}>{saving && <Loader2 size={15} className="gb-spin"/>}{saving ? '保存中…' : '保存博客'}</button></footer></form></div>}
    {themeManagerOpen && <ThemeManager themes={themes} close={() => setThemeManagerOpen(false)} changed={load} notify={(text,error) => setMessage({ text,error })}/>}
    {accessBlog && <AccessManager blog={accessBlog} close={() => setAccessBlog(null)} changed={load} notify={(text,error) => setMessage({ text,error })}/>}
    {sharingBlog && <SharingManager blog={sharingBlog} close={() => setSharingBlog(null)} changed={load} notify={(text,error) => setMessage({ text,error })}/>}
    {logBlog && (
      <Logs blog={logBlog} data={logData} close={() => { setLogBlog(null); setLogData(null); }}/>
    )}
  </div>;
}
