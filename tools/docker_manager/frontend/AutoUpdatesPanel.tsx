import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';

import { apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import { Alert } from './components';
import type { DmServer } from './types';
import { API } from './utils';

type RuleInput = {
  mode: 'run' | 'compose';
  recipe: string;
  project: string;
  service: string;
  intervalMinutes: number;
  recipients: string[];
  selfUpdate: boolean;
  enabled: boolean;
};

type Rule = Omit<RuleInput, 'recipe'> & {
  id: string;
  target: string;
  image: string;
  nextAt: string;
  pauseReason: string;
};

type Run = {
  id: string;
  status: string;
  message: string;
  createdAt: string;
  finishedAt: string | null;
  oldImage: string;
  newImage: string;
  digest: string;
  startMail: string;
  finishMail: string;
};

const initialRule: RuleInput = {
  mode: 'run', recipe: '', project: '', service: '', intervalMinutes: 15,
  recipients: [], selfUpdate: false, enabled: false,
};

const statusLabels: Record<string, string> = {
  queued: '排队中', checking: '检查中', handoff: '宿主机执行中', no_change: '镜像未变更',
  skipped: '已跳过', succeeded: '成功', failed: '失败', rolled_back: '已回滚', needs_review: '待人工核对',
};

const mailLabels: Record<string, string> = {
  skipped: '未安排', pending: '待发送', sending: '发送中', sent: '已发送', failed: '发送失败', unknown: '投递状态未知',
};

function formatTime(value: string) {
  return new Date(value).toLocaleString();
}

export function AutoUpdatesPanel({ servers }: { servers: DmServer[] }) {
  const [selectedServer, setSelectedServer] = useState('');
  const server = servers.find((item) => item.id === selectedServer) ?? servers[0];
  if (!server) return null;

  return (
    <section className="dm-form-field">
      <label htmlFor="auto-update-server">目标服务器</label>
      <select id="auto-update-server" value={server.id} onChange={(event) => setSelectedServer(event.target.value)}>
        {servers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select>
      <ServerUpdates key={server.id} server={server} />
    </section>
  );
}

function ServerUpdates({ server }: { server: DmServer }) {
  const path = `${API}/servers/${encodeURIComponent(server.id)}/auto-updates`;
  const [rules, setRules] = useState<Rule[]>([]);
  const [editor, setEditor] = useState<{ id?: string; rule: RuleInput } | null>(null);
  const [history, setHistory] = useState<Rule | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [notice, setNotice] = useState('');
  const historyId = history?.id;

  async function loadRules() {
    const result = await apiGet<{ rules: Rule[] }>(path);
    setRules(result.rules);
  }

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await action();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '操作失败');
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const result = await apiGet<{ rules: Rule[] }>(path);
        if (active) {
          setRules(result.rules);
          setLoadError('');
        }
        if (historyId) {
          const historyResult = await apiGet<{ runs: Run[] }>(`${path}/${historyId}/runs`);
          if (active) setRuns(historyResult.runs);
        }
      } catch (failure) {
        if (active) setLoadError(failure instanceof Error ? failure.message : '读取更新记录失败');
      } finally {
        if (active) setLoading(false);
      }
    }
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [path, historyId]);

  async function edit(rule: Rule) {
    const result = await apiGet<{ rule: Rule & { recipe: string } }>(`${path}/${rule.id}`);
    setEditor({ id: rule.id, rule: result.rule });
  }

  async function toggle(rule: Rule) {
    const { rule: current } = await apiGet<{ rule: Rule & { recipe: string } }>(`${path}/${rule.id}`);
    await apiPut(`${path}/${rule.id}`, { ...current, enabled: !current.enabled });
    await loadRules();
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editor) return;
    const addresses = new FormData(event.currentTarget).get('recipients')?.toString() ?? '';
    const rule = { ...editor.rule, recipients: addresses.split(/[,，]/).map((item) => item.trim()).filter(Boolean) };
    await act(async () => {
      if (editor.id) {
        await apiPut(`${path}/${editor.id}`, rule);
      } else {
        await apiPost(path, rule);
      }
      setEditor(null);
      await loadRules();
    });
  }

  function changeRule(changes: Partial<RuleInput>) {
    setEditor((current) => current && { ...current, rule: { ...current.rule, ...changes } });
  }

  function showHistory(rule: Rule) {
    if (historyId !== rule.id) setRuns([]);
    setHistory(rule);
  }

  return (
    <>
      {(error || loadError) && <div role="alert"><Alert type="error">{error || loadError}</Alert></div>}
      {notice && <div role="status"><Alert type="success">{notice}</Alert></div>}
      <div className="dm-card-actions">
        <button type="button" className="btn primary" disabled={busy} onClick={() => setEditor({ rule: initialRule })}>新增规则</button>
      </div>
      {editor && (
        <form key={editor.id ?? 'new'} className="dm-card dm-form-field" onSubmit={save}>
          <h3>{editor.id ? '编辑更新规则' : '新增更新规则'}</h3>
          <div className="dm-form-grid">
            <label>
              部署方式
              <select value={editor.rule.mode} disabled={!!editor.id} onChange={(event) => changeRule({ mode: event.target.value as RuleInput['mode'] })}>
                <option value="run">docker run</option>
                <option value="compose">Docker Compose</option>
              </select>
            </label>
            <label>
              检查间隔（分钟）
              <input type="number" required min={1} max={1440} value={editor.rule.intervalMinutes || ''} onChange={(event) => changeRule({ intervalMinutes: Number(event.target.value) })} />
            </label>
            {editor.rule.mode === 'compose' && (
              <>
                <label>原项目名<input required readOnly={!!editor.id} value={editor.rule.project} onChange={(event) => changeRule({ project: event.target.value })} /></label>
                <label>服务名<input required readOnly={!!editor.id} value={editor.rule.service} onChange={(event) => changeRule({ service: event.target.value })} /></label>
              </>
            )}
          </div>
          <label htmlFor="auto-update-recipe">{editor.rule.mode === 'run' ? '原 docker run 命令（含 --name）' : '已解析变量的独立 Compose YAML'}</label>
          <textarea id="auto-update-recipe" className="mono" required rows={8} maxLength={128000} spellCheck={false} value={editor.rule.recipe} onChange={(event) => changeRule({ recipe: event.target.value })} />
          <label>
            邮件收件人（逗号分隔，可选）
            <input name="recipients" defaultValue={editor.rule.recipients.join(', ')} />
          </label>
          <label className="dm-form-check">
            <input type="checkbox" checked={editor.rule.selfUpdate} onChange={(event) => changeRule({ selfUpdate: event.target.checked })} />
            更新 Toolbox 自身
          </label>
          <div className="dm-card-actions">
            <button type="button" className="btn" onClick={() => setEditor(null)}>取消</button>
            <button className="btn primary" type="submit" disabled={busy}>保存</button>
          </div>
        </form>
      )}
      {loading && <p role="status">正在加载规则…</p>}
      {!loading && rules.length === 0 && <p className="dm-empty">暂无自动更新规则</p>}
      <div className="dm-card-grid">
        {rules.map((rule) => (
          <article className="dm-card" key={rule.id}>
            <h3 className="dm-card-title">{rule.target}</h3>
            <div className="dm-card-meta">{rule.image} · {rule.enabled ? `每 ${rule.intervalMinutes} 分钟检查` : '已停用'}</div>
            {rule.pauseReason && <p>{rule.pauseReason}</p>}
            <div className="dm-card-actions">
              <button type="button" className="btn" disabled={busy} onClick={() => void act(() => edit(rule))}>编辑</button>
              <button type="button" className="btn" disabled={busy} onClick={() => void act(() => toggle(rule))}>{rule.enabled ? '停用' : '启用'}</button>
              <button type="button" className="btn" disabled={busy} onClick={() => void act(async () => {
                await apiPost(`${path}/${rule.id}/check`, {});
                showHistory(rule);
                setNotice('已提交检查，检测到新镜像后将自动更新。');
              })}>立即检查并更新</button>
              <button type="button" className="btn" onClick={() => showHistory(rule)}>运行记录</button>
              <button type="button" className="btn" disabled={busy} onClick={() => {
                if (!window.confirm(`删除 ${rule.target} 的规则和运行记录？`)) return;
                void act(async () => {
                  await apiDelete(`${path}/${rule.id}`);
                  if (historyId === rule.id) setHistory(null);
                  if (editor?.id === rule.id) setEditor(null);
                  await loadRules();
                });
              }}>删除</button>
            </div>
          </article>
        ))}
      </div>
      {history && (
        <section className="dm-card">
          <div className="dm-card-header">
            <h3 className="dm-card-title">{history.target} · 运行记录</h3>
            <button type="button" className="btn" onClick={() => setHistory(null)}>关闭</button>
          </div>
          {runs.length === 0 && <p className="dm-empty">暂无运行记录</p>}
          {runs.map((run) => (
            <details key={run.id}>
              <summary>{formatTime(run.createdAt)} · {statusLabels[run.status] ?? run.status}{run.message && ` · ${run.message}`}</summary>
              <dl>
                <dt>运行 ID</dt><dd><code>{run.id}</code></dd>
                {run.finishedAt && <><dt>结束时间</dt><dd>{formatTime(run.finishedAt)}</dd></>}
                {run.oldImage && <><dt>旧镜像 ID</dt><dd className="break-all"><code>{run.oldImage}</code></dd></>}
                {run.newImage && <><dt>新镜像 ID</dt><dd className="break-all"><code>{run.newImage}</code></dd></>}
                {run.digest && <><dt>仓库镜像摘要</dt><dd className="break-all"><code>{run.digest}</code></dd></>}
                <dt>开始邮件</dt><dd>{mailLabels[run.startMail] ?? run.startMail}</dd>
                <dt>结果邮件</dt><dd>{mailLabels[run.finishMail] ?? run.finishMail}</dd>
              </dl>
            </details>
          ))}
        </section>
      )}
    </>
  );
}
