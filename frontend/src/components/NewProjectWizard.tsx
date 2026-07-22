// NewProjectWizard — 新建小说三步向导:① 灵感/概念 → ② 书名(AI 候选) → ③ 倾向与篇幅
// 每步只做一个决策,上一步结果作为下一步的上下文,可随时回退。
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Concept, CONCEPT_FIELDS, conceptIsEmpty, Tendency } from "../api";
import TendencySelector from "./TendencySelector";

const STEPS = [
  { no: 1, label: "灵感" },
  { no: 2, label: "书名" },
  { no: 3, label: "设定" },
];

// 概念摘要卡(向导内复用,空字段隐藏)
function ConceptBrief({ c }: { c: Concept }) {
  return (
    <div className="concept-grid">
      {CONCEPT_FIELDS.filter((f) => c[f.key]?.trim()).map((f) => (
        <div key={f.key} className="concept-field">
          <span className="cf-label">{f.label}</span>
          <span className="cf-value">{c[f.key]}</span>
        </div>
      ))}
    </div>
  );
}

export default function NewProjectWizard() {
  const nav = useNavigate();
  const [step, setStep] = useState(1);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  // 第 1 步:灵感 —— 手填主题,或让 AI 出概念方案选一个
  const [topic, setTopic] = useState("");
  const [ideas, setIdeas] = useState<Concept[]>([]);
  const [concept, setConcept] = useState<Concept | null>(null);

  // 第 2 步:书名 —— 进入时自动拉 AI 候选
  const [title, setTitle] = useState("");
  const [titleIdeas, setTitleIdeas] = useState<string[]>([]);
  const [titleBusy, setTitleBusy] = useState(false);
  const fetchedRef = useRef(false);

  // 第 3 步:倾向 + 篇幅
  const [tendency, setTendency] = useState<Tendency>({});
  const [chapters, setChapters] = useState("10");
  const [words, setWords] = useState("3000");

  const effectiveTopic = topic.trim() || (concept?.logline ?? "").trim();

  async function brainstorm() {
    setBusy("AI 正在扩展故事概念(约 1-2 分钟)…"); setErr("");
    try {
      const r = await api.inspire(topic.trim(), tendency, 4);
      setIdeas(r.ideas);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  function pickIdea(c: Concept) {
    setConcept(c);
    setIdeas([]);
    if (!topic.trim()) setTopic(c.logline ?? "");
  }

  async function fetchTitles() {
    setTitleBusy(true); setErr("");
    try {
      const r = await api.suggestTitle(effectiveTopic, (tendency.genre as string) ?? "", concept);
      setTitleIdeas(r.titles);
    } catch (e) { setErr(String(e)); } finally { setTitleBusy(false); }
  }

  // 进入第 2 步时自动拉一次候选书名(仅第一次)
  useEffect(() => {
    if (step === 2 && !fetchedRef.current) {
      fetchedRef.current = true;
      fetchTitles();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  async function create() {
    if (!title.trim()) { setErr("请填写或选择一个书名"); setStep(2); return; }
    const chNum = chapters.trim() === "" ? 10 : Number(chapters);
    if (!Number.isInteger(chNum) || chNum < 1 || chNum > 2000) {
      setErr("目标章节数需为 1-2000 的整数"); return;
    }
    const wNum = words.trim() === "" ? 3000 : Number(words);
    if (!Number.isInteger(wNum) || wNum < 200 || wNum > 20000) {
      setErr("每章目标字数需为 200-20000 的整数"); return;
    }
    setBusy("创建项目…"); setErr("");
    try {
      const p = await api.createProject({
        title: title.trim(),
        topic: effectiveTopic,
        genre: (tendency.genre as string) ?? "",
        target_chapters: chNum,
        target_words_per_chapter: wNum,
        global_tendency: tendency,
        concept: concept && !conceptIsEmpty(concept) ? concept : undefined,
      });
      nav(`/project/${p.id}`);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  return (
    <div className="card wizard">
      {/* 步骤条 */}
      <div className="wiz-steps">
        {STEPS.map((s) => (
          <div key={s.no}
            className={"wiz-step" + (step === s.no ? " on" : "") + (step > s.no ? " done" : "")}
            onClick={() => s.no < step && setStep(s.no)}>
            <span className="no">{step > s.no ? "✓" : s.no}</span>{s.label}
          </div>
        ))}
      </div>

      {/* ===== 第 1 步:灵感 ===== */}
      {step === 1 && (
        <>
          <h2>这本书写什么?</h2>
          <div className="card-desc">
            一句话说说你的想法——一个画面、一个设定都行。没想法就直接让 AI 出方案,或先跳过。
          </div>
          <textarea rows={2} value={topic} onChange={(e) => setTopic(e.target.value)}
            placeholder="如:落魄镖师接下一趟险镖,半路开箱验货时发现镖箱里藏着个大活人…" />
          <div className="actions mt-2">
            <button className="primary" disabled={!!busy} onClick={brainstorm}>
              {busy && <span className="spin" />}✨ 让 AI 出 4 个方案
            </button>
            <button disabled={!!busy} onClick={() => setStep(2)}>
              {effectiveTopic ? "就用这个想法,下一步 →" : "先跳过,直接起名 →"}
            </button>
          </div>
          {busy && <div className="muted mt-2"><span className="spin" />{busy}</div>}
          {ideas.length > 0 && (
            <div className="mt-3">
              <div className="hint mb-2">选一个方案作为本书概念(进项目后还能继续打磨):</div>
              {ideas.map((idea, i) => (
                <div key={i} className="idea-card">
                  <div className="idea-head">
                    <h3 className="grow">{idea.logline || "（无标题）"}</h3>
                    <button className="primary btn-sm" onClick={() => { pickIdea(idea); }}>
                      用这个
                    </button>
                  </div>
                  <ConceptBrief c={idea} />
                </div>
              ))}
              <button disabled={!!busy} onClick={brainstorm}>都不满意,换一批</button>
            </div>
          )}
          {concept && !ideas.length && (
            <div className="notice notice-info mt-3">
              <b>已选定概念:</b>
              <ConceptBrief c={concept} />
              <div className="actions mt-2">
                <button className="primary" onClick={() => setStep(2)}>满意,去起书名 →</button>
                <button onClick={() => setConcept(null)}>不要了,重选</button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ===== 第 2 步:书名 ===== */}
      {step === 2 && (
        <>
          <h2>给它起个名字</h2>
          <div className="card-desc">
            {effectiveTopic ? "AI 根据你的灵感起了几个候选,点一个即选中,也可以自己写。" : "还没有主题,AI 自由发挥了几个候选;也可以自己写。"}
            随时可改,不是一锤定音。
          </div>
          {titleBusy && <div className="muted mt-2"><span className="spin" />AI 正在起名…</div>}
          {titleIdeas.length > 0 && (
            <div className="title-chips mt-2">
              {titleIdeas.map((t) => (
                <button key={t} type="button"
                  className={"title-chip" + (title === t ? " on" : "")}
                  onClick={() => setTitle(t)}>{t}</button>
              ))}
            </div>
          )}
          <div className="input-row mt-3">
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder="或自己输入书名" maxLength={100} />
            <button className="btn-sm" disabled={titleBusy} onClick={fetchTitles}>
              {titleBusy && <span className="spin" />}换一批
            </button>
          </div>
          <div className="actions mt-3">
            <button onClick={() => setStep(1)}>← 上一步</button>
            <button className="primary" disabled={!title.trim()} onClick={() => setStep(3)}>
              下一步 →
            </button>
            {!title.trim() && <span className="hint">选一个候选或自己填一个书名</span>}
          </div>
        </>
      )}

      {/* ===== 第 3 步:倾向 + 篇幅 ===== */}
      {step === 3 && (
        <>
          <h2>定一下基调和篇幅</h2>
          <div className="card-desc">
            《{title}》{effectiveTopic ? ` · ${effectiveTopic}` : ""}
          </div>
          <label className="fl mt-2">全局写作倾向(整本书的默认基调,可不选,单次生成时还能临时调整)</label>
          <TendencySelector node="outline" value={tendency} onChange={setTendency} />
          <div className="row mt-3">
            <div>
              <label className="fl">目标章节数</label>
              <input type="number" value={chapters} min={1} max={2000}
                onChange={(e) => setChapters(e.target.value)} />
              <div className="hint">短篇 10-30,长篇 100+,后续可改</div>
            </div>
            <div>
              <label className="fl">每章目标字数</label>
              <input type="number" value={words} min={200} max={20000} step={500}
                onChange={(e) => setWords(e.target.value)} />
              <div className="hint">网文常见 2000-4000 字/章</div>
            </div>
          </div>
          <div className="actions mt-4">
            <button disabled={!!busy} onClick={() => setStep(2)}>← 上一步</button>
            <button className="primary" disabled={!!busy} onClick={create}>
              {busy && <span className="spin" />}创建项目,开始创作
            </button>
          </div>
        </>
      )}

      {err && <div className="msg-err mt-2">{err}</div>}
    </div>
  );
}
