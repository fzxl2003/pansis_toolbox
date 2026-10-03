import type { FormEvent } from "react";
import { Pencil, Radar, Trash2 } from "lucide-react";
import { API, blankTarget, type Run, type Target } from "../types";
import { Field, Modal, Progress } from "./shared";

export function TargetTable({
  targets,
  active,
  onScan,
  onEdit,
  onDelete,
}: {
  targets: Target[];
  active?: Run;
  onScan: (id?: string) => void;
  onEdit: (item: Target) => void;
  onDelete: (item: Target) => void;
}) {
  return (
    <table className="sn-table">
      <thead>
        <tr>
          <th>目标</th>
          <th>地址</th>
          <th>额外端口</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {targets.map((item) => (
          <tr key={item.id}>
            <td>
              <strong>{item.label}</strong>
              {active?.targetId === item.id && <Progress run={active} />}
            </td>
            <td>
              <code>{item.address}</code>
            </td>
            <td>
              <code>{item.customPorts || "无"}</code>
            </td>
            <td>
              <button
                className="sn-icon-button"
                disabled={!!active}
                onClick={() => onScan(item.id)}
                title="扫描"
                aria-label="扫描"
              >
                <Radar size={15} />
              </button>
              <button className="sn-icon-button" onClick={() => onEdit(item)}>
                <Pencil size={14} />
              </button>
              <button
                className="sn-icon-button danger"
                onClick={() => onDelete(item)}
              >
                <Trash2 size={14} />
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function TargetModal({
  target,
  form,
  setForm,
  onClose,
  onSubmit,
}: {
  target: Target | null;
  form: typeof blankTarget;
  setForm: (value: typeof blankTarget) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <Modal title={target ? "编辑扫描目标" : "添加扫描目标"} onClose={onClose}>
      <form className="sn-form-grid" onSubmit={onSubmit}>
        <Field label="显示名称">
          <input
            value={form.label}
            onChange={(event) =>
              setForm({ ...form, label: event.target.value })
            }
          />
        </Field>
        <Field label="IP 或域名">
          <input
            required
            value={form.address}
            onChange={(event) =>
              setForm({ ...form, address: event.target.value })
            }
          />
        </Field>
        <Field label="额外端口" full>
          <input
            value={form.customPorts}
            onChange={(event) =>
              setForm({ ...form, customPorts: event.target.value })
            }
            placeholder="22,8000-8100"
          />
          <small>支持逗号和端口区间，不限制数量；端口最大值为 65535。</small>
        </Field>
        <div className="sn-form-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button">保存</button>
        </div>
      </form>
    </Modal>
  );
}
