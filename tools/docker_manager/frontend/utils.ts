// ============================================================
// Utils — Docker Manager
// ============================================================

import { useCallback, useState } from 'react';
import { ApiError } from '../../../frontend/src/api/client';

// API 基础路径
export const API = '/api/tools/docker-manager';

// ---- 容器状态辅助 ----

export function containerStateClass(state?: string): string {
  const s = (state ?? '').toLowerCase();
  if (s.includes('running') || s === 'up') return 'running';
  if (s.includes('exited')) return 'exited';
  if (s.includes('paused')) return 'paused';
  if (s.includes('created')) return 'created';
  return 'unknown';
}

// 解析状态时间，如 "Up 2 hours" → 提取 "Up" 和 "2 hours" 两部分
export function parseContainerStatus(status: string): { label: string; time: string } {
  const s = (status ?? '').trim();
  // "Up X hours/minutes/seconds" → label=Up, time=X hours
  const upMatch = s.match(/^(Up)\s+(.+?)(\s*\(.*\))?$/i);
  if (upMatch) return { label: 'Up', time: upMatch[2].trim() };
  // "Exited (N) X hours ago" → label=Exited, time=X hours ago
  const exitMatch = s.match(/^(Exited\s*(?:\(\d+\))?)\s+(.+)$/i);
  if (exitMatch) return { label: exitMatch[1].trim(), time: exitMatch[2].trim() };
  // "Created", "Paused", "Restarting"…
  const wordMatch = s.match(/^(\w+)\s*(.*)$/);
  if (wordMatch) return { label: wordMatch[1], time: wordMatch[2].trim() };
  return { label: s, time: '' };
}

// ---- 错误 Hook ----

export function useErrorMsg(): [string | null, (e: unknown) => void, () => void] {
  const [msg, setMsg] = useState<string | null>(null);
  const set = useCallback((e: unknown) => {
    if (e instanceof ApiError) setMsg(e.message);
    else if (e instanceof Error) setMsg(e.message);
    else setMsg(String(e));
  }, []);
  const clear = useCallback(() => setMsg(null), []);
  return [msg, set, clear];
}

// ---- 文件大小格式化 ----

/**
 * 将 GB 数值格式化为人类可读的大小字符串
 * < 1 MB  → KB；< 1 GB → MB；≥ 1 GB → GB
 */
export function formatSize(sizeGb: number): string {
  const bytes = sizeGb * 1024 * 1024 * 1024;
  if (bytes < 1024) return `${bytes.toFixed(0)} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  const gb = mb / 1024;
  return `${gb.toFixed(2)} GB`;
}

// ---- Markdown 渲染 ----


export function renderMarkdown(md: string): string {
  let html = md
    // Code blocks
    .replace(/```[\s\S]*?```/g, (m) => {
      const inner = m.replace(/^```[^\n]*\n?/, '').replace(/```$/, '');
      return `<pre><code>${inner.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>`;
    })
    // Inline code
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    // Headers
    .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // Bold & italic
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // Blockquote
    .replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>')
    // Unordered list
    .replace(/^[\-\*] (.+)$/gm, '<li>$1</li>')
    // Ordered list
    .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
    // Horizontal rule
    .replace(/^---$/gm, '<hr>')
    // Links
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    // Paragraphs (double newline)
    .replace(/\n\n+/g, '</p><p>')
    // Line breaks
    .replace(/\n/g, '<br>');

  // Wrap loose li in ul
  html = html.replace(/(<li>.*?<\/li>)+/gs, (m) => `<ul>${m}</ul>`);
  return `<p>${html}</p>`;
}

// ---- 说明文档交互分段（支持在文档中插入变量输入控件） ----

export type DocSegment = { type: 'text'; value: string } | { type: 'var'; value: string };

/**
 * 将说明文档按 {{变量名}} 占位符拆分为文本段与变量段。
 * 部署时，文本段经 renderMarkdown 渲染，变量段渲染为真实输入控件；
 * 编辑预览时，变量段渲染为占位标签。
 * 占位符语法同命令占位符：{{VAR_NAME}}，仅识别合法标识符。
 */
export function splitDocByVariables(doc: string): DocSegment[] {
  const segments: DocSegment[] = [];
  if (!doc) return segments;
  const re = /\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(doc)) !== null) {
    if (m.index > last) segments.push({ type: 'text', value: doc.slice(last, m.index) });
    segments.push({ type: 'var', value: m[1] });
    last = m.index + m[0].length;
  }
  if (last < doc.length) segments.push({ type: 'text', value: doc.slice(last) });
  return segments;
}

/** 提取说明文档中引用的所有变量名（去重，保持顺序）。 */
export function docReferencedVariables(doc: string): string[] {
  const seen: string[] = [];
  for (const seg of splitDocByVariables(doc)) {
    if (seg.type === 'var' && !seen.includes(seg.value)) seen.push(seg.value);
  }
  return seen;
}

/**
 * 行内 Markdown 渲染：仅处理行内元素（行内代码、粗体、斜体、链接），
 * 不处理块级结构（标题、列表、代码块），也不包裹 <p>。
 * 用于交互文档中普通段落内的文本，使其与变量控件行内混合显示。
 */
export function renderMarkdownInline(text: string): string {
  return text
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

/**
 * 文档块类型：
 * - block: 块级 markdown（标题/列表/代码块/引用/分隔线），已渲染为 HTML
 * - inline: 普通段落，含变量分段，文本与变量控件行内混合
 */
export type DocBlock =
  | { kind: 'block'; html: string }
  | { kind: 'inline'; segments: DocSegment[] };

const BLOCK_MARK_RE = /^(#{1,4}\s|[\-\*]\s|\d+\.\s|>\s|```|---\s*$)/m;

/**
 * 将说明文档拆分为块级块与行内块。
 * 块级块（标题、列表、引用、代码块、分隔线）用 renderMarkdown 渲染为 HTML；
 * 行内块（普通段落）保留变量分段，供调用方行内渲染文本 + 控件。
 * 段落以双换行分隔。
 */
export function splitDocIntoBlocks(doc: string): DocBlock[] {
  if (!doc) return [];
  const blocks: DocBlock[] = [];
  const paragraphs = doc.split(/\n{2,}/);
  for (const para of paragraphs) {
    const trimmed = para.trim();
    if (!trimmed) continue;
    if (BLOCK_MARK_RE.test(trimmed)) {
      let html = renderMarkdown(para);
      // 去掉 renderMarkdown 自动包裹的外层 <p>...</p>，避免块级元素嵌套在 <p> 内
      html = html.replace(/^<p>([\s\S]*)<\/p>$/, '$1');
      blocks.push({ kind: 'block', html });
    } else {
      blocks.push({ kind: 'inline', segments: splitDocByVariables(para) });
    }
  }
  return blocks;
}

// ---- 模板变量筛选条件（与后端 templates.py 保持一致） ----

/**
 * 将通配符模式转成正则。
 * 支持：* 任意序列、? 单字符、| 多模式 OR。大小写不敏感。
 */
export function wildcardToRegex(pattern: string): RegExp {
  const alternatives = (pattern || '').split('|');
  const parts = alternatives.map((alt) => {
    let sub = '';
    for (const ch of alt) {
      if (ch === '*') sub += '.*';
      else if (ch === '?') sub += '.';
      else sub += ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }
    return sub;
  });
  return new RegExp(`^(?:${parts.join('|')})$`, 'i');
}

/** 判断 value 是否匹配通配符 pattern。空 pattern 视为匹配所有。 */
export function matchesWildcard(value: string, pattern: string): boolean {
  if (!pattern || !pattern.trim()) return true;
  return wildcardToRegex(pattern).test(value ?? '');
}

export type NumberRange = {
  min: number | null;
  max: number | null;
  minExcl: boolean;
  maxExcl: boolean;
  eq: number | null;
} | null;

/**
 * 解析数字筛选条件。
 * 支持：a-b、>=a、>a、<=b、<b、=a，逗号分隔（AND）。
 * 解析失败返回 null。
 */
export function parseNumberRange(filterStr: string): NumberRange {
  if (!filterStr || !filterStr.trim()) return null;
  const result = { min: null as number | null, max: null as number | null, minExcl: false, maxExcl: false, eq: null as number | null };
  const parts = filterStr.split(',').map((s) => s.trim()).filter(Boolean);
  for (const p of parts) {
    let m: RegExpMatchArray | null;
    if ((m = p.match(/^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$/))) {
      result.min = parseFloat(m[1]); result.max = parseFloat(m[2]); continue;
    }
    if ((m = p.match(/^>=\s*(-?\d+(?:\.\d+)?)$/))) { result.min = parseFloat(m[1]); result.minExcl = false; continue; }
    if ((m = p.match(/^>\s*(-?\d+(?:\.\d+)?)$/))) { result.min = parseFloat(m[1]); result.minExcl = true; continue; }
    if ((m = p.match(/^<=\s*(-?\d+(?:\.\d+)?)$/))) { result.max = parseFloat(m[1]); result.maxExcl = false; continue; }
    if ((m = p.match(/^<\s*(-?\d+(?:\.\d+)?)$/))) { result.max = parseFloat(m[1]); result.maxExcl = true; continue; }
    if ((m = p.match(/^=\s*(-?\d+(?:\.\d+)?)$/))) { result.eq = parseFloat(m[1]); continue; }
    return null; // 含无法识别的片段
  }
  return result;
}

/** 校验数字/端口值，返回错误信息（合法则返回空串）。空值视为合法。 */
export function validateNumberValue(value: string, filterStr: string, isPort: boolean): string {
  if (value === null || value === undefined || value === '') return '';
  const num = Number(value);
  if (!Number.isFinite(num)) return `不是合法的数字：${value}`;
  if (isPort) {
    if (!Number.isInteger(num) || num < 1 || num > 65535) return '端口号必须为 1-65535 的整数';
  }
  const rng = parseNumberRange(filterStr);
  if (rng) {
    if (rng.eq !== null && num !== rng.eq) return `数值必须等于 ${rng.eq}`;
    if (rng.min !== null) {
      if (rng.minExcl && !(num > rng.min)) return `数值必须大于 ${rng.min}`;
      if (!rng.minExcl && !(num >= rng.min)) return `数值必须大于等于 ${rng.min}`;
    }
    if (rng.max !== null) {
      if (rng.maxExcl && !(num < rng.max)) return `数值必须小于 ${rng.max}`;
      if (!rng.maxExcl && !(num <= rng.max)) return `数值必须小于等于 ${rng.max}`;
    }
  }
  return '';
}

/**
 * 根据变量类型与筛选条件校验用户输入值，返回错误信息（合法则返回空串）。
 * 空值统一视为合法（允许变量留空）。
 */
export function validateVariableValue(variable: { type: string; filter: string }, value: string): string {
  if (value === null || value === undefined || value === '') return '';
  const { type, filter } = variable;
  const filterStr = (filter || '').trim();
  const structured = isStructuredFilter(filterStr) ? parseFilter(filterStr, type) : null;

  if (type === 'string' || type === 'text' || type === 'image' || type === 'volume' || type === 'docker_path') {
    if (structured) {
      if (!evaluateStructuredFilter(structured, value, type)) {
        return `值「${value}」不满足筛选条件（${filterSummary(filterStr, type)}）`;
      }
    } else if (filterStr && !matchesWildcard(value, filterStr)) {
      return `值「${value}」不符合筛选条件「${filterStr}」`;
    }
  } else if (type === 'host_path') {
    // 宿主路径：必须为绝对路径
    if (!value.startsWith('/')) {
      return `宿主路径必须为绝对路径（以 / 开头），得到「${value}」`;
    }
    if (structured) {
      if (!evaluateStructuredFilter(structured, value, type)) {
        return `路径「${value}」不满足筛选条件（${filterSummary(filterStr, type)}）`;
      }
    } else if (filterStr && !matchesWildcard(value, filterStr)) {
      return `路径「${value}」不在允许范围「${filterStr}」内`;
    }
  } else if (type === 'gpu') {
    // GPU 选择：值应为 all 或逗号分隔的非负整数索引
    if (value.trim().toLowerCase() === 'all') return '';
    const indices = value.split(',').map((s) => s.trim()).filter(Boolean);
    for (const idx of indices) {
      if (!/^\d+$/.test(idx)) {
        return `GPU 索引必须是非负整数或 all，得到「${idx}」`;
      }
    }
  } else if (type === 'number' || type === 'port') {
    // 数值类：先校验基础合法性
    const num = Number(value);
    if (!Number.isFinite(num)) return `不是合法的数字：${value}`;
    if (type === 'port' && (!Number.isInteger(num) || num < 1 || num > 65535)) {
      return '端口号必须为 1-65535 的整数';
    }
    // 再校验筛选条件
    if (structured) {
      if (!evaluateStructuredFilter(structured, value, type)) {
        return `值「${value}」不满足筛选条件（${filterSummary(filterStr, type)}）`;
      }
    } else {
      return validateNumberValue(value, filterStr, type === 'port');
    }
  } else if (type === 'select') {
    const options = filterStr.split(',').map((s) => s.trim()).filter(Boolean);
    if (options.length > 0 && !options.includes(value)) {
      return `值「${value}」不在允许的选项中`;
    }
  }
  return '';
}

/** 返回某类型筛选条件输入框的 placeholder 提示。 */
export function filterPlaceholderForType(type: string): string {
  switch (type) {
    case 'select': return 'a,b,c';
    case 'image': return 'pytorch/*';
    case 'volume': return 'data_*';
    case 'gpu': return 'RTX*（按 GPU 名称筛选，可留空）';
    case 'host_path': return '/data/*（限制可点选路径前缀，可留空）';
    case 'docker_path': return '/opt/*（通配符，可留空）';
    case 'string':
    case 'text': return 'abc*（通配符，可留空）';
    case 'number': return '1-100 或 >=0,<=100';
    case 'port': return '1-100（可选，默认 1-65535）';
    default: return '（可选）';
  }
}

/** 返回某类型筛选条件的简短说明（用于编辑器提示）。 */
export function filterHintForType(type: string): string {
  switch (type) {
    case 'image': return '通配符筛选服务器镜像下拉选项（* 任意、? 单字符、| 或）';
    case 'volume': return '通配符筛选服务器卷下拉选项（* 任意、? 单字符、| 或）';
    case 'gpu': return '通配符按 GPU 名称筛选可选 GPU（如 RTX*，留空表示不限制）';
    case 'host_path': return '通配符限制可点选的宿主路径前缀（如 /data/*，留空则受用户挂载白名单限制）';
    case 'docker_path': return '通配符匹配容器内路径，用户输入必须符合（如 /opt/*）';
    case 'select': return '逗号分隔的允许选项，用户从下拉中选择';
    case 'string':
    case 'text': return '通配符匹配，用户输入必须符合（* 任意、? 单字符、| 或）';
    case 'number': return '数字范围约束，如 1-100 或 >=0,<=100';
    case 'port': return '端口范围约束（基础 1-65535），如 8080-8090';
    default: return '';
  }
}

// ============================================================
// 结构化筛选条件（DNF：组间 OR、组内 AND）
// ============================================================

/**
 * 结构化筛选条件数据模型。
 *
 * 存储格式：JSON 字符串，以 {"groups": 开头，与旧文本格式向后兼容。
 *
 * - 组(groups)之间为 OR：满足任一组即通过
 * - 组内条件(conditions)之间为 AND：组内所有条件均须满足
 *
 * 条件类型：
 * - 模式类（string/text/image/volume/gpu/host_path/docker_path）：
 *   { op: 'match'|'notMatch', pattern: '通配符' }
 * - 数值类（number/port）：
 *   { op: '>='|'>'|'<='|'<'|'=='|'between', value?, min?, max? }
 * - select 类型不使用结构化筛选，仍用逗号分隔的选项列表
 */

export type FilterOp = 'match' | 'notMatch' | '>=' | '>' | '<=' | '<' | '==' | 'between';

export type FilterCondition = {
  op: FilterOp;
  pattern?: string;   // match / notMatch
  value?: number;     // >=, >, <=, <, ==
  min?: number;       // between
  max?: number;       // between
};

export type FilterGroup = {
  conditions: FilterCondition[];
};

export type StructuredFilter = {
  groups: FilterGroup[];
};

/** 判断 filter 字符串是否为结构化筛选条件（JSON 格式）。 */
export function isStructuredFilter(filter: string): boolean {
  const s = (filter || '').trim();
  return s.startsWith('{"groups":') || s.startsWith('{ "groups":');
}

/** 创建空的结构化筛选条件（无任何条件 = 匹配所有）。 */
export function emptyStructuredFilter(): StructuredFilter {
  return { groups: [] };
}

/** 将结构化筛选条件序列化为 JSON 字符串。 */
export function serializeFilter(f: StructuredFilter): string {
  return JSON.stringify(f);
}

/**
 * 将旧文本格式筛选条件迁移为结构化格式。
 * - 模式类：按 | 拆分为多个 OR 组，每组一个 match 条件
 * - 数值类：按 , 拆分为多个 AND 条件（同一组内）
 * - select：不迁移（保持逗号分隔文本）
 * - 空：返回空结构化筛选
 */
export function legacyToStructured(filter: string, type: string): StructuredFilter {
  const s = (filter || '').trim();
  if (!s) return emptyStructuredFilter();

  // select 类型不使用结构化筛选
  if (type === 'select') return emptyStructuredFilter();

  const isNumeric = type === 'number' || type === 'port';
  if (isNumeric) {
    // 数值类：逗号分隔的条件全部在同一组（AND）
    const parts = s.split(',').map((p) => p.trim()).filter(Boolean);
    const conditions: FilterCondition[] = [];
    for (const p of parts) {
      let m: RegExpMatchArray | null;
      if ((m = p.match(/^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$/))) {
        conditions.push({ op: 'between', min: parseFloat(m[1]), max: parseFloat(m[2]) });
      } else if ((m = p.match(/^>=\s*(-?\d+(?:\.\d+)?)$/))) {
        conditions.push({ op: '>=', value: parseFloat(m[1]) });
      } else if ((m = p.match(/^>\s*(-?\d+(?:\.\d+)?)$/))) {
        conditions.push({ op: '>', value: parseFloat(m[1]) });
      } else if ((m = p.match(/^<=\s*(-?\d+(?:\.\d+)?)$/))) {
        conditions.push({ op: '<=', value: parseFloat(m[1]) });
      } else if ((m = p.match(/^<\s*(-?\d+(?:\.\d+)?)$/))) {
        conditions.push({ op: '<', value: parseFloat(m[1]) });
      } else if ((m = p.match(/^=\s*(-?\d+(?:\.\d+)?)$/))) {
        conditions.push({ op: '==', value: parseFloat(m[1]) });
      }
    }
    return conditions.length > 0 ? { groups: [{ conditions }] } : emptyStructuredFilter();
  }

  // 模式类：按 | 拆分为多个 OR 组
  const alternatives = s.split('|').map((a) => a.trim()).filter(Boolean);
  if (alternatives.length === 0) return emptyStructuredFilter();
  return {
    groups: alternatives.map((a) => ({ conditions: [{ op: 'match' as FilterOp, pattern: a }] })),
  };
}

/**
 * 解析筛选条件字符串为结构化格式。
 * 若已是结构化格式则直接解析；否则按旧格式迁移。
 * 解析失败返回空结构化筛选。
 */
export function parseFilter(filter: string, type: string): StructuredFilter {
  const s = (filter || '').trim();
  if (!s) return emptyStructuredFilter();
  if (isStructuredFilter(s)) {
    try {
      const obj = JSON.parse(s) as StructuredFilter;
      if (obj && Array.isArray(obj.groups)) return obj;
    } catch { /* fall through */ }
  }
  return legacyToStructured(s, type);
}

/** 判断变量类型是否为数值类。 */
function isNumericType(type: string): boolean {
  return type === 'number' || type === 'port';
}

/** 判断变量类型是否为模式类（使用通配符匹配）。 */
function isPatternType(type: string): boolean {
  return type === 'string' || type === 'text' || type === 'image' || type === 'volume'
    || type === 'gpu' || type === 'host_path' || type === 'docker_path';
}

/**
 * 对单个条件求值。
 * @returns true 表示该条件满足
 */
function evalCondition(cond: FilterCondition, value: string, type: string): boolean {
  if (cond.op === 'match') {
    return matchesWildcard(value, cond.pattern || '');
  }
  if (cond.op === 'notMatch') {
    return !matchesWildcard(value, cond.pattern || '');
  }
  // 数值类条件
  if (isNumericType(type)) {
    const num = parseFloat(value);
    if (!Number.isFinite(num)) return false;
    switch (cond.op) {
      case '>=': return num >= (cond.value ?? 0);
      case '>': return num > (cond.value ?? 0);
      case '<=': return num <= (cond.value ?? 0);
      case '<': return num < (cond.value ?? 0);
      case '==': return num === (cond.value ?? 0);
      case 'between': return num >= (cond.min ?? -Infinity) && num <= (cond.max ?? Infinity);
    }
  }
  return true;
}

/**
 * 对结构化筛选条件求值。
 * 组间 OR、组内 AND。空筛选条件视为匹配所有。
 */
export function evaluateStructuredFilter(f: StructuredFilter, value: string, type: string): boolean {
  if (!f.groups || f.groups.length === 0) return true;
  return f.groups.some((group) => {
    if (!group.conditions || group.conditions.length === 0) return true;
    return group.conditions.every((cond) => evalCondition(cond, value, type));
  });
}

/** 数值条件操作符的中文标签。 */
export function numericOpLabel(op: FilterOp): string {
  switch (op) {
    case '>=': return '≥';
    case '>': return '>';
    case '<=': return '≤';
    case '<': return '<';
    case '==': return '=';
    case 'between': return '介于';
    default: return op;
  }
}

/** 模式条件操作符的中文标签。 */
export function patternOpLabel(op: FilterOp): string {
  switch (op) {
    case 'match': return '匹配';
    case 'notMatch': return '不匹配';
    default: return op;
  }
}

/** 将单个条件格式化为人类可读的描述。 */
function conditionSummary(cond: FilterCondition, type: string): string {
  if (isNumericType(type)) {
    if (cond.op === 'between') {
      return `介于 ${cond.min ?? '?'}~${cond.max ?? '?'}`;
    }
    return `${numericOpLabel(cond.op)} ${cond.value ?? '?'}`;
  }
  return `${patternOpLabel(cond.op)} ${cond.pattern || '（空）'}`;
}

/** 将组内条件格式化为人类可读的描述（AND 连接）。 */
function groupSummary(group: FilterGroup, type: string): string {
  if (!group.conditions || group.conditions.length === 0) return '（无条件）';
  return group.conditions.map((c) => conditionSummary(c, type)).join(' 且 ');
}

/**
 * 将筛选条件格式化为人类可读的摘要字符串。
 * 用于表格中显示和按钮标签。
 */
export function filterSummary(filter: string, type: string): string {
  const s = (filter || '').trim();
  if (!s) return '';

  // select 类型：逗号分隔的选项
  if (type === 'select') {
    const opts = s.split(',').map((o) => o.trim()).filter(Boolean);
    return opts.length > 0 ? `选项: ${opts.join(', ')}` : '';
  }

  // 结构化或旧格式 → 统一解析为结构化
  const f = parseFilter(s, type);
  if (f.groups.length === 0) return '';

  return f.groups.map((g) => groupSummary(g, type)).join(' 或 ');
}

/**
 * 统一的筛选条件匹配检查（同时支持结构化 JSON 和旧文本格式）。
 * 用于前端实时筛选下拉选项、校验输入值等场景。
 * 空筛选条件视为匹配所有。
 */
export function filterMatchesValue(filter: string, value: string, type: string): boolean {
  const s = (filter || '').trim();
  if (!s) return true;
  if (isStructuredFilter(s)) {
    const f = parseFilter(s, type);
    return evaluateStructuredFilter(f, value, type);
  }
  // 旧文本格式
  if (type === 'number' || type === 'port') {
    return validateNumberValue(value, s, type === 'port') === '';
  }
  // select 类型：逗号分隔的选项列表
  if (type === 'select') {
    const options = s.split(',').map((o) => o.trim()).filter(Boolean);
    return options.length === 0 || options.includes(value);
  }
  // 模式类：通配符匹配
  return matchesWildcard(value, s);
}

/**
 * 从筛选条件中提取路径前缀（用于 host_path 类型的初始浏览路径）。
 * 结构化格式：取第一个 match 条件的 pattern，去掉尾部通配符。
 * 旧文本格式：直接去掉尾部通配符。
 */
export function extractPathPrefix(filter: string): string {
  const s = (filter || '').trim();
  if (!s) return '/';
  if (isStructuredFilter(s)) {
    const f = parseFilter(s, 'host_path');
    for (const group of f.groups) {
      for (const cond of group.conditions) {
        if (cond.op === 'match' && cond.pattern) {
          const p = cond.pattern.replace(/\/\*$/, '').replace(/\*$/, '').replace(/\/$/, '');
          if (p.startsWith('/')) return p;
        }
      }
    }
    return '/';
  }
  return s.replace(/\/\*$/, '').replace(/\*$/, '') || '/';
}
