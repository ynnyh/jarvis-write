// 创作起步流:建书即建草稿项目,三幕式向导走到点火生成。
// 第一幕 一屏一问:idea → concept → genre → tone → title → scale
// 第二幕 确认墙:confirm(全部设定卡片墙,可回改,下游标"可能受影响")
// 第三幕 点火流水线:launch(架构生成 → 蓝图生成,失败只重跑该步)
// /new → 静默建草稿 → /new/:id/idea → … → /new/:id/launch → 工作台
// 每屏选择实时 PATCH 落库(刷新不丢、列表页可"继续创建");
// localStorage 缓存候选内容,刷新后回到当前屏接着选。
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, LayoutGroup, MotionConfig, motion } from "motion/react";
import { api, conceptIsEmpty, CONCEPT_FIELDS, ShapeSuggestion } from "../api";
import { CandidateCards } from "../ui/CandidateCards";
import { ThinkingText } from "../ui/ThinkingText";
import { conceptSig, conceptStaleText, isStale, titleSig as calcTitleSig, titleStaleText } from "./wizSig";
import { SetupStep, STEP_ORDER, STEP_LABEL } from "./onboarding/steps";
import { SCALE_PRESETS, THINK_CONCEPT, THINK_TITLE } from "./onboarding/presets";
import { composeRandomSeed } from "./onboarding/randomSeeds";
import { ConceptBrief, conceptKey } from "./onboarding/ConceptBrief";
import { ToneDims } from "./onboarding/ToneDims";
import { useOnboarding } from "./onboarding/useOnboarding";

// SetupStep 原在本文件定义,保留 re-export 以兼容潜在外部引用(现仅本文件内部使用)
export type { SetupStep };

// 方向卡屏轻偏好预置(P0-B 偏好前移):都是高频人话选项,全部可不选;
// tone/elements 的 directive 已在后端 tag_presets 里,这里只放 label 让用户点
const PREF_TONES = ["热血", "悬疑", "治愈", "甜", "暗黑", "爽"];
const PREF_ELEMENTS = ["逆袭", "马甲", "身份错位", "成长蜕变", "救赎", "群像", "破镜重圆", "契约关系"];
const PREF_PROTAGONISTS = ["小人物", "行内老手", "边缘人", "天才", "普通人"];
// 排斥项对着题材边界最常拦的套路来(用户明确点了才允许,没点就是「不要」)
const PREF_AVOIDS = ["系统", "重生", "穿越", "觉醒", "异能"];

// AI 推荐的阅读手感:进入基调步时自动预填一次(仅当用户尚未自选),并显示依据横幅
function ToneAutoApply({ shapeSug, setDim }: {
  shapeSug: ShapeSuggestion | null;
  setDim: (key: string, value: string | string[]) => void;
}) {
  const appliedRef = useRef(false);
  useEffect(() => {
    if (!shapeSug || appliedRef.current) return;
    if (shapeSug.tone.length) setDim("tone", shapeSug.tone);
    if (shapeSug.elements.length) setDim("elements", shapeSug.elements);
    appliedRef.current = true;
  }, [shapeSug, setDim]);
  if (!shapeSug) return null;
  return (
    <div className="card card-info mt-2">
      <b>🎴 AI 已按概念预选了阅读手感</b>
      <div className="card-desc mt-1">{shapeSug.tone_reason} 点标签即可改。</div>
    </div>
  );
}

export default function OnboardingFlow() {
  const {
    // 基础 / 路由
    project, err, step, pid, nav,
    // 派生
    concept, tendency, sparkText, allGenreChips, shownSuggests,
    // 各屏 state
    spark, entry, genreDim, pickedGenreCard, chatInput, busy,
    ideas, comparison, ideaSig, customOpen, customConcept,
    prefTone, prefElements, prefProta, prefAvoid,
    engineCards, enginePicked, genrePath,
    inferBusy, customGenre,
    titleIdeas, titleSig, titleBusy, titleInput,
    chapters, words,
    fly, pickedKey, dirty, arch, bp,
    // setter
    setSpark, setEntry, setPickedGenreCard, setChatInput,
    setIdeaSig, setCustomOpen, setCustomConcept,
    setPrefTone, setPrefElements, setPrefProta, setPrefAvoid,
    setGenreSuggests, setSuggestPage, setCustomGenre,
    setTitleSig, setTitleInput, setChapters, setWords, setDirty,
    openEnded, toggleOpenEnded,
    // ref
    stepsRef, chatEndRef, sparkRef, titleInputRef,
    // handler
    submitSpark, pickGenreBrainstorm, sendChat,
    brainstorm, regenWithFeedback, pickConcept, saveCustomConcept,
    fetchEngines, pickEngine, developConcept,
    setGenre, setDim, fetchTitles, pickTitle, pickScale, confirmScale,
    runArch, runBp, enterWorkbench, abandon, goto, editFrom, markDirtyOk,
  } = useOnboarding();

  // 引擎卡抽卡页码:一批 AI 生成 8 张,先翻前 4 张(零成本),翻完才再调 AI 补池
  const [enginePage, setEnginePage] = useState(0);
  const [showAllEngines, setShowAllEngines] = useState(false);
  const [seedHint, setSeedHint] = useState(false);
  const [scaleMode, setScaleMode] = useState<"" | "auto" | "manual">("");
  // 方案轮廓推荐:概念确认后自动请求一次;tone/scale 步进到时预填(可改)
  const [shapeSug, setShapeSug] = useState<ShapeSuggestion | null>(null);
  const [scaleApplied, setScaleApplied] = useState(false);

  // 题材页「随机换一张」:全池重抽题材卡 + 顺带抽口味(与随机开一本同一体验语言)
  function randomizeDraft() {
    if (!allGenreChips.length) return;
    const pick = <T,>(arr: T[]): T => arr[Math.floor(Math.random() * arr.length)];
    const pickSome = <T,>(arr: T[], max: number): T[] =>
      [...arr].sort(() => Math.random() - 0.5).slice(0, Math.floor(Math.random() * (max + 1)));
    setPickedGenreCard(pick(allGenreChips));
    setPrefTone(pickSome(PREF_TONES, 2));
    setPrefElements(pickSome(PREF_ELEMENTS, 2));
    setPrefProta(Math.random() < 0.6 ? pick(PREF_PROTAGONISTS) : "");
    setPrefAvoid(pickSome(PREF_AVOIDS, 1));
  }

  // 随机开一本·零成本阶段:类型卡、一句话灵感全部本地抽签,不调 LLM。
  // 用户看着顺眼再点「让 AI 出方案」(生成 4 个概念方案)——LLM 只花在确认过的方向上。
  function randomBook() {
    if (allGenreChips.length) {
      setPickedGenreCard(allGenreChips[Math.floor(Math.random() * allGenreChips.length)]);
    }
    setSpark(composeRandomSeed());
    setSeedHint(true);
  }


  // 篇幅步:AI 有推荐且用户尚未改过章数(仍是建书默认 30)时,自动选中推荐档
  // 体量「自动」:AI 轮廓推荐一到就生效(概念确认后到达;用户没指定过章数,默认 30 未改视为未指定)
  useEffect(() => {
    if (!shapeSug || scaleMode === "manual" || scaleApplied) return;
    if (Number(chapters) !== 30 || dirty) return;
    const preset = SCALE_PRESETS.find((p) => p.key === shapeSug.scale);
    if (preset) {
      setScaleApplied(true);
      void pickScale(preset);
    }
  }, [shapeSug, scaleMode, scaleApplied, chapters, dirty, pickScale]);

  if (!project) return <div className="muted">{err || "正在创建草稿…"}</div>;

  // 引擎卡抽卡分页:AI 一批生成 8 张,先翻前 4 张;「换一批」先翻池内剩余,
  // 池翻完才再调一次 AI 补新池(省 token)。跨页选中不计失效(按 engine 文本记)。
  const engineHalf = Math.max(1, Math.ceil((engineCards?.length ?? 0) / 2));
  const enginePageCards = (engineCards ?? []).slice(enginePage * engineHalf, enginePage * engineHalf + engineHalf);
  const poolRemain = (engineCards?.length ?? 0) - engineHalf * (enginePage + 1);
  // 两段式引擎选择阶段(未深化出概念):概念候选区不渲染——此时尚无概念,骨架占位只会造成大片空白
  const enginePhase = genrePath && ideas === null && engineCards !== null;
  const conceptPending = genrePath && ideas === null; // 含引擎生成中/引擎待选/深化中

  const stepIdx = STEP_ORDER.indexOf(step);
  const hasConcept = !conceptIsEmpty(concept);
  const chatLog = project.chat_log ?? [];
  const allDone = arch.status === "done" && bp.status === "done";
  const conceptBusy = ideas === null && !!sparkText;

  // 候选过期判定:回改上游(灵感/题材/概念…)后,手里的候选与当前输入签名不一致即过期
  const curIdeaSig = conceptSig(sparkText, tendency);
  const curTitleSig = calcTitleSig(project.topic ?? "", (tendency.genre as string) ?? "", concept);
  const ideasStale = isStale(ideas, ideaSig, curIdeaSig);
  const titlesStale = isStale(titleIdeas, titleSig, curTitleSig);

  // 顶部步骤条:已确认项的缩略文本(FLIP 落点)
  const thumbOf: Partial<Record<SetupStep, string>> = {
    concept: hasConcept ? (concept.logline || "已选定") : "",
    genre: (tendency.genre as string) || "",
    title: project.title !== "未命名新书" ? project.title : "",
    scale: `${project.target_chapters} 章`,
  };

  return (
    <MotionConfig reducedMotion="user">
      <LayoutGroup>
        <div className="onboard">
          {/* ===== 左:主流程 ===== */}
          <div className="onboard-main">
            <div className="wiz-steps" ref={stepsRef}>
              {STEP_ORDER.map((s, i) => {
                const done = i < stepIdx;
                const thumb = (done && thumbOf[s]) || (fly?.step === s ? fly.text : "");
                const flyable = s === "concept" || s === "title";
                return (
                  <button key={s} type="button"
                    className={"wiz-step" + (s === step ? " on" : "") + (done ? " done" : "")}
                    onClick={() => i < stepIdx && nav(`/new/${pid}/${s}`)}>
                    <span className="no">{done ? "✓" : i + 1}</span>
                    <span className="wiz-step-label">{STEP_LABEL[s]}</span>
                    {thumb && (flyable
                      ? <motion.span layoutId={`wiz-thumb-${s}`} className="wiz-thumb">{thumb}</motion.span>
                      : <span className="wiz-thumb">{thumb}</span>)}
                  </button>
                );
              })}
              <div className="grow" />
              <button className="btn-sm" onClick={abandon}>放弃创建</button>
            </div>
            <div className="wiz-progress">
              <motion.div className="wiz-progress-fill"
                animate={{ width: `${(stepIdx / (STEP_ORDER.length - 1)) * 100}%` }}
                transition={{ duration: 0.4 }} />
            </div>

            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={step}
                initial={{ opacity: 0, y: 24 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -24 }}
                transition={{ duration: 0.28 }}>

                {/* ---------- 想法 ---------- */}
                {step === "idea" && (
                  <div className="card">
                    <h2>这本书的核心是什么?</h2>
                    <div className="card-desc">
                      一句话、一个画面、一个设定都行,AI 帮你扩成完整的故事概念。
                    </div>
                    <textarea ref={sparkRef} rows={3} className="mt-2" value={spark}
                      onChange={(e) => setSpark(e.target.value)}
                      placeholder="如:落魄镖师接下一趟险镖,半路开箱验货时发现镖箱里藏着个大活人…"
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey && spark.trim()) {
                          e.preventDefault();
                          void submitSpark();
                        }
                      }} />
                    <div className="actions mt-2">
                      <button className="primary" disabled={!spark.trim()} onClick={submitSpark}>
                        ✨ 让 AI 出方案 →
                      </button>
                      <button onClick={randomBook} disabled={!!busy}>
                        🎴 随机开一本
                      </button>
                      <button onClick={() => setEntry(entry ? null : "more")}>
                        {entry ? "收起" : "没有灵感?"}
                      </button>
                      {hasConcept && (
                        <button onClick={() => goto("genre")}>概念已就绪,跳到题材 →</button>
                      )}
                    </div>
                    {seedHint && (
                      <div className="fld-hint">
                        已抽到一句灵感(已填入上框,可随意改)——觉得方向对,就点「让 AI 出方案」;不对就再抽一次,重抽不花 token。
                      </div>
                    )}

                    {entry === "more" && (
                      <div className="entry-cards">
                        <button type="button" className="entry-card"
                          onClick={() => { setEntry(null); sparkRef.current?.focus(); }}>
                          <h3>💡 我有个想法</h3>
                          <p>一句话、一个画面、一个设定,AI 帮你扩成完整故事概念。</p>
                        </button>
                        <button type="button" className="entry-card" onClick={() => setEntry("genre")}>
                          <h3>📚 我知道想写什么类型</h3>
                          <p>赘婿流、无限流、克苏鲁…选个流派,AI 按套路出方案。</p>
                        </button>
                        <button type="button" className="entry-card" onClick={() => setEntry("chat")}>
                          <h3>💬 和 AI 聊聊</h3>
                          <p>完全没头绪?边聊边捏,概念会随对话慢慢成形。</p>
                        </button>
                        <button type="button" className="entry-card" onClick={randomBook}>
                          <h3>🎴 随机开一本</h3>
                          <p>题材、灵感、口味全抽签,一步不问——抽完不满意随时换、随时改。</p>
                        </button>
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
                                  <button key={c.label} type="button"
                                    className={"genre-card" + (pickedGenreCard?.label === c.label ? " on" : "")}
                                    onClick={() => setPickedGenreCard(c)}>
                                    <b>{c.label}</b>
                                    {c.desc && <span>{c.desc}</span>}
                                  </button>
                                ))}
                              </div>
                            </div>
                          );
                        })}

                        {/* 轻偏好(P0-B 偏好前移):三两个可跳过的小问题,概念生成前先喂给 AI;
                            都不选也行——但选了就不是"流派平均值"了 */}
                        {pickedGenreCard && (
                          <div className="pref-quick mt-3">
                            <div className="pref-row">
                              <span className="pref-label">想要的味道<small>可不选</small></span>
                              <div className="title-chips">
                                {PREF_TONES.map((t) => (
                                  <button key={t} type="button"
                                    className={"title-chip" + (prefTone.includes(t) ? " on" : "")}
                                    onClick={() => setPrefTone((p) =>
                                      p.includes(t) ? p.filter((x) => x !== t) : [...p, t])}>
                                    {t}
                                  </button>
                                ))}
                              </div>
                            </div>
                            <div className="pref-row">
                              <span className="pref-label">想要的元素<small>可多选</small></span>
                              <div className="title-chips">
                                {PREF_ELEMENTS.map((t) => (
                                  <button key={t} type="button"
                                    className={"title-chip" + (prefElements.includes(t) ? " on" : "")}
                                    onClick={() => setPrefElements((p) =>
                                      p.includes(t) ? p.filter((x) => x !== t) : [...p, t])}>
                                    {t}
                                  </button>
                                ))}
                              </div>
                            </div>
                            <div className="pref-row">
                              <span className="pref-label">主角更像<small>可不选</small></span>
                              <div className="title-chips">
                                {PREF_PROTAGONISTS.map((t) => (
                                  <button key={t} type="button"
                                    className={"title-chip" + (prefProta === t ? " on" : "")}
                                    onClick={() => setPrefProta((p) => (p === t ? "" : t))}>
                                    {t}
                                  </button>
                                ))}
                              </div>
                            </div>
                            <div className="pref-row">
                              <span className="pref-label">不想看到<small>可不选</small></span>
                              <div className="title-chips">
                                {PREF_AVOIDS.map((t) => (
                                  <button key={t} type="button"
                                    className={"title-chip" + (prefAvoid.includes(t) ? " on" : "")}
                                    onClick={() => setPrefAvoid((p) =>
                                      p.includes(t) ? p.filter((x) => x !== t) : [...p, t])}>
                                    {t}
                                  </button>
                                ))}
                              </div>
                            </div>
                          </div>
                        )}

                        <div className="actions mt-3">
                          <button className="primary" disabled={!pickedGenreCard}
                            onClick={() => void pickGenreBrainstorm()}>
                            ✨ 按这个流派出方案 →
                          </button>
                          <button onClick={randomizeDraft}>🎴 随机换一张</button>
                          <button onClick={() => setEntry(null)}>← 换个方式</button>
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
                          {hasConcept && (
                            <button className="primary" onClick={() => goto("genre")}>概念已就绪,下一步 →</button>
                          )}
                        </div>
                      </div>
                    )}

                    <div className="actions mt-4 onboard-nav">
                      <span className="grow" />
                      <button onClick={() => goto("genre")}>跳过,以后再想 →</button>
                    </div>
                  </div>
                )}

                {/* ---------- 概念方案 ---------- */}
                {step === "concept" && (
                  <div className="card">
                    <h2>挑一个故事概念</h2>
                    <div className="card-desc">
                      AI 按你的灵感出了几版方案,选一版最顺眼的;都不满意就换一批,或带句话让它重来。
                    </div>
                    {!sparkText && !hasConcept && (
                      <div className="muted mt-3">
                        还没有灵感输入。
                        <button className="btn-sm" onClick={() => nav(`/new/${pid}/idea`)}>← 回想法屏</button>
                      </div>
                    )}
                    {sparkText && (
                      <>
                        {/* 两段式·第一段(P0-A):方向路先挑便宜的引擎卡(FAST 档一句话内核),
                            选 1-2 张再深化——试错成本降一个量级,强模型只跑选中的那一个 */}
                        {enginePhase && (
                          <div className="mt-2 mb-2">
                            {engineCards.length === 0 ? (
                              <div className="muted">
                                引擎卡生成失败。
                                <button className="btn-sm" onClick={() => fetchEngines()}>重试</button>
                              </div>
                            ) : (
                              <>
                                <div className="card-desc">
                                  先挑对味的故事内核——都只是一句话种子,选中后再深化成完整概念
                                  (选两张 = 混搭:A 的主角遇 B 的局面)。
                                </div>
                                <div className="gacha-hand" key={enginePage + (showAllEngines ? "-all" : "")}>
                                  {(showAllEngines ? (engineCards ?? []) : enginePageCards).map((card, i) => (
                                    <button key={card.engine} type="button"
                                      className={"engine-card flip-in" + (enginePicked.includes(card.engine) ? " on" : "")}
                                      style={{ animationDelay: `${i * 110}ms` }}
                                      disabled={enginePicked.length >= 2 && !enginePicked.includes(card.engine)}
                                      title={enginePicked.length >= 2 && !enginePicked.includes(card.engine)
                                        ? "已选满两张——先点选中的卡片取消一张,再选这张"
                                        : undefined}
                                      onClick={() => pickEngine(card.engine)}>
                                      {card.angle && <span className="engine-angle">{card.angle}</span>}
                                      <b>{card.engine}</b>
                                      {card.hook && <span className="engine-hook">抓人点:{card.hook}</span>}
                                    </button>
                                  ))}
                                </div>
                                <div className="actions mt-2">
                                  <button className="primary" disabled={!enginePicked.length || !!busy}
                                    onClick={() => void developConcept()}>
                                    {enginePicked.length > 1 ? "融合这两张,深化成概念 →" : "深化成完整概念 →"}
                                  </button>
                                  {!showAllEngines && (enginePage === 0 ? (
                                    <button disabled={!!busy}
                                      onClick={() => setEnginePage((n) => n + 1)}>
                                      🎴 再抽一批(池里还有 {poolRemain} 张) →
                                    </button>
                                  ) : (
                                    <button disabled={!!busy}
                                      onClick={() => setEnginePage((n) => Math.max(0, n - 1))}>
                                      ← 上一批
                                    </button>
                                  ))}
                                  {showAllEngines || enginePage === 1 ? (
                                    <button disabled={!!busy}
                                      onClick={() => {
                                        fetchEngines((engineCards ?? []).map((c) => c.engine));
                                        setEnginePage(0);
                                      }}>
                                      都不对味,AI 重新出一批
                                    </button>
                                  ) : null}
                                  <button disabled={!!busy}
                                    onClick={() => setShowAllEngines((v) => !v)}>
                                    {showAllEngines ? "回到分批抽" : "一次看全部 8 张"}
                                  </button>
                                </div>
                                {busy && (
                                  <div className="muted mt-2">
                                    <span className="spin" />{busy}
                                  </div>
                                )}
                              </>
                            )}
                          </div>
                        )}
                        {ideas === null && (genrePath ? engineCards === null : true) && (
                          <div className="muted mt-2 mb-2">
                            <span className="spin" /><ThinkingText phrases={THINK_CONCEPT} />
                          </div>
                        )}
                        {!conceptPending && (
                          <>
                            {ideasStale && (
                              <div className="wiz-stale">
                                <span>⚠ {conceptStaleText(ideaSig!, sparkText, tendency)}</span>
                                <span className="grow" />
                                <button className="btn-sm" onClick={() => brainstorm()}>重新生成</button>
                                <button className="btn-sm" onClick={() => setIdeaSig(curIdeaSig)}>仍用这批</button>
                              </div>
                            )}
                            {comparison && ideas && ideas.length > 0 && (
                              <div className="card card-info mt-2">
                                <b>这几个方案怎么选</b>
                                <div className="card-desc mt-1">{comparison}</div>
                              </div>
                            )}
                            <CandidateCards
                              items={ideas} skeletonCount={4} keyOf={conceptKey}
                              layoutIdPrefix="concept" pickedKey={pickedKey}
                              busy={conceptBusy || !!pickedKey}
                              renderCard={(c) => (
                                <>
                                  <h3 className="wiz-cand-title">{c.logline || "(无标题)"}</h3>
                                  <ConceptBrief c={c} />
                                </>
                              )}
                              onPick={(c) => {
                              pickConcept(c);
                              // 方案定了 → 顺手要一份「阅读手感 + 篇幅」推荐(轻量调用,失败静默)
                              if (pid == null) return;
                              api.suggestShape(pid).then((sug: ShapeSuggestion) => {
                                setShapeSug(sug);
                                setScaleApplied(false);
                              }).catch(() => undefined);
                            }}
                              onRefresh={() => brainstorm()}
                              onRefine={regenWithFeedback}
                              onCustom={() => setCustomOpen((v) => !v)}
                            />
                          </>
                        )}
                      </>
                    )}
                    {!sparkText && hasConcept && (
                      <CandidateCards
                        items={ideas ?? [concept]} skeletonCount={1} keyOf={conceptKey}
                        layoutIdPrefix="concept" pickedKey={pickedKey}
                        busy={!!pickedKey}
                        renderCard={(c) => (
                          <>
                            <h3 className="wiz-cand-title">{c.logline || "(无标题)"}</h3>
                            <ConceptBrief c={c} />
                          </>
                        )}
                        onPick={(c) => {
                              pickConcept(c);
                              // 方案定了 → 顺手要一份「阅读手感 + 篇幅」推荐(轻量调用,失败静默)
                              if (pid == null) return;
                              api.suggestShape(pid).then((sug: ShapeSuggestion) => {
                                setShapeSug(sug);
                                setScaleApplied(false);
                              }).catch(() => undefined);
                            }}
                        onRefine={regenWithFeedback}
                        onCustom={() => setCustomOpen((v) => !v)}
                      />
                    )}

                    {customOpen && (
                      <div className="wiz-custom mt-3">
                        <label className="fl">自己写概念(至少填一项)</label>
                        {CONCEPT_FIELDS.map((f) => (
                          <div key={f.key} className="mt-2">
                            <div className="hint">{f.label} · {f.hint}</div>
                            <textarea rows={1} value={customConcept[f.key]}
                              onChange={(e) => setCustomConcept({ ...customConcept, [f.key]: e.target.value })} />
                          </div>
                        ))}
                        <div className="actions mt-2">
                          <button className="primary" onClick={saveCustomConcept}>保存并继续 →</button>
                          <button onClick={() => setCustomOpen(false)}>收起</button>
                        </div>
                      </div>
                    )}

                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/idea`)}>← 上一步</button>
                      <button className="primary" disabled={!hasConcept} onClick={() => goto("genre")}>
                        {hasConcept ? "概念可以了,下一步 →" : "先挑一个概念"}
                      </button>
                      {!hasConcept && <button onClick={() => goto("genre")}>跳过,以后再想</button>}
                    </div>
                  </div>
                )}

                {/* ---------- 题材 ---------- */}
                {step === "genre" && (
                  <div className="card">
                    <h2>这是什么类型的故事?</h2>
                    <div className="card-desc">
                      {inferBusy ? "AI 正在根据你的概念推断题材…" : tendency.genre
                        ? `AI 推断这本书是「${tendency.genre}」,不对就点别的或自己写。`
                        : "选一个题材流派,或自己写。"}
                    </div>
                    {inferBusy && (
                      <div className="muted mt-2"><span className="spin" />
                        <ThinkingText phrases={["正在掂量故事的类型基因…", "正在比对流派特征…"]} />
                      </div>
                    )}
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
                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/concept`)}>← 上一步</button>
                      <button className="primary" onClick={() => goto("tone")}>下一步 →</button>
                    </div>
                  </div>
                )}

                {/* ---------- 基调倾向 ---------- */}
                {step === "tone" && (
                  <div className="card">
                    <h2>想要什么样的阅读手感?</h2>
                    <ToneAutoApply shapeSug={shapeSug} setDim={setDim} />
                    <div className="card-desc">
                      节奏 / 结构 / 基调,可不选,AI 会均衡处理;想叠加的剧情元素(暗恋、双向奔赴、逆袭…)也可在这里勾选。进了工作台也能随时调。
                    </div>
                    <ToneAutoApply shapeSug={shapeSug} setDim={setDim} />
                    {genreDim ? (
                      <div className="mt-2"><ToneDims tendency={tendency} onSet={setDim} /></div>
                    ) : (
                      <div className="muted mt-2"><span className="spin" />加载倾向选项…</div>
                    )}
                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/genre`)}>← 上一步</button>
                      <button className="primary" onClick={() => goto("title")}>下一步 →</button>
                    </div>
                  </div>
                )}

                {/* ---------- 书名 ---------- */}
                {step === "title" && (
                  <div className="card">
                    <h2>给它起个名字</h2>
                    <div className="card-desc">
                      AI 根据概念和题材起的候选,点"用这个"即定;随时可改,不是一锤定音。
                    </div>
                    {titleIdeas === null && (
                      <div className="muted mt-2 mb-2">
                        <span className="spin" /><ThinkingText phrases={THINK_TITLE} />
                      </div>
                    )}
                    {titlesStale && (
                      <div className="wiz-stale">
                        <span>⚠ {titleStaleText(titleSig!, project.topic ?? "", (tendency.genre as string) ?? "", concept)}</span>
                        <span className="grow" />
                        <button className="btn-sm" onClick={() => fetchTitles()}>重新生成</button>
                        <button className="btn-sm" onClick={() => setTitleSig(curTitleSig)}>仍用这批</button>
                      </div>
                    )}
                    <CandidateCards
                      items={titleIdeas} skeletonCount={4} keyOf={(t) => t}
                      layoutIdPrefix="title" pickedKey={pickedKey}
                      busy={titleBusy || !!pickedKey}
                      renderCard={(t) => <h3 className="wiz-cand-title">{t}</h3>}
                      onPick={pickTitle}
                      onRefresh={() => fetchTitles()}
                      onRefine={(f) => fetchTitles(f)}
                      onCustom={() => titleInputRef.current?.focus()}
                    />
                    <div className="input-row mt-3">
                      <input ref={titleInputRef} type="text" value={titleInput}
                        onChange={(e) => setTitleInput(e.target.value)}
                        placeholder="或自己输入书名" maxLength={100}
                        onKeyDown={(e) => e.key === "Enter" && pickTitle(titleInput)} />
                      <button className="btn-sm" disabled={!titleInput.trim() || !!pickedKey}
                        onClick={() => pickTitle(titleInput)}>
                        就用这个名
                      </button>
                    </div>
                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/tone`)}>← 上一步</button>
                      <button className="primary" disabled={!titleInput.trim()}
                        onClick={() => pickTitle(titleInput)}>
                        下一步 →
                      </button>
                    </div>
                  </div>
                )}

                {/* ---------- 篇幅(前置:自动/指定双模式) ---------- */}
                {step === "scale" && (
                  <div className="card">
                    <h2>这本书打算写多长?</h2>
                    <div className="card-desc">
                      「自动」= AI 按概念与题材推荐档位(概念确认后生效,随时可改);「我指定」= 按你填的章数生成——
                      题材与章数不匹配时也按你的来,蓝图自动适配节奏。超过 150 章自动**分卷连载**:先出全书卷纲,
                      每卷定主题,首铺只铺第一卷,写到卷尾自动展开下一卷——不用一次规划所有细节。
                    </div>

                    <div className="title-chips mt-3">
                      <button type="button"
                        className={"title-chip" + (scaleMode === "auto" ? " on" : "")}
                        onClick={() => setScaleMode("auto")}>🎴 自动(AI 按题材定)</button>
                      <button type="button"
                        className={"title-chip" + (scaleMode === "manual" ? " on" : "")}
                        onClick={() => setScaleMode("manual")}>✍️ 我指定章数</button>
                    </div>

                    {scaleMode === "" && (
                      <div className="muted mt-2">选一种方式继续;不确定就选「自动」。</div>
                    )}

                    {scaleMode === "auto" && (
                      <>
                        <div className="scale-cards mt-3">
                          {SCALE_PRESETS.map((p) => (
                            <button key={p.key} type="button"
                              className={"scale-card" + (Number(chapters) === p.chapters ? " on" : "")}
                              onClick={() => pickScale(p)}>
                              <b>{p.label}</b>
                              <div className="scale-num">{p.chapters} 章 × {p.words} 字</div>
                              <div className="hint">{p.desc}</div>
                            </button>
                          ))}
                        </div>
                        <div className="fld-hint mt-2">
                          点卡为「预选」,仍可在概念确认后接受 AI 推荐档位;不点直接下一步 = 完全交给 AI。
                        </div>
                        {shapeSug && scaleApplied && (
                          <div className="card card-info mt-2">
                            <b>🎴 AI 按概念推荐篇幅:{shapeSug.scale === "short" ? "短篇" : shapeSug.scale === "long" ? "长篇" : shapeSug.scale === "serial" ? "连载" : "中篇"}</b>
                            <div className="card-desc mt-1">{shapeSug.scale_reason} 已帮你选好(点其他卡可改)。</div>
                          </div>
                        )}
                      </>
                    )}

                    {scaleMode === "manual" && (
                      <div className="row mt-3">
                        <div>
                          <label className="fl">目标章节数(1-5000)</label>
                          <input type="number" value={chapters} min={1} max={5000}
                            onChange={(e) => setChapters(e.target.value)} />
                        </div>
                        <div>
                          <label className="fl">每章目标字数</label>
                          <input type="number" value={words} min={200} max={20000} step={500}
                            onChange={(e) => setWords(e.target.value)} />
                        </div>
                      </div>
                    )}

                    <label className="row mt-3" style={{ gap: 8, alignItems: "flex-start", cursor: "pointer" }}>
                      <input type="checkbox" checked={openEnded}
                        onChange={(e) => toggleOpenEnded(e.target.checked)} style={{ marginTop: 3 }} />
                      <span>
                        <b>开放式连载(结局未定)</b>
                        <span className="hint" style={{ display: "block" }}>
                          勾上后架构不预设全书终局,只定「长线引擎 + 首批方向」;上面的章数是本批次体量,
                          写满后蓝图页一键「续订」顺延 30 章接着写——写到哪续到哪。
                        </span>
                      </span>
                    </label>

                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/idea`)}>← 上一步</button>
                      <button className="primary" onClick={confirmScale}>下一步 →</button>
                    </div>
                  </div>
                )}

                {/* ---------- 确认墙 ---------- */}
                {step === "confirm" && (
                  <div className="card">
                    <h2>最后过一遍</h2>
                    <div className="card-desc">
                      都对就「开始创建」;哪张卡不对,点「改」跳回去调整,不强制重选。
                    </div>
                    <div className="wiz-wall mt-3">
                      {([
                        {
                          key: "concept" as SetupStep, label: "概念", set: hasConcept,
                          body: hasConcept ? <ConceptBrief c={concept} /> : null,
                          text: concept.logline,
                        },
                        {
                          key: "genre" as SetupStep, label: "题材", set: !!tendency.genre,
                          body: null, text: (tendency.genre as string) || "",
                        },
                        {
                          key: "tone" as SetupStep, label: "倾向",
                          set: ["pace", "structure", "tone"].some((k) => {
                            const v = tendency[k];
                            return Array.isArray(v) ? v.length > 0 : !!v;
                          }),
                          body: null,
                          text: ["pace", "structure", "tone"]
                            .flatMap((k) => {
                              const v = tendency[k];
                              return Array.isArray(v) ? v : v ? [v] : [];
                            }).join(" / "),
                        },
                        {
                          key: "title" as SetupStep, label: "书名",
                          set: project.title !== "未命名新书",
                          body: null,
                          text: project.title !== "未命名新书" ? project.title : "",
                        },
                        {
                          key: "scale" as SetupStep, label: "篇幅", set: true,
                          body: null,
                          text: `${project.target_chapters} 章 × ${project.target_words_per_chapter} 字,合计约 ${project.target_chapters * project.target_words_per_chapter} 字`,
                        },
                      ]).map((c) => {
                        const affected = !!dirty
                          && STEP_ORDER.indexOf(c.key) > STEP_ORDER.indexOf(dirty.from)
                          && !dirty.ok.includes(c.key);
                        return (
                          <div key={c.key} className="wiz-wall-card">
                            <div className="wiz-wall-head">
                              <span className="wiz-wall-label">{c.label}</span>
                              {affected && <span className="wiz-flag">⚠ 可能受影响</span>}
                              <span className="grow" />
                              {affected && (
                                <button className="btn-sm" onClick={() => markDirtyOk(c.key)}>仍用这个</button>
                              )}
                              <button className="btn-sm" onClick={() => editFrom(c.key)}>改</button>
                            </div>
                            <div className="wiz-wall-body">
                              {c.set
                                ? (c.body ?? <span className="wiz-wall-text">{c.text}</span>)
                                : <span className="muted">未定</span>}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/scale`)}>← 上一步</button>
                      <button className="primary" onClick={() => {
                        setDirty(null);
                        localStorage.removeItem(`wiz-dirty:${pid}`);
                        void goto("launch");
                      }}>
                        🔥 开始创建
                      </button>
                    </div>
                  </div>
                )}

                {/* ---------- 点火流水线 ---------- */}
                {step === "launch" && (
                  <div className="card">
                    <h2>《{project.title}》点火</h2>
                    <div className="card-desc">
                      AI 按确认好的设定,先生成全书架构,再展开分章蓝图;都在后台跑,切走也继续。
                    </div>
                    <div className="wiz-pipe mt-3">
                      {([
                        { key: "arch" as const, label: "生成架构",
                          desc: "核心种子 / 角色关系 / 世界观 / 情节框架", st: arch, retry: runArch },
                        { key: "bp" as const, label: "生成蓝图",
                          desc: "按架构展开分章大纲", st: bp, retry: runBp },
                      ]).map((c) => (
                        <div key={c.key} className={"wiz-pipe-card " + c.st.status}>
                          <div className="wiz-pipe-icon">
                            {c.st.status === "run" ? <span className="spin" />
                              : c.st.status === "done" ? "✓"
                              : c.st.status === "err" ? "✕" : "○"}
                          </div>
                          <div className="grow">
                            <div className="wiz-pipe-label">{c.label}</div>
                            <div className="hint">{c.desc}</div>
                            {c.st.status === "run" && (
                              <div className="muted mt-1">
                                <ThinkingText phrases={[c.st.stage || "生成中"]} interval={4000} />
                                …
                              </div>
                            )}
                            {c.st.status === "err" && (
                              <div className="msg-err mt-1">
                                {c.st.error}
                                <button className="btn-sm ml-2" onClick={c.retry}>重跑本步</button>
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                    {allDone && (
                      <motion.div className="wiz-celebrate"
                        initial={{ scale: 0.6, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        transition={{ type: "spring", stiffness: 260, damping: 15 }}>
                        🎉 架构和蓝图都生成好了,去审阅吧
                      </motion.div>
                    )}
                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/confirm`)}>← 上一步</button>
                      {arch.status === "wait" && bp.status === "wait" && (
                        <button className="primary" onClick={runArch}>🔥 开始生成</button>
                      )}
                      {allDone
                        ? <button className="primary" onClick={enterWorkbench}>进入工作台 →</button>
                        : <button onClick={enterWorkbench}>先不生成,直接进工作台</button>}
                    </div>
                  </div>
                )}
              </motion.div>
            </AnimatePresence>

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
                  <span className="dr-v wrap">{hasConcept ? (concept.logline || "已定") : "未定"}</span>
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
                  <span className="dr-v">{project.target_chapters} 章 × {project.target_words_per_chapter} 字,合计约 {project.target_chapters * project.target_words_per_chapter} 字</span>
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
      </LayoutGroup>
    </MotionConfig>
  );
}

