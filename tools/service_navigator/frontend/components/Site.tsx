import type { FormEvent } from "react";
import { Field, Modal } from "./shared";

export function SiteModal({
  form,
  setForm,
  onClose,
  onSubmit,
  create = false,
}: {
  form: { title: string; slug: string; description: string };
  setForm: (value: {
    title: string;
    slug: string;
    description: string;
  }) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
  create?: boolean;
}) {
  return (
    <Modal title={create ? "创建服务导航站" : "站点设置"} onClose={onClose}>
      <form className="sn-form-grid" onSubmit={onSubmit}>
        <Field label="站点标题">
          <input
            required
            value={form.title}
            onChange={(event) =>
              setForm({ ...form, title: event.target.value })
            }
          />
        </Field>
        <Field label="公开地址 slug">
          <input
            required
            value={form.slug}
            onChange={(event) => setForm({ ...form, slug: event.target.value })}
          />
        </Field>
        <Field label="简介" full>
          <textarea
            value={form.description}
            onChange={(event) =>
              setForm({ ...form, description: event.target.value })
            }
          />
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
