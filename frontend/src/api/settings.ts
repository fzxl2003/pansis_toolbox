import { apiGet, apiPost } from './client';

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
