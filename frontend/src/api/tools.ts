import { apiDelete, apiGet, apiPost } from './client';

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
  icon: ToolIcon;
  displayMode: 'standard' | 'fullscreen' | 'flexible';
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

export type ToolIcon =
  | {
      type: 'lucide';
      name: string;
      alt?: string | null;
    }
  | {
      type: 'image';
      src: string;
      alt?: string | null;
    }
  | string;

export type ToolAccessItem = {
  tool: ToolManifest;
  globalPublic: boolean;
  allowedUsers: Array<{ id: string; username: string; displayName: string }>;
};

export function fetchTools() {
  return apiGet<ToolManifest[]>('/api/tools');
}

export function fetchTool(toolId: string) {
  return apiGet<ToolManifest>(`/api/tools/${toolId}`);
}

export function fetchToolAccess() {
  return apiGet<{ items: ToolAccessItem[] }>('/api/tools-admin/access');
}

export function saveToolAccess(toolId: string, payload: { globalPublic: boolean; allowedUserIds: string[] }) {
  return apiPost<{ toolId: string; globalPublic: boolean; allowedUsers: ToolAccessItem['allowedUsers'] }>(
    `/api/tools-admin/${toolId}/access`,
    payload,
  );
}

export function clearToolStorage(toolId: string) {
  return apiDelete<{ toolId: string; droppedTables: string[]; removedPaths: string[] }>(`/api/tools-admin/${toolId}/storage`);
}
