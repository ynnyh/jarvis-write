// 创作起步流:建书即建草稿项目,三幕式向导走到点火生成。
// 第一幕 一屏一问:idea → concept → genre → tone → title → scale
// 第二幕 确认墙:confirm(全部设定卡片墙,可回改,下游标"可能受影响")
// 第三幕 点火流水线:launch(架构生成 → 蓝图生成,失败只重跑该步)
// /new → 静默建草稿 → /new/:id/idea → … → /new/:id/launch → 工作台
// 每屏选择实时 PATCH 落库(刷新不丢、列表页可"继续创建");
// localStorage 缓存候选内容,刷新后回到当前屏接着选。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AnimatePresence, LayoutGroup, MotionConfig, motion } from "motion/react";
import {
  api, ChatTurn, Chip, Concept, CONCEPT_FIELDS, conceptIsEmpty, Dimension,
  EMPTY_CONCEPT, Project, RefineResult, Tendency,
} from "../api";
import { useJob } from "../ui/useJob";
import { pollJob } from "../pollJob";
import { toast } from "../ui/Toaster";
import { confirmDialog } from "../ui/ConfirmDialog";
import { CandidateCards } from "../ui/CandidateCards";
import { ThinkingText } from "../ui/ThinkingText";
import { conceptSig, conceptStaleText, isStale, titleSig as calcTitleSig, titleStaleText } from "./wizSig";

export type SetupStep =
  | "idea" | "concept" | "genre" | "tone" | "title" | "scale" | "confirm" | "launch";
const STEP_ORDER: SetupStep[] = [
  "idea", "concept", "genre", "tone", "title", "scale", "confirm", "launch",
];
const STEP_LABEL: Record<SetupStep, string> = {
  idea: "想法", concept: "概念", genre: "题材", tone: "倾向",
  title: "书名", scale: "篇幅", confirm: "确认", launch: "点火",
};
// setup_state(服务端字符串字段,直接扩展取值):launch 屏记为 generating,语义更准
const SETUP_STATE: Record<SetupStep, string> = {
  idea: "idea", concept: "concept", genre: "genre", tone: "tone",
  title: "title", scale: "scale", confirm: "confirm", launch: "generating",
};
// 路由 step → 屏;兼容历史取值(generating = 流水线屏)
function parseStep(p?: string): SetupStep {
  if (p === "generating") return "launch";
  return (STEP_ORDER as string[]).includes(p ?? "") ? (p as SetupStep) : "idea";
}

// 篇幅预设卡
const SCALE_PRESETS = [
  { key: "short", label: "短篇", chapters: 20, words: 3000, desc: "约 6 万字,适合练手或中短故事" },
  { key: "mid", label: "中篇", chapters: 60, words: 3000, desc: "约 18 万字,完整起承转合" },
  { key: "long", label: "长篇", chapters: 150, words: 3000, desc: "约 45 万字,网文连载体量" },
];

// "AI 构思中"轮换微文案
const THINK_CONCEPT = [
  "正在揣摩题材气质…", "正在搭建核心冲突…", "正在给主角找困境…", "正在埋藏反转的种子…",
];
const THINK_TITLE = [
  "正在咀嚼故事的味儿…", "正在掂量每个字的分量…", "正在试着念出声来…",
];

// ---- localStorage:候选内容缓存 + 回改影响标记 ----
interface WizCache {
  spark: string; ideas: Concept[] | null; titleIdeas: string[] | null;
  ideaSig?: string | null; titleSig?: string | null;
}
interface Dirty { from: SetupStep; ok: SetupStep[]; }
function loadJSON<T>(key: string): T | null {
  try { const s = localStorage.getItem(key); return s ? JSON.parse(s) as T : null; }
  catch { return null; }
}
function saveJSON(key: string, v: unknown) {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* 缓存失败不阻塞 */ }
}

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

// 概念卡的稳定 key(logline 可能为空,拼主角字段兜底)
function conceptKey(c: Concept) {
  return (c.logline || "").slice(0, 24) + "|" + (c.protagonist || "").slice(0, 8);
}

type PipeStatus = "wait" | "run" | "done" | "err";
interface PipeStep { status: PipeStatus; stage: string; error: string; }
const PIPE_WAIT: PipeStep = { status: "wait", stage: "", error: "" };

export default function OnboardingFlow() {
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
      }).catch((e) => setErr(String(e)));
      return;
    }
    // /new 无 id:静默创建草稿项目,replace 进第一步
    api.createProject({ title: "未命名新书", setup_state: "idea" })
      .then((p) => nav(`/new/${p.id}/idea`, { replace: true }))
      .catch((e) => setErr(String(e)));
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

  // ---------- 第 2 屏:概念方案 ----------
  async function brainstorm(feedback = "") {
    const base = sparkText;
    if (!base) return;
    brainstormedFor.current = base + "|" + feedback;
    const sig = conceptSig(base, tendency); // 与实际生成入参一致
    setErr(""); setIdeas(null);
    try {
      const r = await runJob<{ ideas: Concept[] }>(
        () => api.inspireAsync(
          feedback ? `${base}\n补充要求:${feedback}` : base, tendency, 4),
        { kind: "inspire" },
      );
      if (r) { setIdeas(r.ideas); setIdeaSig(sig); }
    } catch (e) { setErr(String(e)); setIdeas([]); }
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
          () => api.refineConceptAsync(concept, f, tendency), { kind: "inspire" });
        if (r) { setIdeas([r.concept]); setIdeaSig(sig); }
      } catch (e) { setErr(String(e)); setIdeas([]); }
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
    } catch (e) { setErr(String(e)); }
    flyTo("concept", c.logline || "已选定概念", "genre");
  }

  async function saveCustomConcept() {
    if (conceptIsEmpty(customConcept)) { setErr("至少填一个字段再保存"); return; }
    try { await patch({ concept: customConcept }); } catch (e) { setErr(String(e)); return; }
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
      const r = await api.suggestTitle(
        (project?.topic ?? "") + (feedback ? `(命名偏好:${feedback})` : ""),
        (tendency.genre as string) ?? "",
        conceptIsEmpty(concept) ? null : concept,
      );
      setTitleIdeas(r.titles);
      setTitleSig(sig);
    } catch (e) { setErr(String(e)); setTitleIdeas([]); } finally { setTitleBusy(false); }
  }

  async function pickTitle(t: string) {
    const v = t.trim();
    if (!v) { setErr("先选一个候选或自己写一个书名"); return; }
    if (pickedKey) return;
    setPickedKey(v);
    setTitleInput(v);
    try { await patch({ title: v }); } catch (e) { setErr(String(e)); }
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
      .catch((e) => set({ status: "err", stage: "", error: String(e) }));
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
      setArch({ status: "err", stage: "", error: String(e) });
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
      setBp({ status: "err", stage: "", error: String(e) });
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
    nav(`/project/${pid}/arch`);
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
    } catch (e) { setErr(String(e)); }
  }

  if (!project) return <div className="muted">{err || "正在创建草稿…"}</div>;

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
                      <button onClick={() => setEntry(entry ? null : "more")}>
                        {entry ? "收起" : "没有灵感?"}
                      </button>
                      {hasConcept && (
                        <button onClick={() => goto("genre")}>概念已就绪,跳到题材 →</button>
                      )}
                    </div>

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
                            onClick={pickGenreBrainstorm}>
                            ✨ 按这个流派出方案 →
                          </button>
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
                        {ideas === null && (
                          <div className="muted mt-2 mb-2">
                            <span className="spin" /><ThinkingText phrases={THINK_CONCEPT} />
                          </div>
                        )}
                        {ideasStale && (
                          <div className="wiz-stale">
                            <span>⚠ {conceptStaleText(ideaSig!, sparkText, tendency)}</span>
                            <span className="grow" />
                            <button className="btn-sm" onClick={() => brainstorm()}>重新生成</button>
                            <button className="btn-sm" onClick={() => setIdeaSig(curIdeaSig)}>仍用这批</button>
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
                          onPick={pickConcept}
                          onRefresh={() => brainstorm()}
                          onRefine={regenWithFeedback}
                          onCustom={() => setCustomOpen((v) => !v)}
                        />
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
                        onPick={pickConcept}
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
                    <div className="card-desc">
                      节奏 / 结构 / 基调,可不选,AI 会均衡处理;进了工作台也能随时调。
                    </div>
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

                {/* ---------- 篇幅 ---------- */}
                {step === "scale" && (
                  <div className="card">
                    <h2>打算写多长?</h2>
                    <div className="card-desc">先选个预设,数字收在「高级选项」里,之后随时能改。</div>
                    <div className="scale-cards mt-2">
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
                    <div className="mt-3">
                      <button type="button" className="btn-sm" onClick={() => setAdvOpen((v) => !v)}>
                        {advOpen ? "▾" : "▸"} 高级选项(章数 / 每章字数)
                      </button>
                      {advOpen && (
                        <div className="row mt-2">
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
                      )}
                    </div>
                    <div className="actions mt-4 onboard-nav">
                      <button onClick={() => nav(`/new/${pid}/title`)}>← 上一步</button>
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
                          text: `${project.target_chapters} 章 × ${project.target_words_per_chapter} 字`,
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
      </LayoutGroup>
    </MotionConfig>
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
