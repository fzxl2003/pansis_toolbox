// ============================================================
// Types — Docker Manager
// ============================================================

export type PermLevel = 'manage' | 'use' | 'view' | 'none';

export type GpuInfo = {
  index: number;
  name: string;
  memoryTotal: string;
};

export type DmServer = {
  id: string;
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  permissionLevel: PermLevel;
  createdAt: string;
  // CUDA 信息
  cudaAvailable?: boolean;
  gpuCount?: number;
  gpuInfo?: GpuInfo[];
};

// 细粒度权限结构
export type UserPerms = {
  server_visible: boolean;
  // 镜像
  img_pull: boolean;
  img_delete: boolean;
  img_copy: boolean;
  // 容器
  ctr_view_own: boolean;
  ctr_view_all: boolean;
  ctr_create_run: boolean;
  ctr_create_compose: boolean;
  ctr_create_template: boolean;
  ctr_manage_own: boolean;
  ctr_manage_all: boolean;
  ctr_path_whitelist: string[];
  // 卷
  vol_create: boolean;
  vol_delete_own: boolean;
  vol_delete_all: boolean;
  vol_copy: boolean;
  vol_quota_gb: number;
  // 模板
  tpl_use: boolean;
  tpl_create: boolean;
  tpl_edit: boolean;
  // CUDA 权限（可使用的显卡序号列表）
  cuda_gpu_indices: number[];
};

export const DEFAULT_PERMS: UserPerms = {
  server_visible: false,
  img_pull: false, img_delete: false, img_copy: false,
  ctr_view_own: false, ctr_view_all: false,
  ctr_create_run: false, ctr_create_compose: false, ctr_create_template: false,
  ctr_manage_own: false, ctr_manage_all: false, ctr_path_whitelist: [],
  vol_create: false, vol_delete_own: false, vol_delete_all: false, vol_copy: false, vol_quota_gb: 0,
  tpl_use: false, tpl_create: false, tpl_edit: false,
  cuda_gpu_indices: [],
};

export type ServerPermEntry = {
  userId: string;
  username: string;
  displayName: string;
  role: string;
  level: PermLevel;
  perms: UserPerms;
};

// 兼容旧配额类型（内部使用）
export type UserQuota = {
  volumeTotalGb: number;
  volumeUsedGb?: number;
  pathWhitelist: string[];
  canCreateContainer: boolean;
  canManageContainer: boolean;
};

export type DockerImage = {
  id: string;
  repo: string;
  tag: string;
  size: string;
  created: string;
};

export type DockerContainer = {
  ID?: string;
  Names?: string;
  Image?: string;
  Status?: string;
  State?: string;
  Ports?: string;
  CreatedAt?: string;
};

// 容器详情类型（来自 docker inspect）
export type ContainerPortBinding = {
  containerPort: string;
  hostIp: string;
  hostPort: string;
};

export type ContainerMount = {
  type: string;
  source: string;
  destination: string;
  mode: string;
  rw: boolean;
  name: string;
};

export type ContainerNetwork = {
  name: string;
  ipAddress: string;
  gateway: string;
  macAddress: string;
};

export type ContainerDetail = {
  id: string;
  shortId: string;
  name: string;
  image: string;
  imageId: string;
  status: string;
  running: boolean;
  paused: boolean;
  restarting: boolean;
  startedAt: string;
  finishedAt: string;
  exitCode: number;
  created: string;
  restartPolicy: string;
  platform: string;
  hostname: string;
  cmd: string[];
  entrypoint: string[];
  workingDir: string;
  user: string;
  envs: string[];
  ports: ContainerPortBinding[];
  mounts: ContainerMount[];
  networks: ContainerNetwork[];
  sshHostPort: string | null;
  serverHost: string;
  serverSshUsername: string;
  platformMeta: {
    ownerUserId: string | null;
    assignedAt: string | null;
    displayPorts: string[] | null;
  };
};

export type DockerVolume = {
  name: string;
  driver: string;
  mountpoint: string;
  ownerUserId?: string;
  sizeGb?: number;
  createdAt?: string;
  platformManaged: boolean;
};

export type VolumeDetailUser = {
  userId: string;
  username: string;
  displayName: string;
};

export type MountedContainer = {
  id: string;
  name: string;
  image: string;
  status: string;
  state: string;
};

export type VolumeDetail = {
  serverId: string;
  name: string;
  sizeGb: number | null;
  createdAt: string | null;
  platformManaged: boolean;
  roles: {
    creatorUserId: string | null;
    creator: VolumeDetailUser | null;
    ownerUserIds: string[];
    owners: VolumeDetailUser[];
    viewerUserIds: string[];
    viewers: VolumeDetailUser[];
  };
  mountedContainers: MountedContainer[];
  hiddenContainerCount: number;
};

export type Template = {
  id: string;
  name: string;
  description: string;
  category: string;
  creatorId: string;
  hasDoc: boolean;
  config: Record<string, unknown>;
  isPublic: boolean;
  createdAt: string;
  updatedAt: string;
};

export type TemplateDetail = Template & { docContent: string };

// 资源多角色管理相关类型
export type ResourceRoles = {
  ownerUserIds: string[];       // 多所有者
  viewerUserIds: string[];      // 多查看者
  creatorUserId: string | null; // 创建者（唯一）
  platformManaged: boolean;
  ownerUserId?: string | null;  // 兼容旧字段
};

export type ResourceItem = ResourceRoles;

export type ContainerResource = DockerContainer & ResourceItem;
export type ImageResource = DockerImage & ResourceItem;
export type VolumeResource = { name: string } & ResourceItem;

export type ServerResources = {
  serverId: string;
  containers: ContainerResource[];
  images: ImageResource[];
  volumes: VolumeResource[];
};

// 我的资源（非管理员用于查看和管理 viewer）
export type ViewerDetail = {
  userId: string;
  username: string;
  displayName: string;
};

export type MyOwnedResource = {
  serverId: string;
  serverName: string;
  resourceType: 'container' | 'image' | 'volume';
  resourceRef: string;
  creatorUserId: string | null;
  viewerUserIds: string[];
  viewers: ViewerDetail[];
};

export type BasicUser = { id: string; username: string; displayName: string };

export type CreateMode = 'run' | 'compose' | 'template';

export type TabId = 'servers' | 'images' | 'containers' | 'templates' | 'volumes' | 'my_resources' | 'admin_servers' | 'admin_templates';

// 服务器资源概览（用户侧）
export type ServerResourceOverview = {
  serverId: string;
  volume: {
    quotaGb: number;      // 0 = 不限
    usedSelfGb: number;
    usedTotalGb: number;
    remainingGb: number | null; // null = 不限
  };
  paths: Array<{
    path: string;
    totalGb: number | null;
    usedGb: number | null;
    availGb: number | null;
    pathUsedGb: number | null; // 该路径整体占用（非用户个人）
  }>;
  cuda: {
    serverHasCuda: boolean;
    allowedGpuIndices: number[];
    availableGpus: GpuInfo[];
  };
};
