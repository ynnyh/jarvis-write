// 起步流的状态机:所有 state / effect / handler 集中于此,视图组件只消费其返回值。
// 拆自 OnboardingFlow.tsx —— 逻辑与渲染分离,hook 调用顺序、effect 依赖原样保留。
//
// 开书对话式确认流(2026-09-24 重构,作者拍板的五步主线):
//   想法 → 简介(和策划聊/🎲提案兜底 → 开书订单拍板) → 概念(订单深化→打磨拍板)
//   → 配置 → 点火
// 「选流派/自写想法直接抽卡」的旧入口交互整体废除:引擎卡墙、概念卡墙、混搭、
// 锚点重抽、带话重出批全部下线;收敛靠对谈,发散只剩 🎲 提案(勾起拍板,不是方案)。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  api, ChatTurn, Chip, Concept, conceptIsEmpty, Dimension,
  EMPTY_CONCEPT, Pitch, Project, ShapeSuggestion, Tendency,
} from "../../api";
import { useJob } from "../../ui/useJob";
import { pollJob, errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { titleSig as calcTitleSig } from "../wizSig";
import { SetupStep, STEP_ORDER, SETUP_STATE, parseStep } from "./steps";
import { WizCache, Dirty, loadJSON, saveJSON, sweepLegacyWizKeys, wizKeys } from "./storage";
import { SCALE_PRESETS, PipeStep, PIPE_WAIT } from "./presets";

export function useOnboarding() {
  const { id, step: stepParam } = useParams();
  const nav = useNavigate();
  const { run: runJob } = useJob();
  const pid = id ? Number(id) : null;
  const step: SetupStep = parseStep(stepParam);

  const [project, setProject] = useState<Project | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const stepsRef = useRef<HTMLDivElement | null>(null);

  // 窄屏步骤条横滚时,当前步可能滚出视野,换步后拉回可见区
  useEffect(() => {
    stepsRef.current
      ?.querySelector(".wiz-step.on")
      ?.scrollIntoView({ inline: "nearest", block: "nearest", behavior: "smooth" });
  }, [step]);

  // 想法屏
  const [entry, setEntry] = useState<"more" | "genre" | null>(null);
  const [spark, setSpark] = useState("");
  const [genreDim, setGenreDim] = useState<Dimension | null>(null);
  // 口味定标(P1):outline 目录整体缓存,偏好面板据此渲染感情线/开局强度/主角底色等维度
  const [outlineDims, setOutlineDims] = useState<Dimension[]>([]);
  // 选中的题材卡(含 flavors 分叉):按书持久化,刷新/回跳不丢
  const [pickedGenreCard, setPickedGenreCard] = useState<Chip | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const sparkRef = useRef<HTMLTextAreaElement | null>(null);
  // 轻偏好:基调/元素/流派口味/画像/避雷,全部可选可跳过,收窄对谈与生成的空间
  const [prefTone, setPrefTone] = useState<string[]>([]);
  const [prefElements, setPrefElements] = useState<string[]>([]);
  const [prefFlavors, setPrefFlavors] = useState<string[]>([]);
  const [prefPersona, setPrefPersona] = useState("");
  const [prefAvoid, setPrefAvoid] = useState<string[]>([]);
  const [prefAvoidText, setPrefAvoidText] = useState("");

  // 简介屏(对话式确认流 L0):聊/提案 → 开书订单草稿 → 拍板
  const [briefInput, setBriefInput] = useState("");   // 对谈输入框
  const [briefDraft, setBriefDraft] = useState("");   // 订单手改草稿
  const [ideaCards, setIdeaCards] = useState<Pitch[] | null>(null); // 🎲 提案(三选一兜底)
  const [pitchFeedback, setPitchFeedback] = useState(""); // 对上一批提案的修改要求(带话重出)
  // 开场只烧一次:想法路把灵感句自动发进对谈 / 空手路自动出提案
  const briefAutoFor = useRef("");

  // 概念屏:拍板订单深化的结果直接进打磨房;自己写概念是逃生口
  const [customOpen, setCustomOpen] = useState(false);
  const [customConcept, setCustomConcept] = useState<Concept>({ ...EMPTY_CONCEPT });
  const developedFor = useRef(""); // 已深化过的订单文本,防重复烧 token
  const [developing, setDeveloping] = useState(false);
  // 轮廓推荐(阅读手感+篇幅):概念深化完成后要一次,配置页签预填用
  const [shapeSug, setShapeSug] = useState<ShapeSuggestion | null>(null);

  // 题材屏(配置页签)
  const [inferBusy, setInferBusy] = useState(false);
  const [genreSuggests, setGenreSuggests] = useState<Chip[]>([]);
  const [suggestPage, setSuggestPage] = useState(0);
  const [customGenre, setCustomGenre] = useState("");

  // 书名屏(配置页签)
  const [titleIdeas, setTitleIdeas] = useState<string[] | null>(null);
  const [titleSig, setTitleSig] = useState<string | null>(null);
  const [titleBusy, setTitleBusy] = useState(false);
  const [titleInput, setTitleInput] = useState("");
  const titleInputRef = useRef<HTMLInputElement | null>(null);

  // 篇幅屏(配置页签)
  const [chapters, setChapters] = useState("");
  const [words, setWords] = useState("");
  const [advOpen, setAdvOpen] = useState(false);

  // 选用卡 FLIP:飞入顶部步骤条缩略位
  const [fly, setFly] = useState<{ step: SetupStep; text: string } | null>(null);
  const [pickedKey, setPickedKey] = useState<string | null>(null);

  // 概念打磨房(确认链 L1):深化/自写出的概念在打磨房过目、改、拍板。
  // forgeSeed 只在「换了一个概念」时自增(驱动 ConceptForge 重挂载)。
  const [forgeOpen, setForgeOpen] = useState(false);
  const [forgeSeed, setForgeSeed] = useState(0);
  const forgeDismissed = useRef("");

  // 确认墙:回改上游后,下游已确认项标"可能受影响"
  const [dirty, setDirty] = useState<Dirty | null>(null);

  // 点火流水线
  const [arch, setArch] = useState<PipeStep>(PIPE_WAIT);
  const [bp, setBp] = useState<PipeStep>(PIPE_WAIT);
  // 信任模式(docs/20 §4.4):跳过骨架墙一枪铺全书;默认 false = 架构完停在骨架墙
  const [trustMode, setTrustMode] = useState(false);
  const trustRef = useRef(false);
  trustRef.current = trustMode;
  const pipeInit = useRef(false);

  const concept: Concept = useMemo(
    () => ({ ...EMPTY_CONCEPT, ...(project?.concept ?? {}) }),
    [project],
  );
  const tendency: Tendency = project?.global_tendency ?? {};
  const conceptText = [
    concept.logline, concept.hook, concept.protagonist, concept.setting,
  ].filter((s) => s?.trim()).join("\n") || project?.topic || "";
  const sparkText = spark.trim() || project?.topic?.trim() || "";
  const briefText = (project?.brief ?? "").trim();
  const briefConfirmed = !!project?.brief_confirmed;
  const chatLog: ChatTurn[] = project?.chat_log ?? [];

  // ---------- 建草稿 / 载入(含 localStorage 恢复) ----------
  // 只建一次草稿:StrictMode/重渲染下 effect 可能重入,无守卫会静默建出多个空项目
  const createdRef = useRef(false);
  useEffect(() => {
    sweepLegacyWizKeys(); // v1 遗留键没有所有权标记,一次性清掉(见 storage.ts 注释)
    if (pid !== null) {
      // 跨书状态重置(串档防线三):/new/1 → /new/2 是同路由参数变化,组件不卸载,
      // 上一本书的屏级 state(灵感文字/提案/订单草稿/开场标记)会残留进新书——
      // localStorage 有 v2 键隔离,React state 没有,必须在 pid 变化时显式清场。
      setSpark(""); setEntry(null); setPickedGenreCard(null);
      setIdeaCards(null); setBriefInput(""); setBriefDraft("");
      briefAutoFor.current = ""; developedFor.current = "";
      setCustomOpen(false); setForgeOpen(false); setForgeSeed(0);
      forgeDismissed.current = "";
      api.getProject(pid).then((p) => {
        setProject(p);
        setTitleInput(p.title === "未命名新书" ? "" : p.title);
        setChapters(String(p.target_chapters));
        setWords(String(p.target_words_per_chapter));
        const c = loadJSON<WizCache>(wizKeys(pid).cache);
        // 所有权校验(开书串档防线二):缓存记录的项目创建时间与当前项目对不上,
        // 说明这份缓存属于一个已删除的同号旧项目,整份丢弃
        const cacheOwned = !c?.createdAt || !p.created_at || c.createdAt === p.created_at;
        if (!c || cacheOwned) {
          if (c) {
            setSpark(c.spark); setTitleIdeas(c.titleIdeas);
            setTitleSig(c.titleSig ?? null);
            setIdeaCards(c.ideaCards ?? null);
          }
          setDirty(loadJSON<Dirty>(wizKeys(pid).dirty));
        }
        // 确认链 L1:回到概念屏且已有概念但未拍板 → 直接进打磨房(自然续接)
        if (p.concept && !conceptIsEmpty(p.concept) && !p.concept_confirmed) {
          setForgeOpen(true);
        }
        // 直达续建:无 step 参数时按 setup_state 落到对应屏
        if (!stepParam) {
          nav(`/new/${pid}/${p.setup_state ? parseStep(p.setup_state) : "idea"}`, { replace: true });
        }
      }).catch((e) => setErr(errMsg(e)));
      return;
    }
    // /new 无 id:静默创建草稿项目,replace 进第一步(createdRef 防重复建)
    if (createdRef.current) return;
    createdRef.current = true;
    api.createProject({ title: "未命名新书", setup_state: "idea" })
      .then((p) => nav(`/new/${p.id}/idea`, { replace: true }))
      .catch((e) => { createdRef.current = false; setErr(errMsg(e)); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  // 流派卡片墙 + 口味维度数据(想法屏/配置屏共用)
  useEffect(() => {
    api.tendencyCatalog("outline").then((cat) => {
      setGenreDim(cat.dimensions.find((d) => d.key === "genre") ?? null);
      setOutlineDims(cat.dimensions);
    }).catch(() => undefined);
  }, []);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [project?.chat_log, busy]);

  // 候选内容写入 localStorage:刷新后回到当前屏接着选。createdAt 随存(所有权校验)
  useEffect(() => {
    if (pid === null || !project) return;
    saveJSON(wizKeys(pid).cache, {
      spark, titleIdeas, titleSig, ideaCards,
      createdAt: project.created_at ?? undefined,
    } satisfies WizCache);
  }, [pid, project, spark, titleIdeas, titleSig, ideaCards]);

  const patch = useCallback(async (updates: Partial<Project> & { setup_state?: string }) => {
    if (pid === null) return null;
    const p = await api.patchProject(pid, updates);
    setProject(p);
    return p;
  }, [pid]);

  // ---------- 确认墙"可能受影响"标记 ----------
  function markDirtyOk(s: SetupStep) {
    setDirty((prev) => {
      if (!prev || prev.ok.includes(s)) return prev;
      const d = { ...prev, ok: [...prev.ok, s] };
      saveJSON(`wiz-dirty:${pid}`, d);
      return d;
    });
  }
  function editFrom(s: SetupStep) {
    const d: Dirty = { from: s, ok: [] };
    setDirty(d);
    saveJSON(`wiz-dirty:${pid}`, d);
    nav(`/new/${pid}/${s}`);
  }

  async function goto(next: SetupStep) {
    // 回改上游后向前确认下游屏:视为用户已重看该屏,摘掉"可能受影响"
    if (dirty && STEP_ORDER.indexOf(step) > STEP_ORDER.indexOf(dirty.from)
        && STEP_ORDER.indexOf(next) > STEP_ORDER.indexOf(step)) {
      markDirtyOk(step);
    }
    try { await patch({ setup_state: SETUP_STATE[next] }); } catch { /* 步进不因保存失败而卡死 */ }
    nav(`/new/${pid}/${next}`);
  }

  // 选用/拍板卡:PATCH 落库 + FLIP 飞入顶部缩略位,稍作停留再进下一屏
  function flyTo(s: SetupStep, text: string, next: SetupStep) {
    setFly({ step: s, text });
    window.setTimeout(() => { setFly(null); setPickedKey(null); void goto(next); }, 420);
  }

  // ---------- 第 1 屏:想法 ----------
  // 「和策划聊聊」:灵感/方向落库后进简介屏——对谈在那里发生,这里不再出任何卡
  async function submitSpark() {
    const t = spark.trim();
    if (!t) return;
    try { await patch({ topic: t }); } catch { /* 灵感落库失败不阻塞 */ }
    await goto("brief");
  }

  // 选流派出方案:流派+口味写成初始信号落库(对谈/提案的硬上下文),进简介屏开场
  async function pickGenreBrainstorm() {
    if (!pickedGenreCard) return;
    const t: Tendency = { ...tendency, genre: pickedGenreCard.label };
    if (prefTone.length) t.tone = prefTone;
    if (prefElements.length) t.elements = prefElements;
    const extras: string[] = [];
    if (prefFlavors.length) extras.push(`流派口味(在这个流派里,只想看这些子类型):${prefFlavors.join("、")}`);
    if (prefPersona.trim()) extras.push(`主角画像:${prefPersona.trim()}`);
    if (prefAvoid.length) extras.push(`不要出现:${prefAvoid.join("、")}`);
    if (prefAvoidText.trim()) extras.push(`不要出现:${prefAvoidText.trim()}`);
    const text = extras.length
      ? `按「${pickedGenreCard.label}」的套路来。${extras.join(";")}`
      : `按「${pickedGenreCard.label}」的套路来`;
    setSpark(text);
    try {
      await patch({
        global_tendency: t, genre: pickedGenreCard.label, topic: text,
      });
    } catch { /* 同上 */ }
    await goto("brief");
  }

  // ---------- 第 2 屏:简介(对话式确认流) ----------
  // 一轮对谈:乐观上屏(先显作者的话),成功落服务端线程+新订单草稿(自动重新上锁),
  // 失败回滚本地线程。message 为空时不发(纯手改订单不经过这里)。
  async function sendBrief(raw?: string) {
    const text = (raw ?? briefInput).trim();
    if (!text || !project || busy) return;
    const log: ChatTurn[] = [...chatLog, { role: "user", content: text }];
    setBriefInput("");
    setProject({ ...project, chat_log: log });
    setBusy("策划正在接住你的想法…"); setErr("");
    try {
      const r = await api.briefChat(pid!, text);
      setProject(r.project);
      setBriefDraft(r.brief);
      setIdeaCards(null); // 已经聊起来了,提案区收起
    } catch (e) {
      setErr(errMsg(e));
      setProject((prev) => (prev ? { ...prev, chat_log: log.slice(0, -1) } : prev));
    } finally { setBusy(""); }
  }

  // 🎲 没灵感兜底:出 3 个方向提案(FAST 档);「再来一组」带上一批避免趋同;
  // 带话重出把修改要求以最高优先级注入
  async function fetchPitches(avoid: string[] = [], feedback = "") {
    if (busy) return;
    setErr("");
    const fb = (feedback || pitchFeedback).trim();
    setBusy(fb ? "AI 正在按你的要求重新出提案…" : "AI 正在出三个方向提案…");
    try {
      const r = await runJob<{ pitches: Pitch[] }>(
        () => api.pitchesAsync(sparkText, tendency, project?.dna ?? null, avoid, fb),
        { kind: "inspire" },
      );
      if (r) setIdeaCards(r.pitches);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  // 选中提案:提案变成作者的一句话,进对谈由策划接住聊实(提案不是方案,仍要拍板)
  function pickPitch(p: Pitch) {
    const text = `我选「${p.label || "这个方向"}」:${p.pitch}`;
    void sendBrief(text);
  }

  // 订单手改:保存 = 新草稿,后端自动重新上锁(重新拍板才往下走)
  async function saveBriefDraft() {
    if (!project) return;
    const t = briefDraft.trim();
    if (!t) { setErr("订单不能为空"); return; }
    if (t === briefText) return; // 没改就不落库
    try {
      const p = await patch({ brief: t });
      if (p) toast.ok("订单已更新", "内容变了要重新拍板;继续聊也可以");
    } catch (e) { setErr(errMsg(e)); }
  }

  // 拍板:这版订单就是全书的硬约束;飞入进度条进概念屏
  async function confirmBrief() {
    if (!briefText) return;
    try {
      await patch({ brief_confirmed: true });
      toast.ok("开书订单已拍板", "接下来 AI 照单深化概念;订单仍是硬约束");
      flyTo("brief", "订单已拍板 ✓", "concept");
    } catch (e) { setErr(errMsg(e)); }
  }

  // 撤拍板:改主意了回简介屏继续聊/手改(确认是权利不是门槛)
  async function unconfirmBrief() {
    try {
      await patch({ brief_confirmed: false });
      toast.ok("已撤回拍板", "订单回到草稿态,聊/改完再拍");
    } catch (e) { setErr(errMsg(e)); }
  }

  // 简介屏开场只烧一次:自写想法 → 自动把灵感句发进对谈;空手/方向路 → 自动出提案
  useEffect(() => {
    if (step !== "brief" || !project || busy) return;
    if (chatLog.length > 0 || briefText) return; // 聊过了/已有草稿:自然续接
    const key = sparkText || "(空手)";
    if (briefAutoFor.current === key) return;
    briefAutoFor.current = key;
    if (sparkText) void sendBrief(sparkText);
    else void fetchPitches();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, project]);

  // ---------- 第 3 屏:概念(订单深化 → 打磨房) ----------
  // 进屏自动把拍板订单交给强模型深化(一次);深化 prompt 里订单是最高约束。
  // 手动「重新深化」走同一入口(developedFor 清掉即可)。
  async function developFromBrief() {
    if (!project || !briefConfirmed || !briefText || busy) return;
    developedFor.current = briefText;
    setErr(""); setDeveloping(true);
    try {
      const r = await runJob<{ concept: Concept }>(
        () => api.conceptFromBriefAsync(pid!),
        { kind: "inspire" },
      );
      if (r) {
        const p = await patch({ concept: r.concept });
        if (p) {
          setForgeOpen(true);
          setForgeSeed((n) => n + 1);
          toast.ok("概念已按订单深化", "进打磨房逐项过目、改、拍板");
          // 方案定了 → 顺手要一份「阅读手感 + 篇幅」推荐(轻量调用,失败静默)
          api.suggestShape(pid!).then((sug: ShapeSuggestion) => {
            setShapeSug(sug);
          }).catch(() => undefined);
        }
      }
    } catch (e) { setErr(errMsg(e)); developedFor.current = ""; }
    finally { setDeveloping(false); }
  }

  // 概念屏自动深化(有概念则不重复烧):签名=订单文本,订单变了才允许再烧
  useEffect(() => {
    if (step !== "concept" || !project) return;
    if (!conceptIsEmpty(concept) || developing) return;
    if (!briefConfirmed || !briefText) return;
    if (developedFor.current === briefText) return;
    void developFromBrief();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, project]);

  // 概念相对订单的过期提示:订单改过(签名对不上)但概念还在 → 标黄让作者决定
  const conceptStaleVsBrief = !!briefText && developedFor.current !== "" && developedFor.current !== briefText;

  async function saveCustomConcept() {
    if (conceptIsEmpty(customConcept)) { setErr("至少填一个字段再保存"); return; }
    try { await patch({ concept: customConcept }); } catch (e) { setErr(errMsg(e)); return; }
    setCustomOpen(false);
    setForgeOpen(true);
    setForgeSeed((n) => n + 1);
  }

  // 打磨房回调:内容变化(手改/重捏落库后)同步本地 project;拍板 → 飞入配置屏
  function forgeChanged(c: Concept) {
    setProject((prev) => (prev ? { ...prev, concept: c } : prev));
  }
  function forgeConfirmed(c: Concept) {
    toast.ok("概念已拍板", "它现在是全书的硬约束;配置屏里仍可改(改后需重新拍板)");
    flyTo("concept", c.logline || "概念已拍板", "setup");
  }
  function forgeUnconfirmed() {
    setProject((prev) => (prev ? { ...prev, concept_confirmed: false } : prev));
  }
  /** 这版概念的打磨房是否被作者收起过(只读,effect 用) */
  function isForgeDismissedFor(key: string) {
    return forgeDismissed.current === key;
  }
  /** 收起打磨房(记住收起时对着哪版概念,同一版不再自动弹开) */
  function dismissForge(key: string) {
    forgeDismissed.current = key;
    setForgeOpen(false);
  }
  /** 重新打开打磨房(清除收起记忆) */
  function reopenForge() {
    forgeDismissed.current = "";
    setForgeOpen(true);
  }

  // ---------- 配置屏:题材 AI 预填(进屏推断一次,成功落库不跳屏) ----------
  useEffect(() => {
    if (step !== "setup" || !conceptText.trim() || tendency.genre) return;
    setInferBusy(true);
    api.genreInfer(conceptText).then(async (r) => {
      setGenreSuggests(r.suggestions.map((s) => ({ directive: "", ...s })));
      if (r.genre) {
        await patch({ global_tendency: { ...tendency, genre: r.genre } });
        toast.ok(`题材已定为「${r.genre}」`, "不对就在配置屏换一个或自己写");
      }
      // 推断为空:停在配置屏,用户手选或自写
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

  async function fetchTitles(feedback = "") {
    // 签名只取规范字段(不含一次性反馈词),与 suggestTitle 的语义入参一致
    const sig = calcTitleSig(project?.topic ?? "", (tendency.genre as string) ?? "", concept);
    setTitleBusy(true); setErr(""); setTitleIdeas(null);
    try {
      const { job_id } = await api.suggestTitleAsync(
        (project?.topic ?? "") + (feedback ? `(命名偏好:${feedback})` : ""),
        (tendency.genre as string) ?? "",
        conceptIsEmpty(concept) ? null : concept,
      );
      const r = await pollJob<{ titles: string[] }>(job_id, { intervalMs: 1500 });
      setTitleIdeas(r.titles);
      setTitleSig(sig);
    } catch (e) { setErr(errMsg(e)); setTitleIdeas([]); } finally { setTitleBusy(false); }
  }

  async function pickTitle(t: string) {
    const v = t.trim();
    if (!v) { setErr("先选一个候选或自己写一个书名"); return; }
    setTitleInput(v);
    try {
      await patch({ title: v });
      toast.ok("书名已定", "进工作台后随时可改");
    } catch (e) { setErr(errMsg(e)); }
  }

  // ---------- 篇幅(配置页签) ----------
  async function pickScale(preset: typeof SCALE_PRESETS[number]) {
    setChapters(String(preset.chapters)); setWords(String(preset.words));
    await patch({ target_chapters: preset.chapters, target_words_per_chapter: preset.words });
  }

  async function confirmScale() {
    const ch = Number(chapters), w = Number(words);
    if (!Number.isInteger(ch) || ch < 1 || ch > 5000) { setErr("章节数需为 1-5000 的整数"); return; }
    if (!Number.isInteger(w) || w < 200 || w > 20000) { setErr("每章字数需为 200-20000 的整数"); return; }
    await patch({ target_chapters: ch, target_words_per_chapter: w });
  }

  // 开放式连载开关(篇幅页签勾选):True=结局未定,架构只定长线引擎+首批方向
  const openEnded = !!project?.open_ended;
  async function toggleOpenEnded(v: boolean) {
    await patch({ open_ended: v });
  }

  // ---------- 第 5 屏:点火流水线 ----------
  function reattach(kind: "arch" | "bp", jobId: string, stage: string) {
    const set = kind === "arch" ? setArch : setBp;
    set({ status: "run", stage: stage || "生成中", error: "" });
    pollJob(jobId, { onStage: (s) => set((p) => (p.status === "run" ? { ...p, stage: s } : p)) })
      .then(() => {
        set({ status: "done", stage: "", error: "" });
        // docs/20 两段式点火:默认架构完停在骨架墙;信任模式保持旧链路直通蓝图
        if (kind === "arch" && trustRef.current) void runBp();
      })
      .catch((e) => set({ status: "err", stage: "", error: errMsg(e) }));
  }

  async function runArch() {
    if (pid === null || arch.status === "run") return;
    setArch({ status: "run", stage: "排队中", error: "" });
    try {
      const r = await runJob(() => api.generateArchitectureAsync(pid, tendency), {
        kind: "architecture",
        onStage: (s) => setArch((a) => (a.status === "run" ? { ...a, stage: s } : a)),
      });
      if (r === null) return; // 本地等待被中止(切走),任务继续在后台跑
      setArch({ status: "done", stage: "", error: "" });
      // docs/20 两段式点火:默认停在骨架墙;信任模式直通蓝图
      if (trustRef.current) void runBp();
    } catch (e) {
      setArch({ status: "err", stage: "", error: errMsg(e) });
    }
  }

  async function runBp() {
    if (pid === null || bp.status === "run") return;
    setBp({ status: "run", stage: "排队中", error: "" });
    try {
      const r = await runJob(() => api.generateBlueprintAsync(pid, tendency), {
        kind: "blueprint",
        onStage: (s) => setBp((b) => (b.status === "run" ? { ...b, stage: s } : b)),
      });
      if (r === null) return;
      setBp({ status: "done", stage: "", error: "" });
    } catch (e) {
      setBp({ status: "err", stage: "", error: errMsg(e) });
    }
  }

  // 旧流水线屏恢复(信任模式专用;闸门模式的架构自举在 ArchGate 内)
  useEffect(() => {
    if (step !== "launch" || pid === null || pipeInit.current || !trustMode) return;
    pipeInit.current = true;
    (async () => {
      const { jobs } = await api.runningJobs(pid)
        .catch(() => ({ jobs: [] as { job_id: string; kind: string; stage: string }[] }));
      const jArch = jobs.find((j) => j.kind.startsWith("architecture-"));
      const jBp = jobs.find((j) => j.kind.startsWith("blueprint-"));
      if (jArch) { reattach("arch", jArch.job_id, jArch.stage); return; }
      if (jBp) {
        setArch({ status: "done", stage: "", error: "" });
        reattach("bp", jBp.job_id, jBp.stage);
        return;
      }
      let archDone = false, bpDone = false;
      try { const a = await api.getArchitecture(pid); archDone = !!a.core_seed?.trim(); }
      catch { /* 无架构 */ }
      try { const o = await api.listOutlines(pid); bpDone = o.length > 0; }
      catch { /* 无蓝图 */ }
      if (archDone) setArch({ status: "done", stage: "", error: "" });
      if (bpDone) setBp({ status: "done", stage: "", error: "" });
      // 点火标记带项目创建时间戳:id 被复用(换库/回滚)时旧标记不拦新书的自动点火
      const createdStamp = project?.created_at ?? "";
      if (!archDone && loadJSON<string>(wizKeys(pid).pipe) !== createdStamp) {
        saveJSON(wizKeys(pid).pipe, createdStamp);
        void runArch();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, pid]);

  async function enterWorkbench() {
    if (pid === null) return;
    try { await patch({ setup_state: "" }); } catch { /* 不阻塞进台 */ }
    // 刚点完火的心智是「开始写」——落写作区第 1 章,而不是开书区(架构卡)。
    nav(`/project/${pid}/write?ch=1`);
  }

  async function abandon() {
    if (pid === null || !project) return;
    // 已有实质产出(起步流已过「想法」步,或进了流水线):删除前确认
    const progressed = !!project.setup_state && project.setup_state !== "idea";
    if (progressed) {
      const ok = await confirmDialog({
        title: "放弃创建并删除该项目?",
        body: "将删除该项目及已生成内容(订单/概念/架构/蓝图等),不可恢复。",
        confirmText: "放弃并删除",
        danger: true,
      });
      if (!ok) return;
    }
    try {
      await api.deleteProject(pid);
      if (project) {
        const k = wizKeys(pid);
        localStorage.removeItem(k.cache);
        localStorage.removeItem(k.dirty);
        localStorage.removeItem(k.pipe);
      }
      toast.ok("已放弃创建");
      nav("/");
    } catch (e) { setErr(errMsg(e)); }
  }

  return {
    // 基础 / 路由
    project, err, step, pid, nav,
    // 派生
    concept, tendency, sparkText, briefText, briefConfirmed, chatLog,
    conceptStaleVsBrief, allGenreChips, shownSuggests, shapeSug,
    // 想法屏 state
    spark, entry, genreDim, outlineDims, pickedGenreCard,
    prefTone, prefElements, prefFlavors, prefPersona, prefAvoid, prefAvoidText,
    // 简介屏 state
    briefInput, briefDraft, ideaCards, busy, pitchFeedback,
    // 概念屏 state
    customOpen, customConcept, developing,
    // 配置屏 state
    inferBusy, customGenre, genreSuggests, suggestPage,
    titleIdeas, titleSig, titleBusy, titleInput,
    chapters, words, advOpen, openEnded,
    fly, pickedKey, dirty, arch, bp, setBp, trustMode, setTrustMode,
    forgeOpen, forgeSeed, setForgeOpen,
    // setter
    setSpark, setEntry, setPickedGenreCard,
    setPrefTone, setPrefElements, setPrefFlavors, setPrefPersona, setPrefAvoid, setPrefAvoidText,
    setBriefInput, setBriefDraft, setIdeaCards, setPitchFeedback,
    setCustomOpen, setCustomConcept,
    setGenreSuggests, setSuggestPage, setCustomGenre,
    setTitleSig, setTitleInput, setChapters, setWords, setAdvOpen, setDirty,
    // ref
    stepsRef, chatEndRef, sparkRef, titleInputRef,
    // handler
    submitSpark, pickGenreBrainstorm,
    sendBrief, fetchPitches, pickPitch, saveBriefDraft, confirmBrief, unconfirmBrief,
    developFromBrief, saveCustomConcept,
    forgeChanged, forgeConfirmed, forgeUnconfirmed, isForgeDismissedFor, dismissForge, reopenForge,
    setGenre, setDim, fetchTitles, pickTitle, pickScale, confirmScale, toggleOpenEnded,
    runArch, runBp, enterWorkbench, abandon, goto, editFrom, markDirtyOk,
  };
}

// 方向路判定留作语义说明:spark 以「按「XX」的套路来」开头即方向路
// (选流派进来);现在对谈开场统一处理,不再据此分流。
