// 创作起步流(对话式确认流,2026-09-24 重构):建书即建草稿项目,五步主线走到点火。
// idea → brief(和策划聊/🎲提案兜底 → 开书订单拍板) → concept(订单深化→打磨拍板)
// → setup(题材口味/篇幅书名/总检三页签) → launch(架构闸门→骨架墙→铺章)
// 「选方向直接抽卡」的旧入口交互已废除(引擎卡墙/概念卡墙下线);旧八步路由由
// steps.parseStep 兼容映射;信任模式(一枪整本)保留为逃生通道。
// /new → 静默建草稿 → /new/:id/idea → … → /new/:id/launch → 工作台
// 每屏选择实时 PATCH 落库(刷新不丢、列表页可"继续创建");
// localStorage 缓存候选内容,刷新后回到当前屏接着选。
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, LayoutGroup, MotionConfig, motion } from "motion/react";
import { api, conceptIsEmpty, CONCEPT_FIELDS, Premise, ShapeSuggestion } from "../api";
import PremiseCard from "../ui/PremiseCard";
import { ThinkingText } from "../ui/ThinkingText";
import { ConfirmGate } from "../ui/confirmKit";
import { isStale, titleSig as calcTitleSig, titleStaleText } from "./wizSig";
import { SetupStep, STEP_ORDER, STEP_LABEL } from "./onboarding/steps";
import ModeGate from "./onboarding/ModeGate";
import PlanFlow from "./onboarding/PlanFlow";
import SkeletonWall from "./onboarding/SkeletonWall";
import { SCALE_PRESETS, THINK_TITLE, scaleDisplay } from "./onboarding/presets";
import { composeRandomSeed } from "./onboarding/randomSeeds";
import { ConceptBrief, conceptKey } from "./onboarding/ConceptBrief";
import ConceptForge from "./onboarding/ConceptForge";
import ArchGate from "./onboarding/ArchGate";
import { ToneDims } from "./onboarding/ToneDims";
import { useOnboarding } from "./onboarding/useOnboarding";

// SetupStep 原在本文件定义,保留 re-export 以兼容潜在外部引用(现仅本文件内部使用)
export type { SetupStep };

// 方向卡屏轻偏好预置(P0-B 偏好前移):都是高频人话选项,全部可不选;
// tone/elements 的 directive 已在后端 tag_presets 里,这里只放 label 让用户点。
// P1 口味定标:感情线/开局强度/主角底色/主角视角不再硬编码,直接读 outlineDims(配置驱动)。
const PREF_TONES = ["热血", "悬疑", "治愈", "甜", "暗黑", "爽"];
const PREF_ELEMENTS = ["逆袭", "马甲", "身份错位", "成长蜕变", "救赎", "群像", "破镜重圆", "契约关系"];
// 排斥项对着题材边界最常拦的套路来(用户明确点了才允许,没点就是「不要」)
const PREF_AVOIDS = ["系统", "重生", "穿越", "觉醒", "异能"];
// 口味定标维度(outline 目录里的 key,按此顺序渲染):收窄抽卡空间的核心四问
const TASTE_DIM_KEYS = ["lead_gender", "romance", "opening", "protagonist"] as const;

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
  // 两个数组都空 = 模型给的标签全被目录过滤掉了:说实话,别让用户对着空芯片找「预选」
  if (!shapeSug.tone.length && !shapeSug.elements.length) {
    return (
      <div className="card card-info mt-2">
        <b>🎴 这次没给出合适的预选标签</b>
        <div className="card-desc mt-1">{shapeSug.tone_reason} 想加就手动点标签,不选也行。</div>
      </div>
    );
  }
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
    concept, tendency, sparkText, briefText, briefConfirmed,
    conceptStaleVsBrief, allGenreChips, shownSuggests, shapeSug,
    // 想法屏
    spark, entry, genreDim, outlineDims, pickedGenreCard,
    prefTone, prefElements, prefFlavors, prefPersona, prefAvoid, prefAvoidText,
    // 方案屏(docs/22 P0:三问 → 整书方案×3 → 拍板)
    questions, qAnswers, plans, selectedPlan, planBusy, planFeedback,
    planMode,
    busy,
    // 概念屏
    customOpen, customConcept, developing,
    // 配置屏
    inferBusy, customGenre,
    titleIdeas, titleSig, titleBusy, titleInput,
    chapters, words,
    fly, arch, bp,
    forgeOpen, forgeSeed, setForgeOpen,
    // setter
    setSpark, setEntry, setPickedGenreCard,
    setPrefTone, setPrefElements, setPrefFlavors, setPrefPersona, setPrefAvoid, setPrefAvoidText,
    setPlanFeedback, setSelectedPlan,
    setCustomOpen, setCustomConcept,
    setGenreSuggests, setSuggestPage, setCustomGenre,
    setTitleSig, setTitleInput, setChapters, setWords,
    openEnded, toggleOpenEnded,
    // ref
    stepsRef, sparkRef, titleInputRef,
    // handler
    submitSpark, pickGenreBrainstorm,
    pickMode, fetchQuestions, answerQ, adoptAllRecommended,
    genPlans, reviseOnePlan, confirmChosenPlan,
    confirmBrief, unconfirmBrief,
    developFromBrief, saveCustomConcept,
    forgeChanged, forgeConfirmed, forgeUnconfirmed, isForgeDismissedFor, reopenForge,
    setGenre, setDim, fetchTitles, pickTitle, pickScale, confirmScale,
    runArch, runBp, enterWorkbench, abandon, goto,
    trustMode, setTrustMode, setBp,
  } = useOnboarding();

  const [seedHint, setSeedHint] = useState(false);
  const [scaleMode, setScaleMode] = useState<"" | "auto" | "manual">("");
  // 核心梗卡的未保存草稿(P0-1):编辑中每次变化上报到这里,点火前兜底落库,
  // 保证「向导里确认过的梗卡」一定进数据库——否则蓝图/对账/体检全部失锚
  const premiseDraftRef = useRef<Premise | null>(null);
  // 配置屏页签:题材口味 / 篇幅书名 / 总检
  const [setupTab, setSetupTab] = useState<"taste" | "scale" | "review">("taste");
  // 架构闸门四层全拍板 → 亮骨架墙
  const [archGateDone, setArchGateDone] = useState(false);

  // 概念就绪 → 打磨房自动展开;用户显式收起后同一版概念不再自动弹开(换概念才会)。
  // 必须挂在「if (!project) 早退」之前:hook 顺序在两次渲染间要一致;
  // 判空直接用 project.concept,不引用早退之后才初始化的派生值(TDZ)。
  useEffect(() => {
    if (!project || step !== "concept") return;
    const c = project.concept;
    if (!c || conceptIsEmpty(c)) return;
    const key = conceptKey(c);
    if (forgeOpen || isForgeDismissedFor(key)) return;
    setForgeOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, step, forgeOpen]);


  // 题材页「随机换一张」:全池重抽题材卡 + 顺带抽口味(与随机开一本同一体验语言)。
  // P1 口味定标:分叉口味跟着新卡走;感情线/开局/底色/视角写进 tendency(setDim 落库)。
  function randomizeDraft() {
    if (!allGenreChips.length) return;
    const pick = <T,>(arr: T[]): T => arr[Math.floor(Math.random() * arr.length)];
    const pickSome = <T,>(arr: T[], max: number): T[] =>
      [...arr].sort(() => Math.random() - 0.5).slice(0, Math.floor(Math.random() * (max + 1)));
    const card = pick(allGenreChips);
    setPickedGenreCard(card);
    setPrefTone(pickSome(PREF_TONES, 2));
    setPrefElements(pickSome(PREF_ELEMENTS, 2));
    setPrefFlavors(card.flavors?.length ? pickSome(card.flavors, 2) : []);
    setPrefPersona("");
    setPrefAvoid(pickSome(PREF_AVOIDS, 1));
    setPrefAvoidText("");
    for (const key of TASTE_DIM_KEYS) {
      const dim = outlineDims.find((d) => d.key === key);
      if (!dim?.chips.length) continue;
      const labels = dim.chips.map((c) => c.label);
      void setDim(key, dim.select === "multi" ? pickSome(labels, 2) : pick(labels));
    }
  }

  // 随机开一本·零成本阶段:类型卡、一句话灵感全部本地抽签,不调 LLM。
  // 用户看着顺眼再点「按这个出方案」——LLM 只花在确认过的方向上。
  function randomBook() {
    if (allGenreChips.length) {
      setPickedGenreCard(allGenreChips[Math.floor(Math.random() * allGenreChips.length)]);
    }
    setSpark(composeRandomSeed());
    setSeedHint(true);
  }

  // (docs/22 P0)篇幅档位已前置到屏 0 明示 + 方案卡带推荐档,旧版「AI 静默自动
  // 选档」effect 删除——用户没选过就保持「未定」,拍板时按方案推荐档落库。

  if (!project) return <div className="muted">{err || "正在创建草稿…"}</div>;

  const stepIdx = STEP_ORDER.indexOf(step);
  const hasConcept = !conceptIsEmpty(concept);
  // 点火完成 = 蓝图落库(信任模式 arch+bp 都 done;闸门模式 bp 由骨架墙 onPaved 置 done)
  const allDone = bp.status === "done";

  // 候选过期判定:回改上游(书名输入…)后,手里的候选与当前输入签名不一致即过期
  const curTitleSig = calcTitleSig(project.topic ?? "", (tendency.genre as string) ?? "", concept);
  const titlesStale = isStale(titleIdeas, titleSig, curTitleSig);

  // 篇幅档位显示:预设反推档位名;建库默认 30×3000 视为未定,不再伪装成已选
  const scale = scaleDisplay(Number(project.target_chapters), Number(project.target_words_per_chapter));

  // 顶部步骤条:已确认项的缩略文本(FLIP 落点)
  const thumbOf: Partial<Record<SetupStep, string>> = {
    mode: project.mode === "short" ? "📖 短故事"
      : project.mode === "drama" ? "🎬 漫剧" : "📚 连载",
    brief: briefConfirmed ? "方案已拍板" : (plans ? "方案已出" : ""),
    concept: hasConcept ? (concept.logline || "已选定") : "",
    setup: [
      (tendency.genre as string) || "",
      project.title !== "未命名新书" ? project.title : "",
      scale.decided ? (scale.tag === "自定" ? `${project.target_chapters} 章` : scale.tag) : "",
    ].filter(Boolean).join(" · "),
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
                const flyable = s === "concept" || s === "brief";
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

                {/* ---------- 屏 0:开哪种书(docs/22 P0,模式级分叉) ---------- */}
                {step === "mode" && (
                  <ModeGate
                    mode={project.mode ?? "serial"}
                    chapters={Number(project.target_chapters)}
                    words={Number(project.target_words_per_chapter)}
                    onPick={(m, preset, audience) => void pickMode(m, preset, audience)} />
                )}

                {/* ---------- 想法 ---------- */}
                {step === "idea" && (
                  <div className="card">
                    <h2>这本书的核心是什么?</h2>
                    <div className="card-desc">
                      一句话、一个画面、一个设定都行——写下来,和策划把它聊成一份可拍板的开书订单。
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
                        💬 按这个出方案 →
                      </button>
                      <button onClick={randomBook} disabled={!!busy}>
                        🎴 随机开一本
                      </button>
                      <button onClick={() => setEntry(entry ? null : "more")}>
                        {entry ? "收起" : "没有灵感?"}
                      </button>
                      {hasConcept && (
                        <button onClick={() => goto("concept")}>概念已就绪,去打磨 →</button>
                      )}
                    </div>
                        {/* 口味定标(P1):对所有路径可见——AI 出方案/方向卡都会立 pickedGenreCard,
                            收窄抽卡空间;全部可跳过,一道不选与旧版一致 */}
                        {pickedGenreCard && (
                          <div className="pref-quick mt-3">
                            {!!pickedGenreCard.flavors?.length && (
                              <div className="pref-row">
                                <span className="pref-label">这个流派里想看<small>可多选</small></span>
                                <div className="title-chips">
                                  {pickedGenreCard.flavors.map((t) => (
                                    <button key={t} type="button"
                                      className={"title-chip" + (prefFlavors.includes(t) ? " on" : "")}
                                      onClick={() => setPrefFlavors((p) =>
                                        p.includes(t) ? p.filter((x) => x !== t) : [...p, t])}>
                                      {t}
                                    </button>
                                  ))}
                                </div>
                              </div>
                            )}
                            {TASTE_DIM_KEYS.map((key) => {
                              const dim = outlineDims.find((d) => d.key === key);
                              if (!dim?.chips.length) return null;
                              const cur = tendency[key];
                              return (
                                <div className="pref-row" key={key}>
                                  <span className="pref-label">{dim.label}<small>{dim.select === "multi" ? "可多选" : "可不选"}</small></span>
                                  <div className="title-chips">
                                    {dim.chips.map((c) => {
                                      const on = dim.select === "multi"
                                        ? Array.isArray(cur) && cur.includes(c.label)
                                        : cur === c.label;
                                      return (
                                        <button key={c.label} type="button"
                                          className={"title-chip" + (on ? " on" : "")}
                                          title={c.directive || undefined}
                                          onClick={() => {
                                            if (dim.select === "multi") {
                                              const arr = Array.isArray(cur) ? [...cur] : [];
                                              void setDim(key, on ? arr.filter((x) => x !== c.label) : [...arr, c.label]);
                                            } else {
                                              void setDim(key, on ? "" : c.label);
                                            }
                                          }}>{c.label}</button>
                                      );
                                    })}
                                  </div>
                                </div>
                              );
                            })}
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
                              <span className="pref-label">想看的元素<small>可多选</small></span>
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
                              <span className="pref-label">主角底色·其它</span>
                              <input className="premise-input" value={prefPersona}
                                onChange={(e) => setPrefPersona(e.target.value)}
                                placeholder="chips 没覆盖的底色写这里(可不填)" />
                            </div>
                            <div className="pref-row">
                              <span className="pref-label">排斥项<small>点了就是「不要」</small></span>
                              <div className="title-chips">
                                {PREF_AVOIDS.map((t) => (
                                  <button key={t} type="button"
                                    className={"title-chip" + (prefAvoid.includes(t) ? " on" : "")}
                                    onClick={() => setPrefAvoid((p) =>
                                      p.includes(t) ? p.filter((x) => x !== t) : [...p, t])}>
                                    {t}
                                  </button>
                                ))}
                                <input className="premise-input" value={prefAvoidText}
                                  onChange={(e) => setPrefAvoidText(e.target.value)}
                                  placeholder="其它不要的(可不填)" />
                              </div>
                            </div>
                          </div>
                        )}
                    {seedHint && (
                      <div className="fld-hint">
                        已抽到一句灵感(已填入上框,可随意改)——觉得方向对,就点「按这个出方案」;
                        不对就再抽一次,重抽不花 token。
                      </div>
                    )}

                    {entry === "more" && (
                      <div className="entry-cards">
                        <button type="button" className="entry-card"
                          onClick={() => { setEntry(null); sparkRef.current?.focus(); }}>
                          <h3>💡 我有个想法</h3>
                          <p>一句话、一个画面、一个设定,写下来和策划聊成完整订单。</p>
                        </button>
                        <button type="button" className="entry-card" onClick={() => setEntry("genre")}>
                          <h3>📚 我知道想写什么类型</h3>
                          <p>赘婿流、无限流、克苏鲁…选个流派定口味,AI 出三个方向提案供你挑。</p>
                        </button>
                        <button type="button" className="entry-card"
                          onClick={() => { setEntry(null); void goto("brief"); }}>
                          <h3>💬 完全没头绪</h3>
                          <p>直接和策划聊:AI 先出三个方向提案,选中哪个就聊哪个。</p>
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

                        {/* 口味定标(P1):流派内分叉 + 感情线/开局/底色/视角,收窄抽卡空间;
                            全部可跳过——一道不选就和旧版完全一样,收窄是加分项不是门槛 */}

                        <div className="actions mt-3">
                          <button className="primary" disabled={!pickedGenreCard}
                            onClick={() => void pickGenreBrainstorm()}>
                            💬 按这个流派,出方案 →
                          </button>
                          <button onClick={randomizeDraft}>🎴 随机换一张</button>
                          <button onClick={() => setEntry(null)}>← 换个方式</button>
                        </div>
                      </div>
                    )}

                    <div className="actions mt-4 onboard-nav">
                      <span className="grow" />
                      <button onClick={() => goto("brief")}>先不定,直接出方案 →</button>
                    </div>
                  </div>
                )}

                {/* ---------- 方案:三问定纲 → 整书方案×3 → 拍板(确认链 L0,docs/22 P0) ---------- */}
                {step === "brief" && (
                  <PlanFlow
                    pid={pid!}
                    mode={planMode}
                    topic={sparkText}
                    briefText={briefText}
                    briefConfirmed={briefConfirmed}
                    questions={questions}
                    qAnswers={qAnswers}
                    plans={plans}
                    selectedPlan={selectedPlan}
                    planBusy={planBusy}
                    planFeedback={planFeedback}
                    onQuestions={() => void fetchQuestions()}
                    onAnswer={answerQ}
                    onAdoptAll={adoptAllRecommended}
                    onGenPlans={() => void genPlans()}
                    onRevise={(i, d) => void reviseOnePlan(i, d)}
                    onConfirmPlan={(i) => void confirmChosenPlan(i)}
                    onUnconfirm={() => void unconfirmBrief()}
                    onFeedback={setPlanFeedback}
                    onSelect={setSelectedPlan}
                    onBack={() => nav(`/new/${pid}/idea`)}
                    onGotoConcept={() => goto("concept")} />
                )}

                {/* ---------- 概念:拍板订单 → 深化 → 打磨房拍板 ---------- */}
                {step === "concept" && (
                  <div className="card">
                    <h2>按订单深化概念</h2>
                    <div className="card-desc">
                      拍板的订单是硬约束:AI 只把它转写成结构化概念,不换故事;深化完进打磨房逐项过目、改、拍板。
                    </div>

                    {/* 订单摘要(拍板内容可见,撤回直达) */}
                    {briefText && (
                      <div className="card card-info mt-2">
                        <div className="card-head mb-2">
                          <b>你的开书订单{briefConfirmed ? "(已拍板)" : "(未拍板)"}</b>
                          <span className="grow" />
                          <ConfirmGate confirmed={briefConfirmed}
                            confirmText="拍板" confirmedText="已拍板 ✓"
                            disabled={!briefText}
                            onConfirm={() => { void confirmBrief(); }}
                            onUnconfirm={() => { void unconfirmBrief(); }} />
                          <button className="btn-sm" onClick={() => nav(`/new/${pid}/brief`)}>改订单</button>
                        </div>
                        <div style={{ whiteSpace: "pre-wrap" }}>{briefText}</div>
                      </div>
                    )}
                    {!briefText && (
                      <div className="muted mt-3">
                        还没有开书订单——先去简介屏聊一份(或自己写概念)。
                        <button className="btn-sm ml-2" onClick={() => nav(`/new/${pid}/brief`)}>去简介屏 →</button>
                      </div>
                    )}
                    {briefText && !briefConfirmed && (
                      <div className="wiz-stale mt-2">
                        <span>⚠ 订单还没拍板:深化按钮锁着——先回简介屏点「✓ 简介就按这个来」。</span>
                        <span className="grow" />
                        <button className="btn-sm" onClick={() => nav(`/new/${pid}/brief`)}>回简介屏</button>
                      </div>
                    )}
                    {conceptStaleVsBrief && hasConcept && (
                      <div className="wiz-stale mt-2">
                        <span>⚠ 订单改过了,当前概念还是按旧订单深化的——看看要不要重新深化。</span>
                        <span className="grow" />
                        <button className="btn-sm" disabled={!briefConfirmed || developing}
                          onClick={() => void developFromBrief()}>按新订单重新深化</button>
                      </div>
                    )}

                    {/* 深化中/深化失败 */}
                    {developing && (
                      <div className="muted mt-3">
                        <span className="spin" />
                        <ThinkingText phrases={[
                          "正在把订单转写成结构化概念…",
                          "订单是硬约束,逐字段对照中…",
                          "世界观底盘和味道原样承接…",
                        ]} />
                      </div>
                    )}
                    {err && !developing && <div className="msg-err mt-2">{err}</div>}
                    {briefConfirmed && !hasConcept && !developing && (
                      <div className="actions mt-3">
                        <button className="primary" onClick={() => void developFromBrief()}>
                          按订单深化概念 →
                        </button>
                        <button onClick={() => setCustomOpen((v) => !v)}>✍️ 不用 AI,自己写概念</button>
                      </div>
                    )}
                    {briefConfirmed && hasConcept && !developing && !forgeOpen && (
                      <div className="actions mt-3">
                        <button className="primary" onClick={reopenForge}>
                          打开打磨房,打磨并拍板 →
                        </button>
                        <button disabled={developing} title="订单不变,重新深化一次"
                          onClick={() => void developFromBrief()}>↻ 重新深化</button>
                      </div>
                    )}

                    {customOpen && (
                      <div className="wiz-custom mt-3">
                        <label className="fl">自己写概念(至少填一项;写完同样进打磨房拍板)</label>
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

                    {/* 打磨房(确认链 L1):选定概念后在这里过目/改/带话重捏,拍板才进配置。
                        key=forgeSeed:换概念才重挂载;打磨中的字段同步不换 key,高亮不丢 */}
                    {hasConcept && forgeOpen && (
                      <ConceptForge key={forgeSeed} pid={pid!} concept={concept}
                        confirmed={!!project.concept_confirmed}
                        tendency={tendency} dna={project.dna ?? null}
                        onChanged={forgeChanged} onConfirmed={forgeConfirmed}
                        onUnconfirm={forgeUnconfirmed} />
                    )}

                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/brief`)}>← 上一步</button>
                      <span className="grow" />
                      {hasConcept && forgeOpen ? (
                        <>
                          {!project.concept_confirmed && (
                            <button className="primary" onClick={() => goto("setup")}>先跳过打磨,直接去配置 →</button>
                          )}
                        </>
                      ) : hasConcept ? (
                        <button className="primary" onClick={reopenForge}>
                          打开打磨房,打磨并拍板 →
                        </button>
                      ) : (
                        <button className="primary" onClick={() => goto("setup")}>先跳过,直接去配置(不推荐) →</button>
                      )}
                    </div>
                  </div>
                )}

                {/* ---------- 配置(题材口味 / 篇幅书名 / 总检):旧四屏并一张卡 ---------- */}
                {step === "setup" && (
                  <div className="card">
                    <h2>定调与盘子</h2>
                    <div className="card-desc">
                      题材、口味、篇幅、书名——AI 都给了预填,逐项过目随手改;不对就换,都认了就去总检点火。
                    </div>
                    <div className="setup-tabs" role="tablist">
                      <button type="button" className={setupTab === "taste" ? "on" : ""}
                        onClick={() => setSetupTab("taste")}>题材与口味</button>
                      <button type="button" className={setupTab === "scale" ? "on" : ""}
                        onClick={() => setSetupTab("scale")}>篇幅与书名</button>
                      <button type="button" className={setupTab === "review" ? "on" : ""}
                        onClick={() => setSetupTab("review")}>总检 {project.concept_confirmed ? "" : "(概念未拍板也可先看)"}</button>
                    </div>

                    {setupTab === "taste" && (
                      <>
                        <h3 className="mt-2">这是什么类型的故事?</h3>
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
                          {!!tendency.genre && !shownSuggests.some((sg) => sg.label === tendency.genre) && (
                            <button type="button" className="title-chip on">{tendency.genre as string}</button>
                          )}
                          {shownSuggests.map((sg) => (
                            <button key={sg.label} type="button"
                              className={"title-chip" + (tendency.genre === sg.label ? " on" : "")}
                              title={sg.desc || undefined}
                              onClick={() => setGenre(sg.label)}>{sg.label}</button>
                          ))}
                          <button type="button" className="title-chip"
                            onClick={() => { setGenreSuggests([]); setSuggestPage((pg) => (pg + 1) % Math.max(1, Math.ceil(allGenreChips.length / 8))); }}>
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

                        <h3 className="mt-4">想要什么样的阅读手感?</h3>
                        <ToneAutoApply shapeSug={shapeSug} setDim={setDim} />
                        <div className="card-desc">
                          节奏 / 结构 / 基调,可不选,AI 会均衡处理;想叠加的剧情元素也可在这里勾选。进了工作台也能随时调。
                        </div>
                        {genreDim ? (
                          <div className="mt-2"><ToneDims tendency={tendency} onSet={setDim} /></div>
                        ) : (
                          <div className="muted mt-2"><span className="spin" />加载倾向选项…</div>
                        )}
                        <div className="actions mt-4 onboard-nav">
                          <button onClick={() => nav(`/new/${pid}/concept`)}>← 上一步</button>
                          <button className="primary" onClick={() => setSetupTab("scale")}>下一步 →</button>
                        </div>
                      </>
                    )}

                    {setupTab === "scale" && (
                      <>
                        <h3 className="mt-2">这本书打算写多长?</h3>
                        <div className="card-desc">
                          「自动」= AI 按概念与题材推荐档位(随时可改);「我指定」= 按你填的章数生成——
                          超过 150 章自动**分卷连载**:先出全书卷纲,首铺只铺第一卷,写到卷尾自动展开下一卷。
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
                              {SCALE_PRESETS.map((pp) => (
                                <button key={pp.key} type="button"
                                  className={"scale-card" + (Number(chapters) === pp.chapters ? " on" : "")}
                                  onClick={() => pickScale(pp)}>
                                  <b>{pp.label}</b>
                                  <div className="scale-num">{pp.chapters} 章 × {pp.words} 字</div>
                                  <div className="hint">{pp.desc}</div>
                                </button>
                              ))}
                            </div>
                            {shapeSug && (
                              <div className="card card-info mt-2">
                                <b>🎴 AI 按概念推荐篇幅:{shapeSug.scale === "short" ? "短篇" : shapeSug.scale === "long" ? "长篇" : shapeSug.scale === "serial" ? "连载" : "中篇"}</b>
                                <div className="card-desc mt-1">{shapeSug.scale_reason}(docs/22:不再静默替你选——要就用,点上面对应的卡)。</div>
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
                              勾上后架构不预设全书终局,只定「长线引擎 + 首批方向」;章数是本批次体量,
                              写满后蓝图页一键「续订」顺延接着写——写到哪续到哪。
                            </span>
                          </span>
                        </label>

                        <h3 className="mt-4">书名(可留空,进工作台再起)</h3>
                        {titleBusy && (
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
                        {titleIdeas !== null && titleIdeas.length > 0 && (
                          <div className="title-chips mt-2">
                            {titleIdeas.map((t) => (
                              <button key={t} type="button"
                                className={"title-chip" + (project.title === t ? " on" : "")}
                                onClick={() => pickTitle(t)}>{t}</button>
                            ))}
                          </div>
                        )}
                        <div className="input-row mt-2">
                          <input ref={titleInputRef} type="text" value={titleInput}
                            onChange={(e) => setTitleInput(e.target.value)}
                            placeholder="或自己输入书名" maxLength={100}
                            onKeyDown={(e) => e.key === "Enter" && titleInput.trim() && pickTitle(titleInput)} />
                          <button className="btn-sm" disabled={!titleInput.trim()}
                            onClick={() => pickTitle(titleInput)}>就用这个名</button>
                          <button className="btn-sm" disabled={titleBusy} title="AI 根据概念与题材出 4 个候选"
                            onClick={() => fetchTitles()}>AI 起名</button>
                        </div>

                        <div className="actions mt-4 onboard-nav">
                          <button onClick={() => setSetupTab("taste")}>← 上一步</button>
                          <button className="primary"
                            onClick={() => { void confirmScale().then(() => setSetupTab("review")); }}>去总检 →</button>
                        </div>
                      </>
                    )}

                    {setupTab === "review" && (
                      <>
                        <div className="card mt-3">
                          <div className="card-head"><h3>核心梗卡</h3></div>
                          <div className="card-desc">
                            梗是全书的纲:AI 已按概念与题材提炼,点字段可改;蓝图逐章标「梗兑现」、
                            交稿对账、体检健康度都以它为轴。点火后仍可在「本书设置」修改。
                          </div>
                          {pid != null && (
                            <PremiseCard
                              pid={pid} initial={null} autoSuggest
                              onDraftChange={(d) => { premiseDraftRef.current = d; }}
                            />
                          )}
                        </div>
                        <div className="wiz-wall mt-3">
                          {([
                            {
                              label: "概念", set: hasConcept,
                              body: hasConcept ? <ConceptBrief c={concept} /> : null,
                              text: concept.logline,
                              go: () => nav(`/new/${pid}/concept`),
                            },
                            {
                              label: "题材", set: !!tendency.genre,
                              body: null, text: (tendency.genre as string) || "",
                              go: () => setSetupTab("taste"),
                            },
                            {
                              label: "倾向",
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
                              go: () => setSetupTab("taste"),
                            },
                            {
                              label: "书名", set: project.title !== "未命名新书",
                              body: null,
                              text: project.title !== "未命名新书" ? project.title : "",
                              go: () => setSetupTab("scale"),
                            },
                            {
                              label: "篇幅", set: scale.decided, body: null,
                              text: scale.text,
                              go: () => setSetupTab("scale"),
                            },
                          ]).map((c) => (
                            <div key={c.label} className="wiz-wall-card">
                              <div className="wiz-wall-head">
                                <span className="wiz-wall-label">{c.label}</span>
                                <span className="grow" />
                                <button className="btn-sm" onClick={c.go}>改</button>
                              </div>
                              <div className="wiz-wall-body">
                                {c.set
                                  ? (c.body ?? <span className="wiz-wall-text">{c.text}</span>)
                                  : <span className="muted">未定</span>}
                              </div>
                            </div>
                          ))}
                        </div>
                        <div className="actions mt-4 onboard-nav">
                          <button onClick={() => setSetupTab("scale")}>← 上一步</button>
                          <button className="primary" onClick={async () => {
                            // P0-1:向导里看过的梗卡必须落库(展示即所得)
                            try {
                              if (premiseDraftRef.current?.high_concept?.trim()) {
                                await api.savePremise(pid!, premiseDraftRef.current);
                                premiseDraftRef.current = null;
                              }
                            } catch { /* 保存失败不拦点火:进工作台后可在本书设置补 */ }
                            void goto("launch");
                          }}>
                            🔥 去点火
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                )}

                {/* ---------- 点火:架构闸门(逐层拍板)→ 骨架墙 → 铺章 ---------- */}
                {step === "launch" && (
                  <div className="card">
                    <h2>《{project.title}》点火</h2>
                    <div className="card-desc">
                      {trustMode
                        ? "信任模式:AI 一枪生成架构 → 蓝图,中途不停。想逐层把关就回上一步关掉信任模式重新来。"
                        : "架构逐层生成:每层你都看过、拍过板才往下走;四层拍完出故事骨架,逐段拍板铺章。都在后台跑,切走也继续。"}
                    </div>
                    {trustMode ? (
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
                    ) : (
                      <>
                        {/* 架构工作墙(确认链 L2):雪花四层逐层生成/看改/带话/拍板;种子默认必停,
                            后三层默认连跑可逐层关;信任模式=一枪整本,走上方旧链路 */}
                        <ArchGate pid={pid!} tendency={tendency}
                          onTrust={() => { setTrustMode(true); void runArch(); }}
                          onAllConfirmed={() => setArchGateDone(true)} />
                        {archGateDone && (
                          <SkeletonWall
                            pid={pid!}
                            tendency={tendency}
                            onTrust={() => { setTrustMode(true); void runBp(); }}
                            onPaved={() => setBp({ status: "done", stage: "", error: "" })}
                          />
                        )}
                      </>
                    )}
                    {trustMode && arch.status === "done" && bp.status === "wait" && (
                      <div className="muted mt-2">信任模式:架构完成,直接铺全书蓝图(跳过骨架墙)。</div>
                    )}
                    {allDone && (
                      <motion.div className="wiz-celebrate"
                        initial={{ scale: 0.6, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        transition={{ type: "spring", stiffness: 260, damping: 15 }}>
                        🎉 架构和蓝图都生成好了,去审阅吧
                        <span className="wiz-celebrate-hint">
                          以后想改也不用怕:工作台左侧「开书」区可随时重调概念/架构/大纲
                          (大纲支持带一句话要求重铺);写崩了点顶部状态条的「重来向导」。
                        </span>
                      </motion.div>
                    )}
                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/setup`)}>← 上一步</button>
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
                <div className={"dossier-row" + (briefText ? " ok" : "")}>
                  <span className="dr-k">模式</span>
                  <span className="dr-v">{project.mode === "short" ? "📖 短故事(一次讲完)"
                        : project.mode === "drama" ? `🎬 漫剧源书(${project.audience === "female" ? "女频" : "男频"})`
                        : "📚 开书连载"}</span>
                </div>
                <div className={"dossier-row" + (briefText ? " ok" : "")}>
                  <span className="dr-k">订单</span>
                  <span className="dr-v wrap">
                    {briefText
                      ? (briefConfirmed ? "已拍板 · " : "草稿 · ") + briefText.split("\n")[0].replace(/^【故事内核】/, "")
                      : "未定"}
                  </span>
                </div>
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
                <div className={"dossier-row" + (scale.decided ? " ok" : "")}>
                  <span className="dr-k">篇幅</span>
                  <span className="dr-v">{scale.text}</span>
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

