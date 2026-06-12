import { apiGet, apiPost } from './client';

export type AuthUser = {
  id: string;
  username: string;
  displayName: string;
  role: 'admin' | 'user';
  disabled: boolean;
};

export type AuthState = {
  authenticated: boolean;
  user: AuthUser | null;
};

export function fetchMe() {
  return apiGet<AuthState>('/api/auth/me');
}

export function login(username: string, password: string) {
  return apiPost<AuthState>('/api/auth/login', { username, password });
}

export function logout() {
  return apiPost<AuthState>('/api/auth/logout', {});
}
