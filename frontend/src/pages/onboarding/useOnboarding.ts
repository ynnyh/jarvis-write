// 起步流的状态机:所有 state / effect / handler 集中于此,视图组件只消费其返回值。
// 拆自 OnboardingFlow.tsx —— 逻辑与渲染分离,零行为变化(hook 调用顺序、effect 依赖原样保留)。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  api, ChatTurn, Chip, Concept, conceptIsEmpty, Dimension,
  EMPTY_CONCEPT, EngineCard, Project, RefineResult, Tendency,
} from "../../api";
import { useJob } from "../../ui/useJob";
import { pollJob, errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { conceptSig, titleSig as calcTitleSig } from "../wizSig";
import { SetupStep, STEP_ORDER, SETUP_STATE, parseStep } from "./steps";
import { WizCache, Dirty, loadJSON, saveJSON, sweepLegacyWizKeys, wizKeys } from "./storage";
import { SCALE_PRESETS, PipeStep, PIPE_WAIT } from "./presets";
import { conceptKey } from "./ConceptBrief";

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
  const [entry, setEntry] = useState<"more" | "genre" | "chat" | null>(null);
  const [spark, setSpark] = useState("");
  const [genreDim, setGenreDim] = useState<Dimension | null>(null);
  // 口味定标(P1):outline 目录整体缓存,偏好面板据此渲染感情线/开局强度/主角底色等维度
  const [outlineDims, setOutlineDims] = useState<Dimension[]>([]);
  // 选中的题材卡(含 flavors 分叉):按书持久化,刷新/回跳不丢(P1 口味定标依赖它回显)
  const [pickedGenreCard, setPickedGenreCard] = useState<Chip | null>(() => {
    try { return null; } catch { return null; }
  });
  const [chatInput, setChatInput] = useState("");
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const sparkRef = useRef<HTMLTextAreaElement | null>(null);
  // 方向卡屏·轻偏好(P0-B 偏好前移 + P1 口味定标):全部可选、可跳过。零信号下模型只能给
  // 流派平均值(=最套路的写法),这就是"选了方向生成的还不对味"的根因;偏好越具体,抽卡越准。
  // 基调/元素/感情线/开局强度/主角底色/主角视角进 tendency(后端结构化注入);流派口味/画像
  // 补充/避雷补充拼进 spark 文本(收敛层语义,不进贯穿全书的指令)。
  const [prefTone, setPrefTone] = useState<string[]>([]);
  const [prefElements, setPrefElements] = useState<string[]>([]);
  const [prefFlavors, setPrefFlavors] = useState<string[]>([]);
  const [prefPersona, setPrefPersona] = useState("");
  const [prefAvoid, setPrefAvoid] = useState<string[]>([]);
  const [prefAvoidText, setPrefAvoidText] = useState("");

  // 概念屏
  const [ideas, setIdeas] = useState<Concept[] | null>(null);
  const [comparison, setComparison] = useState("");
  const [ideaSig, setIdeaSig] = useState<string | null>(null); // 候选生成时的输入签名
  const [customOpen, setCustomOpen] = useState(false);
  const [customConcept, setCustomConcept] = useState<Concept>({ ...EMPTY_CONCEPT });
  const brainstormedFor = useRef("");
  // 两段式构思(P0-A):方向路先出便宜的引擎卡(FAST 档)收敛,选中 1-2 张才花强模型深化
  const [engineCards, setEngineCards] = useState<EngineCard[] | null>(null);
  const [enginePicked, setEnginePicked] = useState<string[]>([]);
  // 引擎卡的常驻修改要求(P0 沟通修改):用户带话重出后一直生效(换一批/锚点重抽都带着),
  // 直到点「不再带这条」;存进 wiz 缓存,刷新不丢
  const [engineFeedback, setEngineFeedback] = useState("");
  const engineFeedbackRef = useRef("");
  function applyEngineFeedback(f: string) {
    engineFeedbackRef.current = f;
    setEngineFeedback(f);
  }

  // 题材屏
  const [inferBusy, setInferBusy] = useState(false);
  const [genreSuggests, setGenreSuggests] = useState<Chip[]>([]);
  const [suggestPage, setSuggestPage] = useState(0);
  const [customGenre, setCustomGenre] = useState("");

  // 书名屏
  const [titleIdeas, setTitleIdeas] = useState<string[] | null>(null);
  const [titleSig, setTitleSig] = useState<string | null>(null); // 同上,书名候选签名
  const [titleBusy, setTitleBusy] = useState(false);
  const [titleInput, setTitleInput] = useState("");
  const titleInputRef = useRef<HTMLInputElement | null>(null);

  // 篇幅屏
  const [chapters, setChapters] = useState("");
  const [words, setWords] = useState("");
  const [advOpen, setAdvOpen] = useState(false);

  // 选用卡 FLIP:飞入顶部步骤条缩略位
  const [fly, setFly] = useState<{ step: SetupStep; text: string } | null>(null);
  const [pickedKey, setPickedKey] = useState<string | null>(null);

  // 概念打磨房(确认链 L1):选定概念后进打磨而非直接飞走。
  // forgeSeed 只在「换了一个概念」时自增(驱动 ConceptForge 重挂载),
  // 打磨中的字段编辑向上同步不换 key,避免高亮/草稿被打断。
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
  // 方向路标记:概念屏据此分流(引擎卡两段式 vs 直接出概念)
  const genrePath = isGenrePath(sparkText);

  // ---------- 建草稿 / 载入(含 localStorage 恢复) ----------
  // 只建一次草稿:StrictMode/重渲染下 effect 可能重入,无守卫会静默建出多个空项目
  const createdRef = useRef(false);
  useEffect(() => {
    sweepLegacyWizKeys(); // v1 遗留键没有所有权标记,一次性清掉(见 storage.ts 注释)
    if (pid !== null) {
      api.getProject(pid).then((p) => {
        setProject(p);
        setTitleInput(p.title === "未命名新书" ? "" : p.title);
        setChapters(String(p.target_chapters));
        setWords(String(p.target_words_per_chapter));
        const c = loadJSON<WizCache>(wizKeys(pid).cache);
        // 所有权校验(开书串档防线二):缓存记录的项目创建时间与当前项目对不上,
        // 说明这份缓存属于一个已删除的同号旧项目(库被回滚/多端同步等 id 复用场景),
        // 整份丢弃——绝不能把上一个项目的提示文字和候选卡灌进新书。
        // dirty 同判:旧项目的影响标记对新书毫无意义
        const cacheOwned = !c?.createdAt || !p.created_at || c.createdAt === p.created_at;
        if (!c || cacheOwned) {
          if (c) {
            setSpark(c.spark); setIdeas(c.ideas); setTitleIdeas(c.titleIdeas);
            setIdeaSig(c.ideaSig ?? null); setTitleSig(c.titleSig ?? null);
            setEngineCards(c.engineCards ?? null);
            applyEngineFeedback(c.engineFeedback ?? "");
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

  // 流派卡片墙 + 口味维度数据(想法屏/题材屏共用)
  useEffect(() => {
    api.tendencyCatalog("outline").then((cat) => {
      setGenreDim(cat.dimensions.find((d) => d.key === "genre") ?? null);
      setOutlineDims(cat.dimensions);
    }).catch(() => undefined);
  }, []);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [project?.chat_log, busy]);

  // 候选内容写入 localStorage:刷新后回到当前屏接着选。
  // createdAt 随存:加载时校验所有权(见上方加载注释)
  useEffect(() => {
    if (pid === null || !project) return;
    saveJSON(wizKeys(pid).cache, {
      spark, ideas, titleIdeas, ideaSig, titleSig, engineCards, engineFeedback,
      createdAt: project.created_at ?? undefined,
    } satisfies WizCache);
  }, [pid, project, spark, ideas, titleIdeas, ideaSig, titleSig, engineCards, engineFeedback]);

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

  // 选用候选卡:PATCH 落库 + 卡片 FLIP 飞入顶部缩略位,稍作停留再进下一屏
  function flyTo(s: SetupStep, text: string, next: SetupStep) {
    setFly({ step: s, text });
    window.setTimeout(() => { setFly(null); setPickedKey(null); void goto(next); }, 420);
  }

  // ---------- 第 1 屏:想法 ----------
  async function submitSpark() {
    const t = spark.trim();
    if (!t) return;
    try { await patch({ topic: t }); } catch { /* 灵感落库失败不阻塞出题 */ }
    await goto("concept");
  }

  async function pickGenreBrainstorm() {
    if (!pickedGenreCard) return;
    // P0-B 偏好前移 + P1 口味定标:基调/元素/感情线/开局强度/主角底色/主角视角走
    // tendency 结构化注入(后端渲染成【本次写作倾向】,全链路可见);流派口味/画像
    // 补充/避雷补充无对应维度,拼进 spark 文本(收敛层最直接且零后端改动)。
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
    await goto("concept");
  }

  async function sendChat() {
    const text = chatInput.trim();
    if (!text || !project) return;
    const log: ChatTurn[] = [...(project.chat_log ?? []), { role: "user", content: text }];
    setChatInput("");
    setProject({ ...project, chat_log: log });
    setBusy("策划思考中…"); setErr("");
    try {
      const r = await api.chatConcept(log, conceptIsEmpty(concept) ? null : concept, tendency, project?.dna ?? null);
      const newLog: ChatTurn[] = [...log, { role: "assistant", content: r.reply }];
      await patch({
        chat_log: newLog,
        ...(conceptIsEmpty(r.concept) ? {} : { concept: r.concept }),
      });
    } catch (e) {
      setErr(errMsg(e));
      await patch({ chat_log: log }).catch(() => undefined);
    } finally { setBusy(""); }
  }

  // ---------- 第 2 屏:概念方案 ----------
  // 方向路判定:spark 是 pickGenreBrainstorm 拼出来的「按「XX」的套路来」格式。
  // 该路走两段式(先便宜引擎卡收敛);用户自写想法的路保持直接出概念(已有明确想法,不需要收敛层)。
  function isGenrePath(text: string): boolean {
    return text.startsWith("按「") && text.includes("套路来");
  }

  // 两段式·第一段:FAST 档出一批故事引擎卡(带差异轴);换一批传上一批引擎句当 avoid,不趋同;
  // 锚点重抽传 anchorEngine——「方向对,照这张再来点」,沿这张卡出变体而非全盘否定;
  // feedbackOverride = 用户对上一批的修改要求(带话重出)。常驻要求 engineFeedbackRef
  // 在换一批/锚点重抽时也生效(「不要系统流」不会因为换了一批就失效)。
  async function fetchEngines(avoidEngines: string[] = [], anchorEngine = "", feedbackOverride = "") {
    const fb = (feedbackOverride || engineFeedbackRef.current).trim();
    setErr(""); setEngineCards(null); setEnginePicked([]);
    setBusy(anchorEngine ? "AI 正在照着锚点引擎出变体(几十秒)…"
      : fb ? "AI 正在按你的要求重出一批(几十秒)…"
      : "AI 正在快速出一批故事引擎(几十秒)…");
    try {
      const r = await runJob<{ engines: EngineCard[] }>(
        () => api.enginesAsync(sparkText, tendency, 8, project?.dna ?? null, avoidEngines, anchorEngine, fb),
        { kind: "inspire" },
      );
      if (r) setEngineCards(r.engines);
    } catch (e) { setErr(errMsg(e)); setEngineCards([]); } finally { setBusy(""); }
  }

  // 引擎卡点选:再点取消;最多 2 张(第 3 张挤掉最早选的),两张 = 混搭(A 的主角遇 B 的局面)
  function pickEngine(engine: string) {
    setEnginePicked((prev) => {
      if (prev.includes(engine)) return prev.filter((x) => x !== engine);
      return [...prev, engine].slice(-2);
    });
  }

  // 两段式·第二段:选中的引擎 → 强模型深化成单个六字段概念
  async function developConcept() {
    if (!enginePicked.length) return;
    setErr(""); setIdeas(null);
    setBusy("AI 正在把选中的引擎深化成完整概念(约 1 分钟)…");
    try {
      const r = await runJob<{ concept: Concept }>(
        () => api.developConceptAsync(enginePicked, sparkText, tendency, project?.dna ?? null),
        { kind: "inspire" },
      );
      if (r) { setIdeas([r.concept]); setComparison(""); setEngineCards(null); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  async function brainstorm(feedback = "") {
    const base = sparkText;
    if (!base) return;
    brainstormedFor.current = base + "|" + feedback;
    const sig = conceptSig(base, tendency); // 与实际生成入参一致
    setErr(""); setIdeas(null);
    try {
      const r = await runJob<{ ideas: Concept[]; comparison?: string }>(
        () => api.inspireAsync(
          feedback ? `${base}\n补充要求:${feedback}` : base, tendency, 4, project?.dna ?? null),
        { kind: "inspire" },
      );
      if (r) { setIdeas(r.ideas); setComparison(r.comparison ?? ""); setIdeaSig(sig); }
    } catch (e) { setErr(errMsg(e)); setIdeas([]); }
  }

  // 进概念屏自动生成(有缓存候选则不重复生成):方向路出引擎卡,想法路直接出概念
  useEffect(() => {
    if (step !== "concept" || !project) return;
    if (ideas !== null || engineCards !== null) return;
    const key = sparkText;
    if (!key || brainstormedFor.current === key) return;
    brainstormedFor.current = key;
    if (isGenrePath(key)) void fetchEngines();
    else void brainstorm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, project, ideas, engineCards]);

  // 带反馈重新生成:有灵感文本 → 追加要求重出 4 个;对话捏出的概念 → refine 精修
  async function regenWithFeedback(f: string) {
    if (!sparkText && !conceptIsEmpty(concept)) {
      const sig = conceptSig(sparkText, tendency);
      setErr(""); setIdeas(null);
      try {
        const r = await runJob<RefineResult>(
          () => api.refineConceptAsync(concept, f, tendency, project?.dna ?? null), { kind: "inspire" });
        if (r) { setIdeas([r.concept]); setIdeaSig(sig); }
      } catch (e) { setErr(errMsg(e)); setIdeas([]); }
    } else {
      await brainstorm(f);
    }
  }

  async function pickConcept(c: Concept) {
    if (pickedKey) return;
    setPickedKey(conceptKey(c));
    try {
      await patch({ concept: c });
    } catch (e) { setErr(errMsg(e)); }
    // 确认链 L1:选定 → 进打磨房(不再直接飞走);血肉在那里面过目、改、拍板
    setForgeOpen(true);
    setForgeSeed((n) => n + 1);
    toast.ok("已选定故事概念", "先打磨:逐项看/改/带话重捏,满意再拍板");
  }

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

  // ---------- 第 6 屏:篇幅 ----------
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

  // 开放式连载开关(篇幅屏勾选):True=结局未定,架构只定长线引擎+首批方向,铺满可续订
  const openEnded = !!project?.open_ended;
  async function toggleOpenEnded(v: boolean) {
    await patch({ open_ended: v });
  }

  // ---------- 第 8 屏:点火流水线 ----------
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

  // 旧流水线屏恢复(信任模式专用;闸门模式的架构自举在 ArchGate 内):
  // 优先接回仍在跑的任务,否则按已有产物推断完成态;
  // 两手空空且首次进入 → 自动点火(仅一次,失败重跑由用户手动触发,避免刷新反复烧 token)
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
    // P1-5:刚点完火的心智是「开始写」——落写作区第 1 章,而不是开书区(架构卡)。
    // 蓝图已铺好的书直接 ch=1;「先不生成,直接进工作台」的空书也会被 write 区
    // 的空态引导到目录/生成,不会白屏。
    nav(`/project/${pid}/write?ch=1`);
  }

  async function abandon() {
    if (pid === null || !project) return;
    // 已有实质产出(起步流已过「想法」步,或进了流水线):删除前确认;
    // 刚进来还没填东西(setup_state 仍是 idea)时不打扰
    const progressed = !!project.setup_state && project.setup_state !== "idea";
    if (progressed) {
      const ok = await confirmDialog({
        title: "放弃创建并删除该项目?",
        body: "将删除该项目及已生成内容(概念/架构/蓝图等),不可恢复。",
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
    concept, tendency, sparkText, allGenreChips, shownSuggests,
    // 各屏 state
    spark, entry, genreDim, outlineDims, pickedGenreCard, chatInput, busy,
    ideas, comparison, ideaSig, customOpen, customConcept,
    prefTone, prefElements, prefFlavors, prefPersona, prefAvoid, prefAvoidText,
    engineCards, enginePicked, genrePath, engineFeedback,
    inferBusy, customGenre,
    titleIdeas, titleSig, titleBusy, titleInput,
    chapters, words, advOpen, openEnded,
    fly, pickedKey, dirty, arch, bp, setBp, trustMode, setTrustMode,
    forgeOpen, forgeSeed, setForgeOpen,
    forgeChanged, forgeConfirmed, forgeUnconfirmed, isForgeDismissedFor, dismissForge, reopenForge,
    // 渲染需要的 setter
    setSpark, setEntry, setPickedGenreCard, setChatInput,
    setIdeaSig, setCustomOpen, setCustomConcept,
    setPrefTone, setPrefElements, setPrefFlavors, setPrefPersona, setPrefAvoid, setPrefAvoidText,
    setGenreSuggests, setSuggestPage, setCustomGenre,
    setTitleSig, setTitleInput, setChapters, setWords, setAdvOpen, setDirty,
    // ref
    stepsRef, chatEndRef, sparkRef, titleInputRef,
    // handler
    submitSpark, pickGenreBrainstorm, sendChat,
    brainstorm, regenWithFeedback, pickConcept, saveCustomConcept,
    fetchEngines, pickEngine, developConcept, applyEngineFeedback,
    setGenre, setDim, fetchTitles, pickTitle, pickScale, confirmScale, toggleOpenEnded,
    runArch, runBp, enterWorkbench, abandon, goto, editFrom, markDirtyOk,
  };
}
