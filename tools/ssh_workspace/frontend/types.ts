// ============================================================
// SSH Workspace Tool — Shared Types
// ============================================================

// ---- Auth ----

export type AuthType = 'password' | 'private_key';

// ---- Server ----

export type SshServer = {
  id: string;
  name: string;
  host: string;
  port: number;
  sshUsername: string;
  authType: AuthType;
  hasScreen: boolean;
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
};

// ---- Screen Session ----

export type ScreenSession = {
  id: string;
  serverId: string;
  sessionName: string;
  pid: string;
  status: 'running' | 'done' | 'unknown';
  createdByTool: boolean;
  command: string;
  startedAt: string;
  checkedAt: string | null;
};

// ---- Command Template (bound to server) ----

export type CommandTemplate = {
  id: string;
  serverId: string;
  name: string;
  command: string;
  description: string;
  variables: string[];
  createdAt: string;
  updatedAt: string;
};

export type TemplateForm = {
  id?: string;
  serverId: string;
  name: string;
  command: string;
  description: string;
};

export const EMPTY_TEMPLATE_FORM: TemplateForm = {
  serverId: '',
  name: '',
  command: '',
  description: '',
};

// ---- Scheduled Task (bound to server) ----

export type ScheduledTask = {
  id: string;
  serverId: string;
  name: string;
  command: string;
  intervalSeconds: number;
  screenNamePrefix: string;
  enabled: boolean;
  nextRunAt: string;
  lastRunAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type TaskForm = {
  id?: string;
  serverId: string;
  name: string;
  command: string;
  intervalSeconds: number;
  screenNamePrefix: string;
  enabled: boolean;
};

export const EMPTY_TASK_FORM: TaskForm = {
  serverId: '',
  name: '',
  command: '',
  intervalSeconds: 3600,
  screenNamePrefix: 'ssh_task',
  enabled: true,
};

// ---- Command History ----

export type CommandHistory = {
  id: string;
  serverId: string | null;
  source: string;
  command: string;
  exitStatus: number | null;
  screenSession: string | null;
  createdAt: string;
};

// ---- Terminal Tab ----

// Session mode: native SSH vs screen
export type SessionMode = 'native' | 'screen_existing' | 'screen_new';

// Persisted tab (matches backend ssh_terminal_tabs)
export type TerminalTab = {
  id: string;
  serverId: string;
  mode: SessionMode;
  screenSession: string;
  label: string;
  tabOrder: number;
  initialCommand?: string;
};

// Runtime state for a tab (not persisted)
export type TerminalTabState = {
  tab: TerminalTab;
  status: 'idle' | 'connecting' | 'connected' | 'error' | 'closed';
  error: string;
};

// ---- New session picker ----

export type NewSessionPick = {
  serverId: string;
  mode: SessionMode;
  screenSession: string; // for screen_existing: the name; for screen_new: the new name
  initialCommand?: string; // command to run after session starts
};

// ---- Terminal API (for sidebar command execution) ----

export type TerminalApi = {
  sendText: (text: string) => void;
};

// ---- Top-level tab ----

export type TopTabId = 'terminal' | 'servers' | 'templates' | 'history';
