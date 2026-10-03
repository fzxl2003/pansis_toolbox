export const API = "/api/tools/service-navigator";

export type View = "overview" | "targets" | "services" | "health" | "scans";

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
  connectionCommand: string;
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
  };
};

export type Site = {
  title: string;
  slug: string;
  description: string;
  visibility: "public" | "private";
};

export type Detail = {
  site: Site;
  targets: Target[];
  services: Service[];
  runs: Run[];
};

export const blankTarget = { label: "", address: "", customPorts: "" };
