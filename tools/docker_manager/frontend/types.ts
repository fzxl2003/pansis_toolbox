// ============================================================
// Types — Docker Manager
// ============================================================

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
  serverVisible: boolean;
  createdAt: string;
  // CUDA 信息
  cudaAvailable?: boolean;
  gpuCount?: number;
  gpuInfo?: GpuInfo[];
  // 当前用户在该服务器上的细粒度权限（由后端 list_servers 附带）
  perms?: UserPerms;
};

// 细粒度权限结构
// 注意：查看/管理"自己的"资源为默认权限，不在此配置
// img_use/ctr_use/vol_use 控制「是否能使用（看到+访问）该类资源」
export type UserPerms = {
  server_visible: boolean;
  // 镜像权限
  img_use: boolean;         // 是否有权使用（查看/访问）镜像
  img_pull: boolean;        // 拉取新镜像
  img_view_all: boolean;    // 查看所有用户的镜像
  img_manage_all: boolean;  // 管理所有用户的镜像（删除权）
  img_copy: boolean;        // 跨服务器复制镜像
  img_quota_gb: number;     // 镜像空间配额(GB，0=不限)
  // 容器权限
  ctr_use: boolean;         // 是否有权使用（查看/访问）容器
  ctr_view_all: boolean;    // 查看所有用户的容器
  ctr_manage_all: boolean;  // 管理所有用户的容器
  ctr_create: boolean;      // 创建容器（run/compose 模式均包含）
  ctr_create_template: boolean;
  ctr_path_whitelist: string[];
  ctr_quota_num: number;    // 容器数量配额（0=不限）
  // 卷权限
  vol_use: boolean;         // 是否有权使用（查看/访问）卷
  vol_view_all: boolean;    // 查看所有用户的卷
  vol_create: boolean;
  vol_manage_all: boolean;  // 管理所有用户的卷（删除权，自动包含查看权）
  vol_copy: boolean;
  vol_quota_gb: number;     // 卷空间配额(GB，0=不限)
  // 模板权限
  tpl_use: boolean;
  tpl_create: boolean;
  tpl_edit: boolean;
  // CUDA 权限（可使用的显卡序号列表）
  cuda_gpu_indices: number[];
};

export const DEFAULT_PERMS: UserPerms = {
  server_visible: false,
  img_use: false, img_pull: false, img_view_all: false, img_manage_all: false, img_copy: false, img_quota_gb: 0,
  ctr_use: false, ctr_view_all: false,
  ctr_manage_all: false,
  ctr_create: false, ctr_create_template: false,
  ctr_path_whitelist: [], ctr_quota_num: 0,
  vol_use: false, vol_view_all: false, vol_create: false, vol_manage_all: false, vol_copy: false, vol_quota_gb: 0,
  tpl_use: false, tpl_create: false, tpl_edit: false,
  cuda_gpu_indices: [],
};

export type ServerPermEntry = {
  userId: string;
  username: string;
  displayName: string;
  role: string;
  perms: UserPerms;
};

export type DockerImage = {
  id: string;
  repo: string;
  tag: string;
  size: string;
  created: string;
  canManage?: boolean;  // 当前用户是否可管理该镜像（删除/复制）
  inUse?: boolean;      // 该镜像是否被服务器上任意容器使用（不受权限过滤，用于禁用删除按钮）
  isPublic?: boolean;   // 是否公开（公开则所有用户自动拥有查看权限）
};

export type DockerContainer = {
  ID?: string;
  Names?: string;
  Image?: string;
  Status?: string;
  State?: string;
  Ports?: string;
  CreatedAt?: string;
  ownerUserId?: string | null;    // 容器所有者用户 ID（来自后端平台元数据）
  platformManaged?: boolean;     // 是否由平台管理（有所有权记录或用户有查看角色）
  isPublic?: boolean;            // 是否公开（公开则所有用户自动拥有查看权限）
  canManage?: boolean;           // 当前用户是否可管理该容器
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
  canSeeVolume: boolean;  // 当前用户是否可查看该卷挂载的卷名（容器全局权不授予卷查看权）
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
    isPublic: boolean;
    canManage: boolean;
    canSeeImage: boolean;  // 当前用户是否可查看该容器的镜像名（容器全局权不授予镜像查看权）
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
  canManage?: boolean;  // 当前用户是否可管理该卷（删除）
  isPublic?: boolean;   // 是否公开（公开则所有用户自动拥有查看权限）
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
  isPublic: boolean;
  canManage: boolean;
  roles: {
    creatorUserId: string | null;
    creator: VolumeDetailUser | null;
    ownerUserIds: string[];
    owners: VolumeDetailUser[];
    viewerUserIds: string[];
    viewers: VolumeDetailUser[];
    quotaHolderUserIds: string[];
    quotaHolders: VolumeDetailUser[];
  };
  mountedContainers: MountedContainer[];
  hiddenContainerCount: number;
};

// 模板变量类型：支持的输入控件类型
// - string     : 单行文本（筛选条件为通配符，输入须匹配）
// - text       : 多行文本（筛选条件为通配符，输入须匹配）
// - number     : 数字（筛选条件为范围，如 1-100 或 >=0,<=100）
// - port       : 端口号（1-65535，筛选条件可进一步限定范围）
// - image      : 镜像选择器（筛选条件为通配符，过滤服务器镜像下拉选项）
// - volume     : 卷选择器（筛选条件为通配符，过滤服务器卷下拉选项）
// - gpu        : GPU 选择器（筛选条件为通配符，按 GPU 名称过滤；值为逗号分隔的索引或 all）
// - host_path  : 宿主路径选择器（筛选条件为通配符前缀，如 /data/*；部署时点选宿主机目录）
// - docker_path: 容器内路径（纯文本输入；筛选条件为通配符，输入须匹配）
// - select     : 下拉选择（筛选条件填逗号分隔的允许选项）
export type TemplateVariableType = 'string' | 'text' | 'number' | 'port' | 'image' | 'volume' | 'gpu' | 'host_path' | 'docker_path' | 'select';

export type TemplateVariable = {
  name: string;            // 变量名，对应 {{VAR_NAME}} 中的 VAR_NAME
  type: TemplateVariableType;
  filter: string;          // 筛选条件：语义随类型变化（通配符 / 数字范围 / 选项列表）
  description: string;     // 说明文字
  defaultValue: string;    // 默认值
};

export type TemplateRoles = {
  ownerUserIds: string[];
  viewerUserIds: string[];
  creatorUserId: string | null;
};

export type Template = {
  id: string;
  name: string;
  description: string;
  category: string;
  creatorId: string;
  hasDoc: boolean;
  isPublic: boolean;
  deployType: 'run' | 'compose';
  rawContent: string;             // docker run 命令 或 docker-compose.yml 内容
  variables: TemplateVariable[];
  createdAt: string;
  updatedAt: string;
  // 列表接口附带的权限标记
  canManage?: boolean;            // 当前用户是否可编辑/删除此模板
  canUse?: boolean;               // 当前用户是否可使用此模板
  // 角色信息（列表/详情均返回）
  roles?: TemplateRoles;
  // 管理员视角下附带的创建者显示名
  creatorName?: string | null;
};

export type TemplateDetail = Template & { docContent: string };

// 模板角色详情（含用户显示名），用于角色管理弹窗
export type TemplateUserInfo = {
  userId: string;
  username: string;
  displayName: string;
};

export type TemplateRoleDetail = {
  templateId: string;
  creatorUserId: string | null;
  creator: TemplateUserInfo | null;
  ownerUserIds: string[];
  owners: TemplateUserInfo[];
  viewerUserIds: string[];
  viewers: TemplateUserInfo[];
  canManage: boolean;
};

// 资源多角色管理相关类型
export type ResourceRoles = {
  ownerUserIds: string[];          // 多所有者
  viewerUserIds: string[];         // 多查看者
  quotaHolderUserIds: string[];    // 配额占用者（无需是所有者，资源大小在所有配额占用者间均分）
  creatorUserId: string | null;    // 创建者（唯一）
  platformManaged: boolean;
};

export type ResourceItem = ResourceRoles;

export type ContainerResource = DockerContainer & ResourceItem;
export type ImageResource = DockerImage & ResourceItem;
export type VolumeResource = { name: string; isPublic?: boolean } & ResourceItem;

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
    quotaGb: number;              // 0 = 不限
    usedSelfGb: number;           // 当前用户的配额占用（按配额占用者均分计算）
    usedTotalGb: number;          // 服务器全部卷使用量
    remainingGb: number | null;   // null = 不限
  };
  image: {
    quotaGb: number;              // 0 = 不限
    usedSelfGb: number;           // 当前用户的配额占用（按配额占用者均分计算）
    usedTotalGb: number;          // 服务器全部镜像使用量
    remainingGb: number | null;   // null = 不限
    countSelf: number;            // 当前用户作为配额占用者的镜像数量
    countTotal: number;           // 服务器全部镜像数量
  };
  container: {
    quotaNum: number;             // 0 = 不限
    usedSelf: number;             // 当前用户作为配额占用者的容器数量
    usedTotal: number;            // 服务器全部容器数量
    remaining: number | null;     // null = 不限
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
    totalGpuCount: number;        // 服务器总 GPU 数
    allGpuInfo: GpuInfo[];        // 所有 GPU 详情
  };
};