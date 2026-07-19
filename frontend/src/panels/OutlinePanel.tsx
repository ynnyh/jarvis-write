// 大纲工作区:蓝图生成 / 内联编辑 / 大改分级 → 影响分析 → 勾选级联
import { useState } from "react";
import { api, EditResult, ImpactReport, Outline, Tendency } from "../api";
import TendencySelector from "../components/TendencySelector";

interface Props {
  pid: number;
  outlines: Outline[];
  hasArch: boolean;
  onChanged: () => Promise<void>;
}

type Form = Partial<Outline>;

export default function OutlinePanel({ pid, outlines, hasArch, onChanged }: Props) {
  const [genTendency, setGenTendency] = useState<Tendency>({});
  const [showGen, setShowGen] = useState(!outlines.length);
  const [editingNum, setEditingNum] = useState<number | null>(null);
  const [form, setForm] = useState<Form>({});
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [editResult, setEditResult] = useState<EditResult | null>(null);
  const [impact, setImpact] = useState<ImpactReport | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [flash, setFlash] = useState("");

  async function generateBlueprint() {
    setBusy(`生成章节蓝图中(约2-6分钟)…`); setErr("");
    try {
      const r = await api.generateBlueprint(pid, genTendency);
      if (r.warnings.length) setErr("警告: " + r.warnings.join(";"));
      await onChanged();
      setShowGen(false);
      setFlash(`已生成 ${r.outlines.length} 章蓝图。`);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  function startEdit(o: Outline) {
    setEditingNum(o.chapter_number);
    setForm({
      title: o.title, summary: o.summary, foreshadowing: o.foreshadowing,
      chapter_role: o.chapter_role, chapter_purpose: o.chapter_purpose,
      scene_location: o.scene_location, suspense_level: o.suspense_level,
    });
    setEditResult(null); setImpact(null); setErr(""); setFlash("");
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
        <div style={{ display: "flex", alignItems: "center" }}>
          <h2 style={{ flex: 1, margin: 0 }}>章节蓝图 <span className="badge">{outlines.length} 章</span></h2>
          <button onClick={() => setShowGen(!showGen)}>
            {outlines.length ? "重新生成蓝图" : "生成蓝图"}
          </button>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          每章都可直接编辑。动了情节的"大改"会自动分析下游影响,由你决定级联范围——不会出现"这里改了那里还是旧的"。
        </div>
        {showGen && (
          <div style={{ marginTop: 10 }}>
            {!hasArch && <div className="msg-err">请先在「架构」生成顶层架构。</div>}
            {hasArch && (
              <>
                <TendencySelector node="outline" value={genTendency} onChange={setGenTendency} compact />
                <button className="primary" disabled={!!busy} onClick={generateBlueprint} style={{ marginTop: 8 }}>
                  {busy && <span className="spin" />}
                  {outlines.length ? "覆盖并重新生成全部蓝图" : "生成章节蓝图"}
                </button>
              </>
            )}
          </div>
        )}
        {busy && <div className="muted" style={{ marginTop: 8 }}><span className="spin" />{busy}</div>}
        {flash && <div className="msg-ok" style={{ marginTop: 8 }}>{flash}</div>}
        {err && <div className="msg-err" style={{ marginTop: 8 }}>{err}</div>}
      </div>

      {outlines.map((o) => {
        const editing = editingNum === o.chapter_number;
        return (
          <div key={o.id} className="outline-item">
            <div className="head">
              <span className="num">第{o.chapter_number}章</span>
              <b>{o.title}</b>
              <span className="badge">{o.chapter_role || "—"}</span>
              <span className="badge">v{o.current_version}</span>
              <div style={{ flex: 1 }} />
              {!editing && <button onClick={() => startEdit(o)}>编辑本章</button>}
            </div>

            {!editing && (
              <>
                <div className="muted" style={{ marginTop: 6 }}>{o.summary}</div>
                <div className="muted" style={{ marginTop: 4, fontSize: 12.5 }}>
                  伏笔:{o.foreshadowing || "无"} · 人物:{(o.characters_involved as string[]).join("、") || "—"} · 场景:{o.scene_location || "—"}
                </div>
              </>
            )}

            {editing && (
              <div style={{ marginTop: 10 }}>
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
                <div style={{ marginTop: 10 }}>
                  <button className="primary" disabled={!!busy} onClick={() => save(o.chapter_number)}>
                    {busy && <span className="spin" />}保存
                  </button>
                  <button disabled={!!busy} onClick={() => { setEditingNum(null); setEditResult(null); setImpact(null); }}>
                    取消
                  </button>
                </div>

                {editResult?.status === "saved" && editResult.needs_impact_analysis && (
                  <div className="card" style={{ marginTop: 12, background: "#fffdf5" }}>
                    <b>大改</b><span className="badge warn">major</span>
                    <div className="muted" style={{ margin: "6px 0" }}>{editResult.change_summary}</div>
                    {editResult.own_chapter_stale && (
                      <div className="msg-err">本章已有正文,已标记「与新大纲不符」。</div>
                    )}
                    {!impact && (
                      <button className="primary" disabled={!!busy} onClick={() => runImpact(o.chapter_number)}>
                        {busy && <span className="spin" />}分析下游影响
                      </button>
                    )}
                    {impact && (
                      <div style={{ marginTop: 8 }}>
                        <div className="muted">{impact.overall}</div>
                        {impact.affected.map((a) => (
                          <div key={a.chapter_number} className="fact-line" style={{ display: "flex", gap: 8 }}>
                            <input type="checkbox" checked={picked.has(a.chapter_number)}
                              onChange={(e) => {
                                const s = new Set(picked);
                                e.target.checked ? s.add(a.chapter_number) : s.delete(a.chapter_number);
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
                          <button className="primary" style={{ marginTop: 8 }}
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
