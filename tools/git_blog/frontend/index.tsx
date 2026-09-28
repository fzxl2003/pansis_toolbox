import './style.css';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { BookOpen, CheckCircle2, CircleAlert, ExternalLink, FolderGit2, Loader2, Pencil, Plus, RefreshCw, ScrollText, Trash2 } from 'lucide-react';
import { apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { fetchMe, type AuthUser } from '../../../frontend/src/api/auth';
import { fetchGithubKeys, type GithubKey } from '../../../frontend/src/api/settings';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

const API = '/api/tools/git-blog';
type Blog = { id:string; name:string; slug:string; repoUrl:string; branch:string; contentRoot:string; githubKeyId:string; syncIntervalMinutes:number; autoSyncEnabled:boolean; config:any; syncStatus:string; lastError:string; currentCommit:string };
type Snapshot = { commit:string; createdAt:string; current:boolean };
type Form = ReturnType<typeof emptyForm> & { id?:string };
const emptyForm = () => ({ name:'', slug:'', repoUrl:'', branch:'main', contentRoot:'', githubKeyId:'', syncIntervalMinutes:15, autoSyncEnabled:true, config:{ site:{ theme:'auto' }, defaults:{ published:false }, logs:{ accessEnabled:true, recordIp:false, recordUserAgent:true, recordReferrer:true, retentionDays:30 } } });

function Field({ label, children, full = false }: { label:string; children:ReactNode; full?:boolean }) {
  return <label className={`gb-field${full ? ' gb-full-col' : ''}`}><span>{label}</span>{children}</label>;
}

function Status({ status }: { status:string }) {
  const value = status.toLowerCase();
  const kind = value.includes('success') || value === 'idle' ? 'success' : value.includes('fail') || value.includes('error') ? 'error' : value.includes('sync') || value.includes('running') ? 'working' : 'pending';
  const Icon = kind === 'success' ? CheckCircle2 : kind === 'error' ? CircleAlert : kind === 'working' ? Loader2 : RefreshCw;
  return <span className={`gb-badge gb-badge-${kind}`}><Icon size={13} className={kind === 'working' ? 'gb-spin' : ''}/>{{ success:'同步正常', error:'同步失败', working:'同步中', pending:'等待同步' }[kind]}</span>;
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

export default function GitBlogTool() {
  const [me, setMe] = useState<AuthUser|null>(null);
  const [blogs, setBlogs] = useState<Blog[]>([]);
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

  const load = async () => {
    try {
      const [result, githubKeys] = await Promise.all([apiGet<{ blogs:Blog[] }>(`${API}/blogs`), fetchGithubKeys()]);
      setBlogs(result.blogs);
      setKeys(githubKeys.keys);
    } catch (error:any) {
      setMessage({ text:error.message, error:true });
    }
  };
  useEffect(() => { fetchMe().then(result => setMe(result.user)).catch(() => setMe(null)).finally(() => setLoading(false)); }, []);
  useEffect(() => { if (me) void load(); }, [me]);
  const stats = useMemo(() => ({
    total:blogs.length,
    healthy:blogs.filter(blog => ['success', 'idle'].some(value => blog.syncStatus.toLowerCase().includes(value))).length,
    pending:blogs.filter(blog => /sync|running|pending/.test(blog.syncStatus.toLowerCase())).length,
  }), [blogs]);
  const set = (value:Partial<Form>) => setForm(current => current ? { ...current, ...value } : current);

  const save = async (event:React.FormEvent) => {
    event.preventDefault();
    if (!form) return;
    setSaving(true);
    try {
      const result = form.id ? await apiPut<{ blog:Blog }>(`${API}/blogs/${form.id}`, form) : await apiPost<{ blog:Blog }>(`${API}/blogs`, form);
      setMessage({ text:result.blog.autoSyncEnabled ? `已保存「${result.blog.name}」，正在等待同步。` : `已保存「${result.blog.name}」，自动同步已关闭。` });
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
    <header className="gb-header"><div><p className="gb-eyebrow">GITHUB PUBLISHING</p><h1><BookOpen size={24}/>Git 博客</h1><p>从 GitHub Markdown 仓库同步并公开发布</p></div><button className="gb-btn gb-btn-primary" onClick={() => { setBranches([]); setSnapshots([]); setForm(emptyForm()); }}><Plus size={16}/>创建博客</button></header>
    {message && <div className={`gb-alert ${message.error ? 'error' : 'success'}`}><CircleAlert size={16}/><span>{message.text}</span><button onClick={() => setMessage(null)}>×</button></div>}
    <section className="gb-metrics"><div className="gb-metric"><span><BookOpen size={16}/>我的博客</span><strong>{stats.total}</strong><small>可独立配置的发布站点</small></div><div className="gb-metric"><span><CheckCircle2 size={16}/>同步正常</span><strong>{stats.healthy}</strong><small>每个博客最多保留 5 个快照</small></div><div className="gb-metric"><span><RefreshCw size={16}/>等待同步</span><strong>{stats.pending}</strong><small>新建、变更或手动请求后执行</small></div></section>
    <section className="gb-panel"><div className="gb-toolbar"><div><h2><FolderGit2 size={19}/>博客仓库</h2><p>管理博客来源、同步状态和公开地址。</p></div><button className="gb-btn gb-btn-secondary gb-btn-sm" onClick={() => void load()}><RefreshCw size={14}/>刷新</button></div>{blogs.length === 0 ? <div className="gb-empty"><FolderGit2 size={32}/><h3>还没有博客仓库</h3><p>连接一个 GitHub Markdown 仓库，即可开始发布。</p><button className="gb-btn gb-btn-primary" onClick={() => { setSnapshots([]); setForm(emptyForm()); }}><Plus size={16}/>创建第一个博客</button></div> : <div className="gb-table-wrap"><table className="gb-table"><thead><tr><th>博客</th><th>仓库与分支</th><th>公开地址</th><th>同步状态</th><th>最近提交</th><th/></tr></thead><tbody>{blogs.map(blog => <tr key={blog.id}><td><strong>{blog.name}</strong><code>{blog.slug}</code></td><td><div className="gb-repo">{blog.repoUrl.replace(/^https:\/\/github\.com\//, '')}</div><small>{blog.branch}{blog.contentRoot ? ` · ${blog.contentRoot}` : ''}{blog.autoSyncEnabled ? ' · 自动同步' : ' · 仅手动'}</small></td><td><a className="gb-public-link" target="_blank" rel="noreferrer" href={`/blog/${blog.slug}`}>/blog/{blog.slug}<ExternalLink size={13}/></a></td><td><Status status={blog.syncStatus}/>{blog.lastError && <small className="gb-row-error" title={blog.lastError}>{blog.lastError}</small>}</td><td>{blog.currentCommit ? <code title={blog.currentCommit}>{blog.currentCommit.slice(0, 8)}</code> : <span className="gb-muted">尚未同步</span>}</td><td><div className="gb-table-actions"><button className="gb-icon-button" title="立即同步" onClick={() => void sync(blog.id)}><RefreshCw size={16}/></button><button className="gb-icon-button" title="编辑博客" onClick={() => openEdit(blog)}><Pencil size={16}/></button><button className="gb-icon-button" title="查看日志" onClick={() => void logs(blog)}><ScrollText size={16}/></button><button className="gb-icon-button gb-danger" title="删除博客" onClick={() => void remove(blog)}><Trash2 size={16}/></button></div></td></tr>)}</tbody></table></div>}</section>
    {form && <div className="gb-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setForm(null); }}><form className="gb-modal" onSubmit={save}><header><div><p className="gb-eyebrow">{form.id ? 'BLOG SETTINGS' : 'NEW BLOG'}</p><h3>{form.id ? '编辑博客' : '创建博客'}</h3></div><button type="button" className="gb-close" onClick={() => setForm(null)}>×</button></header><div className="gb-modal-body"><div className="gb-form-grid"><Field label="博客名称（全局唯一）"><input className="gb-input" required placeholder="my-notes" value={form.slug} onChange={event => set({ slug:event.target.value })}/></Field><Field label="显示名称（可选）"><input className="gb-input" placeholder="留空则使用博客名称" value={form.name} onChange={event => set({ name:event.target.value })}/></Field><Field full label="GitHub 仓库 HTTPS 地址"><input className="gb-input" required placeholder="https://github.com/owner/repo" value={form.repoUrl} onChange={event => set({ repoUrl:event.target.value })}/></Field><Field label="GitHub Deploy Key · 私库需密钥"><select className="gb-input" value={form.githubKeyId} onChange={event => set({ githubKeyId:event.target.value })}><option value="">不使用（公开仓库）</option>{keys.map(key => <option value={key.id} key={key.id}>{key.name}</option>)}</select></Field><Field label="分支"><input className="gb-input" list="git-blog-branches" value={form.branch} onChange={event => set({ branch:event.target.value })}/><datalist id="git-blog-branches">{branches.map(branch => <option key={branch} value={branch}/>)}</datalist></Field><Field full label="内容目录"><input className="gb-input" placeholder="留空为仓库根目录" value={form.contentRoot} onChange={event => set({ contentRoot:event.target.value })}/></Field>{form.id && <><Field label="同步周期（分钟）"><input className="gb-input" type="number" min="5" max="1440" value={form.syncIntervalMinutes} onChange={event => set({ syncIntervalMinutes:Number(event.target.value) })}/></Field><Field label="文章主题"><select className="gb-input" value={form.config.site.theme} onChange={event => set({ config:{ ...form.config, site:{ ...form.config.site, theme:event.target.value } } })}><option value="auto">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></Field><label className="gb-check gb-full-col"><input type="checkbox" checked={!!form.autoSyncEnabled} onChange={event => set({ autoSyncEnabled:event.target.checked })}/>开启周期自动同步（关闭后仍可手动同步）</label><label className="gb-check gb-full-col"><input type="checkbox" checked={!!form.config.defaults.published} onChange={event => set({ config:{ ...form.config, defaults:{ ...form.config.defaults, published:event.target.checked } } })}/>默认发布未标记的文章</label></>}</div>{form.id && <section className="gb-snapshots"><div><h4>版本快照</h4><small>自动保留最近 5 个不同提交。回滚后会关闭自动同步。</small></div>{snapshots === null ? <div className="gb-snapshot-empty"><Loader2 size={15} className="gb-spin"/>正在读取快照…</div> : snapshots.length ? <div className="gb-snapshot-list">{snapshots.map(snapshot => <div className="gb-snapshot" key={snapshot.commit}><div><code>{snapshot.commit.slice(0, 8)}</code>{snapshot.current && <span>当前</span>}<small>{new Date(snapshot.createdAt).toLocaleString()}</small></div><button type="button" className="gb-btn gb-btn-secondary gb-btn-sm" disabled={snapshot.current || !!rollingCommit} onClick={() => void rollback(snapshot)}>{rollingCommit === snapshot.commit ? <Loader2 size={14} className="gb-spin"/> : '回滚'}</button></div>)}</div> : <p className="gb-snapshot-empty">暂无可回滚快照。</p>}</section>}</div><footer><button type="button" className="gb-btn gb-btn-secondary" disabled={probing} onClick={() => void probe()}>{probing ? <Loader2 size={15} className="gb-spin"/> : <FolderGit2 size={15}/>} {probing ? '正在测试…' : '测试仓库并读取分支'}</button><span className="gb-footer-grow"/><button type="button" className="gb-btn gb-btn-ghost" onClick={() => setForm(null)}>取消</button><button className="gb-btn gb-btn-primary" disabled={saving}>{saving && <Loader2 size={15} className="gb-spin"/>}{saving ? '保存中…' : '保存博客'}</button></footer></form></div>}
    {logBlog && (
      <Logs blog={logBlog} data={logData} close={() => { setLogBlog(null); setLogData(null); }}/>
    )}
  </div>;
}
