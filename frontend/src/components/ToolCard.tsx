import { AlertTriangle, CircleOff, Database, Star, Wrench } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { ToolManifest } from '../api/tools';

const statusLabel: Record<ToolManifest['status'], string> = {
  available: '可用',
  disabled: '已禁用',
  failed: '加载失败',
  missing_frontend: '缺少前端',
  missing_backend: '缺少后端',
  dependency_failed: '依赖不可用',
};

export function ToolCard({ tool }: { tool: ToolManifest }) {
  const isAvailable = tool.status === 'available';
  return (
    <article className="tool-card">
      <div className="card-topline">
        <div className="tool-icon"><Wrench size={20} /></div>
        <span className={`status-pill ${isAvailable ? 'ok' : 'warn'}`}>
          {isAvailable ? <Star size={14} /> : tool.status === 'disabled' ? <CircleOff size={14} /> : <AlertTriangle size={14} />}
          {statusLabel[tool.status]}
        </span>
      </div>
      <h3>{tool.name}</h3>
      <p>{tool.description}</p>
      <div className="card-footer">
        <span>{tool.category}</span>
        {tool.permissions.userData && <span className="data-badge"><Database size={14} />个人数据</span>}
        {isAvailable ? <Link to={`/tools/${tool.id}`}>打开</Link> : <span title={tool.errorMessage ?? ''}>不可用</span>}
      </div>
    </article>
  );
}
