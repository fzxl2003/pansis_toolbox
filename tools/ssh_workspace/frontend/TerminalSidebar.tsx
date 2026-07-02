// ============================================================
// SSH Workspace Tool — Terminal Sidebar (history + templates)
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import {
  ClipboardList,
  History,
  Plus,
  Play,
  RefreshCw,
  Trash2,
  Pencil,
} from 'lucide-react';

import { apiDelete, apiGet, apiPost } from '../../../frontend/src/api/client';
import { Alert, Badge, Spin, useConfirm } from './components';
import { API, formatRelativeTime, messageFromError } from './utils';
import type { CommandHistory, CommandTemplate, TerminalApi } from './types';
import { TemplateRunModal } from './TemplateRunModal';
import { TemplateFormModal } from './TemplateFormModal';

export type TerminalSidebarProps = {
  serverId: string | null;
  serverName: string;
  terminalApi: TerminalApi | null;
  visible: boolean;
};

type SideTab = 'history' | 'templates';

export function TerminalSidebar({ serverId, serverName, terminalApi, visible }: TerminalSidebarProps) {
  const [sideTab, setSideTab] = useState<SideTab>('history');
  const [history, setHistory] = useState<CommandHistory[]>([]);
  const [templates, setTemplates] = useState<CommandTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [runTemplate, setRunTemplate] = useState<CommandTemplate | null>(null);
  const [editTemplate, setEditTemplate] = useState<CommandTemplate | null>(null);
  const [addTemplateOpen, setAddTemplateOpen] = useState(false);
  const [prefillCommand, setPrefillCommand] = useState<string | undefined>(undefined);
  const { confirm, dialog } = useConfirm();

  const loadData = useCallback(async () => {
    if (!serverId) return;
    setLoading(true);
    setError('');
    try {
      const [histR, tplR] = await Promise.all([
        apiGet<{ history: CommandHistory[] }>(`${API}/history?serverId=${serverId}&limit=100`),
        apiGet<{ templates: CommandTemplate[] }>(`${API}/servers/${serverId}/templates`),
      ]);
      setHistory(histR.history);
      setTemplates(tplR.templates);
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setLoading(false);
    }
  }, [serverId]);

  useEffect(() => {
    if (visible && serverId) {
      void loadData();
    }
  }, [visible, serverId, loadData]);

  // ---- Run a command in the terminal ----
  async function runCommand(cmd: string) {
    if (!terminalApi) {
      setError('终端未连接，无法执行命令');
      return;
    }
    // Send the command + newline to the terminal
    terminalApi.sendText(cmd.endsWith('\n') ? cmd : cmd + '\n');
    // Record to history
    if (serverId) {
      try {
        await apiPost(`${API}/history`, { serverId, source: 'terminal', command: cmd.replace(/\n$/, '') });
      } catch {
        // ignore history recording errors
      }
      // Refresh history
      void loadData();
    }
  }

  // ---- Run a template (with variables if needed) ----
  function handleRunTemplate(tpl: CommandTemplate) {
    const hasVars = tpl.variables.length > 0 || /\{\{.*?\}\}/.test(tpl.command);
    if (hasVars) {
      setRunTemplate(tpl);
    } else {
      void runCommand(tpl.command);
    }
  }

  // ---- Add history item as template ----
  function handleAddFromHistory(cmd: string) {
    setPrefillCommand(cmd);
    setEditTemplate(null);
    setAddTemplateOpen(true);
  }

  // ---- Delete template ----
  function handleDeleteTemplate(tpl: CommandTemplate) {
    confirm({
      title: '删除模板',
      message: `确认删除「${tpl.name}」？`,
      onConfirm: async () => {
        await apiDelete(`${API}/templates/${tpl.id}`);
        void loadData();
      },
    });
  }

  if (!visible) return null;

  return (
    <div className="sw-sidebar">
      <div className="sw-sidebar-head">
        <span className="sw-sidebar-title">{serverName}</span>
        <button
          className="sw-btn sw-btn-sm sw-btn-ghost"
          onClick={() => void loadData()}
          type="button"
          title="刷新"
        >
          <RefreshCw size={12} />
        </button>
      </div>

      <div className="sw-sidebar-tabs">
        <button
          className={`sw-sidebar-tab${sideTab === 'history' ? ' active' : ''}`}
          onClick={() => setSideTab('history')}
          type="button"
        >
          <History size={13} /> 历史 ({history.length})
        </button>
        <button
          className={`sw-sidebar-tab${sideTab === 'templates' ? ' active' : ''}`}
          onClick={() => setSideTab('templates')}
          type="button"
        >
          <ClipboardList size={13} /> 模板 ({templates.length})
        </button>
      </div>

      <div className="sw-sidebar-body">
        {error && <Alert type="error">{error}</Alert>}
        {loading && <div className="sw-loading-inline"><Spin /> 加载中…</div>}

        {/* History list */}
        {sideTab === 'history' && !loading && (
          <div className="sw-sidebar-list">
            {history.length === 0 ? (
              <div className="sw-empty-inline">暂无历史命令</div>
            ) : (
              history.map((h) => (
                <div key={h.id} className="sw-cmd-item">
                  <div className="sw-cmd-item-main" onClick={() => void runCommand(h.command)}>
                    <code className="sw-cmd-text">{h.command}</code>
                    <span className="sw-cmd-meta">{formatRelativeTime(h.createdAt)}</span>
                  </div>
                  <button
                    className="sw-cmd-btn"
                    onClick={(e) => { e.stopPropagation(); void handleAddFromHistory(h.command); }}
                    type="button"
                    title="添加为模板"
                  >
                    <Plus size={12} />
                  </button>
                </div>
              ))
            )}
          </div>
        )}

        {/* Templates list */}
        {sideTab === 'templates' && !loading && (
          <div className="sw-sidebar-list">
            <button
              className="sw-btn sw-btn-sm sw-btn-primary sw-sidebar-add-btn"
              onClick={() => { setEditTemplate(null); setPrefillCommand(undefined); setAddTemplateOpen(true); }}
              type="button"
            >
              <Plus size={12} /> 新建模板
            </button>
            {templates.length === 0 ? (
              <div className="sw-empty-inline">暂无命令模板</div>
            ) : (
              templates.map((tpl) => (
                <div key={tpl.id} className="sw-cmd-item sw-cmd-item-tpl">
                  <div className="sw-cmd-item-main" onClick={() => handleRunTemplate(tpl)}>
                    <span className="sw-cmd-name">{tpl.name}</span>
                    <code className="sw-cmd-text">{tpl.command}</code>
                    {tpl.variables.length > 0 && (
                      <div className="sw-cmd-vars">
                        {tpl.variables.map((v) => <Badge key={v} color="blue">{`{{${v}}}`}</Badge>)}
                      </div>
                    )}
                  </div>
                  <div className="sw-cmd-item-actions">
                    <button className="sw-cmd-btn" onClick={(e) => { e.stopPropagation(); handleRunTemplate(tpl); }} type="button" title="运行">
                      <Play size={12} />
                    </button>
                    <button className="sw-cmd-btn" onClick={(e) => { e.stopPropagation(); setEditTemplate(tpl); setAddTemplateOpen(true); }} type="button" title="编辑">
                      <Pencil size={12} />
                    </button>
                    <button className="sw-cmd-btn sw-cmd-btn-danger" onClick={(e) => { e.stopPropagation(); void handleDeleteTemplate(tpl); }} type="button" title="删除">
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {/* Run template modal (fill variables) */}
      {runTemplate && (
        <TemplateRunModal
          template={runTemplate}
          onClose={() => setRunTemplate(null)}
          onRun={(cmd) => { void runCommand(cmd); setRunTemplate(null); }}
        />
      )}

      {/* Add/edit template modal */}
      {addTemplateOpen && serverId && (
        <TemplateFormModal
          serverId={serverId}
          template={editTemplate}
          prefillCommand={prefillCommand}
          onClose={() => { setAddTemplateOpen(false); setEditTemplate(null); setPrefillCommand(undefined); }}
          onSaved={() => { setAddTemplateOpen(false); setEditTemplate(null); setPrefillCommand(undefined); void loadData(); }}
        />
      )}
      {dialog}
    </div>
  );
}
