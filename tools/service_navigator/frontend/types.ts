export const API = "/api/tools/service-navigator";

export type View = "overview" | "targets" | "settings" | "access" | "scans";

export type Target = {
  id: string;
  label: string;
  address: string;
  customPorts: string;
};

export type Service = {
  id: string;
  targetId: string;
  port: number;
  protocol: string;
  state: string;
  serviceType: "http" | "port";
  serviceTemplate: "generic" | "ssh" | "sftp" | "rdp" | "vnc" | "ftp" | "smb";
  serviceName: string;
  product: string;
  version: string;
  extraInfo: string;
  resolvedAddresses: string[];
  httpTitle: string;
  detectedUrl: string;
  displayName: string;
  description: string;
  navigationUrl: string;
  commandDescription: string;
  faviconUrl: string;
  healthEnabled: boolean;
  healthUrl: string;
  healthStatus: "healthy" | "unhealthy" | "unknown";
};

export type Run = {
  id: string;
  targetId: string | null;
  status: string;
  requestedAt: string;
  error: string;
  summary: {
    targetCount?: number;
    completedTargetCount?: number;
    successCount?: number;
    portCount?: number;
    completedPortCount?: number;
  };
};

export type Site = {
  id: string;
  title: string;
  slug: string;
  description: string;
  visibility: "public" | "private";
};

export type AccessSettings = {
  visibility: "public" | "private";
  users: { userId: string; username: string; displayName: string }[];
  passwords: {
    id: string;
    label: string;
    enabled: boolean;
    createdAt: string;
    updatedAt: string;
  }[];
};

export type Detail = {
  site: Site;
  targets: Target[];
  services: Service[];
  runs: Run[];
};

export const blankTarget = { label: "", address: "", customPorts: "" };
