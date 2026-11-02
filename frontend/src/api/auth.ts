import { apiDelete, apiGet, apiPost } from './client';

export type AuthUser = {
  id: string;
  username: string;
  displayName: string;
  role: 'admin' | 'user';
  disabled: boolean;
  isSuperAdmin: boolean;
};

export type AuthState = {
  authenticated: boolean;
  user: AuthUser | null;
};

type AuthStateListener = (state: AuthState) => void;

const authStateListeners = new Set<AuthStateListener>();

function publishAuthState(state: AuthState) {
  authStateListeners.forEach((listener) => listener(state));
}

export function onAuthStateChange(listener: AuthStateListener) {
  authStateListeners.add(listener);
  return () => {
    authStateListeners.delete(listener);
  };
}

export function fetchMe() {
  return apiGet<AuthState>('/api/auth/me');
}

export async function login(username: string, password: string) {
  const state = await apiPost<AuthState>('/api/auth/login', { username, password });
  publishAuthState(state);
  return state;
}

export async function logout() {
  const state = await apiPost<AuthState>('/api/auth/logout', {});
  publishAuthState(state);
  return state;
}

export function changePassword(currentPassword: string, newPassword: string) {
  return apiPost<{ user: AuthUser; sessionsRevoked: boolean }>('/api/auth/password', {
    currentPassword,
    newPassword,
  });
}

export function resetUserPassword(userId: string, password: string) {
  return apiPost<{ user: AuthUser }>(`/api/auth/users/${userId}/password`, { password });
}

export function deleteUser(userId: string) {
  return apiDelete<{ deleted: boolean }>(`/api/auth/users/${userId}`);
}

export function updateUserRole(userId: string, role: 'admin' | 'user') {
  return apiPost<{ user: AuthUser }>(`/api/auth/users/${userId}/role`, { role });
}

export function setUserDisabled(userId: string, disabled: boolean) {
  return apiPost<{ user: AuthUser }>(`/api/auth/users/${userId}/disabled`, { disabled });
}
