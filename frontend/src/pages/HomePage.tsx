import { Search, SlidersHorizontal } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { fetchTools, type ToolManifest } from '../api/tools';
import { fetchWidgets, type Widget } from '../api/widgets';
import { ToolCard } from '../components/ToolCard';
import { WidgetHost } from '../components/WidgetHost';

export function HomePage() {
  const [tools, setTools] = useState<ToolManifest[]>([]);
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('all');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchTools(), fetchWidgets()])
      .then(([toolPayload, widgetPayload]) => {
        setTools(toolPayload);
        setWidgets(widgetPayload);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  const categories = useMemo(() => ['all', ...Array.from(new Set(tools.map((tool) => tool.category)))], [tools]);
  const visibleTools = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return tools.filter((tool) => {
      const matchesCategory = category === 'all' || tool.category === category;
      const matchesQuery =
        !normalizedQuery ||
        tool.name.toLowerCase().includes(normalizedQuery) ||
        tool.description.toLowerCase().includes(normalizedQuery);
      return matchesCategory && matchesQuery;
    });
  }, [category, query, tools]);

  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Tool Platform</p>
          <h1>工具箱</h1>
        </div>
        <div className="search-box">
          <Search size={18} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工具" />
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
    </div>
  );
}
