// 大纲工作区:蓝图生成 / 内联编辑 / 大改分级 → 影响分析 → 勾选级联
import { useEffect, useRef, useState } from "react";
import { api, EditResult, ImpactReport, Outline, Tendency } from "../api";
import { pollJob } from "../pollJob";
import TendencySelector from "../components/TendencySelector";
import type { Step } from "../pages/ProjectPage";

interface Props {
  pid: number;
  outlines: Outline[];
  hasArch: boolean;
  onChanged: () => Promise<void>;
  onGotoStep?: (step: Step) => void;
}

type Form = Partial<Outline>;

export default function OutlinePanel({ pid, outlines, hasArch, onChanged, onGotoStep }: Props) {
  const [genTendency, setGenTendency] = useState<Tendency>({});
  const [showGen, setShowGen] = useState(!outlines.length);
  const [showAdv, setShowAdv] = useState(false);
  const [editingNum, setEditingNum] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [form, setForm] = useState<Form>({});
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [editResult, setEditResult] = useState<EditResult | null>(null);
  const [impact, setImpact] = useState<ImpactReport | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [flash, setFlash] = useState("");
  const [genDone, setGenDone] = useState<number | null>(null);
  // 组件卸载时中止轮询,防止卸载后继续 setState
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  function toggleExpand(n: number) {
    const s = new Set(expanded);
    if (s.has(n)) s.delete(n); else s.add(n);
    setExpanded(s);
  }

  async function generateBlueprint() {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy("蓝图生成:排队中…"); setErr("");
    try {
      const { job_id } = await api.generateBlueprintAsync(pid, genTendency);
      // 轮询任务进度(按块生成,阶段文案来自后端 stage)
      const r = await pollJob<{ outlines: Outline[]; warnings: string[] }>(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`蓝图生成中:${stage}`),
      });
      if (ctrl.signal.aborted) return;
      if (r.warnings.length) setErr("警告: " + r.warnings.join(";"));
      await onChanged();
      setShowGen(false);
      setExpanded(new Set());
      setGenDone(r.outlines.length);
    } catch (e) {
      if (!ctrl.signal.aborted) setErr(String(e));
    } finally { if (!ctrl.signal.aborted) setBusy(""); }
  }

  function startEdit(o: Outline) {
    setEditingNum(o.chapter_number);
    setExpanded((prev) => new Set(prev).add(o.chapter_number));
    setForm({
      title: o.title, summary: o.summary, foreshadowing: o.foreshadowing,
      chapter_role: o.chapter_role, chapter_purpose: o.chapter_purpose,
      scene_location: o.scene_location, suspense_level: o.suspense_level,
    });
    setEditResult(null); setImpact(null); setErr(""); setFlash(""); setGenDone(null);
  }

  async function save(n: number) {
    setBusy("保存并判定改动级别…"); setErr("");
    try {
      const r = await api.editOutline(pid, n, form);
      setEditResult(r);
      await onChanged();
      if (r.status === "unchanged") { setFlash("内容无实质变化。"); setEditingNum(null); }
      else if (!r.needs_impact_analysis) { setFlash(`已保存(${r.change_summary})`); setEditingNum(null); }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function runImpact(n: number) {
    setBusy("分析下游影响(逐章判断,约1-3分钟)…"); setErr("");
    try {
      const r = await api.impact(pid, n);
      setImpact(r);
      setPicked(new Set(r.affected.filter((a) => a.action === "regenerate").map((a) => a.chapter_number)));
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function runCascade(n: number) {
    if (!impact) return;
    const chapters = [...picked];
    setBusy(`级联重生成第 ${chapters.join("、")} 章…`); setErr("");
    try {
      const reasons: Record<number, string> = {};
      impact.affected.forEach((a) => { if (picked.has(a.chapter_number)) reasons[a.chapter_number] = a.reason; });
      const r = await api.cascade(pid, n, chapters, reasons);
      setFlash(`级联完成:已更新第 ${r.updated.join("、")} 章大纲` +
        (r.stale_chapters.length ? `;第 ${r.stale_chapters.join("、")} 章正文标记失配` : ""));
      setImpact(null); setEditResult(null); setEditingNum(null);
      await onChanged();
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2 className="grow">章节蓝图 <span className="badge">{outlines.length} 章</span></h2>
          {outlines.length > 0 && (
            <>
              <button className="btn-sm" onClick={() => setExpanded(new Set(outlines.map((o) => o.chapter_number)))}>
                全部展开
              </button>
              <button className="btn-sm" onClick={() => setExpanded(new Set())}>全部收起</button>
            </>
          )}
          <button onClick={() => setShowGen(!showGen)}>
            {outlines.length ? "重新生成蓝图" : "生成蓝图"}
          </button>
        </div>
        <div className="card-desc mt-2">
          每章都可直接编辑。动了情节的"大改"会自动分析下游影响,由你决定级联范围——不会出现"这里改了那里还是旧的"。
        </div>
        {showGen && (
          <div className="mt-3">
            {!hasArch && <div className="msg-err">请先在「架构」生成顶层架构。</div>}
            {hasArch && (outlines.length ? (
              <>
                <TendencySelector node="outline" value={genTendency} onChange={setGenTendency} compact />
                <button className="primary mt-2" disabled={!!busy} onClick={generateBlueprint}>
                  {busy && <span className="spin" />}
                  覆盖并重新生成全部蓝图
                </button>
              </>
            ) : (
              <>
                <div className="muted">根据架构一次性生成全部章节蓝图,生成后可逐章微调。</div>
                <button className="primary btn-lg mt-2" disabled={!!busy} onClick={generateBlueprint}>
                  {busy && <span className="spin" />}
                  生成章节蓝图
                </button>
                <div className="mt-2">
                  <button className="linkbtn" onClick={() => setShowAdv(!showAdv)}>
                    {showAdv ? "▾" : "▸"} 高级选项:本章倾向(可选)
                  </button>
                </div>
                {showAdv && (
                  <TendencySelector node="outline" value={genTendency} onChange={setGenTendency} compact />
                )}
              </>
            ))}
          </div>
        )}
        {busy && <div className="muted mt-2"><span className="spin" />{busy}</div>}
        {flash && <div className="msg-ok mt-2">{flash}</div>}
        {err && <div className="msg-err mt-2">{err}</div>}
      </div>

      {genDone !== null && (
        <div className="card card-ok gen-guide">
          <div className="grow">
            <b>✓ 已生成 {genDone} 章蓝图</b>
            <div className="muted mt-1">接下来:到「写作」步骤生成第 1 章正文。也可以先在下方逐章展开检查、微调。</div>
          </div>
          {onGotoStep && (
            <button className="primary" onClick={() => onGotoStep("write")}>去写作 →</button>
          )}
        </div>
      )}

      {outlines.map((o) => {
        const editing = editingNum === o.chapter_number;
        const open = editing || expanded.has(o.chapter_number);
        return (
          <div key={o.id} className="outline-item">
            <div className="head" onClick={editing ? undefined : () => toggleExpand(o.chapter_number)}>
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
                  <button className="btn-sm" onClick={() => startEdit(o)}>编辑本章</button>
                </div>
              </div>
            )}

            {editing && (
              <div className="mt-3">
                <div className="row">
                  <div>
                    <label className="fl">标题</label>
                    <input type="text" value={form.title as string}
                      onChange={(e) => setForm({ ...form, title: e.target.value })} />
                  </div>
                  <div>
                    <label className="fl">本章定位</label>
                    <input type="text" value={form.chapter_role as string}
                      onChange={(e) => setForm({ ...form, chapter_role: e.target.value })} />
                  </div>
                  <div>
                    <label className="fl">场景地点</label>
                    <input type="text" value={form.scene_location as string}
                      onChange={(e) => setForm({ ...form, scene_location: e.target.value })} />
                  </div>
                </div>
                <label className="fl">本章简述(改情节走向会触发大改分析)</label>
                <textarea rows={4} value={form.summary as string}
                  onChange={(e) => setForm({ ...form, summary: e.target.value })} />
                <label className="fl">伏笔操作(埋设 / 强化 / 回收)</label>
                <textarea rows={2} value={form.foreshadowing as string}
                  onChange={(e) => setForm({ ...form, foreshadowing: e.target.value })} />
                <div className="actions mt-3">
                  <button className="primary" disabled={!!busy} onClick={() => save(o.chapter_number)}>
                    {busy && <span className="spin" />}保存
                  </button>
                  <button disabled={!!busy} onClick={() => { setEditingNum(null); setEditResult(null); setImpact(null); }}>
                    取消
                  </button>
                </div>

                {editResult?.status === "saved" && editResult.needs_impact_analysis && (
                  <div className="card card-warn mt-3">
                    <b>大改</b><span className="badge warn">major</span>
                    <div className="card-desc mt-1">{editResult.change_summary}</div>
                    {editResult.own_chapter_stale && (
                      <div className="msg-err">本章已有正文,已标记「与新大纲不符」。</div>
                    )}
                    {!impact && (
                      <button className="primary" disabled={!!busy} onClick={() => runImpact(o.chapter_number)}>
                        {busy && <span className="spin" />}分析下游影响
                      </button>
                    )}
                    {impact && (
                      <div className="mt-2">
                        <div className="muted">{impact.overall}</div>
                        {impact.affected.map((a) => (
                          <div key={a.chapter_number} className="fact-line fact-check">
                            <input type="checkbox" checked={picked.has(a.chapter_number)}
                              onChange={(e) => {
                                const s = new Set(picked);
                                if (e.target.checked) s.add(a.chapter_number);
                                else s.delete(a.chapter_number);
                                setPicked(s);
                              }} />
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
                            disabled={!!busy || !picked.size} onClick={() => runCascade(o.chapter_number)}>
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
      })}
    </>
  );
}
