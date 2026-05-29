import { apiGet } from './client';

export type ToolStatus =
  | 'available'
  | 'disabled'
  | 'failed'
  | 'missing_frontend'
  | 'missing_backend'
  | 'dependency_failed';

export type ToolManifest = {
  id: string;
  name: string;
  description: string;
  version: string;
  enabled: boolean;
  category: string;
  icon: string;
  permissions: {
    filesystem: boolean;
    network: boolean;
    longRunningTask: boolean;
    userData: boolean;
  };
  api: { prefix: string };
  widgets: unknown[];
  status: ToolStatus;
  errorMessage: string | null;
};

export function fetchTools() {
  return apiGet<ToolManifest[]>('/api/tools');
}

export function fetchTool(toolId: string) {
  return apiGet<ToolManifest>(`/api/tools/${toolId}`);
}
