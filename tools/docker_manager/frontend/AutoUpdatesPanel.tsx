import { useId, useLayoutEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import {
  Box, CircleCheck, CirclePause, Clock3, History, Layers3, Pencil, Play,
  Plus, RefreshCw, Server, Trash2, TriangleAlert,
} from 'lucide-react';

import { apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import type { DmServer } from './types';
import { ConfirmUpdateDialog, RuleEditorDialog, RunHistoryDialog, UpdateFeedback, UpdateEmpty, UpdateLoading } from './AutoUpdateDialogs';
import { formatUpdateTime, ruleName, updateRulesPath, useUpdateResource } from './autoUpdates';
import type { UpdateRule, UpdateRuleInput } from './autoUpdates';

type RuleAction = 'check' | 'enable' | 'pause' | 'delete';
type OpenPanel =
  | { kind: 'edit'; rule?: UpdateRule }
  | { kind: 'history'; rule: UpdateRule; queuedRunId?: string }
  | { kind: 'confirm'; rule: UpdateRule; action: Exclude<RuleAction, 'pause'> };

export function AutoUpdatesPanel({ servers }: { servers: DmServer[] }) {
  const [selected, setSelected] = useState('');
  const restoreServerFocus = useRef(false);
  const server = servers.find((item) => item.id === selected) ?? servers[0];
  if (!server) return null;
  return <ServerUpdates key={server.id} server={server} servers={servers} restoreServerFocus={restoreServerFocus}
    onSelect={(id) => {
      restoreServerFocus.current = true;
      setSelected(id);
    }} />;
}

function ServerUpdates({ server, servers, onSelect, restoreServerFocus }: {
  server: DmServer;
  servers: DmServer[];
  onSelect: (id: string) => void;
  restoreServerFocus: RefObject<boolean>;
}) {
  const path = updateRulesPath(server.id);
  const { data, loading, error, reload } = useUpdateResource<{ rules: UpdateRule[] }>(path, true);
  const rules = data?.rules ?? [];
  const [panel, setPanel] = useState<OpenPanel | null>(null);
  const [actionError, setActionError] = useState<Error | null>(null);
  const [notice, setNotice] = useState('');
  const [pausing, setPausing] = useState<string | null>(null);
  const createButton = useRef<HTMLButtonElement>(null);
  const serverSelect = useRef<HTMLSelectElement>(null);
  const serverField = useId();
  const title = useId();
  const listTitle = useId();

  useLayoutEffect(() => {
    if (restoreServerFocus.current) {
      serverSelect.current?.focus();
      restoreServerFocus.current = false;
    }
  }, [restoreServerFocus]);

  function open(next: OpenPanel) {
    setNotice('');
    setActionError(null);
    setPanel(next);
  }

  async function act(rule: UpdateRule, action: RuleAction) {
    const rulePath = `${path}/${encodeURIComponent(rule.id)}`;
    if (action === 'check') {
      const result = await apiPost<{ runId: string }>(`${rulePath}/check`, {});
      setPanel({ kind: 'history', rule, queuedRunId: result.runId });
      return;
    }
    if (action === 'delete') {
      await apiDelete(rulePath);
      setNotice('规则和运行记录已删除。');
    } else {
      const { rule: current } = await apiGet<{ rule: UpdateRule & UpdateRuleInput }>(rulePath);
      await apiPut(rulePath, { ...current, enabled: action === 'enable' });
      setNotice(action === 'enable' ? '规则已启用。' : '规则已停用。');
    }
    if (action !== 'pause') setPanel(null);
    const refreshed = reload(true);
    if (action === 'delete') {
      void refreshed.then(() => window.requestAnimationFrame(() => {
        if (document.activeElement === document.body) createButton.current?.focus();
      }));
    }
  }

  async function pause(rule: UpdateRule) {
    if (pausing) return;
    setPausing(rule.id);
    setActionError(null);
    setNotice('');
    try {
      await act(rule, 'pause');
    } catch (failure) {
      setActionError(failure instanceof Error ? failure : new Error('停用规则失败，请重试。'));
    } finally {
      setPausing(null);
    }
  }

  return (
    <section className="dm-au" aria-labelledby={title}>
      <header className="dm-au-heading">
        <div>
          <h2 id={title}>自动更新</h2>
          <p>跟踪镜像标签，在检测到新版本时重建容器或 Compose 服务。</p>
        </div>
        <button ref={createButton} type="button" className="primary-button dm-au-button"
          onClick={() => open({ kind: 'edit' })}>
          <Plus size={16} aria-hidden="true" />新增规则
        </button>
      </header>

      <section className="dm-au-panel" aria-labelledby={listTitle}>
        <header className="dm-au-toolbar">
          <div className="dm-au-panel-heading">
            <h3 id={listTitle}>更新规则</h3>
            {data && <span>{rules.length} 条</span>}
          </div>
          <div className="dm-au-toolbar-controls">
            <label htmlFor={serverField}>目标服务器</label>
            <div className="dm-au-select-wrap">
              <Server size={15} aria-hidden="true" />
              <select id={serverField} ref={serverSelect} className="dm-au-input" value={server.id}
                onChange={(event) => onSelect(event.target.value)}>
                {servers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </div>
            <button type="button" className="secondary-button dm-au-button" disabled={loading}
              onClick={() => void reload()}>
              <RefreshCw size={14} className={loading ? 'dm-au-spin' : undefined} aria-hidden="true" />刷新
            </button>
          </div>
        </header>

        {(error || actionError || notice) && <div className="dm-au-feedback-region">
          {error && <UpdateFeedback error={error} onRetry={() => void reload()} />}
          {actionError && <UpdateFeedback error={actionError} />}
          {notice && <UpdateFeedback message={notice} />}
        </div>}

        {!data && loading && <UpdateLoading text="正在读取更新规则…" />}
        {data && rules.length === 0 && <UpdateEmpty
          title="还没有更新规则"
          description="为这台服务器上的容器或 Compose 服务新增规则，即可开始跟踪镜像。"
          icon={<RefreshCw size={22} aria-hidden="true" />}
        />}
        {rules.length > 0 && (
          <table className="dm-au-table" role="table" aria-label="自动更新规则">
            <thead role="rowgroup"><tr role="row">
              <th scope="col">部署目标</th><th scope="col">跟踪镜像</th>
              <th scope="col">状态与计划</th><th scope="col">操作</th>
            </tr></thead>
            <tbody role="rowgroup">
              {rules.map((rule) => {
                const name = ruleName(rule);
                const ModeIcon = rule.mode === 'compose' ? Layers3 : Box;
                const StatusIcon = rule.enabled ? CircleCheck : rule.pauseReason ? TriangleAlert : CirclePause;
                const status = rule.enabled ? '已启用' : rule.pauseReason ? '已暂停' : '已停用';
                const tagStart = rule.image.lastIndexOf(':');
                const hasTag = tagStart > rule.image.lastIndexOf('/');
                return (
                  <tr key={rule.id} role="row">
                    <td role="cell" data-cell="target">
                      <div className="dm-au-target">
                        <span className="dm-au-target-icon"><ModeIcon size={17} aria-hidden="true" /></span>
                        <div className="dm-au-cell-stack">
                          <strong title={name}>{name}</strong>
                          <small title={rule.mode === 'compose' ? rule.project : undefined}>
                            {rule.mode === 'compose' ? `Compose · 项目 ${rule.project}` : 'docker run'}
                          </small>
                        </div>
                      </div>
                    </td>
                    <td role="cell" data-cell="image">
                      <span className="dm-au-mobile-label">跟踪镜像</span>
                      <code className="dm-au-image" title={rule.image}>
                        <span>{hasTag ? rule.image.slice(0, tagStart) : rule.image}</span>
                        {hasTag && <span className="dm-au-image-tag">{rule.image.slice(tagStart)}</span>}
                      </code>
                    </td>
                    <td role="cell" data-cell="schedule">
                      <span className="dm-au-mobile-label">状态与计划</span>
                      <div className="dm-au-cell-stack">
                        <div className="dm-au-rule-state">
                          <span className={`dm-au-badge ${rule.enabled ? 'success' : rule.pauseReason ? 'warning' : 'neutral'}`}>
                            <StatusIcon size={12} aria-hidden="true" />{status}
                          </span>
                          <button type="button" className="dm-au-link" aria-label={`${rule.enabled ? '停用' : '启用'} ${name}`}
                            disabled={!!pausing} onClick={() => rule.enabled
                              ? void pause(rule)
                              : open({ kind: 'confirm', rule, action: 'enable' })}>
                            {pausing === rule.id ? '停用中…' : rule.enabled ? '停用' : '启用'}
                          </button>
                        </div>
                        {rule.enabled && <small className="dm-au-schedule" title={`下次检查 ${formatUpdateTime(rule.nextAt)}`}>
                          <Clock3 size={12} aria-hidden="true" />每 {rule.intervalMinutes} 分钟
                          <span className="dm-au-sr-only">，下次检查 {formatUpdateTime(rule.nextAt)}</span>
                        </small>}
                        {rule.pauseReason && <small className="dm-au-pause" title={rule.pauseReason}>{rule.pauseReason}</small>}
                      </div>
                    </td>
                    <td role="cell" data-cell="actions">
                      <div className="dm-au-row-actions">
                        <button type="button" className="dm-au-row-button" aria-label={`检查更新 ${name}`} title="立即检查并更新"
                          onClick={() => open({ kind: 'confirm', rule, action: 'check' })}>
                          <Play size={14} aria-hidden="true" />检查更新
                        </button>
                        <button type="button" className="dm-au-row-button" aria-label={`查看 ${name} 的运行记录`} title="运行记录"
                          onClick={() => open({ kind: 'history', rule })}>
                          <History size={15} aria-hidden="true" />记录
                        </button>
                        <button type="button" className="dm-au-row-button dm-au-icon-button"
                          aria-label={`编辑 ${name}`} title="编辑规则" onClick={() => open({ kind: 'edit', rule })}>
                          <Pencil size={15} aria-hidden="true" />
                        </button>
                        <button type="button" className="dm-au-row-button dm-au-row-danger" aria-label={`删除 ${name}`} title="删除规则"
                          onClick={() => open({ kind: 'confirm', rule, action: 'delete' })}>
                          <Trash2 size={15} aria-hidden="true" />删除
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {panel?.kind === 'edit' && <RuleEditorDialog path={path} serverName={server.name} rule={panel.rule}
        onClose={() => setPanel(null)} returnFocus={createButton}
        onSaved={() => {
          setPanel(null);
          setNotice(panel.rule ? '规则已保存。' : '规则已创建，可以手动检查或启用定时更新。');
          void reload(true);
        }} />}
      {panel?.kind === 'history' && <RunHistoryDialog path={path} rule={panel.rule} queuedRunId={panel.queuedRunId}
        onClose={() => setPanel(null)} returnFocus={createButton} />}
      {panel?.kind === 'confirm' && <ConfirmUpdateDialog rule={panel.rule} action={panel.action}
        onClose={() => setPanel(null)} returnFocus={createButton} onConfirm={() => act(panel.rule, panel.action)} />}
    </section>
  );
}
