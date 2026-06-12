import { ArrowLeft, Maximize2, Minimize2 } from 'lucide-react';
import { Suspense, useEffect, useState } from 'react';
import { Link, useOutletContext, useParams, useSearchParams } from 'react-router-dom';

import { fetchTool, type ToolManifest } from '../api/tools';
import type { ShellDisplayMode } from '../components/AppShell';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { toolViews } from '../registry/localToolViews';

type ToolDisplayMode = ToolManifest['displayMode'];

export function ToolPage() {
  const { toolId = '' } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { setDisplayMode } = useOutletContext<{ setDisplayMode: (displayMode: ShellDisplayMode) => void }>();
  const [tool, setTool] = useState<ToolManifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ToolView = toolViews[toolId as keyof typeof toolViews];
  const declaredMode = tool?.displayMode ?? 'standard';
  const effectiveMode = resolveDisplayMode(declaredMode, searchParams.get('view'));
  const isFullscreen = effectiveMode === 'fullscreen';
  const canToggleFullscreen = declaredMode === 'flexible';

  useEffect(() => {
    if (!toolId) return;
    setTool(null);
    setError(null);
    fetchTool(toolId)
      .then(setTool)
      .catch((err: Error) => setError(err.message));
  }, [toolId]);

  useEffect(() => {
    setDisplayMode(effectiveMode);
    return () => setDisplayMode('standard');
  }, [effectiveMode, setDisplayMode]);

  if (error) {
    return <div className="empty-state">工具不存在或接口不可用。</div>;
  }

  if (!ToolView) {
    return (
      <div className="page-stack">
        <Link className="inline-link" to="/"><ArrowLeft size={16} />返回首页</Link>
        <div className="empty-state">工具前端未安装或未生成注册表。</div>
      </div>
    );
  }

  if (tool && tool.status !== 'available') {
    return (
      <div className="page-stack">
        <Link className="inline-link" to="/"><ArrowLeft size={16} />返回首页</Link>
        <div className="empty-state">工具当前不可用：{tool.errorMessage ?? tool.status}</div>
      </div>
    );
  }

  return (
    <div className={isFullscreen ? 'tool-page-fullscreen' : 'page-stack'}>
      {!isFullscreen && (
        <div className="tool-page-actions">
          <Link className="inline-link" to="/"><ArrowLeft size={16} />返回首页</Link>
          {canToggleFullscreen && (
            <button className="secondary-button" type="button" onClick={() => setViewMode(setSearchParams, 'fullscreen')}>
              <Maximize2 size={16} />
              全屏显示
            </button>
          )}
        </div>
      )}
      {isFullscreen && canToggleFullscreen && (
        <button className="fullscreen-exit-button" type="button" onClick={() => setViewMode(setSearchParams, 'standard')}>
          <Minimize2 size={16} />
          退出全屏
        </button>
      )}
      <ErrorBoundary fallback={<div className="empty-state">工具前端渲染失败。</div>}>
        <Suspense fallback={<div className="empty-state">正在加载工具...</div>}>
          <ToolView />
        </Suspense>
      </ErrorBoundary>
    </div>
  );
}

function resolveDisplayMode(displayMode: ToolDisplayMode, requestedView: string | null): ShellDisplayMode {
  if (displayMode === 'fullscreen') {
    return 'fullscreen';
  }
  if (displayMode === 'flexible' && requestedView === 'fullscreen') {
    return 'fullscreen';
  }
  return 'standard';
}

function setViewMode(setSearchParams: ReturnType<typeof useSearchParams>[1], view: 'standard' | 'fullscreen') {
  setSearchParams((params) => {
    const next = new URLSearchParams(params);
    if (view === 'fullscreen') {
      next.set('view', 'fullscreen');
    } else {
      next.delete('view');
    }
    return next;
  });
}
