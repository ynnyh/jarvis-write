// 创作起步流(docs/30 / docs/32,2026-10-17 前置环节重构):四屏两确认。
//
//   屏0 mode(开哪种书)→ 屏1 idea(想法:灵感/流派/皮肤/参考方向)
//   → 屏2 intent(理解卡一次确认 + 方向卡墙)→ 屏3 sample(前三章试读,二次确认)
//   → 工作台(全链在后台铺整本)
//
// 旧链的六屏五闸门(brief 三问方案 / concept 概念打磨 / setup 配置三页签 / launch 点火)
// 整体退场:那些产物层(概念/架构/骨架/大纲)现在只做「可看可改」,挂在工作台的开书区,
// 不再是要作者签字才能往下走的关卡。每屏选择实时 PATCH 落库(刷新不丢、列表页可「继续创建」);
// 想法屏的灵感文字缓存在 localStorage,刷新后回到当前屏接着写。
import { useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { Chip } from "../api";
import CreativeReferenceCard from "../ui/CreativeReferenceCard";
import { SetupStep, STEP_ORDER, STEP_LABEL } from "./onboarding/steps";
import ModeGate from "./onboarding/ModeGate";
import IntentScreen from "./onboarding/IntentScreen";
import SampleScreen from "./onboarding/SampleScreen";
import { scaleDisplay } from "./onboarding/presets";
import { composeRandomSeed } from "./onboarding/randomSeeds";
import { useOnboarding } from "./onboarding/useOnboarding";

// SetupStep 原在本文件定义,保留 re-export 以兼容潜在外部引用
export type { SetupStep };

// 想法屏轻偏好预置:都是高频人话选项,全部可不选;
// tone/elements 的 directive 已在后端 tag_presets 里,这里只放 label 让用户点。
const PREF_TONES = ["热血", "悬疑", "治愈", "甜", "暗黑", "爽"];
const PREF_ELEMENTS = ["逆袭", "马甲", "身份错位", "成长蜕变", "救赎", "群像", "破镜重圆", "契约关系"];
// 排斥项对着题材边界最常拦的套路来(用户明确点了才允许,没点就是「不要」)
const PREF_AVOIDS = ["系统", "重生", "穿越", "觉醒", "异能"];
// 口味定标维度(outline 目录里的 key,按此顺序渲染):收窄抽卡空间的核心四问
const TASTE_DIM_KEYS = ["lead_gender", "romance", "opening", "protagonist"] as const;

export default function OnboardingFlow() {
  const {
    project, err, step, pid,
    tendency, mode,
    spark, entry, genreDim, outlineDims, pickedGenreCard,
    prefTone, prefElements, prefFlavors, prefPersona, prefAvoid, prefAvoidText,
    setSpark, setEntry, setPickedGenreCard,
    setPrefTone, setPrefElements, setPrefFlavors, setPrefPersona, setPrefAvoid, setPrefAvoidText,
    stepsRef, sparkRef,
    submitSpark, pickGenreBrainstorm, pickMode, setDim, creativeSaved,
    dramaSkinList, pickedSkin, pickSkinGo,
    goto, abandon, enterWorkbench,
  } = useOnboarding();

  const [seedHint, setSeedHint] = useState(false);
  // 确认① 返回的样稿 job:带给样稿屏即时订阅(刷新回访时样稿屏自己从 running-jobs 找)
  const [sampleJobId, setSampleJobId] = useState<string | null>(null);

  const allGenreChips: Chip[] = genreDim?.chips ?? [];
  // 想法屏勾的排斥项:作为理解卡回读的硬约束带上(「不要系统、不要穿越」这类)
  const avoidList = [...prefAvoid, ...(prefAvoidText.trim() ? [prefAvoidText.trim()] : [])];

  // 题材页「随机换一张」:全池重抽题材卡 + 顺带抽口味(与随机开一本同一体验语言)
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

  if (!project) return <div className="muted">{err || "正在创建草稿…"}</div>;

  const stepIdx = STEP_ORDER.indexOf(step);
  const card = project.intent_card ?? null;
  const scale = scaleDisplay(Number(project.target_chapters), Number(project.target_words_per_chapter));
  const hasTitle = project.title !== "未命名新书";

  // 顶部步骤条:已确认项的缩略文本(一眼看到「前面定了什么」)
  const thumbOf: Partial<Record<SetupStep, string>> = {
    mode: project.mode === "short" ? "📖 短故事"
      : project.mode === "drama" ? "🎬 漫剧" : "📚 连载",
    idea: project.topic ? project.topic.slice(0, 14) : "",
    intent: card ? (project.brief_confirmed ? "理解卡已确认" : "理解卡已回读") : "",
  };

  return (
    <MotionConfig reducedMotion="user">
      <div className="onboard">
        {/* ===== 左:主流程 ===== */}
        <div className="onboard-main">
          <div className="wiz-steps" ref={stepsRef}>
            {STEP_ORDER.map((s, i) => {
              const done = i < stepIdx;
              const thumb = done ? thumbOf[s] : "";
              return (
                <button key={s} type="button"
                  className={"wiz-step" + (s === step ? " on" : "") + (done ? " done" : "")}
                  onClick={() => i < stepIdx && void goto(s)}>
                  <span className="no">{done ? "✓" : i + 1}</span>
                  <span className="wiz-step-label">{STEP_LABEL[s]}</span>
                  {thumb && <span className="wiz-thumb">{thumb}</span>}
                </button>
              );
            })}
            <div className="grow" />
            <button className="btn-sm" onClick={() => void abandon()}>放弃创建</button>
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

              {/* ---------- 屏 0:开哪种书(模式级分叉) ========== */}
              {step === "mode" && (
                <ModeGate
                  mode={project.mode ?? "serial"}
                  chapters={Number(project.target_chapters)}
                  words={Number(project.target_words_per_chapter)}
                  onPick={(m, preset, audience) => void pickMode(m, preset, audience)} />
              )}

              {/* ---------- 屏 1:想法 ---------- */}
              {step === "idea" && (
                <>
                  {pid !== null && (
                    <CreativeReferenceCard
                      key={pid} scope="project" targetId={pid}
                      form={project.mode === "short" ? "short" : project.mode === "drama" ? "continuous" : "serial"}
                      onSaved={creativeSaved} />
                  )}
                  <div className="card">
                    {project.mode === "drama" ? (
                      <>
                        <h2>想让人追什么?</h2>
                        <div className="card-desc">
                          已选{project.audience === "female" ? "女频" : "男频"}漫剧——先挑个爽文题材(选完 AI 按它回读理解卡);
                          已经有具体点子就直接写,挑不挑都行。
                        </div>
                        <div className="pref-row mt-2">
                          <span className="pref-label">爽文题材<small>选一个,理解卡与方向都围着它转</small></span>
                          <div className="genre-cards">
                            {dramaSkinList.map((sk) => (
                              <button key={sk.key} type="button"
                                className={"genre-card" + (pickedSkin === sk.label ? " on" : "")}
                                onClick={() => void pickSkinGo(sk.label)}>
                                <b>{sk.label}</b>
                                <span>{sk.desc}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      </>
                    ) : (
                      <>
                        <h2>这本书的核心是什么?</h2>
                        <div className="card-desc">
                          一句话、一个画面、一个设定都行——写下来,AI 先把它回读成一张理解卡,
                          你对一遍再让它写前三章。
                        </div>
                      </>
                    )}
                    <textarea ref={sparkRef} rows={3} className="mt-2" value={spark}
                      onChange={(e) => setSpark(e.target.value)}
                      placeholder={project.mode === "drama"
                        ? "有具体点子就写(如:废柴杂役被逐出宗门,捡到一本万妖图录…);不写也行,选好题材直接走"
                        : "如:落魄镖师接下一趟险镖,半路开箱验货时发现镖箱里藏着个大活人…"}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey && spark.trim()) {
                          e.preventDefault();
                          void submitSpark();
                        }
                      }} />
                    <div className="actions mt-2">
                      {project.mode === "drama" ? (
                        <button className="primary" onClick={() => void pickSkinGo(pickedSkin || null)}>
                          {pickedSkin ? `按「${pickedSkin}」走 →` : "🎲 让 AI 按货架出三套爽文方向 →"}
                        </button>
                      ) : (
                        <button className="primary" disabled={!spark.trim()} onClick={() => void submitSpark()}>
                          💬 就按这个,让 AI 复述一遍 →
                        </button>
                      )}
                      {project.mode !== "drama" && (
                        <>
                          <button onClick={randomBook}>🎴 随机开一本</button>
                          <button onClick={() => setEntry(entry ? null : "more")}>
                            {entry ? "收起" : "没有灵感?"}
                          </button>
                        </>
                      )}
                    </div>

                    {/* 口味定标:对所有路径可见——理解卡回读与方向卡都会立 pickedGenreCard,
                        收窄生成空间;全部可跳过,一道不选与旧版一致 */}
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
                        已抽到一句灵感(已填入上框,可随意改)——觉得方向对,就点「就按这个」;
                        不对就再抽一次,重抽不花 token。
                      </div>
                    )}

                    {entry === "more" && (
                      <div className="entry-cards">
                        <button type="button" className="entry-card"
                          onClick={() => { setEntry(null); sparkRef.current?.focus(); }}>
                          <h3>💡 我有个想法</h3>
                          <p>一句话、一个画面、一个设定,写下来让 AI 回读成理解卡。</p>
                        </button>
                        <button type="button" className="entry-card" onClick={() => setEntry("genre")}>
                          <h3>📚 我知道想写什么类型</h3>
                          <p>赘婿流、无限流、克苏鲁…选个流派定口味,AI 出三套方向供你挑。</p>
                        </button>
                        <button type="button" className="entry-card"
                          onClick={() => { setEntry(null); void goto("intent"); }}>
                          <h3>💬 完全没头绪</h3>
                          <p>直接往下走:AI 先复述它听到的,拿不准的地方它会给你候选。</p>
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

                        <div className="actions mt-3">
                          <button className="primary" disabled={!pickedGenreCard}
                            onClick={() => void pickGenreBrainstorm()}>
                            💬 按这个流派,让 AI 复述一遍 →
                          </button>
                          <button onClick={randomizeDraft}>🎴 随机换一张</button>
                          <button onClick={() => setEntry(null)}>← 换个方式</button>
                        </div>
                      </div>
                    )}

                    {project.mode !== "drama" && (
                      <div className="actions mt-4 onboard-nav">
                        <span className="grow" />
                        <button onClick={() => void goto("intent")}>先不定,直接往下走 →</button>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* ---------- 屏 2:理解(理解卡一次确认 + 方向卡墙) ---------- */}
              {step === "intent" && pid !== null && (
                <IntentScreen
                  pid={pid}
                  mode={mode}
                  avoid={avoidList}
                  onBack={() => void goto("idea")}
                  onConfirmed={({ sampleJobId: jid }) => {
                    setSampleJobId(jid);
                    void goto("sample");
                  }} />
              )}

              {/* ---------- 屏 3:样稿(前三章试读,确认②) ---------- */}
              {step === "sample" && pid !== null && (
                <SampleScreen
                  pid={pid}
                  initialJobId={sampleJobId}
                  onBackToIntent={() => { setSampleJobId(null); void goto("intent"); }}
                  onEnterWorkbench={() => void enterWorkbench()} />
              )}
            </motion.div>
          </AnimatePresence>

          {err && <div className="msg-err mt-2">{err}</div>}
        </div>

        {/* ===== 右:本书档案 ===== */}
        <div className="onboard-side">
          <div className="dossier">
            <div className="dossier-cover">
              <span>{hasTitle ? project.title : "未命名新书"}</span>
            </div>
            <div className="dossier-rows">
              <div className="dossier-row ok">
                <span className="dr-k">模式</span>
                <span className="dr-v">{project.mode === "short" ? "📖 短故事(一次讲完)"
                      : project.mode === "drama" ? `🎬 漫剧源书(${project.audience === "female" ? "女频" : "男频"})`
                      : "📚 开书连载"}</span>
              </div>
              <div className={"dossier-row" + (project.topic ? " ok" : "")}>
                <span className="dr-k">想法</span>
                <span className="dr-v wrap">{project.topic || "未定"}</span>
              </div>
              <div className={"dossier-row" + (card ? " ok" : "")}>
                <span className="dr-k">世界</span>
                <span className="dr-v wrap">{card?.world || "未定"}</span>
              </div>
              <div className={"dossier-row" + (card ? " ok" : "")}>
                <span className="dr-k">主角</span>
                <span className="dr-v wrap">{card?.viewpoint || "未定"}</span>
              </div>
              <div className={"dossier-row" + (project.genre ? " ok" : "")}>
                <span className="dr-k">题材</span>
                <span className="dr-v">{project.genre || "未定"}</span>
              </div>
              <div className={"dossier-row" + (hasTitle ? " ok" : "")}>
                <span className="dr-k">书名</span>
                <span className="dr-v">{hasTitle ? project.title : "未定"}</span>
              </div>
              <div className={"dossier-row" + (scale.decided ? " ok" : "")}>
                <span className="dr-k">篇幅</span>
                <span className="dr-v">{scale.text}</span>
              </div>
            </div>

            {/* 理解卡确认后:侧栏把「全书硬约束」摆在眼前(改稿时随时回看) */}
            {card && (
              <div className="dossier-card mt-3">
                <div className="dossier-card-head">
                  <b>理解卡</b>
                  <span className="grow" />
                  <span className={"badge " + (project.brief_confirmed ? "ok" : "")}>
                    {project.brief_confirmed ? "已确认 ✓" : "草稿"}
                  </span>
                </div>
                {card.viewpoint && <p className="hint">视角与主角:{card.viewpoint}</p>}
                {!!card.flavor?.length && <p className="hint">味道:{card.flavor.join(" / ")}</p>}
                {!!card.must?.length && <p className="hint">必须保留:{card.must.join("、")}</p>}
                {!!card.avoid?.length && <p className="hint">不要出现:{card.avoid.join("、")}</p>}
              </div>
            )}
          </div>
        </div>
      </div>
    </MotionConfig>
  );
}
