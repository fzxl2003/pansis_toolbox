import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Compass, MoreVertical, Plus, Search, Trash2, Upload, Wand2, X } from 'lucide-react';

import { ApiError, apiDelete, apiGet, apiPost, apiPostForm, apiPut } from '../../../frontend/src/api/client';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

type Strategy = 'latency_first' | 'priority_first';
type LatencyStatus = 'checking' | 'ok' | 'timeout' | 'failed';

type LinkIcon = {
  source: 'auto' | 'custom' | 'none';
  filename: string | null;
  updatedAt: string | null;
};

type LinkEntry = {
  id: string;
  label: string;
  url: string;
  probeUrl: string;
  priority: number;
};

type NavLink = {
  id: string;
  name: string;
  description: string;
  category: string;
  strategy: Strategy;
  entries: LinkEntry[];
  icon: LinkIcon;
  createdAt: string;
  updatedAt: string;
};

type LinkForm = {
  id?: string;
  name: string;
  description: string;
  category: string;
  strategy: Strategy;
  entries: Array<Omit<LinkEntry, 'id'> & { id?: string }>;
};

type ProbeResult = {
  entry: LinkEntry;
  ok: boolean;
  latency: number;
};

type LatencyResult = {
  status: LatencyStatus;
  latency?: number;
  checkedAt?: number;
};

const emptyForm: LinkForm = {
  name: '',
  description: '',
  category: '未分类',
  strategy: 'priority_first',
  entries: [{ label: '默认入口', url: '', probeUrl: '', priority: 10 }],
};

const CACHE_TTL_MS = 10 * 60 * 1000;
const PROBE_INTERVAL_MS = 60 * 1000;

export default function UrlNavigatorTool() {
  const [links, setLinks] = useState<NavLink[]>([]);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [form, setForm] = useState<LinkForm>(emptyForm);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [latencies, setLatencies] = useState<Record<string, LatencyResult>>({});
  const [error, setError] = useState<string | null>(null);
  const [loginRequired, setLoginRequired] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const uploadInputRefs = useRef<Record<string, HTMLInputElement | null>>({});

  useEffect(() => {
    void loadLinks();
  }, []);

  const categories = useMemo(() => ['all', ...Array.from(new Set(links.map((link) => link.category || '未分类')))], [links]);
  const visibleLinks = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return links.filter((link) => {
      const matchesCategory = category === 'all' || link.category === category;
      const haystack = `${link.name} ${link.description} ${link.category} ${link.entries.map((entry) => entry.label).join(' ')}`.toLowerCase();
      return matchesCategory && (!normalized || haystack.includes(normalized));
    });
  }, [category, links, query]);

  useEffect(() => {
    if (!visibleLinks.length) return undefined;
    void probeVisibleEntries(visibleLinks, setLatencies);
    const timer = window.setInterval(() => {
      void probeVisibleEntries(visibleLinks, setLatencies);
    }, PROBE_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [visibleLinks]);

  async function loadLinks() {
    setError(null);
    try {
      setLinks(await apiGet<NavLink[]>('/api/tools/url-navigator/links'));
      setLoginRequired(false);
    } catch (err) {
      handleError(err);
    }
  }

  function openCreateModal() {
    setForm(emptyForm);
    setIsModalOpen(true);
  }

  function openEditModal(link: NavLink) {
    setForm({
      id: link.id,
      name: link.name,
      description: link.description,
      category: link.category,
      strategy: link.strategy,
      entries: link.entries.map((entry) => ({ ...entry })),
    });
    setIsModalOpen(true);
  }

  async function saveLink(event: FormEvent) {
    event.preventDefault();
    setIsLoading(true);
    setError(null);
    try {
      const payload = normalizeForm(form);
      const saved = form.id
        ? await apiPut<NavLink>(`/api/tools/url-navigator/links/${form.id}`, payload)
        : await apiPost<NavLink>('/api/tools/url-navigator/links', payload);
      setLinks((items) => (form.id ? items.map((item) => (item.id === saved.id ? saved : item)) : [saved, ...items]));
      setForm(emptyForm);
      setIsModalOpen(false);
      if (!form.id) {
        void refreshIcon(saved.id, false);
      }
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function deleteLink(linkId: string) {
    setError(null);
    try {
      await apiDelete(`/api/tools/url-navigator/links/${linkId}`);
      setLinks((items) => items.filter((item) => item.id !== linkId));
    } catch (err) {
      handleError(err);
    }
  }

  async function refreshIcon(linkId: string, showStatus = true) {
    setError(null);
    if (showStatus) setStatus('正在刷新网站图标...');
    try {
      const updated = await apiPost<NavLink>(`/api/tools/url-navigator/links/${linkId}/icon/refresh`, {});
      setLinks((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      if (showStatus) setStatus('图标已更新');
    } catch (err) {
      if (showStatus) handleError(err);
    }
  }

  async function uploadIcon(linkId: string, file: File | null) {
    if (!file) return;
    setError(null);
    setStatus('正在上传图标...');
    try {
      const formData = new FormData();
      formData.append('file', file);
      const updated = await apiPostForm<NavLink>(`/api/tools/url-navigator/links/${linkId}/icon/upload`, formData);
      setLinks((items) => items.map((item) => (item.id === updated.id ? updated : item)));
      setStatus('自定义图标已保存');
    } catch (err) {
      handleError(err);
    } finally {
      const input = uploadInputRefs.current[linkId];
      if (input) input.value = '';
    }
  }

  async function autoOpen(link: NavLink) {
    setError(null);
    setStatus(`正在检测 ${link.name} 的可用入口...`);
    const cached = readCachedTarget(link);
    if (cached) {
      setStatus(`使用缓存入口：${cached.label}`);
      window.location.href = cached.url;
      return;
    }

    const results = await Promise.all(link.entries.map((entry) => probeEntry(entry, link.id)));
    setLatencies((items) => ({
      ...items,
      ...Object.fromEntries(results.map((result) => [result.entry.id, resultToLatency(result)])),
    }));
    const reachable = results.filter((result) => result.ok);
    if (!reachable.length) {
      setStatus(null);
      setError(`${link.name} 的入口均不可达，请直接选择入口尝试访问。`);
      return;
    }

    const selected = chooseEntry(reachable, link.strategy);
    writeCachedTarget(link.id, selected.entry);
    setStatus(`正在跳转：${selected.entry.label}，${Math.round(selected.latency)}ms`);
    window.location.href = selected.entry.url;
  }

  function handleError(err: unknown) {
    if (err instanceof ApiError && err.code === 'LOGIN_REQUIRED') {
      setLoginRequired(true);
      setError(null);
      return;
    }
    setError(err instanceof Error ? err.message : '操作失败');
  }

  if (loginRequired) {
    return (
      <div className="tool-surface">
        <div className="tool-header">
          <div>
            <p className="eyebrow">Personal Network Tool</p>
            <h1>网址导航</h1>
          </div>
        </div>
        <LoginPanel onSuccess={() => void loadLinks()} />
      </div>
    );
  }

  return (
    <div className="tool-surface url-navigator">
      <div className="tool-header">
        <div>
          <p className="eyebrow">Personal Network Tool</p>
          <h1>网址导航</h1>
        </div>
        <button className="primary-button" type="button" onClick={openCreateModal}>
          <Plus size={16} />
          新建导航
        </button>
      </div>

      {error && <div className="error-box">{error}</div>}
      {status && <div className="panel compact-panel">{status}</div>}

      <section className="panel nav-control-panel">
        <div className="search-box">
          <Search size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索网站、分类或入口" />
        </div>
        <div className="toolbar">
          {categories.map((item) => (
            <button key={item} className={category === item ? 'chip active' : 'chip'} type="button" onClick={() => setCategory(item)}>
              {item === 'all' ? '全部' : item}
            </button>
          ))}
        </div>
      </section>

      {visibleLinks.length === 0 ? (
        <div className="empty-state">还没有匹配的网址导航。</div>
      ) : (
        <section className="navigator-card-grid">
          {visibleLinks.map((link) => (
            <article className="panel navigator-site-card" key={link.id}>
              <div className="site-card-top">
                <button className="site-main" type="button" onClick={() => void autoOpen(link)} title="自动选择最佳入口访问">
                  <SiteIcon link={link} />
                  <span>
                    <strong>{link.name}</strong>
                    <small>{link.description || '暂无描述'}</small>
                  </span>
                </button>
                <details className="site-menu">
                  <summary aria-label={`${link.name} 更多操作`}>
                    <MoreVertical size={18} />
                  </summary>
                  <div className="site-menu-popover">
                    <button type="button" onClick={() => openEditModal(link)}>编辑</button>
                    <button type="button" onClick={() => void refreshIcon(link.id)}>刷新图标</button>
                    <button type="button" onClick={() => uploadInputRefs.current[link.id]?.click()}>
                      <Upload size={14} />
                      上传图标
                    </button>
                    <button className="danger-text" type="button" onClick={() => void deleteLink(link.id)}>
                      <Trash2 size={14} />
                      删除
                    </button>
                  </div>
                </details>
                <input
                  ref={(element) => {
                    uploadInputRefs.current[link.id] = element;
                  }}
                  className="hidden-file-input"
                  type="file"
                  accept=".ico,.png,.jpg,.jpeg,.webp,.svg,image/*"
                  onChange={(event) => void uploadIcon(link.id, event.target.files?.[0] ?? null)}
                />
              </div>

              <div className="site-meta">
                <span>{link.category || '未分类'}</span>
                <span>{link.strategy === 'latency_first' ? '延迟优先' : '优先级优先'}</span>
              </div>

              <div className="entry-pill-list">
                {link.entries.map((entry) => (
                  <a key={entry.id} className={`entry-pill ${latencyClass(latencies[entry.id])}`} href={entry.url}>
                    <span>{entry.label}</span>
                    <small>优先级 {entry.priority}</small>
                    <em>{latencyLabel(latencies[entry.id])}</em>
                  </a>
                ))}
              </div>
            </article>
          ))}
        </section>
      )}

      {isModalOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={form.id ? '编辑导航' : '新建导航'}>
          <section className="panel navigator-modal">
            <div className="result-header">
              <span>{form.id ? '编辑导航' : '新建导航'}</span>
              <button className="icon-button" type="button" onClick={() => setIsModalOpen(false)} title="关闭">
                <X size={16} />
              </button>
            </div>
            <form onSubmit={saveLink}>
              <label className="field-label" htmlFor="link-name">名称</label>
              <input id="link-name" className="text-input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />

              <label className="field-label" htmlFor="link-description">描述</label>
              <input
                id="link-description"
                className="text-input"
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
              />

              <div className="form-row">
                <label>
                  <span className="field-label">分类</span>
                  <input className="text-input" value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })} />
                </label>
                <label>
                  <span className="field-label">策略</span>
                  <select className="select" value={form.strategy} onChange={(event) => setForm({ ...form, strategy: event.target.value as Strategy })}>
                    <option value="priority_first">优先级优先</option>
                    <option value="latency_first">延迟优先</option>
                  </select>
                </label>
              </div>

              <div className="result-header entry-form-title">
                <span>访问入口</span>
                <button className="secondary-button" type="button" onClick={() => addEntry(form, setForm)}>
                  <Plus size={16} />
                  添加入口
                </button>
              </div>

              {form.entries.map((entry, index) => (
                <div className="entry-form" key={entry.id ?? index}>
                  <div className="form-row">
                    <label>
                      <span className="field-label">标签</span>
                      <input className="text-input" value={entry.label} onChange={(event) => updateEntry(form, setForm, index, 'label', event.target.value)} />
                    </label>
                    <label>
                      <span className="field-label">优先级</span>
                      <input
                        className="text-input"
                        type="number"
                        value={entry.priority}
                        onChange={(event) => updateEntry(form, setForm, index, 'priority', Number(event.target.value))}
                      />
                    </label>
                  </div>
                  <label className="field-label">访问 URL</label>
                  <input className="text-input" value={entry.url} onChange={(event) => updateEntry(form, setForm, index, 'url', event.target.value)} />
                  <label className="field-label">探测 URL</label>
                  <input className="text-input" value={entry.probeUrl} onChange={(event) => updateEntry(form, setForm, index, 'probeUrl', event.target.value)} />
                  {form.entries.length > 1 && (
                    <button className="secondary-button danger-text" type="button" onClick={() => removeEntry(form, setForm, index)}>
                      删除入口
                    </button>
                  )}
                </div>
              ))}

              <button className="primary-button full-button" type="submit" disabled={isLoading}>
                {isLoading ? '保存中' : '保存导航'}
              </button>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}

function SiteIcon({ link }: { link: NavLink }) {
  if (link.icon?.filename && link.icon.updatedAt) {
    return <img className="site-icon" src={`/api/tools/url-navigator/links/${link.id}/icon?version=${encodeURIComponent(link.icon.updatedAt)}`} alt={`${link.name} 图标`} />;
  }
  const initial = link.name.trim().slice(0, 1).toUpperCase();
  return <span className="site-icon fallback-icon">{initial || <Compass size={20} />}</span>;
}

function normalizeForm(form: LinkForm) {
  return {
    name: form.name,
    description: form.description,
    category: form.category || '未分类',
    strategy: form.strategy,
    entries: form.entries.map((entry) => ({
      id: entry.id,
      label: entry.label,
      url: entry.url,
      probeUrl: entry.probeUrl,
      priority: Number.isFinite(entry.priority) ? entry.priority : 10,
    })),
  };
}

function addEntry(form: LinkForm, setForm: (form: LinkForm) => void) {
  setForm({ ...form, entries: [...form.entries, { label: '备用入口', url: '', probeUrl: '', priority: 10 }] });
}

function updateEntry<K extends keyof LinkForm['entries'][number]>(
  form: LinkForm,
  setForm: (form: LinkForm) => void,
  index: number,
  key: K,
  value: LinkForm['entries'][number][K],
) {
  setForm({
    ...form,
    entries: form.entries.map((entry, entryIndex) => (entryIndex === index ? { ...entry, [key]: value } : entry)),
  });
}

function removeEntry(form: LinkForm, setForm: (form: LinkForm) => void, index: number) {
  setForm({ ...form, entries: form.entries.filter((_, entryIndex) => entryIndex !== index) });
}

function chooseEntry(results: ProbeResult[], strategy: Strategy) {
  return [...results].sort((a, b) => {
    if (strategy === 'priority_first') {
      return a.entry.priority - b.entry.priority || a.latency - b.latency;
    }
    return a.latency - b.latency || a.entry.priority - b.entry.priority;
  })[0];
}

async function probeVisibleEntries(links: NavLink[], setLatencies: (updater: (items: Record<string, LatencyResult>) => Record<string, LatencyResult>) => void) {
  const entries = links.flatMap((link) => link.entries.map((entry) => ({ entry, linkId: link.id })));
  setLatencies((items) => ({
    ...items,
    ...Object.fromEntries(entries.map(({ entry }) => [entry.id, { status: 'checking' as const, checkedAt: Date.now() }])),
  }));
  const results = await Promise.all(entries.map(({ entry, linkId }) => probeEntry(entry, linkId)));
  setLatencies((items) => ({
    ...items,
    ...Object.fromEntries(results.map((result) => [result.entry.id, resultToLatency(result)])),
  }));
}

function probeEntry(entry: LinkEntry, linkId: string): Promise<ProbeResult> {
  const target = entry.probeUrl || entry.url;
  const startedAt = performance.now();
  return new Promise((resolve) => {
    const image = new Image();
    const timer = window.setTimeout(() => {
      cleanup();
      resolve({ entry, ok: false, latency: performance.now() - startedAt });
    }, 1500);

    function cleanup() {
      window.clearTimeout(timer);
      image.onload = null;
      image.onerror = null;
    }

    image.onload = () => {
      cleanup();
      resolve({ entry, ok: true, latency: performance.now() - startedAt });
    };
    image.onerror = () => {
      cleanup();
      resolve({ entry, ok: false, latency: performance.now() - startedAt });
    };
    image.src = `${target}${target.includes('?') ? '&' : '?'}_toolbox_probe=${encodeURIComponent(linkId)}_${Date.now()}`;
  });
}

function resultToLatency(result: ProbeResult): LatencyResult {
  if (result.ok) {
    return { status: 'ok', latency: Math.round(result.latency), checkedAt: Date.now() };
  }
  return { status: result.latency >= 1490 ? 'timeout' : 'failed', latency: Math.round(result.latency), checkedAt: Date.now() };
}

function latencyLabel(result?: LatencyResult) {
  if (!result) return '待检测';
  if (result.status === 'checking') return '检测中';
  if (result.status === 'ok') return `${result.latency} ms`;
  if (result.status === 'timeout') return '超时';
  return '失败';
}

function latencyClass(result?: LatencyResult) {
  if (!result) return 'unknown';
  return result.status;
}

function cacheKey(linkId: string) {
  return `url_navigator:${linkId}`;
}

function readCachedTarget(link: NavLink): LinkEntry | null {
  const raw = localStorage.getItem(cacheKey(link.id));
  if (!raw) return null;
  try {
    const payload = JSON.parse(raw) as { entryId: string; expiresAt: number };
    if (payload.expiresAt < Date.now()) {
      localStorage.removeItem(cacheKey(link.id));
      return null;
    }
    return link.entries.find((entry) => entry.id === payload.entryId) ?? null;
  } catch {
    localStorage.removeItem(cacheKey(link.id));
    return null;
  }
}

function writeCachedTarget(linkId: string, entry: LinkEntry) {
  localStorage.setItem(cacheKey(linkId), JSON.stringify({ entryId: entry.id, expiresAt: Date.now() + CACHE_TTL_MS }));
}
