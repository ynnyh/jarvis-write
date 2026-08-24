// 起步流的状态机:所有 state / effect / handler 集中于此,视图组件只消费其返回值。
// 拆自 OnboardingFlow.tsx —— 逻辑与渲染分离,零行为变化(hook 调用顺序、effect 依赖原样保留)。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  api, ChatTurn, Chip, Concept, conceptIsEmpty, Dimension,
  EMPTY_CONCEPT, Project, RefineResult, Tendency,
} from "../../api";
import { useJob } from "../../ui/useJob";
import { pollJob, errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { conceptSig, titleSig as calcTitleSig } from "../wizSig";
import { SetupStep, STEP_ORDER, SETUP_STATE, parseStep } from "./steps";
import { WizCache, Dirty, loadJSON, saveJSON } from "./storage";
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
  const [pickedGenreCard, setPickedGenreCard] = useState<Chip | null>(null);
  const [chatInput, setChatInput] = useState("");
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const sparkRef = useRef<HTMLTextAreaElement | null>(null);

  // 概念屏
  const [ideas, setIdeas] = useState<Concept[] | null>(null);
  const [comparison, setComparison] = useState("");
  const [ideaSig, setIdeaSig] = useState<string | null>(null); // 候选生成时的输入签名
  const [customOpen, setCustomOpen] = useState(false);
  const [customConcept, setCustomConcept] = useState<Concept>({ ...EMPTY_CONCEPT });
  const brainstormedFor = useRef("");

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

  // 确认墙:回改上游后,下游已确认项标"可能受影响"
  const [dirty, setDirty] = useState<Dirty | null>(null);

  // 点火流水线
  const [arch, setArch] = useState<PipeStep>(PIPE_WAIT);
  const [bp, setBp] = useState<PipeStep>(PIPE_WAIT);
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

  // ---------- 建草稿 / 载入(含 localStorage 恢复) ----------
  // 只建一次草稿:StrictMode/重渲染下 effect 可能重入,无守卫会静默建出多个空项目
  const createdRef = useRef(false);
  useEffect(() => {
    if (pid !== null) {
      api.getProject(pid).then((p) => {
        setProject(p);
        setTitleInput(p.title === "未命名新书" ? "" : p.title);
        setChapters(String(p.target_chapters));
        setWords(String(p.target_words_per_chapter));
        const c = loadJSON<WizCache>(`wiz-cache:${pid}`);
        if (c) {
          setSpark(c.spark); setIdeas(c.ideas); setTitleIdeas(c.titleIdeas);
          setIdeaSig(c.ideaSig ?? null); setTitleSig(c.titleSig ?? null);
        }
        setDirty(loadJSON<Dirty>(`wiz-dirty:${pid}`));
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

  // 流派卡片墙数据(想法屏/题材屏共用)
  useEffect(() => {
    api.tendencyCatalog("outline").then((cat) => {
      setGenreDim(cat.dimensions.find((d) => d.key === "genre") ?? null);
    }).catch(() => undefined);
  }, []);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [project?.chat_log, busy]);

  // 候选内容写入 localStorage:刷新后回到当前屏接着选
  useEffect(() => {
    if (pid === null || !project) return;
    saveJSON(`wiz-cache:${pid}`, { spark, ideas, titleIdeas, ideaSig, titleSig } satisfies WizCache);
  }, [pid, project, spark, ideas, titleIdeas, ideaSig, titleSig]);

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
    const text = `按「${pickedGenreCard.label}」的套路来`;
    setSpark(text);
    try {
      await patch({
        global_tendency: { ...tendency, genre: pickedGenreCard.label },
        genre: pickedGenreCard.label, topic: text,
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

  // 进概念屏自动生成一批(有缓存候选则不重复生成)
  useEffect(() => {
    if (step !== "concept" || !project || ideas !== null) return;
    const key = sparkText;
    if (!key || brainstormedFor.current === key) return;
    brainstormedFor.current = key;
    void brainstorm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, project, ideas]);

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
      toast.ok("已选定故事概念", "进入工作台后还能继续打磨");
    } catch (e) { setErr(errMsg(e)); }
    flyTo("concept", c.logline || "已选定概念", "genre");
  }

  async function saveCustomConcept() {
    if (conceptIsEmpty(customConcept)) { setErr("至少填一个字段再保存"); return; }
    try { await patch({ concept: customConcept }); } catch (e) { setErr(errMsg(e)); return; }
    setCustomOpen(false);
    flyTo("concept", customConcept.logline || "手写概念", "genre");
  }

  // ---------- 第 3 屏:题材(AI 预填,推断成功自动跳过) ----------
  useEffect(() => {
    if (step !== "genre" || !conceptText.trim() || tendency.genre) return;
    setInferBusy(true);
    api.genreInfer(conceptText).then(async (r) => {
      setGenreSuggests(r.suggestions.map((s) => ({ directive: "", ...s })));
      if (r.genre) {
        await patch({ global_tendency: { ...tendency, genre: r.genre } });
        // 推断成功即落库并直接进倾向屏,题材屏不再停留(回退/确认墙仍可改)
        toast.ok(`题材已定为「${r.genre}」`, "确认墙里还能改");
        await goto("tone");
      }
      // 推断为空:维持停在题材屏,用户手选或自写
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

  // ---------- 第 5 屏:书名 ----------
  useEffect(() => {
    if (step !== "title" || !project || titleIdeas !== null) return;
    void fetchTitles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, project, titleIdeas]);

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
    if (pickedKey) return;
    setPickedKey(v);
    setTitleInput(v);
    try { await patch({ title: v }); } catch (e) { setErr(errMsg(e)); }
    flyTo("title", v, "scale");
  }

  // ---------- 第 6 屏:篇幅 ----------
  async function pickScale(preset: typeof SCALE_PRESETS[number]) {
    setChapters(String(preset.chapters)); setWords(String(preset.words));
    await patch({ target_chapters: preset.chapters, target_words_per_chapter: preset.words });
  }

  async function confirmScale() {
    const ch = Number(chapters), w = Number(words);
    if (!Number.isInteger(ch) || ch < 1 || ch > 2000) { setErr("章节数需为 1-2000 的整数"); return; }
    if (!Number.isInteger(w) || w < 200 || w > 20000) { setErr("每章字数需为 200-20000 的整数"); return; }
    await patch({ target_chapters: ch, target_words_per_chapter: w });
    await goto("confirm");
  }

  // ---------- 第 8 屏:点火流水线 ----------
  function reattach(kind: "arch" | "bp", jobId: string, stage: string) {
    const set = kind === "arch" ? setArch : setBp;
    set({ status: "run", stage: stage || "生成中", error: "" });
    pollJob(jobId, { onStage: (s) => set((p) => (p.status === "run" ? { ...p, stage: s } : p)) })
      .then(() => {
        set({ status: "done", stage: "", error: "" });
        if (kind === "arch") void runBp();
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
      void runBp();
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

  // 流水线屏恢复:优先接回仍在跑的任务,否则按已有产物推断完成态;
  // 两手空空且首次进入 → 自动点火(仅一次,失败重跑由用户手动触发,避免刷新反复烧 token)
  useEffect(() => {
    if (step !== "launch" || pid === null || pipeInit.current) return;
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
      if (!archDone && loadJSON<string>(`wiz-pipe:${pid}`) !== "started") {
        saveJSON(`wiz-pipe:${pid}`, "started");
        void runArch();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, pid]);

  async function enterWorkbench() {
    if (pid === null) return;
    try { await patch({ setup_state: "" }); } catch { /* 不阻塞进台 */ }
    nav(`/project/${pid}/setup?step=arch`);
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
        localStorage.removeItem(`wiz-cache:${pid}`);
        localStorage.removeItem(`wiz-dirty:${pid}`);
        localStorage.removeItem(`wiz-pipe:${pid}`);
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
    spark, entry, genreDim, pickedGenreCard, chatInput, busy,
    ideas, comparison, ideaSig, customOpen, customConcept,
    inferBusy, customGenre,
    titleIdeas, titleSig, titleBusy, titleInput,
    chapters, words, advOpen,
    fly, pickedKey, dirty, arch, bp,
    // 渲染需要的 setter
    setSpark, setEntry, setPickedGenreCard, setChatInput,
    setIdeaSig, setCustomOpen, setCustomConcept,
    setGenreSuggests, setSuggestPage, setCustomGenre,
    setTitleSig, setTitleInput, setChapters, setWords, setAdvOpen, setDirty,
    // ref
    stepsRef, chatEndRef, sparkRef, titleInputRef,
    // handler
    submitSpark, pickGenreBrainstorm, sendChat,
    brainstorm, regenWithFeedback, pickConcept, saveCustomConcept,
    setGenre, setDim, fetchTitles, pickTitle, pickScale, confirmScale,
    runArch, runBp, enterWorkbench, abandon, goto, editFrom, markDirtyOk,
  };
}
