import { ArrowDown, ArrowUp, Eye, EyeOff, Search, Settings2, SlidersHorizontal, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { fetchTools, type ToolManifest } from '../api/tools';
import { fetchMe } from '../api/auth';
import { fetchWidgets, type Widget } from '../api/widgets';
import { ToolCard } from '../components/ToolCard';
import { WidgetHost } from '../components/WidgetHost';

type ToolPreferences = { hiddenIds: string[]; orderedIds: string[] };

const emptyPreferences: ToolPreferences = { hiddenIds: [], orderedIds: [] };

function preferenceStorageKey(userId: string | null) {
  return `pansis.home-tool-preferences.v1.${userId ?? 'anonymous'}`;
}

function readPreferences(userId: string | null): ToolPreferences {
  try {
    const raw = window.localStorage.getItem(preferenceStorageKey(userId));
    if (!raw) return emptyPreferences;
    const parsed = JSON.parse(raw) as Partial<ToolPreferences>;
    return {
      hiddenIds: Array.isArray(parsed.hiddenIds) ? parsed.hiddenIds : [],
      orderedIds: Array.isArray(parsed.orderedIds) ? parsed.orderedIds : [],
    };
  } catch {
    return emptyPreferences;
  }
}

export function HomePage() {
  const [tools, setTools] = useState<ToolManifest[]>([]);
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [error, setError] = useState<string | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [preferences, setPreferences] = useState<ToolPreferences>(emptyPreferences);
  const [showCustomizer, setShowCustomizer] = useState(false);
  const [draftPreferences, setDraftPreferences] = useState<ToolPreferences>(emptyPreferences);

  useEffect(() => {
    Promise.all([fetchTools(), fetchWidgets(), fetchMe().catch(() => ({ user: null }))])
      .then(([toolPayload, widgetPayload, auth]) => {
        setTools(toolPayload);
        setWidgets(widgetPayload);
        const id = auth.user?.id ?? null;
        setUserId(id);
        setPreferences(readPreferences(id));
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  const categories = useMemo(() => ['all', ...Array.from(new Set(tools.map((tool) => tool.category)))], [tools]);
  const visibleTools = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const order = new Map(preferences.orderedIds.map((id, index) => [id, index]));
    return tools
      .filter((tool) => !preferences.hiddenIds.includes(tool.id))
      .filter((tool) => {
        const matchesCategory = category === 'all' || tool.category === category;
        const matchesQuery =
          !normalizedQuery ||
          tool.name.toLowerCase().includes(normalizedQuery) ||
          tool.description.toLowerCase().includes(normalizedQuery);
        return matchesCategory && matchesQuery;
      })
      .sort((a, b) => {
        const unavailableDelta = Number(a.status !== 'available') - Number(b.status !== 'available');
        if (unavailableDelta) return unavailableDelta;
        return (order.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (order.get(b.id) ?? Number.MAX_SAFE_INTEGER);
      });
  }, [category, preferences, query, tools]);

  const customizerTools = useMemo(() => {
    const order = new Map(preferences.orderedIds.map((id, index) => [id, index]));
    return [...tools].sort((a, b) => (order.get(a.id) ?? Number.MAX_SAFE_INTEGER) - (order.get(b.id) ?? Number.MAX_SAFE_INTEGER));
  }, [preferences.orderedIds, tools]);

  function openCustomizer() {
    setDraftPreferences({ hiddenIds: [...preferences.hiddenIds], orderedIds: customizerTools.map((tool) => tool.id) });
    setShowCustomizer(true);
  }

  function toggleToolVisibility(toolId: string) {
    setDraftPreferences((current) => ({
      ...current,
      hiddenIds: current.hiddenIds.includes(toolId)
        ? current.hiddenIds.filter((id) => id !== toolId)
        : [...current.hiddenIds, toolId],
    }));
  }

  function moveTool(toolId: string, direction: -1 | 1) {
    setDraftPreferences((current) => {
      const index = current.orderedIds.indexOf(toolId);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.orderedIds.length) return current;
      const orderedIds = [...current.orderedIds];
      [orderedIds[index], orderedIds[target]] = [orderedIds[target], orderedIds[index]];
      return { ...current, orderedIds };
    });
  }

  function saveCustomizer() {
    setPreferences(draftPreferences);
    window.localStorage.setItem(preferenceStorageKey(userId), JSON.stringify(draftPreferences));
    setShowCustomizer(false);
  }

  function resetCustomizer() {
    setDraftPreferences({ hiddenIds: [], orderedIds: tools.map((tool) => tool.id) });
  }

  return (
    <div className="page-stack home-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Tool Platform</p>
          <h1>工具箱</h1>
        </div>
        <div className="home-header-actions">
          <div className="search-box">
            <Search size={18} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工具" />
          </div>
          <button className="home-customize-button" type="button" onClick={openCustomizer}>
            <Settings2 size={16} /> 自定义工具
          </button>
        </div>
      </header>

      {error && <div className="error-box">接口加载失败：{error}</div>}

      <section className="toolbar">
        <SlidersHorizontal size={18} />
        {categories.map((item) => (
          <button
            key={item}
            type="button"
            className={item === category ? 'chip active' : 'chip'}
            onClick={() => setCategory(item)}
          >
            {item === 'all' ? '全部' : item}
          </button>
        ))}
      </section>

      <section className="content-band">
        <div className="tool-grid-list">
          {visibleTools.map((tool) => <ToolCard key={tool.id} tool={tool} />)}
        </div>
      </section>

      {widgets.length > 0 && (
        <section className="content-band">
          <div className="widget-grid">
            {widgets.map((widget) => <WidgetHost key={widget.id} widget={widget} />)}
          </div>
        </section>
      )}

      {showCustomizer && (
        <div className="home-customizer-backdrop" role="presentation" onMouseDown={() => setShowCustomizer(false)}>
          <section className="home-customizer-modal" role="dialog" aria-modal="true" aria-label="自定义首页工具" onMouseDown={(event) => event.stopPropagation()}>
            <div className="home-customizer-header">
              <div><h2>自定义工具</h2><p>调整首页显示的工具及其顺序。</p></div>
              <button type="button" className="home-icon-button" onClick={() => setShowCustomizer(false)} aria-label="关闭"><X size={18} /></button>
            </div>
            <div className="home-customizer-list">
              {draftPreferences.orderedIds.map((toolId, index) => {
                const tool = tools.find((item) => item.id === toolId);
                if (!tool) return null;
                const hidden = draftPreferences.hiddenIds.includes(tool.id);
                return (
                  <div className={`home-customizer-row${tool.status !== 'available' ? ' unavailable' : ''}`} key={tool.id}>
                    <button type="button" className="home-visibility-button" onClick={() => toggleToolVisibility(tool.id)} title={hidden ? '显示工具' : '隐藏工具'}>
                      {hidden ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                    <div className="home-customizer-name"><strong>{tool.name}</strong><span>{hidden ? '已隐藏' : tool.category}</span></div>
                    <div className="home-sort-actions">
                      <button type="button" onClick={() => moveTool(tool.id, -1)} disabled={index === 0} aria-label="上移"><ArrowUp size={15} /></button>
                      <button type="button" onClick={() => moveTool(tool.id, 1)} disabled={index === draftPreferences.orderedIds.length - 1} aria-label="下移"><ArrowDown size={15} /></button>
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="home-customizer-footer">
              <button type="button" className="home-text-button" onClick={resetCustomizer}>恢复默认</button>
              <div><button type="button" className="home-text-button" onClick={() => setShowCustomizer(false)}>取消</button><button type="button" className="home-save-button" onClick={saveCustomizer}>保存</button></div>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
