// 单章大纲卡片:展开/收起、详情、内联编辑、大改影响分析、级联重生成
import { EditorAction, EditResult, ImpactReport, Outline } from "../../api";

type Form = Partial<Outline>;

interface Props {
  outline: Outline;
  editing: boolean;
  expanded: boolean;
  form: Form;
  busy: string;
  editResult: EditResult | null;
  impact: ImpactReport | null;
  picked: Set<number>;
  outlineActions: EditorAction[];
  onToggleExpand: () => void;
  onStartEdit: () => void;
  onFormChange: (form: Form) => void;
  onSave: () => void;
  onCancelEdit: () => void;
  onRunImpact: () => void;
  onTogglePick: (n: number, checked: boolean) => void;
  onRunCascade: () => void;
  onDirectiveChip: (directive: string) => void;
}

export default function OutlineItem({
  outline: o, editing, expanded, form, busy, editResult, impact, picked,
  outlineActions, onToggleExpand, onStartEdit, onFormChange, onSave,
  onCancelEdit, onRunImpact, onTogglePick, onRunCascade, onDirectiveChip,
}: Props) {
  const open = editing || expanded;
  return (
    <div className="outline-item">
      <div className="head" onClick={editing ? undefined : onToggleExpand}>
        <span className="num">第{o.chapter_number}章</span>
        <b className="outline-title">{o.title}</b>
        <span className="badge">{o.chapter_role || "—"}</span>
        <span className="badge">v{o.current_version}</span>
        {!editing && <span className="caret">{open ? "▾" : "▸"}</span>}
      </div>

      {open && !editing && (
        <div className="outline-detail">
          <div className="muted">{o.summary}</div>
          <div className="meta-line">
            伏笔:{o.foreshadowing || "无"} · 人物:{(o.characters_involved ?? []).join("、") || "—"} · 场景:{o.scene_location || "—"}
          </div>
          <div className="actions mt-2">
            <button className="btn-sm" onClick={onStartEdit}>编辑本章</button>
            {outlineActions.length > 0 && (
              <span className="chips">
                <span className="hint">让 AI:</span>
                {outlineActions.map((a) => (
                  <span key={a.key} className="chip" title={a.directive}
                    onClick={() => onDirectiveChip(`第${o.chapter_number}章:${a.directive}`)}>
                    {a.label}
                  </span>
                ))}
              </span>
            )}
          </div>
        </div>
      )}

      {editing && (
        <div className="mt-3">
          <div className="row">
            <div>
              <label className="fl">标题</label>
              <input type="text" value={form.title as string}
                onChange={(e) => onFormChange({ ...form, title: e.target.value })} />
            </div>
            <div>
              <label className="fl">本章定位</label>
              <input type="text" value={form.chapter_role as string}
                onChange={(e) => onFormChange({ ...form, chapter_role: e.target.value })} />
            </div>
            <div>
              <label className="fl">场景地点</label>
              <input type="text" value={form.scene_location as string}
                onChange={(e) => onFormChange({ ...form, scene_location: e.target.value })} />
            </div>
          </div>
          <label className="fl">本章简述(改情节走向会触发大改分析)</label>
          <textarea rows={4} value={form.summary as string}
            onChange={(e) => onFormChange({ ...form, summary: e.target.value })} />
          <label className="fl">伏笔操作(埋设 / 强化 / 回收)</label>
          <textarea rows={2} value={form.foreshadowing as string}
            onChange={(e) => onFormChange({ ...form, foreshadowing: e.target.value })} />
          <div className="actions mt-3">
            <button className="primary" disabled={!!busy} onClick={onSave}>
              {busy && <span className="spin" />}保存
            </button>
            <button disabled={!!busy} onClick={onCancelEdit}>取消</button>
          </div>

          {editResult?.status === "saved" && editResult.needs_impact_analysis && (
            <div className="card card-warn mt-3">
              <b>大改</b><span className="badge warn">major</span>
              <div className="card-desc mt-1">{editResult.change_summary}</div>
              {editResult.own_chapter_stale && (
                <div className="msg-err">本章已有正文,已标记「与新大纲不符」。</div>
              )}
              {!impact && (
                <button className="primary" disabled={!!busy} onClick={onRunImpact}>
                  {busy && <span className="spin" />}分析下游影响
                </button>
              )}
              {impact && (
                <div className="mt-2">
                  <div className="muted">{impact.overall}</div>
                  {impact.affected.map((a) => (
                    <div key={a.chapter_number} className="fact-line fact-check">
                      <input type="checkbox" checked={picked.has(a.chapter_number)}
                        onChange={(e) => onTogglePick(a.chapter_number, e.target.checked)} />
                      <div>
                        <b>第{a.chapter_number}章</b>
                        <span className={"badge " + (a.action === "regenerate" ? "warn" : "")}>
                          {a.action === "regenerate" ? "建议重生成" : "建议人工复核"}
                        </span>
                        <div className="muted">{a.reason}</div>
                      </div>
                    </div>
                  ))}
                  {impact.affected.length > 0 ? (
                    <button className="primary mt-2"
                      disabled={!!busy || !picked.size} onClick={onRunCascade}>
                      {busy && <span className="spin" />}级联重生成勾选的 {picked.size} 章
                    </button>
                  ) : <div className="msg-ok">无下游章节受影响。</div>}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
