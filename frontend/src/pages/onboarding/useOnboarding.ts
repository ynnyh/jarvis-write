// 起步流的状态机:所有 state / effect / handler 集中于此,视图组件只消费其返回值。
// 拆自 OnboardingFlow.tsx —— 逻辑与渲染分离,hook 调用顺序、effect 依赖原样保留。
//
// 开书前置重构(docs/30 / docs/32,2026-10-17)后的主线四步:
//   开哪种书(mode)→ 想法(idea)→ 理解(intent:确认①)→ 样稿(sample:确认②)
//
// 这个 hook 现在只管「前两屏 + 导航 + 建草稿」:理解屏与样稿屏的数据与请求各自
// 自管(见 IntentScreen.tsx / SampleScreen.tsx,仿 SkeletonWall 的模式)。
// 被删掉的是旧链的整套状态:方案流(三问/方案墙/拍板)、概念打磨房、配置三页签、
// 点火流水线,以及那个会在进旧方案屏时自动发一轮对话的 ghost effect
// (docs/32 问题 #7:每本新书都偷跑一轮 LLM 对谈,用户从没点过)。
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, Chip, Dimension, Project, Tendency } from "../../api";
import { errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { SetupStep, SETUP_STATE, parseStep } from "./steps";
import { WizCache, loadJSON, saveJSON, sweepLegacyWizKeys, wizKeys } from "./storage";

export function useOnboarding() {
  const { id, step: stepParam } = useParams();
  const nav = useNavigate();
  const pid = id ? Number(id) : null;
  const step: SetupStep = parseStep(stepParam);

  const [project, setProject] = useState<Project | null>(null);
  const [err, setErr] = useState("");
  const stepsRef = useRef<HTMLDivElement | null>(null);

  // 窄屏步骤条横滚时,当前步可能滚出视野,换步后拉回可见区
  useEffect(() => {
    stepsRef.current
      ?.querySelector(".wiz-step.on")
      ?.scrollIntoView({ inline: "nearest", block: "nearest", behavior: "smooth" });
  }, [step]);

  // ---------- 想法屏 ----------
  const [entry, setEntry] = useState<"more" | "genre" | null>(null);
  const [spark, setSpark] = useState("");
  const [genreDim, setGenreDim] = useState<Dimension | null>(null);
  // 口味定标:outline 目录整体缓存,偏好面板据此渲染感情线/开局强度/主角底色等维度
  const [outlineDims, setOutlineDims] = useState<Dimension[]>([]);
  // 选中的题材卡(含 flavors 分叉):按书持久化,刷新/回跳不丢
  const [pickedGenreCard, setPickedGenreCard] = useState<Chip | null>(null);
  // 漫剧源书皮肤墙(存量兼容):按频道渲染爽文题材标签,选中即落 genre
  const [dramaSkinList, setDramaSkinList] = useState<{ key: string; label: string; desc: string }[]>([]);
  const [pickedSkin, setPickedSkin] = useState<string>(() => project?.genre || "");
  const sparkRef = useRef<HTMLTextAreaElement | null>(null);
  // 轻偏好:基调/元素/流派口味/画像/避雷,全部可选可跳过,收窄理解卡回读的空间
  const [prefTone, setPrefTone] = useState<string[]>([]);
  const [prefElements, setPrefElements] = useState<string[]>([]);
  const [prefFlavors, setPrefFlavors] = useState<string[]>([]);
  const [prefPersona, setPrefPersona] = useState("");
  const [prefAvoid, setPrefAvoid] = useState<string[]>([]);
  const [prefAvoidText, setPrefAvoidText] = useState("");

  const tendency: Tendency = project?.global_tendency ?? {};
  const sparkText = spark.trim() || project?.topic?.trim() || "";
  // 漫剧(存量源书)必须原样透传:后端把 mode 归连载形态,但 mode 本身不能被覆写
  // ——漫剧工坊集规划按 mode==drama 切「一章一集」源书档(docs/23)
  const mode = project?.mode === "short" ? "short"
    : project?.mode === "drama" ? "drama" : "serial";

  // ---------- 建草稿 / 载入(含 localStorage 恢复) ----------
  // 只建一次草稿:StrictMode/重渲染下 effect 可能重入,无守卫会静默建出多个空项目
  const createdRef = useRef(false);
  useEffect(() => {
    sweepLegacyWizKeys(); // v1 遗留键没有所有权标记,一次性清掉(见 storage.ts 注释)
    if (pid !== null) {
      // 跨书状态重置(串档防线三):/new/1 → /new/2 是同路由参数变化,组件不卸载,
      // 上一本书的屏级 state(灵感文字/流派卡)会残留进新书——
      // localStorage 有 v2 键隔离,React state 没有,必须在 pid 变化时显式清场。
      setSpark(""); setEntry(null); setPickedGenreCard(null);
      api.getProject(pid).then((p) => {
        setProject(p);
        const c = loadJSON<WizCache>(wizKeys(pid).cache);
        // 所有权校验(开书串档防线二):缓存记录的项目创建时间与当前项目对不上,
        // 说明这份缓存属于一个已删除的同号旧项目,整份丢弃
        const cacheOwned = !c?.createdAt || !p.created_at || c.createdAt === p.created_at;
        if (c && cacheOwned) setSpark(c.spark);
        // 直达续建:无 step 参数时按 setup_state 落到对应屏(存量书按 parseStep 映射)
        if (!stepParam) {
          nav(`/new/${pid}/${p.setup_state ? parseStep(p.setup_state) : "idea"}`, { replace: true });
        }
      }).catch((e) => setErr(errMsg(e)));
      return;
    }
    // /new 无 id:静默创建草稿项目,replace 进第一步(createdRef 防重复建)
    if (createdRef.current) return;
    createdRef.current = true;
    api.createProject({ title: "未命名新书", setup_state: "mode" })
      .then((p) => nav(`/new/${p.id}/mode`, { replace: true }))
      .catch((e) => { createdRef.current = false; setErr(errMsg(e)); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  // 流派卡片墙 + 口味维度数据(想法屏用)
  useEffect(() => {
    api.tendencyCatalog("outline").then((cat) => {
      setGenreDim(cat.dimensions.find((d) => d.key === "genre") ?? null);
      setOutlineDims(cat.dimensions);
    }).catch(() => undefined);
  }, []);

  // 候选内容写入 localStorage:刷新后回到当前屏接着选。createdAt 随存(所有权校验)
  useEffect(() => {
    if (pid === null || !project) return;
    saveJSON(wizKeys(pid).cache, {
      spark, createdAt: project.created_at ?? undefined,
    } satisfies WizCache);
  }, [pid, project, spark]);

  const patch = useCallback(async (updates: Partial<Project> & { setup_state?: string }) => {
    if (pid === null) return null;
    const p = await api.patchProject(pid, updates);
    setProject(p);
    return p;
  }, [pid]);

  /** 从服务端重取项目态(理解屏/样稿屏改过库之后同步给侧栏与父级) */
  const reloadProject = useCallback(async (): Promise<Project | null> => {
    if (pid === null) return null;
    const p = await api.getProject(pid);
    setProject(p);
    return p;
  }, [pid]);

  async function goto(next: SetupStep) {
    try { await patch({ setup_state: SETUP_STATE[next] }); } catch { /* 步进不因保存失败而卡死 */ }
    nav(`/new/${pid}/${next}`);
  }

  // ---------- 想法屏 ----------
  // 写下一句想法 → 落库 → 进理解屏(AI 在那里回读「我听到的」)
  async function submitSpark() {
    const t = spark.trim();
    if (!t) return;
    try { await patch({ topic: t }); } catch { /* 灵感落库失败不阻塞 */ }
    await goto("intent");
  }

  // 皮肤目录:漫剧存量书进想法屏时拉一次(静态目录,失败静默退化)
  useEffect(() => {
    if (project?.mode !== "drama" || !project.audience || dramaSkinList.length) return;
    api.dramaSkins().then((all) => {
      setDramaSkinList(all[project.audience!] ?? []);
    }).catch(() => { /* 拉不到就只显示「让 AI 定」 */ });
  }, [project?.mode, project?.audience, dramaSkinList.length]);

  // 选皮肤出方案(漫剧源书):皮肤落 genre,点子信号拼进 topic——理解卡回读
  // 与方向卡都拿得到「已选题材: XX」;spark 有用户原话就带上,没有就用皮肤兜底文本。
  async function pickSkinGo(skLabel: string | null) {
    if (!project) return;
    const audienceCn = project.audience === "female" ? "女频" : "男频";
    const extra = spark.trim();
    const topic = skLabel
      ? (extra ? `${extra}——按「${skLabel}」的爽文套路来,${audienceCn}漫剧源书`
               : `按「${skLabel}」的爽文套路来,${audienceCn}漫剧源书`)
      : (extra || `按${audienceCn}爽文货架自选题材,漫剧源书`);
    setPickedSkin(skLabel ?? "");
    try {
      await patch({ genre: skLabel ?? "", topic });
    } catch { /* 落库失败不阻塞进理解屏 */ }
    await goto("intent");
  }

  // 选流派:流派+口味写成初始信号落库(理解卡回读与方向卡的硬上下文),进理解屏
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
    await goto("intent");
  }

  // 屏 0「开哪种书」:模式级分叉,连载可顺手选档位(明示,替代旧版静默自动选档)。
  // 手选档位即时落库——确认① 时「非建库默认」的现值会被尊重(见 IntentScreen)。
  // 漫剧源书存量兼容:mode=drama 时必带 audience(男频/女频)。
  async function pickMode(
    m: "short" | "serial" | "drama",
    preset?: { chapters: number; words: number },
    audience?: "male" | "female",
  ) {
    try {
      await patch({
        mode: m,
        ...(m === "drama" && audience ? { audience } : {}),
        ...(preset ? { target_chapters: preset.chapters, target_words_per_chapter: preset.words } : {}),
      });
      await goto("idea");
    } catch (e) { setErr(errMsg(e)); }
  }

  async function setDim(key: string, value: string | string[]) {
    await patch({ global_tendency: { ...tendency, [key]: value } });
  }

  /** 参考方向卡存盘后:重取项目态(它挂在 project 上),想法屏的口味维度据此刷新 */
  async function creativeSaved() {
    if (pid === null) return;
    setProject(await api.getProject(pid));
  }

  /** 进工作台:起步流收尾(清 setup_state,落写作区第 1 章) */
  async function enterWorkbench() {
    if (pid === null) return;
    try { await patch({ setup_state: "" }); } catch { /* 不阻塞进台 */ }
    // 刚确认完样稿的心智是「开始写」——落写作区第 1 章,而不是开书区
    nav(`/project/${pid}/write?ch=1`);
  }

  async function abandon() {
    if (pid === null || !project) return;
    // 已有实质产出(过了想法步):删除前确认
    const progressed = !!project.setup_state && project.setup_state !== "idea";
    if (progressed) {
      const ok = await confirmDialog({
        title: "放弃创建并删除该项目?",
        body: "将删除该项目及已生成内容(理解卡/方向/样稿等),不可恢复。",
        confirmText: "放弃并删除",
        danger: true,
      });
      if (!ok) return;
    }
    try {
      await api.deleteProject(pid);
      const k = wizKeys(pid);
      localStorage.removeItem(k.cache);
      localStorage.removeItem(k.dirty);
      localStorage.removeItem(k.pipe);
      toast.ok("已放弃创建");
      nav("/");
    } catch (e) { setErr(errMsg(e)); }
  }

  return {
    // 基础 / 路由
    project, err, step, pid, nav,
    // 派生
    tendency, sparkText, mode,
    // 想法屏 state
    spark, entry, genreDim, outlineDims, pickedGenreCard,
    prefTone, prefElements, prefFlavors, prefPersona, prefAvoid, prefAvoidText,
    // setter
    setSpark, setEntry, setPickedGenreCard,
    setPrefTone, setPrefElements, setPrefFlavors, setPrefPersona, setPrefAvoid, setPrefAvoidText,
    // ref
    stepsRef, sparkRef,
    // handler
    submitSpark, pickGenreBrainstorm, pickMode, setDim, creativeSaved,
    dramaSkinList, pickedSkin, pickSkinGo,
    goto, abandon, enterWorkbench, reloadProject, setErr,
  };
}

// 参考方向卡与口味维度的关系:参考卡存盘后 creativeSaved 重取项目态,
// outlineDims 是静态目录只拉一次;两者都不参与导航,故没有额外 effect。
export type OnboardingState = ReturnType<typeof useOnboarding>;
