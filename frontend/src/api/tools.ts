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

export function clearUserToolStorage(toolId: string, userId: string) {
  return apiDelete<{ toolId: string; userId: string; droppedTables: string[]; removedPaths: string[] }>(
    `/api/tools-admin/${toolId}/users/${userId}/storage`,
  );
}

export function clearUserStorage(userId: string) {
  return apiDelete<{ userId: string; droppedTables: string[]; removedPaths: string[] }>(
    `/api/tools-admin/users/${userId}/storage`,
  );
}

export type StorageUsageTool = {
  toolId: string;
  toolName: string;
  totalBytes: number;
  sharedBytes: number;
  userBytes: number;
  dbBytes: number;
};

export type StorageUsageUser = {
  userId: string;
  username: string;
  displayName: string;
  totalBytes: number;
};

export type StorageUsageMatrixEntry = {
  userId: string;
  toolId: string;
  bytes: number;
};

export type StorageUsage = {
  grandTotal: number;
  tools: StorageUsageTool[];
  users: StorageUsageUser[];
  matrix: StorageUsageMatrixEntry[];
};

export function fetchStorageUsage() {
  return apiGet<StorageUsage>('/api/tools-admin/storage-usage');
}

export type MyStorageTool = {
  toolId: string;
  toolName: string;
  bytes: number;
};

export type MyStorage = {
  userId: string;
  totalBytes: number;
  tools: MyStorageTool[];
};

export function fetchMyStorage() {
  return apiGet<MyStorage>('/api/tools/my-storage');
}

export function clearMyToolStorage(toolId: string) {
  return apiDelete<{ toolId: string; userId: string; removedPaths: string[] }>(`/api/tools/my-storage/${toolId}`);
}

export function clearMyStorage() {
  return apiDelete<{ userId: string; removedPaths: string[] }>('/api/tools/my-storage');
}

// ── Data category & time-based deletion APIs ───────────────────────────────

export type DataCategoryInfo = {
  name: string;
  description: string;
  timeColumn: string | null;
  storage: 'user_tool_db' | 'platform_db';
  tables: string[];
};

export type ToolDataCategories = {
  toolId: string;
  toolName: string;
  categories: DataCategoryInfo[];
};

export type DataCategoryUsage = {
  category: string;
  description: string;
  timeColumn: string | null;
  totalRows: number;
  dbBytes: number;
  tables: Array<{ table: string; rows: number }>;
};

export type DataDeletionResult = {
  toolId: string;
  deleted: Record<string, Record<string, number>>;
  message?: string;
};

export function fetchDataCategories() {
  return apiGet<{ tools: ToolDataCategories[] }>('/api/settings/data-categories');
}

export function fetchDataUsage(toolId: string, userId?: string | null) {
  const qs = userId ? `?userId=${encodeURIComponent(userId)}` : '';
  return apiGet<{ toolId: string; userId: string; categories: DataCategoryUsage[] }>(
    `/api/settings/data-usage/${toolId}${qs}`,
  );
}

export function deleteData(payload: {
  toolId: string;
  category?: string | null;
  beforeDays?: number | null;
  startDate?: string | null;
  endDate?: string | null;
  userId?: string | null;
}) {
  return apiDelete<DataDeletionResult>('/api/settings/data', payload);
}

export type DataCountResult = {
  toolId: string;
  counts: Record<string, number>;
};

export function fetchDataCount(payload: {
  toolId: string;
  items: Array<{ category?: string | null; startDate?: string | null; endDate?: string | null }>;
  userId?: string | null;
}) {
  return apiPost<DataCountResult>('/api/settings/data-count', payload);
}
