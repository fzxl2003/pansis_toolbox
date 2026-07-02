// ============================================================
// SSH Workspace Tool — Template Form Modal (add/edit)
// ============================================================

import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import { apiPost, apiPut } from '../../../frontend/src/api/client';
import { Alert, Field, Modal } from './components';
import { API, messageFromError } from './utils';
import type { CommandTemplate } from './types';

export type TemplateFormModalProps = {
  serverId: string;
  template: CommandTemplate | null;
  prefillCommand?: string;
  onClose: () => void;
  onSaved: () => void;
};

export function TemplateFormModal({
  serverId,
  template,
  prefillCommand,
  onClose,
  onSaved,
}: TemplateFormModalProps) {
  const [name, setName] = useState(template?.name || '');
  const [command, setCommand] = useState(template?.command || prefillCommand || '');
  const [description, setDescription] = useState(template?.description || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Extract variables preview
  const variables = Array.from(
    new Set([...command.matchAll(/\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g)].map((m) => m[1])),
  );

  async function handleSave() {
    if (!name.trim() || !command.trim()) {
      setError('名称和命令不能为空');
      return;
    }
    setSaving(true);
    setError('');
    try {
      if (template) {
        await apiPut(`${API}/templates/${template.id}`, {
          name: name.trim(),
          command: command.trim(),
          description: description.trim(),
        });
      } else {
        await apiPost(`${API}/templates`, {
          serverId,
          name: name.trim(),
          command: command.trim(),
          description: description.trim(),
        });
      }
      onSaved();
    } catch (exc) {
      setError(messageFromError(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={template ? '编辑命令模板' : '新建命令模板'}
      onClose={onClose}
      foot={
        <>
          <button className="sw-btn sw-btn-secondary" onClick={onClose} type="button" disabled={saving}>取消</button>
          <button className="sw-btn sw-btn-primary" onClick={() => void handleSave()} type="button" disabled={saving}>
            {saving ? <><Loader2 size={14} className="spin" /> 保存中</> : '保存'}
          </button>
        </>
      }
    >
      <div className="sw-form-grid">
        {error && <Alert type="error">{error}</Alert>}
        <Field label="模板名称" full>
          <input className="sw-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：查看GPU使用" />
        </Field>
        <Field label="命令" full>
          <textarea
            className="sw-textarea"
            rows={4}
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder="支持 {{变量}} 占位符，例如：nvidia-smi --query-gpu=name,memory.used --format=csv"
          />
        </Field>
        {variables.length > 0 && (
          <div className="sw-form-hint" style={{ gridColumn: '1 / -1' }}>
            检测到变量：{variables.map((v) => `{{${v}}}`).join('、')}
          </div>
        )}
        <Field label="描述（可选）" full>
          <input className="sw-input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
