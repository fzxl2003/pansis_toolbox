import { useEffect, useState } from 'react';
import { Activity, ExternalLink } from 'lucide-react';
import { Link } from 'react-router-dom';

import { fetchWidgetData, type Widget, type WidgetData } from '../api/widgets';

export function WidgetHost({ widget }: { widget: Widget }) {
  const [data, setData] = useState<WidgetData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchWidgetData(widget.id)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [widget.id]);

  return (
    <section className="widget">
      <div className="widget-heading">
        <Activity size={18} />
        <span>{data?.title ?? widget.name}</span>
      </div>
      {error ? (
        <p className="muted">小组件数据加载失败。</p>
      ) : (
        <p className="muted">{formatWidgetData(data?.data) || '正在加载状态...'}</p>
      )}
      <Link className="inline-link" to={`/tools/${widget.toolId}`}>
        <ExternalLink size={15} />
        打开工具
      </Link>
    </section>
  );
}

function formatWidgetData(data: Record<string, unknown> | undefined) {
  if (!data) return '';
  if (typeof data.description === 'string') return data.description;
  if (typeof data.status === 'string') return `状态：${data.status}`;
  return Object.entries(data)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(' · ');
}
