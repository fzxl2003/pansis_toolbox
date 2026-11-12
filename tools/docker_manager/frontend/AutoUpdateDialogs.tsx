import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode, RefObject } from 'react';
import {
  AlertCircle, ChevronRight, CircleCheck, Clock3, History, Loader2, Mail,
  TriangleAlert, X,
} from 'lucide-react';

import { ApiError, apiPost, apiPut } from '../../../frontend/src/api/client';
import { emptyRule, formatUpdateTime, ruleName, useUpdateResource } from './autoUpdates';
import type { UpdateRule, UpdateRuleInput, UpdateRun } from './autoUpdates';

type DialogProps = {
  onClose: () => void;
  returnFocus?: RefObject<HTMLElement | null>;
};

function UpdateDialog({ title, subtitle, onClose, returnFocus, busy, danger, size, footer, children }: DialogProps & {
  title: string;
  subtitle?: string;
  busy?: boolean;
  danger?: boolean;
  size?: 'compact' | 'wide';
  footer: ReactNode;
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const backdropPressed = useRef(false);
  const titleId = useId();
  const subtitleId = useId();
  useLayoutEffect(() => {
    const node = dialog.current;
    if (!node) return;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    node.showModal();
    node.querySelector<HTMLElement>('[data-initial-focus]')?.focus();
    return () => {
      if (node.open) node.close();
      if (trigger?.isConnected) trigger.focus();
      else returnFocus?.current?.focus();
    };
  }, [returnFocus]);

  return (
    <dialog ref={dialog} className={`dm-au-dialog ${size ?? ''}${busy ? ' is-busy' : ''}`}
      aria-labelledby={titleId} aria-describedby={subtitle ? subtitleId : undefined}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
      onPointerDown={(event) => { backdropPressed.current = event.target === event.currentTarget; }}
      onClick={(event) => {
        if (backdropPressed.current && event.target === event.currentTarget && !busy && !danger) onClose();
      }}>
      <div className="dm-au-dialog-inner">
        <header className="dm-au-dialog-heading">
          <div>
            <h3 id={titleId}>{title}</h3>
            {subtitle && <p id={subtitleId}>{subtitle}</p>}
          </div>
          <button type="button" className="dm-au-row-button dm-au-icon-button" aria-label="关闭弹窗"
            disabled={busy} onClick={onClose}><X size={19} aria-hidden="true" /></button>
        </header>
        <div className="dm-au-dialog-body">{children}</div>
        <footer className="dm-au-dialog-footer">{footer}</footer>
      </div>
    </dialog>
  );
}

export function UpdateFeedback({ error, message, onRetry }: {
  error?: Error;
  message?: string;
  onRetry?: () => void;
}) {
  const loginUrl = error instanceof ApiError && error.code === 'LOGIN_REQUIRED' ? error.loginUrl ?? '/login' : null;
  const Icon = error ? AlertCircle : CircleCheck;
  return (
    <div className={`dm-au-feedback ${error ? 'error' : 'success'}`} role={error ? 'alert' : 'status'}>
      <Icon size={16} aria-hidden="true" />
      <span>{error?.message ?? message}</span>
      {loginUrl ? <a className="dm-au-link" href={loginUrl}>登录</a> : onRetry && (
        <button type="button" className="dm-au-link" onClick={onRetry}>重试</button>
      )}
    </div>
  );
}

export function UpdateLoading({ text }: { text: string }) {
  return <div className="dm-au-loading" role="status"><Loader2 size={17} className="dm-au-spin" aria-hidden="true" />{text}</div>;
}

export function UpdateEmpty({ title, description, icon }: { title: string; description: string; icon: ReactNode }) {
  return (
    <div className="dm-au-empty">
      <span className="dm-au-empty-icon">{icon}</span>
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}

export function RuleEditorDialog({ path, rule, serverName, onSaved, ...dialog }: DialogProps & {
  path: string;
  rule?: UpdateRule;
  serverName: string;
  onSaved: () => void;
}) {
  const rulePath = rule ? `${path}/${encodeURIComponent(rule.id)}` : null;
  const { data, error, loading, reload } = useUpdateResource<{ rule: UpdateRule & UpdateRuleInput }>(rulePath);
  const [draft, setDraft] = useState<UpdateRuleInput | null>(rule ? null : emptyRule);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<Error | null>(null);
  const [editMail, setEditMail] = useState(false);
  const formId = useId();
  const form = draft ?? data?.rule ?? null;

  function change(fields: Partial<UpdateRuleInput>) {
    if (form) setDraft({ ...form, ...fields });
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const payload = {
        ...data?.rule, ...form,
        project: form.mode === 'compose' ? form.project.trim() : '',
        service: form.mode === 'compose' ? form.service.trim() : '',
      };
      if (rulePath) await apiPut(rulePath, payload);
      else await apiPost(path, payload);
      onSaved();
    } catch (failure) {
      setSaveError(failure instanceof Error ? failure : new Error('保存规则失败，请重试。'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <UpdateDialog {...dialog} busy={saving} title={rule ? '编辑更新规则' : '新增更新规则'}
        subtitle={rule ? `${serverName} · ${ruleName(rule)}` : serverName}
        footer={<>
          <button type="button" className="secondary-button dm-au-button" disabled={saving} onClick={dialog.onClose}>取消</button>
          <button type="submit" form={formId} className="primary-button dm-au-button" disabled={!form || saving}>
            {saving && <Loader2 size={15} className="dm-au-spin" aria-hidden="true" />}
            {saving ? '保存中…' : rule ? '保存修改' : '创建规则'}
          </button>
        </>}>
        {error && <UpdateFeedback error={error} onRetry={() => void reload()} />}
        {!form && loading && <UpdateLoading text="正在读取规则配置…" />}
        {form && (
          <form id={formId} className="dm-au-editor" onSubmit={(event) => void save(event)}>
            {saveError && <UpdateFeedback error={saveError} />}
            <fieldset disabled={saving} className="dm-au-fields">
              <legend>部署配置</legend>
              <div className="dm-au-form-grid">
                <label className="dm-au-field">
                  <span>部署方式</span>
                  <select className="dm-au-input" data-initial-focus={!rule ? true : undefined}
                    disabled={!!rule} value={form.mode}
                    onChange={(event) => change({ mode: event.target.value as UpdateRuleInput['mode'] })}>
                    <option value="run">Docker Run</option>
                    <option value="compose">Docker Compose</option>
                  </select>
                </label>
                <label className="dm-au-field">
                  <span>检查间隔</span>
                  <span className="dm-au-input-with-unit">
                    <input className="dm-au-input" type="number" required min={1} max={1440}
                      value={form.intervalMinutes || ''} onChange={(event) => change({ intervalMinutes: Number(event.target.value) })} />
                    <small>分钟</small>
                  </span>
                </label>
                {form.mode === 'compose' && <>
                  <label className="dm-au-field">
                    <span>原项目名</span>
                    <input className="dm-au-input" required maxLength={120} readOnly={!!rule}
                      value={form.project} onChange={(event) => change({ project: event.target.value })} />
                  </label>
                  <label className="dm-au-field">
                    <span>服务名</span>
                    <input className="dm-au-input" required maxLength={120} readOnly={!!rule}
                      value={form.service} onChange={(event) => change({ service: event.target.value })} />
                  </label>
                </>}
              </div>
              <label className="dm-au-field">
                <span>{form.mode === 'run' ? 'Docker Run 命令' : 'Compose YAML'}</span>
                <textarea className="dm-au-input dm-au-recipe" rows={8} wrap="off" required maxLength={128000}
                  spellCheck={false} autoCapitalize="off" autoComplete="off" value={form.recipe}
                  onChange={(event) => change({ recipe: event.target.value })} aria-describedby={formId + '-recipe-help'} />
                <small id={formId + '-recipe-help'}>
                  {form.mode === 'run'
                    ? '填写原部署命令，包含 --name 和要跟踪的镜像标签。'
                    : '粘贴已解析变量的完整 YAML，保留原有挂载，使用仓库镜像。'}
                </small>
              </label>
            </fieldset>

            <div className="dm-au-option">
              <Mail size={17} aria-hidden="true" />
              <div>
                <strong>邮件通知</strong>
                <small>{form.recipients.length ? `已配置 ${form.recipients.length} 个收件人` : '尚未配置收件人'}</small>
              </div>
              <button type="button" className="secondary-button dm-au-button" disabled={saving}
                onClick={() => setEditMail(true)}>配置邮件</button>
            </div>
          </form>
        )}
      </UpdateDialog>
      {editMail && form && <MailDialog recipients={form.recipients} onClose={() => setEditMail(false)}
        onConfirm={(recipients) => {
          change({ recipients });
          setEditMail(false);
        }} />}
    </>
  );
}

function MailDialog({ recipients, onClose, onConfirm }: {
  recipients: string[];
  onClose: () => void;
  onConfirm: (recipients: string[]) => void;
}) {
  const [value, setValue] = useState(recipients.join('\n'));
  const [error, setError] = useState('');
  const form = useId();
  const address = /^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/;

  function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = [...new Set(value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean))];
    if (values.length > 20 || values.some((item) => item.length > 254 || !address.test(item))) {
      setError('请填写有效的邮箱地址，最多 20 个。');
      return;
    }
    onConfirm(values);
  }

  return (
    <UpdateDialog title="邮件通知" size="compact" onClose={onClose}
      subtitle="有新镜像时发送开始邮件，更新结束后发送结果邮件。"
      footer={<>
        <button type="button" className="secondary-button dm-au-button" onClick={onClose}>取消</button>
        <button type="submit" className="primary-button dm-au-button" form={form}>确认</button>
      </>}>
      <form id={form} onSubmit={confirm} className="dm-au-editor">
        <label className="dm-au-field">
          <span>收件人</span>
          <textarea className="dm-au-input" rows={4} data-initial-focus value={value}
            onChange={(event) => { setValue(event.target.value); setError(''); }}
            aria-invalid={!!error} aria-describedby={form + '-help'} />
          <small id={form + '-help'}>{error || '多个地址用逗号或换行分隔。留空可关闭邮件通知。'}</small>
        </label>
        {error && <span role="alert" className="dm-au-sr-only">{error}</span>}
      </form>
    </UpdateDialog>
  );
}

const runStates: Record<string, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: 'warning' },
  checking: { label: '检查中', tone: 'running' },
  handoff: { label: '宿主机执行中', tone: 'running' },
  no_change: { label: '镜像未变更', tone: 'neutral' },
  skipped: { label: '已跳过', tone: 'neutral' },
  succeeded: { label: '成功', tone: 'success' },
  failed: { label: '失败', tone: 'error' },
  rolled_back: { label: '已回滚', tone: 'warning' },
  needs_review: { label: '待人工核对', tone: 'warning' },
};

const mailLabels: Record<string, string> = {
  skipped: '未安排', pending: '待发送', sending: '发送中', sent: '已发送', failed: '发送失败', unknown: '投递状态未知',
};

export function RunHistoryDialog({ path, rule, queuedRunId, ...dialog }: DialogProps & {
  path: string;
  rule: UpdateRule;
  queuedRunId?: string;
}) {
  const { data, loading, error, reload } = useUpdateResource<{ runs: UpdateRun[] }>(
    `${path}/${encodeURIComponent(rule.id)}/runs`, true,
  );
  const runs = data?.runs ?? [];

  return (
    <UpdateDialog {...dialog} title="运行记录" subtitle={ruleName(rule)} size="wide"
      footer={<>
        <button type="button" className="secondary-button dm-au-button" onClick={() => void reload()} disabled={loading}>
          {loading ? '刷新中…' : '刷新记录'}
        </button>
        <button type="button" className="secondary-button dm-au-button" onClick={dialog.onClose}>关闭</button>
      </>}>
      <div className="dm-au-history">
        {queuedRunId && !runs.some((run) => run.id === queuedRunId) && <UpdateFeedback message="检查请求已提交，正在等待运行记录。" />}
        {error && <UpdateFeedback error={error} onRetry={() => void reload()} />}
        {!data && loading && <UpdateLoading text="正在读取运行记录…" />}
        {data && !runs.length && <UpdateEmpty title="还没有运行记录"
          description="首次定时检查或手动检查后，结果会显示在这里。" icon={<History size={22} aria-hidden="true" />} />}
        {runs.length > 0 && <div className="dm-au-runs">
          {runs.map((run) => <RunRecord key={run.id} run={run} />)}
        </div>}
      </div>
    </UpdateDialog>
  );
}

function RunRecord({ run }: { run: UpdateRun }) {
  const status = runStates[run.status] ?? { label: run.status, tone: 'neutral' };
  const Icon = status.tone === 'running' ? Loader2 : status.tone === 'success' ? CircleCheck
    : status.tone === 'error' || status.tone === 'warning' ? TriangleAlert : Clock3;
  return (
    <details className="dm-au-run">
      <summary>
        <ChevronRight size={15} className="dm-au-run-chevron" aria-hidden="true" />
        <time dateTime={run.createdAt}>{formatUpdateTime(run.createdAt)}</time>
        <span className={`dm-au-badge ${status.tone}`}>
          <Icon size={12} className={status.tone === 'running' ? 'dm-au-spin' : undefined} aria-hidden="true" />{status.label}
        </span>
        {run.message && <span className="dm-au-run-summary" title={run.message}>{run.message}</span>}
      </summary>
      <div className="dm-au-run-body">
        <dl>
          <dt>运行 ID</dt><dd><code>{run.id}</code></dd>
          {run.finishedAt && <><dt>结束时间</dt><dd><time dateTime={run.finishedAt}>{formatUpdateTime(run.finishedAt)}</time></dd></>}
          {run.oldImage && <><dt>旧镜像 ID</dt><dd><code>{run.oldImage}</code></dd></>}
          {run.newImage && <><dt>新镜像 ID</dt><dd><code>{run.newImage}</code></dd></>}
          {run.digest && <><dt>仓库镜像摘要</dt><dd><code>{run.digest}</code></dd></>}
          <dt>开始邮件</dt><dd>{mailLabels[run.startMail] ?? run.startMail}</dd>
          <dt>结果邮件</dt><dd>{mailLabels[run.finishMail] ?? run.finishMail}</dd>
        </dl>
      </div>
    </details>
  );
}

const confirmationText = {
  check: { title: '立即检查并更新', body: '检测到新镜像时会重建目标，服务可能短暂中断。', action: '立即检查并更新' },
  enable: { title: '启用定时更新', body: '启用后会开始检查镜像，检测到新版本时自动重建目标。', action: '启用规则' },
  delete: { title: '删除更新规则', body: '这条规则及其全部运行和邮件记录将被永久删除。', action: '删除规则' },
};

export function ConfirmUpdateDialog({ rule, action, onConfirm, ...dialog }: DialogProps & {
  rule: UpdateRule;
  action: keyof typeof confirmationText;
  onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const { title, body, action: label } = confirmationText[action];
  const danger = action === 'delete';

  async function confirm() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (failure) {
      setError(failure instanceof Error ? failure : new Error('操作失败，请重试。'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <UpdateDialog {...dialog} title={title} subtitle={ruleName(rule)} size="compact" busy={busy} danger={danger}
      footer={<>
        <button type="button" data-initial-focus className="secondary-button dm-au-button"
          onClick={dialog.onClose} disabled={busy}>取消</button>
        <button type="button" className={`dm-au-button ${danger ? 'dm-au-danger-button' : 'primary-button'}`}
          disabled={busy} onClick={() => void confirm()}>
          {busy && <Loader2 size={15} className="dm-au-spin" aria-hidden="true" />}{busy ? '处理中…' : label}
        </button>
      </>}>
      <div className="dm-au-confirm">
        <p>{body}</p>
        {error && <UpdateFeedback error={error} />}
      </div>
    </UpdateDialog>
  );
}
