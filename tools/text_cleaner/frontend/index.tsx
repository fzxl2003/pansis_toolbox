import { useMemo, useState } from 'react';

type CaseMode = 'none' | 'lower' | 'upper';

type CleanResponse = {
  originalLength: number;
  cleanedLength: number;
  text: string;
};

export default function TextCleanerTool() {
  const [text, setText] = useState('  粘贴一些文本，然后清理多余空白。  \n\nHello      Toolbox!');
  const [trim, setTrim] = useState(true);
  const [collapseWhitespace, setCollapseWhitespace] = useState(true);
  const [removeBlankLines, setRemoveBlankLines] = useState(true);
  const [caseMode, setCaseMode] = useState<CaseMode>('none');
  const [result, setResult] = useState<CleanResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const delta = useMemo(() => {
    if (!result) return null;
    return result.originalLength - result.cleanedLength;
  }, [result]);

  async function cleanText() {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/tools/text-cleaner/clean', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, trim, collapseWhitespace, removeBlankLines, caseMode }),
      });
      if (!response.ok) {
        throw new Error(`清洗失败：${response.status}`);
      }
      setResult((await response.json()) as CleanResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : '清洗失败');
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="tool-surface">
      <div className="tool-header">
        <div>
          <p className="eyebrow">Text Utility</p>
          <h1>文本清洗</h1>
        </div>
        <button className="primary-button" type="button" onClick={cleanText} disabled={isLoading}>
          {isLoading ? '处理中' : '清洗文本'}
        </button>
      </div>

      <div className="tool-grid">
        <section className="panel">
          <label className="field-label" htmlFor="text-input">输入文本</label>
          <textarea
            id="text-input"
            className="text-area"
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          <div className="option-grid">
            <label className="check-row">
              <input type="checkbox" checked={trim} onChange={(event) => setTrim(event.target.checked)} />
              去除首尾空白
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={collapseWhitespace}
                onChange={(event) => setCollapseWhitespace(event.target.checked)}
              />
              合并连续空白
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={removeBlankLines}
                onChange={(event) => setRemoveBlankLines(event.target.checked)}
              />
              移除空行
            </label>
            <select className="select" value={caseMode} onChange={(event) => setCaseMode(event.target.value as CaseMode)}>
              <option value="none">保持大小写</option>
              <option value="lower">转小写</option>
              <option value="upper">转大写</option>
            </select>
          </div>
        </section>

        <section className="panel">
          <div className="result-header">
            <span>清洗结果</span>
            {delta !== null && <span className="metric">减少 {delta} 字符</span>}
          </div>
          {error && <div className="error-box">{error}</div>}
          <textarea className="text-area result" readOnly value={result?.text ?? ''} placeholder="结果会显示在这里" />
        </section>
      </div>
    </div>
  );
}
