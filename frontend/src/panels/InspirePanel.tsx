// 灵感工作区:碎片/想法 → 结构化「故事概念」→ 定为本书概念
// 三条路并存:AI 出方案(结构化) / 指令式局部改 / 对话式从零捏
import { useEffect, useRef, useState } from "react";
import {
  api, ChatTurn, Concept, CONCEPT_FIELDS, EMPTY_CONCEPT, conceptIsEmpty,
  Project, Tendency,
  StoryDNA, EMPTY_DNA, dnaIsEmpty, DnaOptions, DnaCapsuleChoice, MirrorResult,
} from "../api";
import TendencySelector from "../components/TendencySelector";
import { useJob } from "../ui/useJob";
import { confirmDialog } from "../ui/ConfirmDialog";
import { errMsg } from "../pollJob";
import type { SetupStep } from "../pages/ProjectPage";

interface Props { project: Project; onChanged: () => Promise<void>; onGotoStep?: (step: SetupStep) => void; }

// 从项目已存概念/主题恢复当前草稿:有 concept 用 concept,否则把 topic 灌进 logline
function conceptFromProject(p: Project): Concept {
  if (p.concept && !conceptIsEmpty(p.concept)) return { ...EMPTY_CONCEPT, ...p.concept };
  return { ...EMPTY_CONCEPT, logline: p.topic ?? "" };
}

// 从项目已存 DNA 恢复坐标卡草稿(老项目无 dna → 空坐标)
function dnaFromProject(p: Project): StoryDNA {
  return p.dna ? { ...EMPTY_DNA, ...p.dna } : { ...EMPTY_DNA };
}

// 只读概念卡:展示六字段(空字段淡出)
function ConceptView({ c }: { c: Concept }) {
  return (
    <div className="concept-grid">
      {CONCEPT_FIELDS.map((f) => (
        <div key={f.key} className={"concept-field" + (c[f.key].trim() ? "" : " empty")}>
          <span className="cf-label">{f.label}</span>
          <span className="cf-value">{c[f.key].trim() || <span className="muted">（未填）</span>}</span>
        </div>
      ))}
    </div>
  );
}

// 可编辑概念卡:六字段 textarea
function ConceptEditor({ c, onChange }: { c: Concept; onChange: (c: Concept) => void }) {
  return (
    <div className="concept-edit">
      {CONCEPT_FIELDS.map((f) => (
        <div key={f.key} className="concept-edit-row">
          <label className="fl">{f.label} <span className="hint">· {f.hint}</span></label>
          <textarea rows={f.key === "logline" || f.key === "protagonist" ? 2 : 1}
            value={c[f.key]}
            onChange={(e) => onChange({ ...c, [f.key]: e.target.value })} />
        </div>
      ))}
    </div>
  );
}

// 创作坐标(本书基因):定味道锚——参照系 / 题材模式(硬门)/ 味道轴 / 必须有·绝不能有 / 味道锚胶囊。
// 认领一个胶囊即一步回填「模式 + 味道轴」;题材模式选定即预览会拦哪些网文套路。
function DnaCard({
  dna, options, open, busy, mirrorBusy, onToggle, onChange, onMirror,
}: {
  dna: StoryDNA;
  options: DnaOptions | null;
  open: boolean;
  busy: boolean;
  mirrorBusy: boolean;
  onToggle: () => void;
  onChange: (d: StoryDNA) => void;
  onMirror: () => void;
}) {
  const [mustInput, setMustInput] = useState("");
  const [banInput, setBanInput] = useState("");
  const set = (patch: Partial<StoryDNA>) => onChange({ ...dna, ...patch });

  // 认领味道锚:回填 mode + axes(一步定坐标);再点一次取消
  function pickCapsule(cap: DnaCapsuleChoice) {
    if (dna.taste_key === cap.key) { set({ taste_key: "" }); return; }
    set({ taste_key: cap.key, mode: cap.mode || dna.mode, axes: { ...dna.axes, ...cap.axes } });
  }
  function toggleAxis(axisKey: string, val: string) {
    const next = { ...dna.axes };
    if (next[axisKey] === val) delete next[axisKey];
    else next[axisKey] = val;
    set({ axes: next });
  }
  function addTag(field: "must" | "must_not", raw: string) {
    const v = raw.trim();
    if (!v || dna[field].includes(v)) return;
    set(field === "must" ? { must: [...dna.must, v] } : { must_not: [...dna.must_not, v] });
  }
  function removeTag(field: "must" | "must_not", v: string) {
    set(field === "must"
      ? { must: dna.must.filter((x) => x !== v) }
      : { must_not: dna.must_not.filter((x) => x !== v) });
  }

  const pickedCap = options?.capsules.find((c) => c.key === dna.taste_key) || null;
  const forbidden = (dna.mode && options?.forbidden_by_mode[dna.mode]) || [];
  const empty = dnaIsEmpty(dna);

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">🎯 创作坐标 · 本书基因{!empty && <span className="badge ok" style={{ marginLeft: 8 }}>已定</span>}</h3>
        <button className="btn-sm" onClick={onToggle}>{open ? "收起" : empty ? "去定义" : "展开"}</button>
      </div>
      <div className="card-desc">
        先给这本书定个「味道」——AI 全程盯着它写,越线的套路(现实向里的重生/系统/异能觉醒等)会被自动拦掉重写。留空也能生成,但定了它,下面三种方式都更准。
      </div>
      {open && (
        <>
          {/* 味道锚胶囊:一步定味道 */}
          <div className="dna-field">
            <label className="fl">认领一个味道 <span className="dna-field-hint">· 选中即一步定「模式 + 味道轴」,可再手调</span></label>
            <div className="title-chips">
              {(options?.capsules ?? []).map((c) => (
                <button key={c.key} type="button"
                  className={"title-chip sm" + (dna.taste_key === c.key ? " on" : "")}
                  onClick={() => pickCapsule(c)}>{c.name}</button>
              ))}
            </div>
            {pickedCap && (
              <div className="dna-cap-hint">
                <b>{pickedCap.name}</b>（参照:{pickedCap.comps_hint}）<br />{pickedCap.directive}
              </div>
            )}
          </div>

          {/* 参照系 */}
          <div className="dna-field">
            <label className="fl">参照系 <span className="dna-field-hint">· 像哪部作品的味道(只指路,不照搬内容)</span></label>
            <input type="text" value={dna.comps}
              onChange={(e) => set({ comps: e.target.value })}
              placeholder='如:"《XX》那种细腻的现实初恋" / "《XX》遇上《XX》"' />
          </div>

          {/* 题材模式(硬门) */}
          <div className="dna-field">
            <label className="fl">题材模式 <span className="dna-field-hint">· 定了才开硬门(越线自动拦+重写)</span></label>
            <div className="title-chips">
              {(options?.modes ?? []).map((m) => (
                <button key={m.key} type="button"
                  className={"title-chip sm" + (dna.mode === m.key ? " on" : "")}
                  onClick={() => set({ mode: dna.mode === m.key ? "" : m.key })}>{m.label}</button>
              ))}
            </div>
            {forbidden.length > 0 && (
              <div className="dna-ban-note">
                选它后,这些网文套路会被自动拦下(除非你在「必须有」里点名要):
                <div className="mt-1">{forbidden.map((f) => <span key={f} className="dna-ban-chip">{f}</span>)}</div>
              </div>
            )}
          </div>

          {/* 味道轴 */}
          <div className="dna-field">
            <label className="fl">味道轴 <span className="dna-field-hint">· 各挑一边,不选=不表态</span></label>
            {(options?.axes ?? []).map((ax) => (
              <div key={ax.key} className="dna-axis">
                <span className="dna-axis-label">{ax.label}</span>
                {[ax.left, ax.right].map((side) => (
                  <button key={side} type="button"
                    className={"title-chip sm" + (dna.axes[ax.key] === side ? " on" : "")}
                    onClick={() => toggleAxis(ax.key, side)}>{side}</button>
                ))}
              </div>
            ))}
          </div>

          {/* 必须有 */}
          <div className="dna-field">
            <label className="fl">必须有的看点 <span className="dna-field-hint">· 回车添加;AI 会尽力落地</span></label>
            <div className="dna-tags">
              {dna.must.map((m) => (
                <span key={m} className="dna-tag">{m}
                  <button type="button" className="dna-tag-x" onClick={() => removeTag("must", m)}>×</button>
                </span>
              ))}
              <input type="text" className="dna-tag-input" value={mustInput}
                onChange={(e) => setMustInput(e.target.value)} placeholder="如:暗恋、双向奔赴…"
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag("must", mustInput); setMustInput(""); } }} />
            </div>
          </div>

          {/* 绝不能有 */}
          <div className="dna-field">
            <label className="fl">绝不能有 <span className="dna-field-hint">· 硬门之外额外拉黑的元素</span></label>
            <div className="dna-tags">
              {dna.must_not.map((m) => (
                <span key={m} className="dna-tag ban">{m}
                  <button type="button" className="dna-tag-x" onClick={() => removeTag("must_not", m)}>×</button>
                </span>
              ))}
              <input type="text" className="dna-tag-input" value={banInput}
                onChange={(e) => setBanInput(e.target.value)} placeholder="如:出轨、无脑虐…"
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag("must_not", banInput); setBanInput(""); } }} />
            </div>
          </div>

          <div className="actions mt-3">
            <button className="primary" disabled={empty || busy || mirrorBusy} onClick={onMirror}>
              {mirrorBusy && <span className="spin" />}🪞 照镜子:看看 AI 理解的味道
            </button>
            <span className="hint">先照镜子核对,再出方案,少走弯路。</span>
          </div>
        </>
      )}
    </div>
  );
}

// 品味镜:AI 把坐标卡复述成一段人话 + 矛盾检测 + 会拦的套路;「就是这个味」再出方案
function MirrorView({ mirror, busy, onProceed, onClose }: {
  mirror: MirrorResult;
  busy: boolean;
  onProceed: () => void;
  onClose: () => void;
}) {
  return (
    <div className="card card-info">
      <div className="card-head">
        <h3 className="grow">🪞 AI 理解的味道</h3>
        <button className="btn-sm" disabled={busy} onClick={onClose}>继续改坐标</button>
      </div>
      <div className="card-desc">照镜子核对——「对,就是这个味」再出方案,省得烧完 token 才发现跑偏。</div>
      <div className="mirror-reflection">{mirror.reflection}</div>
      {mirror.forbidden.length > 0 && (
        <div className="dna-ban-note">
          已开启硬门,以下套路会被自动拦下并重写:
          <div className="mt-1">{mirror.forbidden.map((f) => <span key={f} className="dna-ban-chip">{f}</span>)}</div>
        </div>
      )}
      {mirror.contradictions.length > 0 && (
        <div className="mirror-contradict card card-warn">
          <b>⚠ 坐标里有几处要你确认</b>
          <ul>{mirror.contradictions.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
      <div className="actions mt-3">
        <button className="primary" disabled={busy} onClick={onProceed}>
          {busy && <span className="spin" />}✅ 对,就照这个味出方案
        </button>
        <button disabled={busy} onClick={onClose}>再改改</button>
      </div>
    </div>
  );
}

export default function InspirePanel({ project, onChanged, onGotoStep }: Props) {
  const { run: runJob } = useJob();
  // 当前正在打磨的概念草稿(三条路都往它上收敛)
  const [concept, setConcept] = useState<Concept>(() => conceptFromProject(project));
  // 概念有手动编辑未「定为本书概念」:AI 对话返回新概念覆盖前先确认(与 ArchPanel dirty 同一思路)
  const [conceptDirty, setConceptDirty] = useState(false);
  // AI/方案/采纳路径统一从这里换概念(顺带清手改标记)
  function applyConcept(c: Concept) {
    setConcept(c);
    setConceptDirty(false);
  }
  const [editing, setEditing] = useState(false);
  const [tendency, setTendency] = useState<Tendency>(project.global_tendency ?? {});
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  // 出方案
  const [spark, setSpark] = useState("");
  const [ideas, setIdeas] = useState<Concept[]>([]);
  const [comparison, setComparison] = useState("");

  // 坐标卡(本书基因)+ 品味镜:定味道锚 → 照镜子确认 → 出方案(全程盯着味道写)
  const [dna, setDna] = useState<StoryDNA>(() => dnaFromProject(project));
  // 新项目(无概念/主题)默认展开坐标卡,引导先定味道;老项目收起
  const [dnaOpen, setDnaOpen] = useState(() => conceptIsEmpty(project.concept) && !project.topic);
  const [dnaOptions, setDnaOptions] = useState<DnaOptions | null>(null);
  const [mirror, setMirror] = useState<MirrorResult | null>(null);
  const [mirrorBusy, setMirrorBusy] = useState(false);

  // 指令式改:输入 → 预览(带 diff)→ 采纳
  const [directive, setDirective] = useState("");
  const [refinePreview, setRefinePreview] = useState<{ concept: Concept; changed: (keyof Concept)[]; note: string } | null>(null);

  // 对话式(记录落库:刷新/切步骤不丢)
  const [chatOpen, setChatOpen] = useState(false);
  const [chatLog, setChatLog] = useState<ChatTurn[]>(project.chat_log ?? []);
  const [chatInput, setChatInput] = useState("");
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  // 简介(沿用旧逻辑,项目已有主题后出现)
  const [synopsis, setSynopsis] = useState(project.synopsis ?? "");
  const [synBusy, setSynBusy] = useState("");
  const [synMsg, setSynMsg] = useState("");
  const [synErr, setSynErr] = useState("");

  const hasConcept = !conceptIsEmpty(concept);
  const savedConcept = !conceptIsEmpty(project.concept) || !!project.topic;

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chatLog, busy]);
  // 坐标卡静态选项(胶囊/模式/味道轴/各模式禁忌清单),失败静默(坐标卡仍可留空生成)
  useEffect(() => { api.dnaOptions().then(setDnaOptions).catch(() => undefined); }, []);

  function flash(m: string) { setMsg(m); setErr(""); }

  // ---------- 出方案 ----------
  async function brainstorm() {
    setBusy("AI 正在扩展故事概念(约1-2分钟,可切到别处,进度看右上角任务)…"); setErr(""); setMsg("");
    try {
      const r = await runJob<{ ideas: Concept[]; comparison?: string }>(
        () => api.inspireAsync(spark, tendency, 4, dnaIsEmpty(dna) ? null : dna),
        { kind: "inspire", onStage: (s) => setBusy(`${s}…`) },
      );
      if (r) { setIdeas(r.ideas); setComparison(r.comparison ?? ""); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  // 品味镜:把坐标卡蒸馏成一段人话 + 矛盾检测,先照镜子核对再烧 token
  async function openMirror() {
    if (dnaIsEmpty(dna)) return;
    setMirrorBusy(true); setErr(""); setMsg("");
    try {
      setMirror(await api.dnaMirror(dna, spark));
    } catch (e) { setErr(errMsg(e)); } finally { setMirrorBusy(false); }
  }
  // 「就照这个味出方案」:收起镜子,直接走出方案(dna 已随 brainstorm 注入)
  function proceedFromMirror() { setMirror(null); brainstorm(); }

  function pickIdea(c: Concept) {
    applyConcept({ ...EMPTY_CONCEPT, ...c });
    setIdeas([]); setEditing(false); setRefinePreview(null);
    flash("已载入该方案为当前概念,可继续用「让 AI 改一处」或手动编辑打磨,满意后「定为本书概念」。");
  }

  // ---------- 指令式改 ----------
  async function runRefine() {
    if (!directive.trim()) return;
    setBusy("AI 正在按你的意见改写概念…"); setErr(""); setMsg("");
    try {
      const r = await runJob<{ concept: Concept; changed: (keyof Concept)[]; note: string }>(
        () => api.refineConceptAsync(concept, directive, tendency, dnaIsEmpty(dna) ? null : dna),
        { kind: "inspire-refine" },
      );
      if (r) setRefinePreview({ concept: r.concept, changed: r.changed, note: r.note });
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  function acceptRefine() {
    if (!refinePreview) return;
    applyConcept(refinePreview.concept);
    setRefinePreview(null); setDirective("");
    flash("已应用改动到当前概念。");
  }

  // ---------- 对话式 ----------
  async function sendChat() { await sendChatText(chatInput); }

  async function sendChatText(text: string) {
    const t = text.trim();
    if (!t) return;
    const nextLog: ChatTurn[] = [...chatLog, { role: "user", content: t }];
    setChatLog(nextLog); setChatInput("");
    setBusy("策划思考中…"); setErr("");
    try {
      const r = await api.chatConcept(nextLog, concept, tendency, dnaIsEmpty(dna) ? null : dna);
      const finalLog: ChatTurn[] = [...nextLog, { role: "assistant", content: r.reply }];
      setChatLog(finalLog);
      if (!conceptIsEmpty(r.concept)) {
        // AI 返回了新概念:有手动编辑未定时先确认,防手改被静默覆盖
        if (conceptDirty) {
          const ok = await confirmDialog({
            title: "用 AI 的新概念覆盖当前编辑?",
            body: "当前概念有手动修改,覆盖后将丢失(聊天记录保留)。",
            confirmText: "覆盖",
            danger: true,
          });
          if (ok) applyConcept(r.concept);
        } else {
          applyConcept(r.concept);
        }
      }
      // 对话记录落库(失败不打扰,下轮再存)
      api.patchProject(project.id, { chat_log: finalLog }).catch(() => undefined);
    } catch (e) {
      setErr(errMsg(e));
      setChatLog(nextLog);  // 回退到用户发言,允许重发
    } finally { setBusy(""); }
  }

  // 灵感激发器:没头绪时让策划直接抛几个方向,而不是反问
  function booster() { sendChatText("我还没想好,先给我 2-3 个具体的故事方向让我挑,每个方向用一两句话说明白。"); }

  // ---------- 定概念 / 保存 ----------
  async function commitConcept() {
    if (!hasConcept) { setErr("概念还是空的,先捏出点内容。"); return; }
    setBusy("写入本书概念…"); setErr(""); setMsg("");
    try {
      await api.patchProject(project.id, {
        concept,
        title: project.title,
        global_tendency: tendency,
        dna: dnaIsEmpty(dna) ? null : dna,
      });
      await onChanged();
      setConceptDirty(false); // 已落库,手改不再算"未保存"
      flash("已定为本书概念,主题已同步。下一步:去「架构」按此概念生成顶层设计。");
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  // ---------- 简介 ----------
  async function genSynopsis() {
    setSynBusy("AI 正在撰写书籍简介(约1分钟)…"); setSynErr(""); setSynMsg("");
    try {
      const r = await runJob<{ synopsis: string }>(
        () => api.synopsisAsync(project.id),
        { kind: `synopsis-${project.id}` },
      );
      if (r) {
        setSynopsis(r.synopsis);
        setSynMsg("简介已生成,可继续修改,点「保存简介」写入项目。");
      }
    } catch (e) { setSynErr(errMsg(e)); } finally { setSynBusy(""); }
  }
  async function saveSynopsis() {
    setSynBusy("保存…"); setSynErr(""); setSynMsg("");
    try {
      await api.patchProject(project.id, { synopsis });
      await onChanged();
      setSynMsg("简介已保存。");
    } catch (e) { setSynErr(errMsg(e)); } finally { setSynBusy(""); }
  }

  return (
    <>
      {/* ===== 当前概念(核心) ===== */}
      <div className="card">
        <div className="card-head">
          <h2 className="grow">当前故事概念</h2>
          {hasConcept && !editing && (
            <button className="btn-sm" onClick={() => setEditing(true)}>手动编辑</button>
          )}
          {editing && (
            <button className="btn-sm" onClick={() => setEditing(false)}>完成编辑</button>
          )}
        </div>
        <div className="card-desc mt-1">
          整本书的地基。架构、大纲、正文都会围绕它展开——这里对了,后面才立得住。
        </div>
        {hasConcept || editing ? (
          editing
            ? <ConceptEditor c={concept} onChange={(c) => { setConcept(c); setConceptDirty(true); }} />
            : <ConceptView c={concept} />
        ) : (
          <div className="muted mt-2">还没有概念。用下面三种方式之一开始。</div>
        )}
        <label className="fl mt-3">全局写作倾向(题材/节奏/结构/基调)</label>
        <div className="hint mb-2">可不选——影响所有生成环节;定概念时一并保存。</div>
        <TendencySelector node="outline" value={tendency} onChange={setTendency} compact />
        <div className="actions mt-3">
          <button className="primary" disabled={!!busy || !hasConcept} onClick={commitConcept}>
            {busy && <span className="spin" />}定为本书概念
          </button>
          {savedConcept && hasConcept && onGotoStep && (
            <button disabled={!!busy} onClick={() => onGotoStep("arch")}>去架构 →</button>
          )}
        </div>
        {busy && <div className="muted mt-2"><span className="spin" />{busy}</div>}
        {msg && <div className="msg-ok mt-2">{msg}</div>}
        {err && <div className="msg-err mt-2">{err}</div>}
      </div>

      {/* ===== 创作坐标(本书基因):治漂的锚,统领下面三条路 ===== */}
      <DnaCard
        dna={dna}
        options={dnaOptions}
        open={dnaOpen}
        busy={!!busy}
        mirrorBusy={mirrorBusy}
        onToggle={() => setDnaOpen((v) => !v)}
        onChange={setDna}
        onMirror={openMirror}
      />
      {mirror && (
        <MirrorView
          mirror={mirror}
          busy={!!busy}
          onProceed={proceedFromMirror}
          onClose={() => setMirror(null)}
        />
      )}

      {/* ===== 路 1:AI 出方案 ===== */}
      <div className="card">
        <h3>① 让 AI 给几个方案</h3>
        <div className="card-desc">
          丢一个碎片(一个画面/一句设定,留空则按倾向自由发挥),AI 给 4 个差异化的完整概念。
        </div>
        <div className="input-row">
          <input type="text" value={spark} onChange={(e) => setSpark(e.target.value)}
            placeholder='如:"一个能听见建筑物说话的拆迁评估员"'
            onKeyDown={(e) => e.key === "Enter" && !busy && brainstorm()} />
          <button className="primary" disabled={!!busy} onClick={brainstorm}>
            {busy && <span className="spin" />}给我灵感
          </button>
        </div>
        {ideas.length > 0 && (
          <div className="mt-3">
            {comparison && (
              <div className="card card-info mt-2">
                <b>这几个方案怎么选</b>
                <div className="card-desc mt-1">{comparison}</div>
              </div>
            )}
            {ideas.map((idea, i) => (
              <div key={i} className="idea-card">
                <div className="idea-head">
                  <h3 className="grow">{idea.logline || "（无标题）"}</h3>
                  <button className="primary btn-sm" disabled={!!busy} onClick={() => pickIdea(idea)}>
                    用这个
                  </button>
                </div>
                <ConceptView c={idea} />
              </div>
            ))}
            <button disabled={!!busy} onClick={brainstorm}>都不满意,换一批</button>
          </div>
        )}
      </div>

      {/* ===== 路 2:指令式局部改 ===== */}
      {hasConcept && (
        <div className="card">
          <h3>② 让 AI 改一处</h3>
          <div className="card-desc">
            对当前概念说一句怎么改——AI 只动相关字段,给你新旧对照,确认才生效。
          </div>
          <div className="input-row">
            <input type="text" value={directive} onChange={(e) => setDirective(e.target.value)}
              placeholder='如:"主角换成女性" / "反转再狠一点" / "背景搬到民国"'
              onKeyDown={(e) => e.key === "Enter" && !busy && directive.trim() && runRefine()} />
            <button className="primary" disabled={!!busy || !directive.trim()} onClick={runRefine}>
              {busy && <span className="spin" />}改
            </button>
          </div>
          {refinePreview && (
            <div className="card card-warn mt-3">
              <b>改动预览</b>
              {refinePreview.note && <div className="card-desc mt-1">{refinePreview.note}</div>}
              {refinePreview.changed.length === 0 ? (
                <div className="msg-ok mt-2">AI 认为无需改动(或改动可忽略)。</div>
              ) : (
                <div className="mt-2">
                  {CONCEPT_FIELDS.filter((f) => refinePreview.changed.includes(f.key)).map((f) => (
                    <div key={f.key} className="refine-diff">
                      <div className="cf-label">{f.label}</div>
                      <div className="diff-old">旧:{concept[f.key].trim() || "（空）"}</div>
                      <div className="diff-new">新:{refinePreview.concept[f.key].trim() || "（空）"}</div>
                    </div>
                  ))}
                </div>
              )}
              <div className="actions mt-2">
                <button className="primary" disabled={!!busy || !refinePreview.changed.length}
                  onClick={acceptRefine}>采纳改动</button>
                <button disabled={!!busy} onClick={() => setRefinePreview(null)}>取消</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ===== 路 3:对话式从零捏 ===== */}
      <div className="card">
        <div className="card-head">
          <h3 className="grow">③ 和 AI 边聊边捏</h3>
          <button className="btn-sm" onClick={() => setChatOpen(!chatOpen)}>
            {chatOpen ? "收起" : "开始对话"}
          </button>
        </div>
        <div className="card-desc">
          没头绪时最好用——一问一答帮你把想法聊清楚,右侧「当前概念」会随对话实时长出来。
        </div>
        {chatOpen && (
          <div className="mt-2">
            <div className="chat-log">
              {chatLog.length === 0 && (
                <div className="muted">对 AI 说说你的模糊想法,比如"想写个关于复仇的故事,但不落俗套"。</div>
              )}
              {chatLog.map((m, i) => (
                <div key={i} className={"chat-msg " + m.role}>
                  <span className="chat-who">{m.role === "user" ? "你" : "策划"}</span>
                  <span className="chat-text">{m.content}</span>
                </div>
              ))}
              {busy && chatOpen && <div className="chat-msg assistant"><span className="chat-who">策划</span><span className="chat-text muted"><span className="spin" />思考中…</span></div>}
              <div ref={chatEndRef} />
            </div>
            <div className="mt-2">
              <button className="btn-sm" disabled={!!busy} onClick={booster}>
                没头绪?让策划先给几个方向
              </button>
            </div>
            <div className="input-row mt-2">
              <input type="text" value={chatInput} onChange={(e) => setChatInput(e.target.value)}
                placeholder="说点什么…" disabled={!!busy}
                onKeyDown={(e) => e.key === "Enter" && !busy && sendChat()} />
              <button className="primary" disabled={!!busy || !chatInput.trim()} onClick={sendChat}>
                发送
              </button>
            </div>
          </div>
        )}
      </div>

      {/* ===== 简介(定概念后) ===== */}
      {savedConcept && (
        <div className="card">
          <h2>书籍简介</h2>
          {synopsis.trim() ? (
            <>
              <div className="card-desc">展示在「阅读全书」目录栏顶部。可随意修改后保存。</div>
              <textarea rows={5} value={synopsis} onChange={(e) => setSynopsis(e.target.value)} />
              <div className="actions mt-3">
                <button className="primary" disabled={!!synBusy} onClick={saveSynopsis}>保存简介</button>
                <button disabled={!!synBusy} onClick={genSynopsis}>
                  {synBusy && <span className="spin" />}重新生成
                </button>
                {synMsg && <span className="msg-ok">{synMsg}</span>}
              </div>
            </>
          ) : (
            <>
              <div className="card-desc">
                让 AI 根据概念{project.genre ? `与题材(${project.genre})` : ""}写一段 150-300 字的网文风简介,吸引人但不剧透结局。
              </div>
              <div className="actions mt-3">
                <button className="primary" disabled={!!synBusy} onClick={genSynopsis}>
                  {synBusy && <span className="spin" />}✨ AI 生成简介
                </button>
                {synMsg && <span className="msg-ok">{synMsg}</span>}
              </div>
            </>
          )}
          {synBusy && <div className="muted mt-2">{synBusy}</div>}
          {synErr && <div className="msg-err mt-2">{synErr}</div>}
        </div>
      )}
    </>
  );
}
