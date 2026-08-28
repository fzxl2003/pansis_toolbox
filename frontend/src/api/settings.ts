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
