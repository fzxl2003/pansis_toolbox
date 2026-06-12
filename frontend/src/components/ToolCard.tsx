import { AlertTriangle, CircleOff, Compass, Database, FileText, Notebook, Server, Star, Wrench } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { ToolIcon, ToolManifest } from '../api/tools';

const statusLabel: Record<ToolManifest['status'], string> = {
  available: '可用',
  disabled: '已禁用',
  failed: '加载失败',
  missing_frontend: '缺少前端',
  missing_backend: '缺少后端',
  dependency_failed: '依赖不可用',
};

const lucideIcons: Record<string, LucideIcon> = {
  compass: Compass,
  notebook: Notebook,
  server: Server,
  text: FileText,
  wrench: Wrench,
};

export function ToolCard({ tool }: { tool: ToolManifest }) {
  const isAvailable = tool.status === 'available';
  return (
    <article className="tool-card">
      <div className="card-topline">
        <ToolIconView icon={tool.icon} toolId={tool.id} toolName={tool.name} />
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

function ToolIconView({ icon, toolId, toolName }: { icon: ToolIcon; toolId: string; toolName: string }) {
  const normalized = normalizeIcon(icon);
  if (normalized.type === 'image') {
    return (
      <div className="tool-icon image-icon">
        <img src={resolveIconSrc(toolId, normalized.src)} alt={normalized.alt || `${toolName} 图标`} />
      </div>
    );
  }

  const Icon = lucideIcons[normalized.name] ?? Wrench;
  return (
    <div className="tool-icon">
      <Icon size={22} />
    </div>
  );
}

function normalizeIcon(icon: ToolIcon): Exclude<ToolIcon, string> {
  if (typeof icon === 'string') {
    return { type: 'lucide', name: icon };
  }
  if (icon.type === 'image') {
    return { type: 'image', src: icon.src, alt: icon.alt };
  }
  return { type: 'lucide', name: icon.name || 'wrench', alt: icon.alt };
}

function resolveIconSrc(toolId: string, src: string) {
  if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('/')) {
    return src;
  }
  return `/tool-assets/${toolId}/${src}`;
}
