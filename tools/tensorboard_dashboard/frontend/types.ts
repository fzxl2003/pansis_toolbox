// ============================================================
// TensorBoard Dashboard Tool — Shared Types
// ============================================================

// ---- Auth ----

export type AuthType = 'password' | 'private_key';

// ---- Server ----

export type TbServer = {
  id: string;
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  authType: AuthType;
  condaBasePath: string;
  lastTestStatus: string; // 'ok' | 'failed' | 'unknown'
  lastTestError: string;
  lastTestedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type ServerForm = {
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  authType: AuthType;
  sshPassword: string;
  privateKey: string;
  privateKeyPassphrase: string;
  condaBasePath: string;
};

export const EMPTY_SERVER_FORM: ServerForm = {
  name: '',
  host: '',
  port: 22,
  sshUsername: '',
  authType: 'password',
  sshPassword: '',
  privateKey: '',
  privateKeyPassphrase: '',
  condaBasePath: '',
};

// ---- Session ----

export type SessionStatus = 'starting' | 'running' | 'stopped' | 'failed';

export type PythonMode = 'conda' | 'path';

export type TbSession = {
  id: string;
  serverId: string;
  name: string;
  logdir: string;
  remotePort: number;
  localPort: number;
  pythonMode: PythonMode;
  condaEnv: string;
  pythonPath: string;
  extraParams: string;
  remotePid: string;
  tbSessionId: string;
  status: SessionStatus;
  error: string;
  startedAt: string;
  stoppedAt: string | null;
  updatedAt: string;
  url: string;
};

export type SessionForm = {
  serverId: string;
  name: string;
  logdir: string;
  pythonMode: PythonMode;
  condaEnv: string;
  pythonPath: string;
  extraParams: string;
};

export const EMPTY_SESSION_FORM: SessionForm = {
  serverId: '',
  name: '',
  logdir: '',
  pythonMode: 'conda',
  condaEnv: '',
  pythonPath: '',
  extraParams: '',
};

// ---- Top-level tab ----

export type TopTabId = 'sessions' | 'servers';
