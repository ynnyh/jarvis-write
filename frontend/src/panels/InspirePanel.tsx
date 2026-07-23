// 灵感工作区:碎片/想法 → 结构化「故事概念」→ 定为本书概念
// 三条路并存:AI 出方案(结构化) / 指令式局部改 / 对话式从零捏
import { useEffect, useRef, useState } from "react";
import {
  api, ChatTurn, Concept, CONCEPT_FIELDS, EMPTY_CONCEPT, conceptIsEmpty,
  Project, Tendency,
} from "../api";
import TendencySelector from "../components/TendencySelector";
import { useJob } from "../ui/useJob";
import type { Step } from "../pages/ProjectPage";

interface Props { project: Project; onChanged: () => Promise<void>; onGotoStep?: (step: Step) => void; }

// 从项目已存概念/主题恢复当前草稿:有 concept 用 concept,否则把 topic 灌进 logline
function conceptFromProject(p: Project): Concept {
  if (p.concept && !conceptIsEmpty(p.concept)) return { ...EMPTY_CONCEPT, ...p.concept };
  return { ...EMPTY_CONCEPT, logline: p.topic ?? "" };
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

export default function InspirePanel({ project, onChanged, onGotoStep }: Props) {
  const { run: runJob } = useJob();
  // 当前正在打磨的概念草稿(三条路都往它上收敛)
  const [concept, setConcept] = useState<Concept>(() => conceptFromProject(project));
  const [editing, setEditing] = useState(false);
  const [tendency, setTendency] = useState<Tendency>(project.global_tendency ?? {});
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  // 出方案
  const [spark, setSpark] = useState("");
  const [ideas, setIdeas] = useState<Concept[]>([]);

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

  function flash(m: string) { setMsg(m); setErr(""); }

  // ---------- 出方案 ----------
  async function brainstorm() {
    setBusy("AI 正在扩展故事概念(约1-2分钟,可切到别处,进度看右上角任务)…"); setErr(""); setMsg("");
    try {
      const r = await runJob<{ ideas: Concept[] }>(
        () => api.inspireAsync(spark, tendency, 4),
        { kind: "inspire", onStage: (s) => setBusy(`${s}…`) },
      );
      if (r) setIdeas(r.ideas);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  function pickIdea(c: Concept) {
    setConcept({ ...EMPTY_CONCEPT, ...c });
    setIdeas([]); setEditing(false); setRefinePreview(null);
    flash("已载入该方案为当前概念,可继续用「让 AI 改一处」或手动编辑打磨,满意后「定为本书概念」。");
  }

  // ---------- 指令式改 ----------
  async function runRefine() {
    if (!directive.trim()) return;
    setBusy("AI 正在按你的意见改写概念…"); setErr(""); setMsg("");
    try {
      const r = await runJob<{ concept: Concept; changed: (keyof Concept)[]; note: string }>(
        () => api.refineConceptAsync(concept, directive, tendency),
        { kind: "inspire-refine" },
      );
      if (r) setRefinePreview({ concept: r.concept, changed: r.changed, note: r.note });
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  function acceptRefine() {
    if (!refinePreview) return;
    setConcept(refinePreview.concept);
    setRefinePreview(null); setDirective("");
    flash("已应用改动到当前概念。");
  }

  // ---------- 对话式 ----------
  async function sendChat() {
    const text = chatInput.trim();
    if (!text) return;
    const nextLog: ChatTurn[] = [...chatLog, { role: "user", content: text }];
    setChatLog(nextLog); setChatInput("");
    setBusy("策划思考中…"); setErr("");
    try {
      const r = await api.chatConcept(nextLog, concept, tendency);
      const finalLog: ChatTurn[] = [...nextLog, { role: "assistant", content: r.reply }];
      setChatLog(finalLog);
      if (!conceptIsEmpty(r.concept)) setConcept(r.concept);
      // 对话记录落库(失败不打扰,下轮再存)
      api.patchProject(project.id, { chat_log: finalLog }).catch(() => undefined);
    } catch (e) {
      setErr(String(e));
      setChatLog(nextLog);  // 回退到用户发言,允许重发
    } finally { setBusy(""); }
  }

  // ---------- 定概念 / 保存 ----------
  async function commitConcept() {
    if (!hasConcept) { setErr("概念还是空的,先捏出点内容。"); return; }
    setBusy("写入本书概念…"); setErr(""); setMsg("");
    try {
      await api.patchProject(project.id, {
        concept,
        title: project.title,
        global_tendency: tendency,
      });
      await onChanged();
      flash("已定为本书概念,主题已同步。下一步:去「架构」按此概念生成顶层设计。");
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
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
    } catch (e) { setSynErr(String(e)); } finally { setSynBusy(""); }
  }
  async function saveSynopsis() {
    setSynBusy("保存…"); setSynErr(""); setSynMsg("");
    try {
      await api.patchProject(project.id, { synopsis });
      await onChanged();
      setSynMsg("简介已保存。");
    } catch (e) { setSynErr(String(e)); } finally { setSynBusy(""); }
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
            ? <ConceptEditor c={concept} onChange={setConcept} />
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
