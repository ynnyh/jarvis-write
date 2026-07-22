// 大纲工作区:蓝图生成 / 内联编辑 / 大改分级 → 影响分析 → 勾选级联
import { useEffect, useRef, useState } from "react";
import { api, CascadeResult, DirectiveApplyResult, DirectiveItem, DirectivePreview, EditorAction, EditResult, ImpactReport, Outline, Project, Tendency } from "../api";
import { pollJob } from "../pollJob";
import TendencySelector from "../components/TendencySelector";
import { useJob } from "../ui/useJob";
import type { Step } from "../pages/ProjectPage";
import DirectivePanel from "./outline/DirectivePanel";
import OutlineItem from "./outline/OutlineItem";

interface Props {
  pid: number;
  project?: Project;
  outlines: Outline[];
  hasArch: boolean;
  onChanged: () => Promise<void>;
  onGotoStep?: (step: Step) => void;
}

type Form = Partial<Outline>;

export default function OutlinePanel({ pid, project, outlines, hasArch, onChanged, onGotoStep }: Props) {
  const { run: runAsyncJob } = useJob();
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
  // 修改指令:输入 → LLM 预览(可再编辑/勾选) → 应用
  const [showDirective, setShowDirective] = useState(false);
  const [directiveText, setDirectiveText] = useState("");
  // 编辑部预设优化动作(大纲级 chips:深化冲突/增加伏笔…,点了走指令改预览链路)
  const [outlineActions, setOutlineActions] = useState<EditorAction[]>([]);
  useEffect(() => {
    api.editorialActions().then((a) => setOutlineActions(a.outline)).catch(() => undefined);
  }, []);
  const [preview, setPreview] = useState<DirectivePreview | null>(null);
  const [drafts, setDrafts] = useState<DirectiveItem[]>([]);
  const [dirPicked, setDirPicked] = useState<Set<number>>(new Set());
  const [dirResult, setDirResult] = useState<DirectiveApplyResult | null>(null);
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

  // 滚动规划:展开下一卷蓝图(卷纲 + 已成文状态)
  async function extendBlueprint() {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy("展开下一卷:排队中…"); setErr("");
    try {
      const { job_id } = await api.extendBlueprintAsync(pid);
      const r = await pollJob<{ outlines: Outline[]; planned_range: [number, number] }>(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`展开下一卷:${stage}`),
      });
      if (ctrl.signal.aborted) return;
      await onChanged();
      setGenDone(r.outlines.length);
      setFlash(`已展开第 ${r.planned_range[0]}-${r.planned_range[1]} 章蓝图。`);
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
    setBusy("分析下游影响(逐章判断,约1-3分钟,可切到别处,进度看右上角任务)…"); setErr("");
    try {
      const r = await runAsyncJob<ImpactReport>(
        () => api.impactAsync(pid, n),
        { kind: `impact-${pid}-${n}`, onStage: (s) => setBusy(`${s}…`) },
      );
      if (r) {
        setImpact(r);
        setPicked(new Set(r.affected.filter((a) => a.action === "regenerate").map((a) => a.chapter_number)));
      }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function runCascade(n: number) {
    if (!impact) return;
    const chapters = [...picked];
    setBusy(`级联重生成第 ${chapters.join("、")} 章…`); setErr("");
    try {
      const reasons: Record<number, string> = {};
      impact.affected.forEach((a) => { if (picked.has(a.chapter_number)) reasons[a.chapter_number] = a.reason; });
      const r = await runAsyncJob<CascadeResult>(
        () => api.cascadeAsync(pid, {
          source_chapter: n, chapter_numbers: chapters, reasons, tendency: {},
        }),
        { kind: `cascade-${pid}`, onStage: (s) => setBusy(`${s}…`) },
      );
      if (r) {
        setFlash(`级联完成:已更新第 ${r.updated.join("、")} 章大纲` +
          (r.stale_chapters.length ? `;第 ${r.stale_chapters.join("、")} 章正文标记失配` : ""));
        setImpact(null); setEditResult(null); setEditingNum(null);
        await onChanged();
      }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function runDirectiveParse() {
    setBusy("分析修改指令的影响(可切到别处,进度看右上角任务)…"); setErr(""); setPreview(null); setDirResult(null);
    try {
      const r = await runAsyncJob<DirectivePreview>(
        () => api.parseEditDirectiveAsync(pid, directiveText),
        { kind: `directive-${pid}`, onStage: (s) => setBusy(`${s}…`) },
      );
      if (r) {
        setPreview(r);
        setDrafts(r.items.map((i) => ({ ...i })));
        setDirPicked(new Set(r.items.map((i) => i.chapter_number)));
      }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  function closeDirective() {
    setShowDirective(false); setPreview(null); setDrafts([]);
    setDirPicked(new Set()); setDirResult(null);
  }

  async function applyDirective() {
    const items = drafts.filter((d) => dirPicked.has(d.chapter_number));
    if (!items.length) return;
    setBusy(`应用修改(第 ${items.map((i) => i.chapter_number).join("、")} 章)…`); setErr("");
    try {
      const r = await api.applyEditDirective(pid, items);
      setDirResult(r);
      setPreview(null); setDrafts([]); setDirectiveText("");
      await onChanged();
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  const oldOf = (n: number) => outlines.find((o) => o.chapter_number === n);

  // 滚动规划:已规划边界与"下一卷"区间(有卷纲按卷纲,没有按 30 章一卷)
  const target = project?.target_chapters ?? 0;
  const plannedUpto = outlines.length ? Math.max(...outlines.map((o) => o.chapter_number)) : 0;
  const canExtend = plannedUpto > 0 && target > plannedUpto;
  const nextSeg = (() => {
    if (!canExtend) return null;
    const seg = project?.macro_plan?.find((s) => s.start <= plannedUpto + 1 && plannedUpto + 1 <= s.end);
    return { start: plannedUpto + 1, end: Math.min(seg?.end ?? plannedUpto + 30, target) };
  })();
  const [showMacro, setShowMacro] = useState(false);

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2 className="grow">章节蓝图 <span className="badge">
            {target ? `已规划 ${outlines.length}/${target} 章` : `${outlines.length} 章`}
          </span></h2>
          {!!project?.macro_plan?.length && (
            <button className="btn-sm" onClick={() => setShowMacro(!showMacro)}>
              {showMacro ? "收起卷纲" : "卷纲"}
            </button>
          )}
          {canExtend && nextSeg && (
            <button className="primary btn-sm" disabled={!!busy} onClick={extendBlueprint}
              title="按卷纲和已写正文的实际走向,展开下一段章节蓝图">
              {busy.startsWith("展开") && <span className="spin" />}
              展开下一卷(第 {nextSeg.start}-{nextSeg.end} 章)
            </button>
          )}
          {outlines.length > 0 && (
            <>
              <button className="btn-sm" onClick={() => setExpanded(new Set(outlines.map((o) => o.chapter_number)))}>
                全部展开
              </button>
              <button className="btn-sm" onClick={() => setExpanded(new Set())}>全部收起</button>
            </>
          )}
          {outlines.length > 0 && (
            <button onClick={() => (showDirective ? closeDirective() : setShowDirective(true))}>
              修改指令
            </button>
          )}
          <button onClick={() => setShowGen(!showGen)}>
            {outlines.length ? "重新生成蓝图" : "生成蓝图"}
          </button>
        </div>
        <div className="card-desc mt-2">
          每章都可直接编辑。动了情节的"大改"会自动分析下游影响,由你决定级联范围——不会出现"这里改了那里还是旧的"。
          {target > 40 && !outlines.length && "长篇采用滚动规划:先出全书卷纲定方向,蓝图只铺第一卷,写到卷尾再按实际剧情展开下一卷——远期章节不再空洞跑偏。"}
        </div>
        {showMacro && !!project?.macro_plan?.length && (
          <div className="macro-plan mt-3">
            {project.macro_plan.map((s, i) => (
              <div key={i} className={"macro-seg" + (plannedUpto >= s.end ? " done" : plannedUpto >= s.start - 1 ? " current" : "")}>
                <b>卷{i + 1}(第 {s.start}-{s.end} 章){plannedUpto >= s.end ? " ✓已规划" : plannedUpto + 1 >= s.start && plannedUpto < s.end ? " · 当前" : ""}</b>
                <div>{s.goal}</div>
              </div>
            ))}
          </div>
        )}
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
        {showDirective && (
          <DirectivePanel
            busy={busy}
            directiveText={directiveText}
            preview={preview}
            drafts={drafts}
            dirPicked={dirPicked}
            dirResult={dirResult}
            getOld={oldOf}
            onDirectiveTextChange={setDirectiveText}
            onRunParse={runDirectiveParse}
            onTogglePick={(n, checked) => {
              const s = new Set(dirPicked);
              if (checked) s.add(n); else s.delete(n);
              setDirPicked(s);
            }}
            onDraftChange={(n, summary) => setDrafts(drafts.map((x) =>
              x.chapter_number === n ? { ...x, new_summary: summary } : x))}
            onApply={applyDirective}
            onClose={closeDirective}
            onGotoStep={onGotoStep}
          />
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

      {outlines.map((o) => (
        <OutlineItem
          key={o.id}
          outline={o}
          editing={editingNum === o.chapter_number}
          expanded={expanded.has(o.chapter_number)}
          form={form}
          busy={busy}
          editResult={editingNum === o.chapter_number ? editResult : null}
          impact={editingNum === o.chapter_number ? impact : null}
          picked={picked}
          outlineActions={outlineActions}
          onToggleExpand={() => toggleExpand(o.chapter_number)}
          onStartEdit={() => startEdit(o)}
          onFormChange={setForm}
          onSave={() => save(o.chapter_number)}
          onCancelEdit={() => { setEditingNum(null); setEditResult(null); setImpact(null); }}
          onRunImpact={() => runImpact(o.chapter_number)}
          onTogglePick={(n, checked) => {
            const s = new Set(picked);
            if (checked) s.add(n); else s.delete(n);
            setPicked(s);
          }}
          onRunCascade={() => runCascade(o.chapter_number)}
          onDirectiveChip={(directive) => {
            setShowDirective(true);
            setDirectiveText(directive);
            window.scrollTo({ top: 0, behavior: "smooth" });
          }}
        />
      ))}
    </>
  );
}
