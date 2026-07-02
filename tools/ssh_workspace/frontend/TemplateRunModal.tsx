// ============================================================
// SSH Workspace Tool — Template Run Modal (fill variables)
// ============================================================

import { useMemo, useState } from 'react';
import { Play, Variable } from 'lucide-react';

import { Modal } from './components';
import type { CommandTemplate } from './types';

export type TemplateRunModalProps = {
  template: CommandTemplate;
  onClose: () => void;
  onRun: (resolvedCommand: string) => void;
};

export function TemplateRunModal({ template, onClose, onRun }: TemplateRunModalProps) {
  // Extract variables from the command using {{var}} pattern
  const variables = useMemo(
    () => template.variables.length > 0
      ? template.variables
      : Array.from(new Set([...template.command.matchAll(/\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g)].map((m) => m[1]))),
    [template],
  );

  const [values, setValues] = useState<Record<string, string>>({});

  const resolvedCommand = useMemo(() => {
    let cmd = template.command;
    for (const v of variables) {
      const val = values[v] ?? '';
      cmd = cmd.replace(new RegExp(`\\{\\{\\s*${v}\\s*\\}\\}`, 'g'), val);
    }
    return cmd;
  }, [template.command, variables, values]);

  function handleRun() {
    onRun(resolvedCommand + '\n');
  }

  return (
    <Modal
      title={`运行模板：${template.name}`}
      onClose={onClose}
      foot={
        <>
          <button className="sw-btn sw-btn-secondary" onClick={onClose} type="button">取消</button>
          <button className="sw-btn sw-btn-primary" onClick={handleRun} type="button">
            <Play size={14} /> 执行
          </button>
        </>
      }
    >
      <div className="sw-tpl-run">
        {variables.length > 0 && (
          <div className="sw-tpl-run-vars">
            <div className="sw-tpl-run-vars-title"><Variable size={14} /> 填写变量</div>
            {variables.map((v) => (
              <div key={v} className="sw-form-field">
                <label>{`{{${v}}}`}</label>
                <input
                  className="sw-input"
                  type="text"
                  value={values[v] ?? ''}
                  onChange={(e) => setValues({ ...values, [v]: e.target.value })}
                  placeholder={`输入 ${v} 的值`}
                />
              </div>
            ))}
          </div>
        )}
        {variables.length === 0 && (
          <div className="sw-tpl-run-novars">该命令没有变量，直接点击执行。</div>
        )}
        <div className="sw-tpl-run-preview">
          <label>最终命令</label>
          <pre className="sw-tpl-run-cmd">{resolvedCommand}</pre>
        </div>
      </div>
    </Modal>
  );
}
