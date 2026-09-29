import { apiDelete, apiGet, apiPost, apiPut } from './client';

export type EmailConfig = {
  smtpHost: string;
  smtpPort: number;
  smtpUsername: string;
  smtpFromAddress: string;
  smtpFromName: string;
  configured: boolean;
};

export type EmailConfigPayload = {
  smtpHost: string;
  smtpPort: number;
  smtpUsername: string;
  smtpPassword: string;
  smtpFromAddress: string;
  smtpFromName: string;
};

export function fetchEmailConfig(): Promise<EmailConfig> {
  return apiGet<EmailConfig>('/api/settings/email-config');
}

export function saveEmailConfig(payload: EmailConfigPayload): Promise<EmailConfig> {
  return apiPost<EmailConfig>('/api/settings/email-config', payload);
}

export function testEmailConfig(payload: EmailConfigPayload): Promise<{ success: boolean; testTo: string }> {
  return apiPost<{ success: boolean; testTo: string }>('/api/settings/email-config/test', payload);
}

export type AboutItem = {
  label: string;
  value: string;
  type?: 'text' | 'email' | 'url';
};

export type AboutInfo = {
  title?: string;
  description?: string;
  items?: AboutItem[];
};

export function fetchAbout(): Promise<AboutInfo> {
  return apiGet<AboutInfo>('/api/settings/about');
}

export type SshServer = {
  id: string;
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  authType: 'password' | 'private_key';
  isPublic: boolean;
  enabled: boolean;
  ownerUserId: string;
  allowedUserIds: string[];
  canManage: boolean;
  canShare: boolean;
};

export type SshServerPayload = {
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  authType: 'password' | 'private_key';
  sshPassword: string;
  privateKey: string;
  privateKeyPassphrase: string;
  isPublic: boolean;
  allowedUserIds: string[];
};

export function fetchSshServers(): Promise<{ servers: SshServer[] }> {
  return apiGet('/api/settings/ssh-servers');
}
export function createSshServer(payload: SshServerPayload): Promise<{ server: SshServer }> {
  return apiPost('/api/settings/ssh-servers', payload);
}
export function updateSshServer(id: string, payload: SshServerPayload): Promise<{ server: SshServer }> {
  return apiPut(`/api/settings/ssh-servers/${id}`, payload);
}
export function deleteSshServer(id: string): Promise<{ deleted: boolean }> {
  return apiDelete(`/api/settings/ssh-servers/${id}`);
}
export function testSshServer(id: string): Promise<{ connected: boolean; message: string }> {
  return apiPost(`/api/settings/ssh-servers/${id}/test`, {});
}

export type GithubKey = { id: string; name: string; publicKey: string; createdAt: string; updatedAt: string };
export function fetchGithubKeys(): Promise<{ keys: GithubKey[] }> { return apiGet('/api/settings/github-keys'); }
export function createGithubKey(name: string): Promise<{ key: GithubKey }> { return apiPost('/api/settings/github-keys', { name }); }
export function deleteGithubKey(id: string): Promise<{ deleted: boolean }> { return apiDelete(`/api/settings/github-keys/${id}`); }
export function testGithubKey(id: string, repoUrl: string): Promise<{ repoUrl: string; branchCount: number }> { return apiPost(`/api/settings/github-keys/${id}/test`, { repoUrl }); }

export type ProxySuggestedDomain = { domain: string; toolId: string; toolName: string; description: string };
export type ProxySettings = { protocol: 'http' | 'https' | 'socks5' | 'socks5h'; host: string; port: number; selectedDomains: string[]; customDomains: string[]; suggestedDomains: ProxySuggestedDomain[] };
export function fetchProxySettings(): Promise<ProxySettings> { return apiGet('/api/settings/proxy'); }
export function saveProxySettings(payload: Omit<ProxySettings, 'suggestedDomains'>): Promise<ProxySettings> { return apiPut('/api/settings/proxy', payload); }
