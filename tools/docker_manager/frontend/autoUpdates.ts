import { useCallback, useEffect, useRef, useState } from 'react';

import { apiGet } from '../../../frontend/src/api/client';
import { API } from './utils';

export type UpdateRuleInput = {
  mode: 'run' | 'compose';
  recipe: string;
  project: string;
  service: string;
  intervalMinutes: number;
  recipients: string[];
  enabled: boolean;
};

export type UpdateRule = Omit<UpdateRuleInput, 'recipe'> & {
  id: string;
  target: string;
  image: string;
  nextAt: string;
  pauseReason: string;
};

export type UpdateRun = {
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

export const emptyRule: UpdateRuleInput = {
  mode: 'run', recipe: '', project: '', service: '', intervalMinutes: 15,
  recipients: [], enabled: false,
};

export function updateRulesPath(serverId: string) {
  return `${API}/servers/${encodeURIComponent(serverId)}/auto-updates`;
}

export function ruleName(rule: UpdateRule) {
  return rule.mode === 'compose' ? rule.service : rule.target.replace(/^run:/, '');
}

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

export function formatUpdateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '—' : dateTime.format(date);
}

type ResourceState<T> = { data: T | null; loading: boolean; error: Error | null };

export function useUpdateResource<T>(path: string | null, poll = false) {
  const [state, setState] = useState<ResourceState<T>>({ data: null, loading: path !== null, error: null });
  const requestId = useRef(0);
  const pending = useRef<Promise<void> | null>(null);
  const reload = useCallback((supersede = false) => {
    if (!path) return Promise.resolve();
    if (pending.current && !supersede) return pending.current;
    const current = ++requestId.current;
    setState((previous) => ({ ...previous, loading: true }));
    const request = apiGet<T>(path)
      .then((data) => {
        if (requestId.current === current) setState({ data, loading: false, error: null });
      })
      .catch((failure: unknown) => {
        if (requestId.current !== current) return;
        const error = failure instanceof Error ? failure : new Error('读取数据失败，请重试。');
        setState((previous) => ({ ...previous, loading: false, error }));
      })
      .finally(() => {
        if (requestId.current === current) pending.current = null;
      });
    pending.current = request;
    return request;
  }, [path]);

  useEffect(() => {
    const refreshVisible = () => {
      if (!document.hidden) void reload();
    };
    void reload();
    const timer = poll ? window.setInterval(refreshVisible, 5000) : undefined;
    if (poll) document.addEventListener('visibilitychange', refreshVisible);
    return () => {
      requestId.current += 1;
      pending.current = null;
      if (timer !== undefined) window.clearInterval(timer);
      if (poll) document.removeEventListener('visibilitychange', refreshVisible);
    };
  }, [poll, reload]);

  return { ...state, reload };
}
