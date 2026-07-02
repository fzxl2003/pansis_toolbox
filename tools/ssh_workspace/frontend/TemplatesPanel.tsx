// ============================================================
// SSH Workspace Tool — Templates Management Panel
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import { ClipboardList, Pencil, Plus, Server, Trash2 } from 'lucide-react';

import { apiDelete, apiGet } from '../../../frontend/src/api/client';
import { Alert, Badge, EmptyState, Spin, useConfirm } from './components';
import { API, messageFromError } from './utils';
import type { CommandTemplate, SshServer } from './types';
import { TemplateFormModal } from './TemplateFormModal';

export type TemplatesPanelProps = {
  servers: SshServer[];
};

export function TemplatesPanel({ servers }: TemplatesPanelProps) {
  const [selectedServerId, setSelectedServerId] = useState<string>('');
  const [templates, setTemplates] = useState<CommandTemplate[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<CommandTemplate | null>(null);
  const { confirm, dialog } = useConfirm();

  // Auto-select first server
  useEffect(() => {
    if (servers.length > 0 && !selectedServerId) {
      setSelectedServerId(servers[0].id);
    }
    if (selectedServerId && !servers.find((s) => s.id === selectedServerId)) {
      setSelectedServerId(servers[0]?.id || '');
    }
  }, [servers, selectedServerId]);

  const loadTemplates = useCallback(async () => {
    if (!selectedServerId) {
      setTemplates([]);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const r = await apiGet<{ templates: CommandTemplate[] }>(`${API}/servers/${selectedServerId}/templates`);
      setTemplates(r.templates);
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setLoading(false);
    }
  }, [selectedServerId]);

  useEffect(() => { void loadTemplates(); }, [loadTemplates]);

  function handleAdd() {
    setEditing(null);
    setShowForm(true);
  }

  function handleEdit(tpl: CommandTemplate) {
    setEditing(tpl);
    setShowForm(true);
  }

  function handleDelete(tpl: CommandTemplate) {
    confirm({
      title: '删除模板',
      message: `确认删除「${tpl.name}」？`,
      onConfirm: async () => {
        await apiDelete(`${API}/templates/${tpl.id}`);
        void loadTemplates();
      },
    });
  }

  const selectedServer = servers.find((s) => s.id === selectedServerId);

  return (
    <div className="sw-panel">
      <div className="sw-panel-head">
        <div>
          <h2 className="sw-panel-title"><ClipboardList size={18} /> 命令模板</h2>
          <p className="sw-panel-desc">管理命令模板，支持 {'{{变量}}'} 占位符</p>
        </div>
      </div>

      {/* Server selector */}
      {servers.length === 0 ? (
        <EmptyState
          icon={<Server size={32} />}
          title="暂无服务器"
          hint="请先在「服务器」页添加 SSH 服务器"
        />
      ) : (
        <>
          <div className="sw-server-selector">
            <span className="sw-server-selector-label">选择服务器：</span>
            {servers.map((s) => (
              <button
                key={s.id}
                className={`sw-server-chip${selectedServerId === s.id ? ' active' : ''}`}
                onClick={() => setSelectedServerId(s.id)}
                type="button"
              >
                <Server size={13} /> {s.name}
              </button>
            ))}
          </div>

          {error && <Alert type="error">{error}</Alert>}

          <div className="sw-subsection-head" style={{ marginBottom: 10 }}>
            <span className="sw-subsection-title">
              {selectedServer?.name} 的命令模板 ({templates.length})
            </span>
            <button className="sw-btn sw-btn-sm sw-btn-primary" onClick={handleAdd} type="button">
              <Plus size={12} /> 新建模板
            </button>
          </div>

          {loading ? (
            <div className="sw-loading-block"><Spin /> 加载模板…</div>
          ) : templates.length === 0 ? (
            <div className="sw-empty-inline">暂无命令模板，点击「新建模板」开始</div>
          ) : (
            <div className="sw-tpl-list">
              {templates.map((tpl) => (
                <div key={tpl.id} className="sw-tpl-item">
                  <div className="sw-tpl-item-main">
                    <span className="sw-tpl-name">{tpl.name}</span>
                    <code className="sw-tpl-cmd">{tpl.command}</code>
                    {tpl.description && <span className="sw-tpl-desc">{tpl.description}</span>}
                    {tpl.variables.length > 0 && (
                      <div className="sw-cmd-vars">
                        {tpl.variables.map((v) => <Badge key={v} color="blue">{`{{${v}}}`}</Badge>)}
                      </div>
                    )}
                  </div>
                  <div className="sw-tpl-item-actions">
                    <button className="sw-btn sw-btn-sm sw-btn-ghost" onClick={() => handleEdit(tpl)} type="button"><Pencil size={11} /></button>
                    <button className="sw-btn sw-btn-sm sw-btn-danger-ghost" onClick={() => handleDelete(tpl)} type="button"><Trash2 size={11} /></button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {showForm && selectedServerId && (
        <TemplateFormModal
          serverId={selectedServerId}
          template={editing}
          onClose={() => { setShowForm(false); setEditing(null); }}
          onSaved={() => { setShowForm(false); setEditing(null); void loadTemplates(); }}
        />
      )}
      {dialog}
    </div>
  );
}
