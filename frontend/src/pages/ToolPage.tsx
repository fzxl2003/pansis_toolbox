import { ArrowLeft } from 'lucide-react';
import { Suspense, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { fetchTool, type ToolManifest } from '../api/tools';
import { ErrorBoundary } from '../components/ErrorBoundary';
import { toolViews } from '../registry/localToolViews';

export function ToolPage() {
  const { toolId = '' } = useParams();
  const [tool, setTool] = useState<ToolManifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ToolView = toolViews[toolId as keyof typeof toolViews];

  useEffect(() => {
    if (!toolId) return;
    fetchTool(toolId)
      .then(setTool)
      .catch((err: Error) => setError(err.message));
  }, [toolId]);

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
    <div className="page-stack">
      <Link className="inline-link" to="/"><ArrowLeft size={16} />返回首页</Link>
      <ErrorBoundary fallback={<div className="empty-state">工具前端渲染失败。</div>}>
        <Suspense fallback={<div className="empty-state">正在加载工具...</div>}>
          <ToolView />
        </Suspense>
      </ErrorBoundary>
    </div>
  );
}
