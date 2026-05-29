import { FormEvent, useEffect, useState } from 'react';
import { LogIn, Trash2, Upload } from 'lucide-react';

import { ApiError, apiDelete, apiGet, apiPost, apiPostForm } from '../../../frontend/src/api/client';
import { LoginPanel } from '../../../frontend/src/components/LoginPanel';

type MemoSummary = {
  id: string;
  title: string;
  filename: string;
  createdAt: string;
  updatedAt: string;
  sizeBytes: number;
};

type MemoDetail = MemoSummary & {
  content: string;
};

export default function MemoDemoTool() {
  const [memos, setMemos] = useState<MemoSummary[]>([]);
  const [selected, setSelected] = useState<MemoDetail | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loginRequired, setLoginRequired] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    void loadMemos();
  }, []);

  async function loadMemos() {
    setError(null);
    try {
      const payload = await apiGet<MemoSummary[]>('/api/tools/memo-demo/memos');
      setMemos(payload);
      setLoginRequired(false);
      if (payload.length && !selected) {
        await openMemo(payload[0].id);
      }
    } catch (err) {
      handleError(err);
    }
  }

  async function openMemo(memoId: string) {
    try {
      setSelected(await apiGet<MemoDetail>(`/api/tools/memo-demo/memos/${memoId}`));
      setLoginRequired(false);
    } catch (err) {
      handleError(err);
    }
  }

  async function uploadMemo(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError('请选择一个 .txt 文件');
      return;
    }
    if (!file.name.toLowerCase().endsWith('.txt')) {
      setError('只支持上传 .txt 文件');
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append('file', file);
      const uploaded = await apiPostForm<MemoSummary>('/api/tools/memo-demo/upload', form);
      setFile(null);
      setMemos((items) => [uploaded, ...items]);
      await openMemo(uploaded.id);
    } catch (err) {
      handleError(err);
    } finally {
      setIsLoading(false);
    }
  }

  async function deleteMemo(memoId: string) {
    try {
      await apiDelete(`/api/tools/memo-demo/memos/${memoId}`);
      setMemos((items) => items.filter((item) => item.id !== memoId));
      if (selected?.id === memoId) {
        setSelected(null);
      }
    } catch (err) {
      handleError(err);
    }
  }

  function handleError(err: unknown) {
    if (err instanceof ApiError && err.code === 'LOGIN_REQUIRED') {
      setLoginRequired(true);
      setError(null);
      return;
    }
    setError(err instanceof Error ? err.message : '操作失败');
  }

  if (loginRequired) {
    return (
      <div className="tool-surface">
        <div className="tool-header">
          <div>
            <p className="eyebrow">Personal Data Tool</p>
            <h1>备忘录 Demo</h1>
          </div>
        </div>
        <LoginPanel onSuccess={() => void loadMemos()} />
      </div>
    );
  }

  return (
    <div className="tool-surface">
      <div className="tool-header">
        <div>
          <p className="eyebrow">Personal Data Tool</p>
          <h1>备忘录 Demo</h1>
        </div>
        <span className="status-pill ok"><LogIn size={14} />需要登录</span>
      </div>

      {error && <div className="error-box">{error}</div>}

      <form className="panel upload-panel" onSubmit={uploadMemo}>
        <label className="field-label" htmlFor="memo-file">上传 TXT 备忘录</label>
        <div className="file-row">
          <input
            id="memo-file"
            type="file"
            accept=".txt,text/plain"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          <button className="primary-button" type="submit" disabled={isLoading}>
            <Upload size={16} />
            {isLoading ? '上传中' : '保存'}
          </button>
        </div>
      </form>

      <div className="tool-grid">
        <section className="panel memo-list">
          <div className="result-header">
            <span>我的备忘录</span>
            <span className="metric">{memos.length} 条</span>
          </div>
          {memos.length === 0 ? (
            <p className="muted">还没有备忘录。</p>
          ) : (
            memos.map((memo) => (
              <button
                key={memo.id}
                type="button"
                className={selected?.id === memo.id ? 'memo-item active' : 'memo-item'}
                onClick={() => void openMemo(memo.id)}
              >
                <span>{memo.title}</span>
                <small>{Math.ceil(memo.sizeBytes / 1024)} KB</small>
              </button>
            ))
          )}
        </section>

        <section className="panel memo-detail">
          {selected ? (
            <>
              <div className="result-header">
                <span>{selected.title}</span>
                <button className="icon-button danger" type="button" onClick={() => void deleteMemo(selected.id)} title="删除">
                  <Trash2 size={16} />
                </button>
              </div>
              <pre className="memo-content">{selected.content}</pre>
            </>
          ) : (
            <p className="muted">选择一条备忘录查看内容。</p>
          )}
        </section>
      </div>
    </div>
  );
}
