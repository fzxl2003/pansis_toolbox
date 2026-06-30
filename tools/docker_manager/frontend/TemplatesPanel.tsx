// ============================================================
// Templates Panel (User View) — Docker Manager
// 含：TemplateEditorModal（共享三步走向导）、TemplatesPanel、MyResourcesPanel
// ============================================================

import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Box,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Code,
  Database,
  Eye,
  FileText,
  Image,
  Layers,
  Lock,
  Pencil,
  Plus,
  RefreshCw,
  Shield,
  Sparkles,
  Trash2,
  Users,
  Wand2,
} from 'lucide-react';
import { apiDelete, apiGet, apiPost, apiPut } from '../../../frontend/src/api/client';
import type { AuthUser } from '../../../frontend/src/api/auth';
import { Alert, Field, Modal, SkeletonRows, Spin, TruncText } from './components';
import { API, filterHintForType, filterSummary, parseFilter, serializeFilter, emptyStructuredFilter, renderMarkdownInline, renderMarkdown, splitDocByVariables, splitDocIntoBlocks, useErrorMsg } from './utils';
import type { FilterCondition, FilterGroup, FilterOp, StructuredFilter } from './utils';
import type {
  BasicUser,
  MyOwnedResource,
  Template,
  TemplateDetail,
  TemplateRoleDetail,
  TemplateVariable,
  TemplateVariableType,
} from './types';

// ============================================================
// 共享：模板编辑器（三步走向导）
// ============================================================

const VARIABLE_TYPE_OPTIONS: { value: TemplateVariableType; label: string; desc: string }[] = [
  { value: 'string', label: '单行文本', desc: '普通字符串输入框；筛选条件为通配符，输入须匹配' },
  { value: 'text', label: '多行文本', desc: '多行文本域；筛选条件为通配符，输入须匹配' },
  { value: 'number', label: '数字', desc: '数字输入框；筛选条件为范围约束（如 1-100）' },
  { value: 'port', label: '端口号', desc: '1-65535 端口号；筛选条件可进一步限定范围' },
  { value: 'image', label: '镜像选择', desc: '从服务器镜像列表中选择；筛选条件为通配符，过滤下拉选项' },
  { value: 'volume', label: '卷选择', desc: '从服务器卷列表中选择；筛选条件为通配符，过滤下拉选项' },
  { value: 'gpu', label: 'GPU 选择', desc: '从服务器可用 GPU 中多选；筛选条件为通配符，按 GPU 名称过滤' },
  { value: 'host_path', label: '宿主路径', desc: '宿主机目录路径；部署时可点选浏览服务器目录，筛选条件为通配符前缀（如 /data/*）' },
  { value: 'docker_path', label: '容器路径', desc: '容器内路径；纯文本输入，筛选条件为通配符' },
  { value: 'select', label: '下拉选择', desc: '筛选条件填逗号分隔的允许选项' },
];

const PLACEHOLDER_RE = /\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}/g;

function detectPlaceholders(rawContent: string): string[] {
  const seen: string[] = [];
  for (const m of rawContent.matchAll(PLACEHOLDER_RE)) {
    if (!seen.includes(m[1])) seen.push(m[1]);
  }
  return seen;
}

function mkVariable(name = ''): TemplateVariable {
  return { name, type: 'string', filter: '', description: '', defaultValue: '' };
}

// ============================================================
// 结构化筛选条件编辑器弹窗（DNF：组间 OR、组内 AND）
// ============================================================

/** 模式类条件操作符选项 */
const PATTERN_OPS: { value: FilterOp; label: string }[] = [
  { value: 'match', label: '匹配' },
  { value: 'notMatch', label: '不匹配' },
];

/** 数值类条件操作符选项 */
const NUMERIC_OPS: { value: FilterOp; label: string }[] = [
  { value: '>=', label: '≥ 大于等于' },
  { value: '>', label: '> 大于' },
  { value: '<=', label: '≤ 小于等于' },
  { value: '<', label: '< 小于' },
  { value: '==', label: '= 等于' },
  { value: 'between', label: '介于（闭区间）' },
];

/** 判断变量类型是否为数值类 */
function isNumericFilterType(type: string): boolean {
  return type === 'number' || type === 'port';
}

/** 创建默认条件（根据类型） */
function makeDefaultCondition(type: string): FilterCondition {
  if (isNumericFilterType(type)) {
    return { op: '>=', value: 0 };
  }
  return { op: 'match', pattern: '' };
}

/** 单个条件编辑行 */
function ConditionRow({
  cond,
  type,
  onChange,
  onRemove,
}: {
  cond: FilterCondition;
  type: string;
  onChange: (c: FilterCondition) => void;
  onRemove: () => void;
}) {
  const numeric = isNumericFilterType(type);
  const ops = numeric ? NUMERIC_OPS : PATTERN_OPS;

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
      <select
        value={cond.op}
        onChange={(e) => {
          const newOp = e.target.value as FilterOp;
          if (numeric) {
            if (newOp === 'between') {
              onChange({ op: newOp, min: cond.value ?? 0, max: (cond.value ?? 0) + 100 });
            } else {
              onChange({ op: newOp, value: cond.value ?? 0 });
            }
          } else {
            onChange({ op: newOp, pattern: cond.pattern || '' });
          }
        }}
        style={{ width: 'auto', minWidth: 90 }}
      >
        {ops.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>

      {numeric ? (
        cond.op === 'between' ? (
          <>
            <input
              className="mono"
              type="number"
              value={cond.min ?? ''}
              onChange={(e) => onChange({ ...cond, min: parseFloat(e.target.value) || 0 })}
              placeholder="最小值"
              style={{ width: 80 }}
            />
            <span style={{ color: '#94a3b8', fontSize: 12 }}>~</span>
            <input
              className="mono"
              type="number"
              value={cond.max ?? ''}
              onChange={(e) => onChange({ ...cond, max: parseFloat(e.target.value) || 0 })}
              placeholder="最大值"
              style={{ width: 80 }}
            />
          </>
        ) : (
          <input
            className="mono"
            type="number"
            value={cond.value ?? ''}
            onChange={(e) => onChange({ ...cond, value: parseFloat(e.target.value) || 0 })}
            placeholder="数值"
            style={{ width: 100 }}
          />
        )
      ) : (
        <input
          className="mono"
          value={cond.pattern || ''}
          onChange={(e) => onChange({ ...cond, pattern: e.target.value })}
          placeholder={type === 'host_path' ? '/data/*' : type === 'image' ? 'pytorch/*' : 'abc*'}
          style={{ flex: 1, minWidth: 120 }}
        />
      )}

      <button
        className="dm-btn-icon danger"
        title="删除条件"
        onClick={onRemove}
      >
        <Trash2 size={11} />
      </button>
    </div>
  );
}

/** 单个条件组编辑器（组内 AND） */
function FilterGroupEditor({
  group,
  index,
  type,
  onChange,
  onRemove,
  canRemove,
}: {
  group: FilterGroup;
  index: number;
  type: string;
  onChange: (g: FilterGroup) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  const updateCond = (ci: number, c: FilterCondition) => {
    const next = [...group.conditions];
    next[ci] = c;
    onChange({ conditions: next });
  };
  const removeCond = (ci: number) => {
    onChange({ conditions: group.conditions.filter((_, i) => i !== ci) });
  };
  const addCond = () => {
    onChange({ conditions: [...group.conditions, makeDefaultCondition(type)] });
  };

  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, padding: 10, background: '#fff' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: '#526071' }}>
          条件组 {index + 1}
          <span style={{ fontWeight: 400, color: '#94a3b8', marginLeft: 6 }}>（组内全部满足 = AND）</span>
        </span>
        {canRemove && (
          <button className="dm-btn-icon danger" title="删除条件组" onClick={onRemove}>
            <Trash2 size={11} />
          </button>
        )}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {group.conditions.map((c, ci) => (
          <ConditionRow
            key={ci}
            cond={c}
            type={type}
            onChange={(nc) => updateCond(ci, nc)}
            onRemove={() => removeCond(ci)}
          />
        ))}
        {group.conditions.length === 0 && (
          <span style={{ fontSize: 12, color: '#94a3b8' }}>（空条件组 = 匹配所有）</span>
        )}
        <button className="btn" style={{ alignSelf: 'flex-start', fontSize: 12, padding: '3px 10px' }} onClick={addCond}>
          <Plus size={11} /> 添加条件
        </button>
      </div>
    </div>
  );
}

/** select 类型的选项列表编辑器 */
function SelectOptionsEditor({
  filter,
  onSave,
  onClose,
}: {
  filter: string;
  onSave: (f: string) => void;
  onClose: () => void;
}) {
  const initial = (filter || '').split(',').map((s) => s.trim()).filter(Boolean);
  const [options, setOptions] = useState<string[]>(initial);
  const [input, setInput] = useState('');

  const addOption = () => {
    const v = input.trim();
    if (v && !options.includes(v)) {
      setOptions([...options, v]);
      setInput('');
    }
  };

  return (
    <Modal title="下拉选项编辑器" onClose={onClose} width={480}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: 12, color: '#526071', lineHeight: 1.6 }}>
          配置下拉选择的可选值。用户部署时只能从这些选项中选择一个。留空（无选项）表示不限制。
        </div>

        <div style={{ display: 'flex', gap: 6 }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addOption(); } }}
            placeholder="输入选项值后回车"
            style={{ flex: 1 }}
          />
          <button className="btn" onClick={addOption} disabled={!input.trim()}>
            <Plus size={13} /> 添加
          </button>
        </div>

        <div style={{ border: '1px solid #e2e8f0', borderRadius: 6, minHeight: 80, maxHeight: 240, overflowY: 'auto', background: '#fff' }}>
          {options.length === 0 ? (
            <div style={{ padding: 20, textAlign: 'center', color: '#94a3b8', fontSize: 12 }}>
              暂无选项（不限制用户输入）
            </div>
          ) : (
            options.map((opt, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderBottom: '1px solid #f1f5f9' }}>
                <span className="mono" style={{ flex: 1, fontSize: 13 }}>{opt}</span>
                <button
                  className="dm-btn-icon danger"
                  title="删除选项"
                  onClick={() => setOptions(options.filter((_, j) => j !== i))}
                >
                  <Trash2 size={11} />
                </button>
              </div>
            ))
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={() => { onSave(options.join(',')); onClose(); }}>
            <CheckCircle size={14} /> 确定
          </button>
        </div>
      </div>
    </Modal>
  );
}

/** 结构化筛选条件编辑器弹窗 */
function FilterEditorModal({
  variableType,
  filter,
  onSave,
  onClose,
}: {
  variableType: string;
  filter: string;
  onSave: (f: string) => void;
  onClose: () => void;
}) {
  // select 类型使用独立的选项编辑器
  if (variableType === 'select') {
    return <SelectOptionsEditor filter={filter} onSave={onSave} onClose={onClose} />;
  }

  const numeric = isNumericFilterType(variableType);
  const [sf, setSf] = useState<StructuredFilter>(() => parseFilter(filter, variableType));

  const addGroup = () => {
    setSf({ groups: [...sf.groups, { conditions: [makeDefaultCondition(variableType)] }] });
  };
  const updateGroup = (gi: number, g: FilterGroup) => {
    const next = [...sf.groups];
    next[gi] = g;
    setSf({ groups: next });
  };
  const removeGroup = (gi: number) => {
    setSf({ groups: sf.groups.filter((_, i) => i !== gi) });
  };
  const clearAll = () => setSf(emptyStructuredFilter());

  const typeLabel = VARIABLE_TYPE_OPTIONS.find((o) => o.value === variableType)?.label || variableType;

  return (
    <Modal title="筛选条件编辑器" onClose={onClose} width={580}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* 类型与逻辑说明 */}
        <div style={{ fontSize: 12, color: '#526071', lineHeight: 1.6, background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 6, padding: '8px 12px' }}>
          <strong>变量类型：</strong>{typeLabel}
          <br />
          <strong>逻辑结构：</strong>满足以下任一<span style={{ color: '#2563eb', fontWeight: 600 }}>条件组</span>（组间 <strong>OR</strong>），
          组内所有条件须全部满足（<strong>AND</strong>）。留空（无条件组）表示<strong>不限制</strong>。
          {numeric && <span> 端口类型自动限制 1-65535。</span>}
        </div>

        {/* 条件组列表 */}
        {sf.groups.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 24, color: '#94a3b8', fontSize: 13, border: '1px dashed #e2e8f0', borderRadius: 6 }}>
            当前无筛选条件（匹配所有输入）
            <br />
            <button className="btn btn-primary" style={{ marginTop: 10 }} onClick={addGroup}>
              <Plus size={13} /> 添加第一个条件组
            </button>
          </div>
        ) : (
          <>
            {sf.groups.map((g, gi) => (
              <div key={gi}>
                <FilterGroupEditor
                  group={g}
                  index={gi}
                  type={variableType}
                  onChange={(ng) => updateGroup(gi, ng)}
                  onRemove={() => removeGroup(gi)}
                  canRemove={sf.groups.length > 1}
                />
                {gi < sf.groups.length - 1 && (
                  <div style={{ textAlign: 'center', padding: '4px 0', fontSize: 12, fontWeight: 600, color: '#2563eb' }}>
                    ── 或 (OR) ──
                  </div>
                )}
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <button className="btn" onClick={addGroup}>
                <Plus size={13} /> 添加条件组
              </button>
              <button className="btn" onClick={clearAll}>
                <Trash2 size={13} /> 清空全部
              </button>
            </div>
          </>
        )}

        {/* 预览摘要 */}
        {sf.groups.length > 0 && (
          <div style={{ fontSize: 11, color: '#64748b', background: '#f1f5f9', borderRadius: 4, padding: '6px 10px', fontFamily: 'monospace' }}>
            {filterSummary(serializeFilter(sf), variableType)}
          </div>
        )}

        {/* 操作按钮 */}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose}>取消</button>
          <button
            className="btn btn-primary"
            onClick={() => { onSave(serializeFilter(sf)); onClose(); }}
          >
            <CheckCircle size={14} /> 确定
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ============================================================
// 说明文档交互式编辑器：支持 Markdown + 变量输入控件插入
// ============================================================

function InteractiveDocEditor({
  value,
  onChange,
  variables,
}: {
  value: string;
  onChange: (v: string) => void;
  variables: TemplateVariable[];
}) {
  const [ta, setTa] = useState<HTMLTextAreaElement | null>(null);
  const blocks = splitDocIntoBlocks(value);
  const varMap = new Map(variables.map((v) => [v.name, v]));
  const referencedUnknown = splitDocByVariables(value)
    .filter((s) => s.type === 'var' && !varMap.has(s.value))
    .map((s) => (s.type === 'var' ? s.value : ''));

  /** 在文本域当前光标位置插入变量占位符 */
  function insertVariable(name: string) {
    if (!ta) { onChange(value + `{{${name}}}`); return; }
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    const insert = `{{${name}}}`;
    const next = value.slice(0, start) + insert + value.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      const pos = start + insert.length;
      ta.focus();
      ta.setSelectionRange(pos, pos);
    });
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="dm-step-header">
        <span className="dm-step-num">2</span>
        <div>
          <div className="dm-step-title">说明文档（Markdown + 交互变量）</div>
          <div className="dm-step-desc">
            为模板编写使用说明。支持基础 Markdown 语法，并可在文档中插入 <code>{'{{变量名}}'}</code> —— 部署时该处将直接显示对应的输入控件
          </div>
        </div>
      </div>

      {variables.length > 0 && (
        <div className="dm-perm-section">
          <div className="dm-perm-section-title"><Wand2 size={13} /> 插入变量到文档</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
            {variables.map((v) => (
              <button
                key={v.name}
                className="btn"
                style={{ fontSize: 12, padding: '3px 10px' }}
                title={v.description || v.type}
                onClick={() => insertVariable(v.name)}
                disabled={!v.name.trim()}
              >
                <code style={{ background: 'transparent', padding: 0 }}>{`{{${v.name || '未命名'}}}`}</code>
                <span style={{ color: '#94a3b8', marginLeft: 4, fontSize: 11 }}>{v.type}</span>
              </button>
            ))}
          </div>
          <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 6 }}>
            点击按钮在光标处插入变量占位符；也可直接在文本中输入 <code>{'{{变量名}}'}</code>。未在文档中插入的变量仍会在部署页底部「参数配置」区显示。
          </div>
        </div>
      )}

      <Field label="Markdown 文档" full>
        <textarea
          ref={setTa}
          className="mono"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={'## 使用说明\n\n本模板用于部署 Jupyter Notebook。\n\n### 端口配置\n\n请输入宿主机映射端口：{{PORT}}\n\n### 镜像选择\n\n选择要使用的镜像：{{IMAGE}}\n\n> 未插入变量的内容将作为普通说明文字展示。'}
          style={{ minHeight: 200 }}
        />
      </Field>

      {referencedUnknown.length > 0 && (
        <Alert type="error">
          文档中引用了未定义的变量：<code>{referencedUnknown.map((n) => `{{${n}}}`).join('、')}</code>。请在步骤 1 的变量表中声明，或修正文档中的变量名。
        </Alert>
      )}

      {value && (
        <div className="dm-perm-section">
          <div className="dm-perm-section-title"><FileText size={13} /> 预览（部署时效果）</div>
          <div className="dm-md-preview">
            {blocks.map((blk, bi) => {
              if (blk.kind === 'block') {
                return <div key={bi} dangerouslySetInnerHTML={{ __html: blk.html }} />;
              }
              // 行内段落：文本与变量占位行内混合
              return (
                <p key={bi} style={{ margin: '0 0 8px 0', lineHeight: 2 }}>
                  {blk.segments.map((seg, si) => {
                    if (seg.type === 'text') {
                      return <span key={si} dangerouslySetInnerHTML={{ __html: renderMarkdownInline(seg.value) }} />;
                    }
                    const v = varMap.get(seg.value);
                    if (!v) {
                      return (
                        <span key={si} style={{ display: 'inline-block', background: '#fef2f2', color: '#dc2626', border: '1px dashed #fca5a5', borderRadius: 4, padding: '1px 8px', margin: '0 2px', fontSize: 12, verticalAlign: 'middle' }}>
                          {`{{${seg.value}}}`} ⚠ 未定义
                        </span>
                      );
                    }
                    return (
                      <span key={si} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: '#eff6ff', color: '#1d4ed8', border: '1px solid #bfdbfe', borderRadius: 4, padding: '2px 10px', margin: '0 2px', fontSize: 12, verticalAlign: 'middle' }} title={`${v.type}${v.filter ? ` · ${v.filter}` : ''}`}>
                        <Code size={11} />
                        <code style={{ background: 'transparent', padding: 0 }}>{`{{${seg.value}}}`}</code>
                        <span style={{ color: '#64748b', fontSize: 10 }}>→ {v.type} 输入</span>
                      </span>
                    );
                  })}
                </p>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function TemplateEditorModal({
  editing,
  onClose,
  onSaved,
}: {
  editing: TemplateDetail | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [step, setStep] = useState<1 | 2>(1);
  const [form, setForm] = useState({
    name: '', description: '', category: 'general',
    deployType: 'run' as 'run' | 'compose',
    isPublic: true,
    rawContent: '',
    variables: [] as TemplateVariable[],
    docContent: '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [filterEditIdx, setFilterEditIdx] = useState<number | null>(null);
  useEffect(() => {
    if (editing) {
      setForm({
        name: editing.name,
        description: editing.description,
        category: editing.category,
        deployType: editing.deployType,
        isPublic: editing.isPublic,
        rawContent: editing.rawContent,
        variables: editing.variables?.length ? editing.variables.map((v) => ({ ...v })) : [],
        docContent: editing.docContent,
      });
    } else {
      setForm({
        name: '', description: '', category: 'general',
        deployType: 'run', isPublic: true,
        rawContent: '', variables: [], docContent: '',
      });
    }
    setStep(1);
    clearError();
  }, [editing, clearError]);

  function autoDetectVariables() {
    const detected = detectPlaceholders(form.rawContent);
    const existing = new Map(form.variables.map((v) => [v.name, v]));
    const merged: TemplateVariable[] = [];
    for (const name of detected) {
      if (existing.has(name)) {
        merged.push(existing.get(name)!);
      } else {
        merged.push(mkVariable(name));
      }
    }
    setForm((p) => ({ ...p, variables: merged }));
  }

  function updateVariable(idx: number, patch: Partial<TemplateVariable>) {
    setForm((p) => ({
      ...p,
      variables: p.variables.map((v, i) => (i === idx ? { ...v, ...patch } : v)),
    }));
  }

  function removeVariable(idx: number) {
    setForm((p) => ({ ...p, variables: p.variables.filter((_, i) => i !== idx) }));
  }

  function addVariable() {
    setForm((p) => ({ ...p, variables: [...p.variables, mkVariable()] }));
  }

  async function doSave() {
    setSaving(true);
    clearError();
    try {
      const payload = {
        name: form.name,
        description: form.description,
        category: form.category,
        deployType: form.deployType,
        isPublic: form.isPublic,
        rawContent: form.rawContent,
        variables: form.variables,
        docContent: form.docContent,
      };
      if (editing) {
        await apiPut(`${API}/templates/${editing.id}`, payload);
      } else {
        await apiPost(`${API}/templates`, payload);
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  const f = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm((p) => ({ ...p, [k]: e.target.type === 'checkbox' ? (e.target as HTMLInputElement).checked : e.target.value }));

  const step1Valid = form.name.trim().length > 0 && form.rawContent.trim().length > 0;

  return (
    <Modal
      title={editing ? `编辑模板 — ${editing.name}` : '创建模板'}
      onClose={onClose}
      wide
      foot={
        <>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 4 }}>
            <button
              className="btn"
              style={{ fontSize: 12 }}
              disabled={step === 1}
              onClick={() => setStep((s) => (s > 1 ? (s - 1) as 1 | 2 : s))}
            >
              <ChevronLeft size={14} /> 上一步
            </button>
            <span className="dm-step-indicator">
              <span className={step >= 1 ? 'active' : ''}>1</span>
              <span className="dm-step-line" />
              <span className={step >= 2 ? 'active' : ''}>2</span>
            </span>
          </div>
          <button className="btn" onClick={onClose}>取消</button>
          {step < 2 ? (
            <button
              className="btn btn-primary"
              disabled={step === 1 && !step1Valid}
              onClick={() => setStep((s) => (s < 2 ? (s + 1) as 1 | 2 : s))}
            >
              下一步 <ChevronRight size={14} />
            </button>
          ) : (
            <button className="btn btn-primary" onClick={doSave} disabled={saving || !step1Valid}>
              {saving ? <Spin /> : <CheckCircle size={14} />} {editing ? '保存更改' : '创建'}
            </button>
          )}
        </>
      }
    >
      {error && <Alert type="error">{error}</Alert>}

      {/* ── 步骤 1：基本信息 + 命令/YAML 内容 + 变量配置 ── */}
      {step === 1 && (
        <div style={{ display: 'grid', gap: 16 }}>
          <div className="dm-step-header">
            <span className="dm-step-num">1</span>
            <div>
              <div className="dm-step-title">基本信息与命令内容</div>
              <div className="dm-step-desc">设置模板名称、部署类型，并编写命令/YAML 与变量配置</div>
            </div>
          </div>
          <div className="dm-form-grid">
            <Field label="模板名称 *">
              <input value={form.name} onChange={f('name')} placeholder="Jupyter Notebook" />
            </Field>
            <Field label="分类">
              <input value={form.category} onChange={f('category')} placeholder="ml / web / database / general" />
            </Field>
            <Field label="描述" full>
              <input value={form.description} onChange={f('description')} placeholder="简短说明（可选）" />
            </Field>
          </div>

          <Field label="部署类型 *" full>
            <div style={{ display: 'flex', gap: 8 }}>
              <label className={`dm-deploy-choice${form.deployType === 'run' ? ' active' : ''}`}>
                <input
                  type="radio"
                  name="deployType"
                  checked={form.deployType === 'run'}
                  onChange={() => setForm((p) => ({ ...p, deployType: 'run' }))}
                />
                <Code size={16} />
                <div>
                  <strong>docker run</strong>
                  <small>基于 docker run 命令创建容器</small>
                </div>
              </label>
              <label className={`dm-deploy-choice${form.deployType === 'compose' ? ' active' : ''}`}>
                <input
                  type="radio"
                  name="deployType"
                  checked={form.deployType === 'compose'}
                  onChange={() => setForm((p) => ({ ...p, deployType: 'compose' }))}
                />
                <Layers size={16} />
                <div>
                  <strong>docker compose</strong>
                  <small>基于 docker-compose.yml 编排多容器</small>
                </div>
              </label>
            </div>
          </Field>

          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Eye size={13} /> 可见性</div>
            <label className="dm-form-check">
              <input type="checkbox" checked={form.isPublic} onChange={f('isPublic')} />
              <span>公开模板</span>
              <small style={{ color: '#94a3b8' }}>（公开模板对所有有「使用模板」权限的用户可见；取消勾选则仅所有者和查看者可见）</small>
            </label>
          </div>

          <Field label={form.deployType === 'compose' ? 'docker-compose.yml *' : 'docker run 命令 *'} full>
            <textarea
              className="mono"
              value={form.rawContent}
              onChange={f('rawContent')}
              placeholder={form.deployType === 'compose'
                ? 'services:\n  web:\n    image: {{IMAGE}}\n    ports:\n      - "{{PORT}}:80"'
                : 'docker run -d --name {{NAME}} -p {{PORT}}:8888 {{IMAGE}}'}
              style={{ minHeight: 160 }}
            />
          </Field>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <button className="btn" onClick={autoDetectVariables}>
              <Wand2 size={13} /> 自动检测占位符
            </button>
            <button className="btn" onClick={addVariable}>
              <Plus size={13} /> 手动添加变量
            </button>
            <span style={{ fontSize: 12, color: '#94a3b8' }}>
              <Sparkles size={11} style={{ verticalAlign: 'middle' }} /> 使用 <code>{'{{变量名}}'}</code> 占位符定义可配置参数，下方配置类型与说明
            </span>
          </div>

          {form.variables.length > 0 && (
            <div className="dm-var-table">
              <div className="dm-var-table-header">
                <span>变量名</span>
                <span>类型</span>
                <span>筛选条件</span>
                <span>默认值</span>
                <span>说明</span>
                <span></span>
              </div>
              {form.variables.map((v, i) => (
                <div key={i} className="dm-var-table-row">
                  <input
                    className="mono dm-var-name"
                    value={v.name}
                    onChange={(e) => updateVariable(i, { name: e.target.value })}
                    placeholder="VAR_NAME"
                  />
                  <select
                    value={v.type}
                    onChange={(e) => updateVariable(i, { type: e.target.value as TemplateVariableType })}
                  >
                    {VARIABLE_TYPE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                  <button
                    className="btn"
                    style={{
                      fontSize: 11,
                      padding: '4px 8px',
                      textAlign: 'left',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      color: v.filter ? '#1d4ed8' : '#94a3b8',
                      borderColor: v.filter ? '#bfdbfe' : '#e2e8f0',
                      background: v.filter ? '#eff6ff' : '#fff',
                    }}
                    title={filterHintForType(v.type)}
                    onClick={() => setFilterEditIdx(i)}
                  >
                    <Shield size={11} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                    {v.filter ? filterSummary(v.filter, v.type) : '点击设置'}
                  </button>
                  <input
                    value={v.defaultValue}
                    onChange={(e) => updateVariable(i, { defaultValue: e.target.value })}
                    placeholder="默认值"
                  />
                  <input
                    value={v.description}
                    onChange={(e) => updateVariable(i, { description: e.target.value })}
                    placeholder="说明文字"
                  />
                  <button
                    className="dm-btn-icon danger"
                    title="删除变量"
                    onClick={() => removeVariable(i)}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
              <div style={{ padding: '8px 10px', fontSize: 11, color: '#94a3b8', lineHeight: 1.6, background: '#f8fafc', borderTop: '1px solid #f1f5f9' }}>
                <strong style={{ color: '#526071' }}>筛选条件说明：</strong>
                镜像/卷 → 通配符过滤下拉选项；文本 → 通配符匹配输入（<code>*</code> 任意、<code>?</code> 单字符、<code>|</code> 或）；数字 → 范围约束（如 <code>1-100</code> 或 <code>&gt;=0,&lt;=100</code>）；下拉 → 逗号分隔的允许选项。留空表示不限制。
              </div>
            </div>
          )}
          {form.variables.length === 0 && (
            <Alert type="info">
              暂无变量。您可以在命令中使用 <code>{'{{VAR_NAME}}'}</code> 格式定义占位符，然后点击「自动检测占位符」生成变量声明。
            </Alert>
          )}
        </div>
      )}

      {/* ── 步骤 2：说明文档（支持交互变量插入） ── */}
      {step === 2 && (
        <InteractiveDocEditor
          value={form.docContent}
          onChange={(v) => setForm((p) => ({ ...p, docContent: v }))}
          variables={form.variables}
        />
      )}

      {/* 筛选条件编辑器弹窗 */}
      {filterEditIdx !== null && form.variables[filterEditIdx] && (
        <FilterEditorModal
          variableType={form.variables[filterEditIdx].type}
          filter={form.variables[filterEditIdx].filter}
          onSave={(f) => updateVariable(filterEditIdx, { filter: f })}
          onClose={() => setFilterEditIdx(null)}
        />
      )}
    </Modal>
  );
}

// ============================================================
// 模板角色管理弹窗（共享）
// ============================================================

export function TemplateRolesModal({
  template,
  onClose,
  onSaved,
}: {
  template: Template;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [detail, setDetail] = useState<TemplateRoleDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [allUsers, setAllUsers] = useState<BasicUser[]>([]);
  const [editOwnerIds, setEditOwnerIds] = useState<string[]>([]);
  const [editViewerIds, setEditViewerIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  useEffect(() => {
    setLoading(true);
    clearError();
    Promise.all([
      apiGet<TemplateRoleDetail>(`${API}/templates/${template.id}/roles`),
      apiGet<{ users: BasicUser[] }>('/api/auth/users-basic').catch(() => ({ users: [] })),
    ]).then(([r, u]) => {
      setDetail(r);
      setEditOwnerIds(r.ownerUserIds);
      setEditViewerIds(r.viewerUserIds);
      setAllUsers(u.users);
    }).catch(setError).finally(() => setLoading(false));
  }, [template.id, clearError]);

  async function save() {
    setSaving(true);
    clearError();
    try {
      await apiPut(`${API}/templates/${template.id}/roles`, {
        ownerUserIds: editOwnerIds,
        viewerUserIds: editViewerIds,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={`角色管理 — ${template.name}`}
      onClose={onClose}
      foot={
        <>
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={save} disabled={saving || !detail}>
            {saving ? <Spin /> : <CheckCircle size={14} />} 保存
          </button>
        </>
      }
    >
      {error && <Alert type="error">{error}</Alert>}
      {loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : detail ? (
        <>
          <div style={{
            background: '#f0f9ff', border: '1px solid #bae6fd', borderRadius: 8,
            padding: '10px 14px', marginBottom: 16, fontSize: 12, color: '#0369a1',
            display: 'grid', gap: 4,
          }}>
            <div><strong>角色说明：</strong></div>
            <div>• <strong>创建者</strong>：平台自动记录，不可修改，默认同时是所有者</div>
            <div>• <strong>所有者（Owner）</strong>：可编辑/删除模板、管理查看者</div>
            <div>• <strong>查看者（Viewer）</strong>：可查看并使用非公开模板</div>
            <div>• 公开模板对所有有「使用模板」权限的用户可见，无需逐一添加查看者</div>
          </div>

          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Users size={13} /> 创建者（平台自动记录）</div>
            {detail.creator ? (
              <span className="dm-role-tag creator">
                {detail.creator.displayName}
                <small style={{ color: '#64748b' }}> @{detail.creator.username}</small>
              </span>
            ) : (
              <span style={{ fontSize: 12, color: '#94a3b8' }}>无</span>
            )}
          </div>

          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <Shield size={13} /> 所有者
              <span style={{ fontSize: 11, color: '#64748b', fontWeight: 400, marginLeft: 6 }}>
                可编辑/删除模板并管理查看者
              </span>
            </div>
            <div className="dm-roles-checklist">
              {allUsers.map((u) => (
                <label key={u.id} className="dm-form-check">
                  <input
                    type="checkbox"
                    checked={editOwnerIds.includes(u.id)}
                    onChange={(e) => {
                      setEditOwnerIds((prev) =>
                        e.target.checked ? [...prev, u.id] : prev.filter((id) => id !== u.id)
                      );
                      if (e.target.checked) {
                        setEditViewerIds((prev) => prev.filter((id) => id !== u.id));
                      }
                    }}
                  />
                  <span>{u.displayName}</span>
                  <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                </label>
              ))}
              {allUsers.length === 0 && <span style={{ fontSize: 12, color: '#94a3b8' }}>暂无用户</span>}
            </div>
          </div>

          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <Eye size={13} /> 查看者
              <span style={{ fontSize: 11, color: '#64748b', fontWeight: 400, marginLeft: 6 }}>
                可查看并使用非公开模板
              </span>
            </div>
            <div className="dm-roles-checklist">
              {allUsers.map((u) => {
                const isOwner = editOwnerIds.includes(u.id);
                return (
                  <label key={u.id} className={`dm-form-check${isOwner ? ' disabled' : ''}`}>
                    <input
                      type="checkbox"
                      checked={editViewerIds.includes(u.id)}
                      disabled={isOwner}
                      onChange={(e) => {
                        setEditViewerIds((prev) =>
                          e.target.checked ? [...prev, u.id] : prev.filter((id) => id !== u.id)
                        );
                      }}
                    />
                    <span>{u.displayName}</span>
                    <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                    {isOwner && <span style={{ fontSize: 11, color: '#64748b', marginLeft: 4 }}>(已是所有者)</span>}
                  </label>
                );
              })}
              {allUsers.length === 0 && <span style={{ fontSize: 12, color: '#94a3b8' }}>暂无用户</span>}
            </div>
          </div>
        </>
      ) : null}
    </Modal>
  );
}

// ============================================================
// TemplatesPanel — 用户模板浏览 + 管理
// ============================================================

export function TemplatesPanel({ me }: { me: AuthUser }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState<TemplateDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();

  // 管理状态
  const [showEditor, setShowEditor] = useState(false);
  const [editingTarget, setEditingTarget] = useState<TemplateDetail | null>(null);
  const [rolesTarget, setRolesTarget] = useState<Template | null>(null);
  const [filterMode, setFilterMode] = useState<'all' | 'mine'>('all');

  const load = useCallback(async () => {
    setLoading(true);
    clearError();
    try {
      const r = await apiGet<{ templates: Template[] }>(`${API}/templates`);
      setTemplates(r.templates);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  useEffect(() => { void load(); }, [load]);

  async function selectTemplate(id: string) {
    clearError();
    try {
      const r = await apiGet<{ template: TemplateDetail }>(`${API}/templates/${id}`);
      setSelected(r.template);
    } catch (e) {
      setError(e);
    }
  }

  async function openEdit(t: Template) {
    clearError();
    try {
      const r = await apiGet<{ template: TemplateDetail }>(`${API}/templates/${t.id}`);
      setEditingTarget(r.template);
      setShowEditor(true);
    } catch (e) {
      setError(e);
    }
  }

  function openCreate() {
    setEditingTarget(null);
    setShowEditor(true);
  }

  async function doDelete(id: string, name: string) {
    if (!confirm(`确定删除模板「${name}」？此操作不可恢复。`)) return;
    clearError();
    try {
      await apiDelete(`${API}/templates/${id}`);
      void load();
      if (selected?.id === id) setSelected(null);
    } catch (e) {
      setError(e);
    }
  }

  const isAdmin = me.role === 'admin';
  // 非管理员中，拥有自己模板的可管理
  const myTemplates = templates.filter((t) => t.canManage);
  const displayTemplates = filterMode === 'mine' ? myTemplates : templates;
  const categories = [...new Set(displayTemplates.map((t) => t.category))];
  const hasManageable = myTemplates.length > 0 || isAdmin;

  // 详情视图
  if (selected) {
    return (
      <div style={{ display: 'grid', gap: 14 }}>
        {error && <Alert type="error">{error}</Alert>}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <button className="btn" onClick={() => setSelected(null)}>← 返回</button>
          <strong style={{ fontSize: 16 }}>{selected.name}</strong>
          <span className="dm-category-tag">{selected.category}</span>
          {selected.deployType === 'compose'
            ? <span className="dm-deploy-tag compose"><Layers size={11} /> compose</span>
            : <span className="dm-deploy-tag run"><Code size={11} /> run</span>}
          {selected.isPublic
            ? <span style={{ color: '#065f46', fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 3 }}><Eye size={11} /> 公开</span>
            : <span style={{ color: '#92400e', fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 3 }}><Lock size={11} /> 私有</span>}
          {selected.canManage && (
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
              <button className="dm-btn-icon" title="编辑" onClick={() => { setEditingTarget(selected); setShowEditor(true); }}>
                <Pencil size={13} />
              </button>
              <button className="dm-btn-icon" title="管理角色" onClick={() => setRolesTarget(selected)}>
                <Users size={13} />
              </button>
              <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(selected.id, selected.name)}>
                <Trash2 size={13} />
              </button>
            </span>
          )}
        </div>

        {selected.description && <p style={{ color: '#526071', margin: 0 }}>{selected.description}</p>}

        {/* 变量说明 */}
        {selected.variables && selected.variables.length > 0 && (
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><Code size={13} /> 模板变量</div>
            <div className="dm-var-info-table">
              {selected.variables.map((v, i) => (
                <div key={i} className="dm-var-info-row">
                  <code className="dm-var-info-name">{`{{${v.name}}}`}</code>
                  <span className="dm-var-info-type">{VARIABLE_TYPE_OPTIONS.find((o) => o.value === v.type)?.label ?? v.type}</span>
                  {v.description && <span className="dm-var-info-desc">{v.description}</span>}
                  {v.defaultValue && <span className="dm-var-info-default">默认: {v.defaultValue}</span>}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* 文档 */}
        {selected.docContent ? (
          <div className="dm-md-preview" dangerouslySetInnerHTML={{ __html: renderMarkdown(selected.docContent) }} />
        ) : (
          <Alert type="info">该模板暂无说明文档</Alert>
        )}

        {/* 原始内容预览 */}
        {selected.canManage && selected.rawContent && (
          <div className="dm-perm-section">
            <div className="dm-perm-section-title"><FileText size={13} /> 原始内容（仅所有者可见）</div>
            <pre className="dm-raw-content-preview">{selected.rawContent}</pre>
          </div>
        )}

        {/* 编辑弹窗 */}
        {showEditor && (
          <TemplateEditorModal
            editing={editingTarget}
            onClose={() => { setShowEditor(false); setEditingTarget(null); }}
            onSaved={() => {
              void load();
              // 刷新详情
              if (selected) void selectTemplate(selected.id);
            }}
          />
        )}

        {/* 角色管理弹窗 */}
        {rolesTarget && (
          <TemplateRolesModal
            template={rolesTarget}
            onClose={() => setRolesTarget(null)}
            onSaved={() => void load()}
          />
        )}
      </div>
    );
  }

  // 列表视图
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      {error && <Alert type="error">{error}</Alert>}

      {/* 工具栏 */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-primary" onClick={openCreate}>
          <Plus size={14} /> 创建模板
        </button>
        <button className="btn" onClick={load} disabled={loading}>
          {loading ? <Spin /> : <RefreshCw size={14} />} 刷新
        </button>
        {hasManageable && (
          <div style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
            <button
              className={`dm-server-chip${filterMode === 'all' ? ' active' : ''}`}
              onClick={() => setFilterMode('all')}
            >
              全部模板
            </button>
            <button
              className={`dm-server-chip${filterMode === 'mine' ? ' active' : ''}`}
              onClick={() => setFilterMode('mine')}
            >
              我管理的 ({myTemplates.length})
            </button>
          </div>
        )}
      </div>

      {loading ? (
        <div className="dm-empty"><Spin /> 加载中…</div>
      ) : displayTemplates.length === 0 ? (
        <div className="dm-empty">
          <ClipboardList size={32} />
          {filterMode === 'mine' ? '您目前没有管理的模板' : '暂无可用模板'}
        </div>
      ) : (
        categories.map((cat) => (
          <div key={cat} style={{ display: 'grid', gap: 10 }}>
            <div style={{ fontWeight: 700, color: '#526071', fontSize: 13, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              {cat}
            </div>
            <div className="dm-card-grid">
              {displayTemplates.filter((t) => t.category === cat).map((t) => (
                <div key={t.id} className="dm-card dm-template-card" style={{ cursor: 'pointer' }} onClick={() => selectTemplate(t.id)}>
                  <div className="dm-card-header">
                    <span className="dm-card-title">{t.name}</span>
                    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                      {t.deployType === 'compose'
                        ? <span className="dm-deploy-tag compose" onClick={(e) => e.stopPropagation()}><Layers size={10} /> compose</span>
                        : <span className="dm-deploy-tag run" onClick={(e) => e.stopPropagation()}><Code size={10} /> run</span>}
                      {t.hasDoc && <FileText size={14} style={{ color: '#94a3b8', flexShrink: 0 }} />}
                    </div>
                  </div>
                  {t.description && <span style={{ color: '#64748b', fontSize: 13 }}><TruncText text={t.description} /></span>}
                  <div style={{ display: 'flex', gap: 4, marginTop: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    {t.isPublic
                      ? <span style={{ fontSize: 11, color: '#065f46', display: 'inline-flex', alignItems: 'center', gap: 2 }}><Eye size={10} /> 公开</span>
                      : <span style={{ fontSize: 11, color: '#92400e', display: 'inline-flex', alignItems: 'center', gap: 2 }}><Lock size={10} /> 私有</span>}
                    {(t.variables?.length ?? 0) > 0 && (
                      <span className="dm-var-count-badge" title={`${t.variables.length} 个变量`}>
                        <Code size={10} /> {t.variables.length} 变量
                      </span>
                    )}
                  </div>
                  {t.canManage && (
                    <div className="dm-card-actions" onClick={(e) => e.stopPropagation()}>
                      <button className="dm-btn-icon" title="编辑" onClick={() => openEdit(t)}><Pencil size={12} /></button>
                      <button className="dm-btn-icon" title="管理角色" onClick={() => setRolesTarget(t)}><Users size={12} /></button>
                      <button className="dm-btn-icon danger" title="删除" onClick={() => doDelete(t.id, t.name)}><Trash2 size={12} /></button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))
      )}

      {/* 编辑/创建弹窗 */}
      {showEditor && (
        <TemplateEditorModal
          editing={editingTarget}
          onClose={() => { setShowEditor(false); setEditingTarget(null); }}
          onSaved={() => void load()}
        />
      )}

      {/* 角色管理弹窗 */}
      {rolesTarget && (
        <TemplateRolesModal
          template={rolesTarget}
          onClose={() => setRolesTarget(null)}
          onSaved={() => void load()}
        />
      )}
    </div>
  );
}

// ============================================================
// MyResourcesPanel — 我的资源（非管理员查看/管理 viewer）
// ============================================================

export function MyResourcesPanel({ me }: { me: AuthUser }) {
  const [resources, setResources] = useState<MyOwnedResource[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError, clearError] = useErrorMsg();
  const [success, setSuccess] = useState<string | null>(null);
  const [allUsers, setAllUsers] = useState<BasicUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);

  const [editTarget, setEditTarget] = useState<MyOwnedResource | null>(null);
  const [editViewerIds, setEditViewerIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError, clearEditError] = useErrorMsg();

  const [filterType, setFilterType] = useState<'all' | 'container' | 'image' | 'volume'>('all');

  const load = useCallback(async () => {
    setLoading(true);
    clearError();
    setSuccess(null);
    try {
      const r = await apiGet<{ resources: MyOwnedResource[] }>(`${API}/my-owned-resources`);
      setResources(r.resources);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [clearError, setError]);

  const loadUsers = useCallback(async () => {
    setUsersLoading(true);
    try {
      const r = await apiGet<{ users: BasicUser[] }>('/api/auth/users-basic');
      setAllUsers(r.users);
    } catch {
      // 加载失败不影响主功能
    } finally {
      setUsersLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadUsers();
  }, [load, loadUsers]);

  function openEdit(res: MyOwnedResource) {
    setEditTarget(res);
    setEditViewerIds(res.viewerUserIds);
    clearEditError();
  }

  async function saveViewers() {
    if (!editTarget) return;
    setSaving(true);
    clearEditError();
    try {
      await apiPut(`${API}/servers/${editTarget.serverId}/resource-viewers`, {
        resourceType: editTarget.resourceType,
        resourceRef: editTarget.resourceRef,
        viewerUserIds: editViewerIds,
      });
      setSuccess(`「${editTarget.resourceRef}」的查看者已更新`);
      setEditTarget(null);
      void load();
    } catch (e) {
      setEditError(e);
    } finally {
      setSaving(false);
    }
  }

  const resourceTypeLabel: Record<string, string> = {
    container: '容器',
    image: '镜像',
    volume: '卷',
  };
  const resourceTypeIcon: Record<string, ReactNode> = {
    container: <Box size={13} />,
    image: <Image size={13} />,
    volume: <Database size={13} />,
  };

  const filtered = resources.filter((r) => filterType === 'all' || r.resourceType === filterType);
  const selectableViewers = allUsers.filter((u) => u.id !== me.id);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, color: '#526071', fontWeight: 600 }}>我管理的资源</span>
        <span style={{ fontSize: 12, color: '#94a3b8' }}>（您作为所有者的资源，可以为其分配查看者）</span>
        <button className="btn" style={{ marginLeft: 'auto', padding: '4px 12px' }} onClick={() => void load()} disabled={loading}>
          {loading ? <Spin /> : <RefreshCw size={13} />} 刷新
        </button>
      </div>

      <div style={{ display: 'flex', gap: 6 }}>
        {(['all', 'container', 'image', 'volume'] as const).map((t) => (
          <button
            key={t}
            className={`dm-server-chip${filterType === t ? ' active' : ''}`}
            onClick={() => setFilterType(t)}
          >
            {t === 'all' ? '全部' : resourceTypeLabel[t]}
          </button>
        ))}
      </div>

      {error && <Alert type="error">{error}</Alert>}
      {success && <Alert type="success">{success}</Alert>}

      {loading ? (
        <SkeletonRows cols={['2fr', '1fr', '1.5fr', '1.5fr', 'auto']} />
      ) : filtered.length === 0 ? (
        <div className="dm-empty">
          <Shield size={32} />
          {resources.length === 0 ? '您目前没有任何作为所有者的资源' : '当前筛选条件下无资源'}
        </div>
      ) : (
        <div className="dm-table">
          <div className="dm-table-header" style={{ gridTemplateColumns: '2fr 1fr 2fr 2fr auto' }}>
            <span>资源名称</span>
            <span>类型</span>
            <span>所属服务器</span>
            <span>查看者</span>
            <span>操作</span>
          </div>

          {filtered.map((res) => (
            <div key={`${res.serverId}-${res.resourceType}-${res.resourceRef}`}
              className="dm-table-row"
              style={{ gridTemplateColumns: '2fr 1fr 2fr 2fr auto' }}>
              <span style={{ fontWeight: 500, minWidth: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
                {resourceTypeIcon[res.resourceType]}
                <TruncText text={res.resourceRef} />
              </span>
              <span>
                <span className="dm-role-tag" style={{ background: res.resourceType === 'container' ? '#dbeafe' : res.resourceType === 'image' ? '#fce7f3' : '#d1fae5', color: '#1e293b' }}>
                  {resourceTypeLabel[res.resourceType]}
                </span>
              </span>
              <span style={{ color: '#526071', fontSize: 13 }}>{res.serverName}</span>
              <span>
                {res.viewers.length === 0 ? (
                  <span style={{ color: '#94a3b8', fontSize: 12 }}>暂无查看者</span>
                ) : (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {res.viewers.map((v) => (
                      <span key={v.userId} className="dm-role-tag viewer">
                        {v.displayName} <small style={{ color: '#64748b' }}>@{v.username}</small>
                      </span>
                    ))}
                  </div>
                )}
              </span>
              <span>
                <button className="dm-btn-icon" title="管理查看者" onClick={() => openEdit(res)}>
                  <Users size={13} />
                </button>
              </span>
            </div>
          ))}
        </div>
      )}

      {editTarget && (
        <Modal
          title={`管理查看者 — ${editTarget.resourceRef}`}
          onClose={() => setEditTarget(null)}
          foot={
            <>
              <button className="btn" onClick={() => setEditTarget(null)}>取消</button>
              <button className="btn btn-primary" onClick={saveViewers} disabled={saving}>
                {saving ? <Spin /> : <CheckCircle size={14} />} 保存
              </button>
            </>
          }
        >
          {editError && <Alert type="error">{editError}</Alert>}

          <div className="dm-perm-section">
            <div className="dm-perm-section-title" style={{ marginBottom: 4 }}>
              <Shield size={13} /> 资源信息
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', fontSize: 13, color: '#526071' }}>
              <span>类型：{resourceTypeLabel[editTarget.resourceType]}</span>
              <span>服务器：{editTarget.serverName}</span>
              {editTarget.creatorUserId && (
                <span>创建者 ID：{editTarget.creatorUserId}</span>
              )}
            </div>
          </div>

          <div className="dm-perm-section">
            <div className="dm-perm-section-title">
              <FileText size={13} /> 查看者（勾选后可查看该资源）
            </div>
            {usersLoading ? (
              <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 6 }}><Spin /> 加载用户列表…</div>
            ) : (
              <div className="dm-roles-checklist">
                {selectableViewers.map((u) => (
                  <label key={u.id} className="dm-form-check">
                    <input
                      type="checkbox"
                      checked={editViewerIds.includes(u.id)}
                      onChange={(e) => {
                        setEditViewerIds((prev) =>
                          e.target.checked ? [...prev, u.id] : prev.filter((id) => id !== u.id)
                        );
                      }}
                    />
                    <span>{u.displayName}</span>
                    <small style={{ color: '#94a3b8' }}>@{u.username}</small>
                  </label>
                ))}
                {selectableViewers.length === 0 && (
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>暂无其他可选用户</div>
                )}
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
