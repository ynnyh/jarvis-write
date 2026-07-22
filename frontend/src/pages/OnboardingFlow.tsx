// 创作起步流:建书即建草稿项目,五步走到点火生成。
// /new → 静默建草稿 → /new/:id/idea → tone → title → scale → launch → 工作台
// 每步选择实时 PATCH 落库:刷新不丢、中途退出列表页可"继续创建"。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  api, ChatTurn, Chip, Concept, CONCEPT_FIELDS, conceptIsEmpty, Dimension,
  EMPTY_CONCEPT, Project, Tendency,
} from "../api";
import { useJob } from "../ui/useJob";
import { toast } from "../ui/Toaster";

export type SetupStep = "idea" | "tone" | "title" | "scale" | "launch";
const STEP_ORDER: SetupStep[] = ["idea", "tone", "title", "scale", "launch"];
const STEP_LABEL: Record<SetupStep, string> = {
  idea: "想法", tone: "基调", title: "书名", scale: "篇幅", launch: "蓝图",
};

// 篇幅预设卡
const SCALE_PRESETS = [
  { key: "short", label: "短篇", chapters: 20, words: 3000, desc: "约 6 万字,适合练手或中短故事" },
  { key: "mid", label: "中篇", chapters: 60, words: 3000, desc: "约 18 万字,完整起承转合" },
  { key: "long", label: "长篇", chapters: 150, words: 3000, desc: "约 45 万字,网文连载体量" },
];

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

export default function OnboardingFlow() {
  const { id, step: stepParam } = useParams();
  const nav = useNavigate();
  const { run: runJob } = useJob();
  const pid = id ? Number(id) : null;
  const step: SetupStep = STEP_ORDER.includes(stepParam as SetupStep)
    ? (stepParam as SetupStep) : "idea";

  const [project, setProject] = useState<Project | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  // 想法步
  const [entry, setEntry] = useState<"spark" | "genre" | "chat" | null>(null);
  const [spark, setSpark] = useState("");
  const [ideas, setIdeas] = useState<Concept[]>([]);
  const [genreDim, setGenreDim] = useState<Dimension | null>(null);
  const [pickedGenreCard, setPickedGenreCard] = useState<Chip | null>(null);
  const [chatInput, setChatInput] = useState("");
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  // 基调步
  const [inferBusy, setInferBusy] = useState(false);
  const [genreSuggests, setGenreSuggests] = useState<Chip[]>([]);
  const [suggestPage, setSuggestPage] = useState(0);
  const [customGenre, setCustomGenre] = useState("");

  // 书名步
  const [titleIdeas, setTitleIdeas] = useState<string[]>([]);
  const [titleBusy, setTitleBusy] = useState(false);
  const [titleInput, setTitleInput] = useState("");
  const titleFetchedFor = useRef("");

  // 篇幅步
  const [chapters, setChapters] = useState("");
  const [words, setWords] = useState("");

  const concept: Concept = useMemo(
    () => ({ ...EMPTY_CONCEPT, ...(project?.concept ?? {}) }),
    [project],
  );
  const tendency: Tendency = project?.global_tendency ?? {};
  const conceptText = [
    concept.logline, concept.hook, concept.protagonist, concept.setting,
  ].filter((s) => s?.trim()).join("\n") || project?.topic || "";

  // ---------- 建草稿 / 载入 ----------
  useEffect(() => {
    if (pid !== null) {
      api.getProject(pid).then((p) => {
        setProject(p);
        setTitleInput(p.title === "未命名新书" ? "" : p.title);
        setChapters(String(p.target_chapters));
        setWords(String(p.target_words_per_chapter));
      }).catch((e) => setErr(String(e)));
      return;
    }
    // /new 无 id:静默创建草稿项目,replace 进第一步
    api.createProject({ title: "未命名新书", setup_state: "idea" })
      .then((p) => nav(`/new/${p.id}/idea`, { replace: true }))
      .catch((e) => setErr(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  // 流派卡片墙数据(想法步/基调步共用)
  useEffect(() => {
    api.tendencyCatalog("outline").then((cat) => {
      setGenreDim(cat.dimensions.find((d) => d.key === "genre") ?? null);
    }).catch(() => undefined);
  }, []);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [project?.chat_log, busy]);

  const patch = useCallback(async (updates: Partial<Project> & { setup_state?: string }) => {
    if (pid === null) return null;
    const p = await api.patchProject(pid, updates);
    setProject(p);
    return p;
  }, [pid]);

  async function goto(next: SetupStep) {
    try { await patch({ setup_state: next }); } catch { /* 步进不因保存失败而卡死 */ }
    nav(`/new/${pid}/${next}`);
  }

  // ---------- 第 1 步:想法 ----------
  async function brainstorm(sparkText: string, genreChip?: Chip) {
    setBusy("AI 正在按你的方向出 4 个故事方案(约 1-2 分钟,可切走,进度看右上角)…");
    setErr(""); setIdeas([]);
    try {
      const t: Tendency = genreChip ? { ...tendency, genre: genreChip.label } : tendency;
      const r = await runJob<{ ideas: Concept[] }>(
        () => api.inspireAsync(sparkText, t, 4),
        { kind: "inspire", onStage: (s) => setBusy(`${s}…`) },
      );
      if (r) setIdeas(r.ideas);
      if (genreChip) await patch({ global_tendency: { ...tendency, genre: genreChip.label } });
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function pickIdea(c: Concept) {
    setIdeas([]);
    try {
      await patch({ concept: c });
      toast.ok("已选定故事概念", "进入工作台后还能继续打磨");
    } catch (e) { setErr(String(e)); }
  }

  async function sendChat() {
    const text = chatInput.trim();
    if (!text || !project) return;
    const log: ChatTurn[] = [...(project.chat_log ?? []), { role: "user", content: text }];
    setChatInput("");
    setProject({ ...project, chat_log: log });
    setBusy("策划思考中…"); setErr("");
    try {
      const r = await api.chatConcept(log, conceptIsEmpty(concept) ? null : concept, tendency);
      const newLog: ChatTurn[] = [...log, { role: "assistant", content: r.reply }];
      await patch({
        chat_log: newLog,
        ...(conceptIsEmpty(r.concept) ? {} : { concept: r.concept }),
      });
    } catch (e) {
      setErr(String(e));
      await patch({ chat_log: log }).catch(() => undefined);
    } finally { setBusy(""); }
  }

  // ---------- 第 2 步:基调(AI 预填) ----------
  useEffect(() => {
    if (step !== "tone" || !conceptText.trim() || tendency.genre) return;
    setInferBusy(true);
    api.genreInfer(conceptText).then(async (r) => {
      setGenreSuggests(r.suggestions.map((s) => ({ directive: "", ...s })));
      if (r.genre) await patch({ global_tendency: { ...tendency, genre: r.genre } });
    }).catch(() => undefined).finally(() => setInferBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  const allGenreChips = genreDim?.chips ?? [];
  const shownSuggests = genreSuggests.length
    ? genreSuggests
    : allGenreChips.slice(suggestPage * 8, suggestPage * 8 + 8);

  async function setGenre(label: string) {
    await patch({ global_tendency: { ...tendency, genre: label }, genre: label });
  }
  async function setDim(key: string, value: string | string[]) {
    await patch({ global_tendency: { ...tendency, [key]: value } });
  }

  // ---------- 第 3 步:书名 ----------
  useEffect(() => {
    if (step !== "title") return;
    const ctxKey = conceptText + "|" + (tendency.genre ?? "");
    if (titleFetchedFor.current === ctxKey && titleIdeas.length) return;
    titleFetchedFor.current = ctxKey;
    fetchTitles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  async function fetchTitles() {
    setTitleBusy(true); setErr("");
    try {
      const r = await api.suggestTitle(
        project?.topic ?? "", (tendency.genre as string) ?? "",
        conceptIsEmpty(concept) ? null : concept,
      );
      setTitleIdeas(r.titles);
    } catch (e) { setErr(String(e)); } finally { setTitleBusy(false); }
  }

  async function confirmTitle(next: SetupStep) {
    const t = titleInput.trim();
    if (!t) { setErr("先选一个候选或自己写一个书名"); return; }
    await patch({ title: t });
    await goto(next);
  }

  // ---------- 第 4 步:篇幅 ----------
  async function pickScale(preset: typeof SCALE_PRESETS[number]) {
    setChapters(String(preset.chapters)); setWords(String(preset.words));
    await patch({ target_chapters: preset.chapters, target_words_per_chapter: preset.words });
  }

  async function confirmScale() {
    const ch = Number(chapters), w = Number(words);
    if (!Number.isInteger(ch) || ch < 1 || ch > 2000) { setErr("章节数需为 1-2000 的整数"); return; }
    if (!Number.isInteger(w) || w < 200 || w > 20000) { setErr("每章字数需为 200-20000 的整数"); return; }
    await patch({ target_chapters: ch, target_words_per_chapter: w });
    await goto("launch");
  }

  // ---------- 第 5 步:点火 ----------
  async function launch(withArch: boolean) {
    if (pid === null) return;
    try {
      await patch({ setup_state: "" });  // 起步完成
      if (withArch) {
        const { job_id } = await api.generateArchitectureAsync(pid, {});
        toast.ok("架构生成已启动", "进入工作台即可看到进度");
        // 任务进任务中心,工作台架构页会经 runningJobs 逻辑或任务中心感知
        void job_id;
      }
      nav(`/project/${pid}/arch`);
    } catch (e) { setErr(String(e)); }
  }

  async function abandon() {
    if (pid === null || !project) return;
    try {
      await api.deleteProject(pid);
      toast.ok("已放弃创建");
      nav("/");
    } catch (e) { setErr(String(e)); }
  }

  if (!project) return <div className="muted">{err || "正在创建草稿…"}</div>;

  const stepIdx = STEP_ORDER.indexOf(step);
  const hasConcept = !conceptIsEmpty(concept);
  const chatLog = project.chat_log ?? [];

  return (
    <div className="onboard">
      {/* ===== 左:主流程 ===== */}
      <div className="onboard-main">
        <div className="wiz-steps">
          {STEP_ORDER.map((s, i) => (
            <div key={s}
              className={"wiz-step" + (s === step ? " on" : "") + (i < stepIdx ? " done" : "")}
              onClick={() => i < stepIdx && nav(`/new/${pid}/${s}`)}>
              <span className="no">{i < stepIdx ? "✓" : i + 1}</span>{STEP_LABEL[s]}
            </div>
          ))}
          <div className="grow" />
          <button className="btn-sm" onClick={abandon}>放弃创建</button>
        </div>

        {/* ---------- 想法 ---------- */}
        {step === "idea" && (
          <div className="card">
            <h2>这本书写什么?</h2>
            {!entry && !hasConcept && (
              <div className="entry-cards">
                <div className="entry-card" onClick={() => setEntry("spark")}>
                  <h3>💡 我有个想法</h3>
                  <p>一句话、一个画面、一个设定,AI 帮你扩成完整故事概念。</p>
                </div>
                <div className="entry-card" onClick={() => setEntry("genre")}>
                  <h3>📚 我知道想写什么类型</h3>
                  <p>赘婿流、无限流、克苏鲁…选个流派,AI 按套路出方案。</p>
                </div>
                <div className="entry-card" onClick={() => setEntry("chat")}>
                  <h3>💬 和 AI 聊聊</h3>
                  <p>完全没头绪?边聊边捏,概念会随对话慢慢成形。</p>
                </div>
              </div>
            )}

            {entry === "spark" && (
              <div className="mt-3">
                <textarea rows={2} value={spark} onChange={(e) => setSpark(e.target.value)}
                  placeholder="如:落魄镖师接下一趟险镖,半路开箱验货时发现镖箱里藏着个大活人…" />
                <div className="actions mt-2">
                  <button className="primary" disabled={!!busy || !spark.trim()}
                    onClick={() => brainstorm(spark)}>
                    {busy && <span className="spin" />}✨ 出 4 个方案
                  </button>
                  <button disabled={!!busy} onClick={() => setEntry(null)}>← 换个方式</button>
                </div>
              </div>
            )}

            {entry === "genre" && genreDim && (
              <div className="mt-3">
                {(genreDim.categories ?? []).map((cat) => {
                  const chips = allGenreChips.filter((c) => c.category === cat.key);
                  if (!chips.length) return null;
                  return (
                    <div key={cat.key} className="genre-group">
                      <div className="genre-cat">{cat.label}</div>
                      <div className="genre-cards">
                        {chips.map((c) => (
                          <div key={c.label}
                            className={"genre-card" + (pickedGenreCard?.label === c.label ? " on" : "")}
                            onClick={() => setPickedGenreCard(c)}>
                            <b>{c.label}</b>
                            {c.desc && <span>{c.desc}</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
                <div className="actions mt-3">
                  <button className="primary" disabled={!!busy || !pickedGenreCard}
                    onClick={() => brainstorm(
                      `按「${pickedGenreCard!.label}」的套路来`, pickedGenreCard!)}>
                    {busy && <span className="spin" />}✨ 按这个流派出 4 个方案
                  </button>
                  <button disabled={!!busy} onClick={() => setEntry(null)}>← 换个方式</button>
                </div>
              </div>
            )}

            {entry === "chat" && (
              <div className="mt-3">
                <div className="chat-log">
                  {chatLog.length === 0 && (
                    <div className="muted">说说你的模糊想法,比如"想写个关于复仇的故事,但不落俗套"。</div>
                  )}
                  {chatLog.map((m, i) => (
                    <div key={i} className={"chat-msg " + m.role}>
                      <span className="chat-who">{m.role === "user" ? "你" : "策划"}</span>
                      <span className="chat-text">{m.content}</span>
                    </div>
                  ))}
                  {busy && <div className="chat-msg assistant"><span className="chat-who">策划</span><span className="chat-text muted"><span className="spin" />思考中…</span></div>}
                  <div ref={chatEndRef} />
                </div>
                <div className="input-row mt-2">
                  <input type="text" value={chatInput} onChange={(e) => setChatInput(e.target.value)}
                    placeholder="说点什么…" disabled={!!busy}
                    onKeyDown={(e) => e.key === "Enter" && !busy && sendChat()} />
                  <button className="primary" disabled={!!busy || !chatInput.trim()} onClick={sendChat}>发送</button>
                </div>
                <div className="actions mt-2">
                  <button disabled={!!busy} onClick={() => setEntry(null)}>← 换个方式</button>
                </div>
              </div>
            )}

            {busy && entry !== "chat" && <div className="muted mt-3"><span className="spin" />{busy}</div>}

            {ideas.length > 0 && (
              <div className="mt-3">
                <div className="hint mb-2">选一个作为本书概念(之后随时能改):</div>
                {ideas.map((idea, i) => (
                  <div key={i} className="idea-card">
                    <div className="idea-head">
                      <h3 className="grow">{idea.logline || "(无标题)"}</h3>
                      <button className="primary btn-sm" onClick={() => pickIdea(idea)}>用这个</button>
                    </div>
                    <ConceptBrief c={idea} />
                  </div>
                ))}
                <button disabled={!!busy}
                  onClick={() => entry === "genre" && pickedGenreCard
                    ? brainstorm(`按「${pickedGenreCard.label}」的套路来`, pickedGenreCard)
                    : brainstorm(spark)}>
                  都不满意,换一批
                </button>
              </div>
            )}

            <div className="actions mt-4 onboard-nav">
              <button className="primary" disabled={!hasConcept} onClick={() => goto("tone")}>
                {hasConcept ? "概念可以了,下一步 →" : "先捏出一个概念"}
              </button>
              {!hasConcept && <button onClick={() => goto("tone")}>跳过,以后再想</button>}
            </div>
          </div>
        )}

        {/* ---------- 基调 ---------- */}
        {step === "tone" && (
          <div className="card">
            <h2>定一下题材和基调</h2>
            <div className="card-desc">
              {inferBusy ? "AI 正在根据你的概念推断题材…" : tendency.genre
                ? `AI 推断这本书是「${tendency.genre}」,不对就点别的或自己写。`
                : "选一个题材流派,或自己写。"}
            </div>
            <div className="title-chips mt-2">
              {!!tendency.genre && !shownSuggests.some((s) => s.label === tendency.genre) && (
                <button type="button" className="title-chip on">{tendency.genre as string}</button>
              )}
              {shownSuggests.map((s) => (
                <button key={s.label} type="button"
                  className={"title-chip" + (tendency.genre === s.label ? " on" : "")}
                  title={s.desc || undefined}
                  onClick={() => setGenre(s.label)}>{s.label}</button>
              ))}
              <button type="button" className="title-chip"
                onClick={() => { setGenreSuggests([]); setSuggestPage((p) => (p + 1) % Math.max(1, Math.ceil(allGenreChips.length / 8))); }}>
                ↻ 换一批
              </button>
            </div>
            <div className="input-row mt-2">
              <input type="text" value={customGenre} onChange={(e) => setCustomGenre(e.target.value)}
                placeholder="都不合适?直接写你的题材,如:民国武侠"
                onKeyDown={(e) => e.key === "Enter" && customGenre.trim() && setGenre(customGenre.trim())} />
              <button className="btn-sm" disabled={!customGenre.trim()}
                onClick={() => setGenre(customGenre.trim())}>就用它</button>
            </div>

            {genreDim && (
              <div className="mt-4">
                <label className="fl">节奏 / 结构 / 基调(可不选,AI 会均衡处理)</label>
                <ToneDims tendency={tendency} onSet={setDim} />
              </div>
            )}

            <div className="actions mt-4 onboard-nav">
              <button onClick={() => nav(`/new/${pid}/idea`)}>← 上一步</button>
              <button className="primary" onClick={() => goto("title")}>下一步 →</button>
            </div>
          </div>
        )}

        {/* ---------- 书名 ---------- */}
        {step === "title" && (
          <div className="card">
            <h2>给它起个名字</h2>
            <div className="card-desc">AI 根据概念和题材起的候选,点一个即选中;随时可改,不是一锤定音。</div>
            {titleBusy && <div className="muted mt-2"><span className="spin" />AI 正在起名…</div>}
            {titleIdeas.length > 0 && (
              <div className="title-chips mt-2">
                {titleIdeas.map((t) => (
                  <button key={t} type="button"
                    className={"title-chip" + (titleInput === t ? " on" : "")}
                    onClick={() => setTitleInput(t)}>{t}</button>
                ))}
              </div>
            )}
            <div className="input-row mt-3">
              <input type="text" value={titleInput} onChange={(e) => setTitleInput(e.target.value)}
                placeholder="或自己输入书名" maxLength={100} />
              <button className="btn-sm" disabled={titleBusy} onClick={fetchTitles}>
                {titleBusy && <span className="spin" />}换一批
              </button>
            </div>
            <div className="actions mt-4 onboard-nav">
              <button onClick={() => nav(`/new/${pid}/tone`)}>← 上一步</button>
              <button className="primary" disabled={!titleInput.trim()} onClick={() => confirmTitle("scale")}>
                下一步 →
              </button>
            </div>
          </div>
        )}

        {/* ---------- 篇幅 ---------- */}
        {step === "scale" && (
          <div className="card">
            <h2>写多长?</h2>
            <div className="card-desc">先选个预设,数字之后随时能改。</div>
            <div className="scale-cards mt-2">
              {SCALE_PRESETS.map((p) => (
                <div key={p.key}
                  className={"scale-card" + (Number(chapters) === p.chapters ? " on" : "")}
                  onClick={() => pickScale(p)}>
                  <b>{p.label}</b>
                  <div className="scale-num">{p.chapters} 章 × {p.words} 字</div>
                  <div className="hint">{p.desc}</div>
                </div>
              ))}
            </div>
            <div className="row mt-3">
              <div>
                <label className="fl">目标章节数</label>
                <input type="number" value={chapters} min={1} max={2000}
                  onChange={(e) => setChapters(e.target.value)} />
              </div>
              <div>
                <label className="fl">每章目标字数</label>
                <input type="number" value={words} min={200} max={20000} step={500}
                  onChange={(e) => setWords(e.target.value)} />
              </div>
            </div>
            <div className="actions mt-4 onboard-nav">
              <button onClick={() => nav(`/new/${pid}/title`)}>← 上一步</button>
              <button className="primary" onClick={confirmScale}>下一步 →</button>
            </div>
          </div>
        )}

        {/* ---------- 点火 ---------- */}
        {step === "launch" && (
          <div className="card">
            <h2>《{project.title}》准备好了</h2>
            <div className="card-desc">
              要不要现在就让 AI 生成全书架构(核心种子/角色/世界观/情节,约 3-5 分钟)?
              生成在后台跑,你进工作台就能看到进度,到了直接审阅。
            </div>
            <div className="actions mt-4 onboard-nav">
              <button onClick={() => nav(`/new/${pid}/scale`)}>← 上一步</button>
              <button className="primary" onClick={() => launch(true)}>🔥 生成架构,进入工作台</button>
              <button onClick={() => launch(false)}>先不生成,直接进工作台</button>
            </div>
          </div>
        )}

        {err && <div className="msg-err mt-2">{err}</div>}
      </div>

      {/* ===== 右:本书档案 ===== */}
      <div className="onboard-side">
        <div className="dossier">
          <div className="dossier-cover">
            <span>{project.title === "未命名新书" && titleInput ? titleInput : project.title}</span>
          </div>
          <div className="dossier-rows">
            <div className={"dossier-row" + (hasConcept ? " ok" : "")}>
              <span className="dr-k">概念</span>
              <span className="dr-v">{hasConcept ? (concept.logline || "已定") : "未定"}</span>
            </div>
            <div className={"dossier-row" + (tendency.genre ? " ok" : "")}>
              <span className="dr-k">题材</span>
              <span className="dr-v">{(tendency.genre as string) || "未定"}</span>
            </div>
            <div className={"dossier-row" + (project.title !== "未命名新书" ? " ok" : "")}>
              <span className="dr-k">书名</span>
              <span className="dr-v">{project.title === "未命名新书" ? "未定" : project.title}</span>
            </div>
            <div className="dossier-row ok">
              <span className="dr-k">篇幅</span>
              <span className="dr-v">{project.target_chapters} 章 × {project.target_words_per_chapter} 字</span>
            </div>
          </div>
          {hasConcept && (
            <div className="mt-3">
              <ConceptBrief c={concept} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// 节奏/结构/基调三个通用维度的 chips(从目录动态取)
function ToneDims({ tendency, onSet }: {
  tendency: Tendency;
  onSet: (key: string, value: string | string[]) => void;
}) {
  const [dims, setDims] = useState<Dimension[]>([]);
  useEffect(() => {
    api.tendencyCatalog("outline").then((cat) => {
      setDims(cat.dimensions.filter((d) => ["pace", "structure", "tone"].includes(d.key)));
    }).catch(() => undefined);
  }, []);
  return (
    <>
      {dims.map((dim) => (
        <div key={dim.key} className="mt-2">
          <div className="hint">{dim.label}</div>
          <div className="title-chips mt-1">
            {dim.chips.map((c) => {
              const cur = tendency[dim.key];
              const on = dim.select === "multi"
                ? Array.isArray(cur) && cur.includes(c.label)
                : cur === c.label;
              return (
                <button key={c.label} type="button"
                  className={"title-chip sm" + (on ? " on" : "")}
                  onClick={() => {
                    if (dim.select === "multi") {
                      const arr = Array.isArray(cur) ? [...cur] : [];
                      onSet(dim.key, on ? arr.filter((x) => x !== c.label) : [...arr, c.label]);
                    } else {
                      onSet(dim.key, on ? "" : c.label);
                    }
                  }}>{c.label}</button>
              );
            })}
          </div>
        </div>
      ))}
    </>
  );
}
