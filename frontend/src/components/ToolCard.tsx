import { Compass, FileText, Notebook, Server, Wrench } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Link } from 'react-router-dom';

import type { ToolIcon, ToolManifest } from '../api/tools';

const lucideIcons: Record<string, LucideIcon> = {
  compass: Compass,
  notebook: Notebook,
  server: Server,
  text: FileText,
  wrench: Wrench,
};

export function ToolCard({ tool }: { tool: ToolManifest }) {
  const isAvailable = tool.status === 'available';
  const content = (
    <>
      <div className="card-topline">
        <ToolIconView icon={tool.icon} toolId={tool.id} toolName={tool.name} />
        <h3>{tool.name}</h3>
        <span className="tool-category">{tool.category}</span>
      </div>
      <div className="tool-card-copy">
        <p>{tool.description}</p>
      </div>
    </>
  );

  if (isAvailable) {
    return (
      <Link className="tool-card tool-card-link" to={`/tools/${tool.id}`} aria-label={`打开 ${tool.name}`}>
        {content}
      </Link>
    );
  }

  return (
    <article className="tool-card tool-card-disabled" aria-disabled="true" title={tool.errorMessage ?? undefined}>
      {content}
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
