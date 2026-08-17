// write 区主场(「正文即界面」P1+P2,docs/10):单列居中正文为视觉重心——
// 章首=状态卡(三态人话 + 「更多」过渡入口)+ 蓝图卡(<details> 默认收起),
// 中=可点选正文(Prose 段落气泡:改这段/手改),章尾=下一章卡。
// 目录=覆盖式抽屉(章题按钮/Ctrl+B 唤出,双端同一份 CatalogDrawer);StageBar/底部动作条/
// FAB/动作扇/参考抽屉已退场(见 docs/10 §8 迁移映射)。
// P2:右侧 AI 窄栏(AiDock,移动端=底部输入条+全屏 sheet)承接「随便聊/这章整体不满意」
// 两个对话通道;③整章优化结果在正文区顶部出 PolishCompareCard 对照;④整章重生成走
// generate 链路。整章重写卡(ReviseCard)/润色工作台(PolishPanel)/研讨(ReviseChat)已退场。
// 「当前章」以 URL ch 参数为唯一来源(useChapterContext),正文走 qk.chapter 共享缓存。
// 未拆技术债(勿再挂"下一期拆"的空头支票):act= 校对/评分两张动作卡、GenResultCard、
// 版本对比、全屏 Reader 仍内联于此,本组件仍是全区状态中枢(job 轮询/多章同步并发/
// 版本对比/连写队列)。拆分属独立重构、风险集中在状态中枢,不在本轮「正文即界面」范围,单独排期。
// 移动端:m-topbar(←/章题/阅读)+ 左右滑切章 + act 卡全屏 sheet。
import { useCallback, useEffect, useRef, useState } from "react";
import type { TouchEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  api, ChapterBrief, ChapterDetail, ChapterVersionBrief,
  ChapterVersionDetail, GenerateChapterResponse, Outline, PolishResult, RevisePair, Tendency,
} from "../api";
import { pollJob, errMsg } from "../pollJob";
import { qk, useChapter, useChapters, useInvalidateProject } from "../hooks/queries";
import { useChapterContext } from "../hooks/useChapterContext";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { AppAction, registerActionHandler } from "../ui/actions";
import { toast } from "../ui/Toaster";
import { confirmDialog } from "../ui/ConfirmDialog";
import Reader from "../components/Reader";
import GenResultCard from "./chapters/GenResultCard";
import { confirmAndReleaseGate } from "./chapters/releaseGate";
import VersionCompare from "./chapters/VersionCompare";
import ChapterRail from "./write/ChapterRail";
import CatalogDrawer from "./write/CatalogDrawer";
import Prose from "./write/Prose";
import ChapterStatusCard from "./write/ChapterStatusCard";
import NextChapterCard from "./write/NextChapterCard";
import ReviewCard from "./write/ReviewCard";
import ProofreadCard from "./write/ProofreadCard";
import AiDock, { DockMode, DockPrefill } from "./write/AiDock";
import PolishCompareCard from "./write/PolishCompareCard";
import AnnotatedReviseCard from "./write/AnnotatedReviseCard";
import { Annotation } from "./write/paraEdit";
import { deriveStage } from "./write/chapterStage";
import { useJob } from "../ui/useJob";

interface Props {
  pid: number; outlines: Outline[];
}

// 稳定的空数组引用:RQ 数据未就绪时用它,避免每次渲染新建 [] 触发 effect 抖动
const EMPTY_CHAPTERS: ChapterBrief[] = [];

// 动作卡(act= URL 参数):状态卡「更多」/快捷键/命令面板打开,在中栏展示(未拆技术债,见文件头注)。
// P2 后只剩校对/评分两张;重写/整章优化已由 AI 窄栏(AiDock)承接。
type Act = "proofread" | "review";
const ACTS: Act[] = ["proofread", "review"];
// 移动端动作卡全屏 sheet 的标题
const ACT_TITLE: Record<Act, string> = {
  proofread: "校对本章", review: "主编评分",
};

export default function WritePanel({ pid, outlines }: Props) {
  const invalidateProject = useInvalidateProject(pid);
  const nav = useNavigate();
  // 放行/按建议修订等异步长任务的统一入口(进全局任务中心,本地轮询拿结果)
  const { run } = useJob();
  // 章节列表走 React Query(与父级顶栏统计同一缓存,消除双真相);reload 即失效缓存重拉。
  const chaptersQuery = useChapters(pid);
  const chapters = chaptersQuery.data ?? EMPTY_CHAPTERS;
  // 当前章:URL ch 为唯一来源,正文详情走共享缓存(qk.chapter)
  const { chapterNum, setChapterNum } = useChapterContext();
  const chapterQuery = useChapter(pid, chapterNum);
  const current = chapterQuery.data ?? null;
  const qc = useQueryClient();
  // 本地拿到更新的正文(保存/通过审核/回退/生成完成)时直接写缓存,等效旧 setCurrent
  const setCurrent = useCallback(
    (c: ChapterDetail) => { qc.setQueryData(qk.chapter(pid, c.chapter_number), c); },
    [qc, pid],
  );
  // 动作卡进 URL(与 ch 共存):act= 中栏动作卡(ctx= 参考抽屉参数族已随 RefDrawer 废除)
  const [searchParams, setSearchParams] = useSearchParams();
  const actRaw = searchParams.get("act");
  const act: Act | null = ACTS.includes(actRaw as Act) ? (actRaw as Act) : null;
  const setAct = useCallback((a: Act | null) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (a === null) next.delete("act");
      else next.set("act", a);
      return next;
    });
  }, [setSearchParams]);

  // ---- 移动端壳(isMobile 由 useBreakpoint 判定,与 @media (max-width: 767px) 对齐)----
  const { isMobile } = useBreakpoint();
  // 沉浸模式(F11,class 挂在 write-zone,外层 chrome 由 CSS :has 隐藏)
  const [immersive, setImmersive] = useState(false);
  // 目录抽屉(章题按钮/空态引导/Ctrl+B 都可触发,双端同一份 CatalogDrawer)
  const [railOpen, setRailOpen] = useState(false);
  // 左右滑切章的起点坐标(见 write-main 的 touch 处理器)
  const swipeRef = useRef<{ x: number; y: number } | null>(null);

  // 进行中的「生成/重写」任务:阻塞式(顶部横幅 + 锁住章级动作)。
  const [genJob, setGenJob] = useState<{ num: number; stage: string } | null>(null);
  // 进行中的「保存后一致性同步」任务:非阻塞轻量角标,按章号并发(多章各自独立收尾,
  // 互不覆盖清空),不影响阅读/编辑其他章节。key=章号,value=当前阶段文案。
  const [syncJobs, setSyncJobs] = useState<Map<number, string>>(new Map());
  // 保存正文后待用户确认是否同步的章号(null=无待确认)。小幅修改可跳过同步。
  const [pendingSync, setPendingSync] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const [genResult, setGenResult] = useState<GenerateChapterResponse | null>(null);
  const [genTendency, setGenTendency] = useState<Tendency>({});
  // AI 窄栏(P2):开合(桌面默认展开/移动默认收起)、外部预填(nonce 触发)、正文选中段、
  // ③整章优化的对照结果(渲染在正文区顶部 PolishCompareCard)
  const [dockCollapsed, setDockCollapsed] = useState(isMobile);
  const [dockPrefill, setDockPrefill] = useState<DockPrefill | null>(null);
  const dockNonceRef = useRef(0);
  const [selectedPara, setSelectedPara] = useState<{ idx: number; text: string } | null>(null);
  const [polishCompare, setPolishCompare] = useState<{ original: string; result: PolishResult } | null>(null);
  // ②档批注(docs/10 §4):正文里 🖍 攒下的多条待处理意见(段号+快照+一句话),父级持有;
  // Prose 记入、AiDock 列出并成批发,「按批注改」job 完成后清空并出 reviseResult 验收卡。
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [reviseResult, setReviseResult] = useState<RevisePair[] | null>(null);
  // Prose 手改的未保存脏标记(组件内状态,经回调上报):切章前据此弹「丢弃修改」确认
  const proseDirtyRef = useRef(false);
  // 阅读器(全屏遮罩,共用组件 Reader):当前阅读章节
  const [reader, setReader] = useState<ChapterDetail | null>(null);
  const [readerLoading, setReaderLoading] = useState(false);
  // 正文版本对比:versionsFor=打开历史的章号,versions=该章快照列表,compareVer=选中对比的旧版全文
  const [versionsFor, setVersionsFor] = useState<number | null>(null);
  const [versions, setVersions] = useState<ChapterVersionBrief[] | null>(null);
  const [compareVer, setCompareVer] = useState<ChapterVersionDetail | null>(null);
  // 连写队列:勾选多章 → 后端一个 job 串行生成(状态在此持有,目录抽屉头部跟随切换)
  const [queueMode, setQueueMode] = useState(false);
  const [queuePicked, setQueuePicked] = useState<Set<number>>(new Set());
  // 组件卸载时中止轮询,防止卸载后继续 setState(生成与同步各用一个,互不覆盖)
  const abortRef = useRef<AbortController | null>(null);
  // 同步任务的中止器:按章号存,允许多章并发各自独立中止。
  const syncAbortRefs = useRef<Map<number, AbortController>>(new Map());
  useEffect(() => () => {
    abortRef.current?.abort();
    syncAbortRefs.current.forEach((c) => c.abort());
  }, []);

  // 同步角标按章号读写(函数式更新,避免并发覆盖);清除时同时移除中止器。
  const setSyncStage = useCallback((num: number, stage: string) => {
    setSyncJobs((m) => new Map(m).set(num, stage));
  }, []);
  const clearSync = useCallback((num: number) => {
    setSyncJobs((m) => { const n = new Map(m); n.delete(num); return n; });
    syncAbortRefs.current.delete(num);
  }, []);

  // reload 沿用旧调用点:失效 RQ 缓存 → 章节列表与父级顶栏统计一并重拉(消除陈旧)。
  const reload = invalidateProject;
  // 章节初次加载由 RQ 负责;仅把加载错误透传到面板错误区。
  useEffect(() => {
    if (chaptersQuery.error) setErr(String(chaptersQuery.error));
  }, [chaptersQuery.error]);

  const byNum = new Map(chapters.map((c) => [c.chapter_number, c]));
  const currentBrief = chapterNum !== null ? byNum.get(chapterNum) : undefined;

  // 人工审核通过(docs/08 §5.5):pending_review → approved;quarantined 后端 400 拦截
  async function approve(n: number) {
    try {
      const updated = await api.approveChapter(pid, n);
      toast.ok(`第 ${n} 章已通过审核`);
      if (current?.chapter_number === n) setCurrent(updated);
      await reload();
    } catch (e) {
      toast.err("通过审核失败", errMsg(e));
    }
  }

  // quarantined 放行(状态卡[放行]按钮;审核报告卡内也有一条,见 GenResultCard):
  // 确认+调用逻辑在 chapters/releaseGate 共用,完成后刷新列表与打开的正文
  async function releaseChapter(n: number) {
    const released = await confirmAndReleaseGate({ pid, n, run });
    if (released) {
      await reload();
      qc.invalidateQueries({ queryKey: qk.chapter(pid, n) });
    }
  }

  // 挂载时查有没有还在跑的任务(切走页面再回来的场景),有则接上轮询而不是装作没事。
  // 生成任务 → 阻塞横幅;同步任务 → 非阻塞轻量角标(二者独立,可同时重连)。
  useEffect(() => {
    let cancelled = false;
    api.runningJobs(pid).then(({ jobs }) => {
      if (cancelled) return;
      const gen = jobs.find((j) => j.kind.startsWith(`chapter-${pid}-`));
      if (gen) {
        const tail = gen.kind.split("-").pop()!;
        const ctrl = new AbortController();
        abortRef.current = ctrl;
        if (tail === "queue") {
          // 连写队列:通用轮询,完成后刷新列表
          setGenJob({ num: 0, stage: gen.stage });
          pollJob(gen.job_id, {
            signal: ctrl.signal,
            onStage: (stage) => setGenJob({ num: 0, stage }),
          }).then(() => reload())
            .catch(() => reload().catch(() => undefined))
            .finally(() => { if (!ctrl.signal.aborted) setGenJob(null); });
        } else {
          const n = Number(tail);
          setGenJob({ num: n, stage: gen.stage });
          trackGenerate(n, gen.job_id, ctrl);
        }
      }
      // 遗留同步任务可能有多章并发,全部接上非阻塞角标(各自独立收尾)
      jobs.filter((j) => j.kind.startsWith(`re-extract-${pid}-`)).forEach((sync) => {
        const n = Number(sync.kind.split("-").pop());
        if (syncAbortRefs.current.has(n)) return; // 已在跟踪,不重复接
        const ctrl = new AbortController();
        syncAbortRefs.current.set(n, ctrl);
        setSyncStage(n, sync.stage);
        pollJob(sync.job_id, {
          signal: ctrl.signal,
          onStage: (stage) => setSyncStage(n, stage),
        }).catch(() => undefined)
          .finally(() => { if (!ctrl.signal.aborted) clearSync(n); });
      });
    }).catch(() => undefined);
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  // 看板跳章/跨面板选章都收敛为改 URL ch;ch 变化时重置跟随旧章的视图态
  // (Prose 手改未保存有 proseDirtyRef + confirmDialog 把守,到这里一定是用户确认过的切换)
  useEffect(() => {
    setErr(""); setGenResult(null); closeVersions();
    setSelectedPara(null); setPolishCompare(null);
    setAnnotations([]); setReviseResult(null);
  }, [chapterNum]);

  // 章节正文拉取失败透传到面板错误区;未写的章 404 属预期(空态大卡承担引导),不透传
  useEffect(() => {
    if (chapterQuery.error && currentBrief) setErr(errMsg(chapterQuery.error));
  }, [chapterQuery.error, currentBrief]);

  // 章节阶段(deriveStage 纯函数):章首状态卡的三态映射与快捷键动作共用同一份推断
  const stage = deriveStage({ chapterNum, currentBrief, genJob });
  // 「写下一章」目标:大纲中第一个还没有正文的章(章尾卡/空态大卡共用)
  const nextChapterNum = outlines.find((o) => !byNum.get(o.chapter_number))?.chapter_number ?? null;
  // 生成完成后自动选章要用「当时的」选章状态做守卫,轮询回调里闭包会过期,故走 ref
  const chapterNumRef = useRef(chapterNum);
  useEffect(() => { chapterNumRef.current = chapterNum; }, [chapterNum]);

  // 打开 AI 窄栏并预填(P2):命令面板/快捷键/评分卡/状态卡的「梳理意见」入口都汇聚到这里
  function openDock(p: { text?: string; mode?: DockMode }) {
    dockNonceRef.current += 1;
    setDockPrefill({ text: p.text ?? "", mode: p.mode, nonce: dockNonceRef.current });
  }

  function pickNextBatch() {
    const unwritten = outlines
      .filter((o) => !byNum.get(o.chapter_number))
      .map((o) => o.chapter_number)
      .slice(0, 5);
    setQueuePicked(new Set(unwritten));
  }

  async function startQueue() {
    const nums = [...queuePicked].sort((a, b) => a - b);
    if (!nums.length) return;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setErr(""); setGenResult(null);
    setGenJob({ num: nums[0], stage: `队列 ${nums.length} 章:排队中…` });
    setQueueMode(false); setQueuePicked(new Set());
    try {
      const { job_id } = await api.generateQueue(pid, nums, genTendency);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setGenJob({ num: nums[0], stage }),
      });
      if (ctrl.signal.aborted) return;
      await reload();
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const msg = errMsg(e);
        setErr(msg);
        // 严格连写模式暂停:引导先去通过被卡住的那一章
        const paused = /第\s*(\d+)\s*章尚未人工审核通过/.exec(msg);
        if (paused) {
          toast.err("连写队列已暂停",
            `先去目录选中第 ${paused[1]} 章并通过审核,再重新排队(或在「设置」关闭「连写要求上一章审核通过」)`);
        }
        await reload().catch(() => undefined);
      }
    } finally { if (!ctrl.signal.aborted) setGenJob(null); }
  }

  const currentOutline = current
    ? outlines.find((o) => o.chapter_number === current.chapter_number) ?? null
    : null;
  // 顶栏/空态用:当前章蓝图(不要求已生成正文,未写的章也能显示标题)
  const activeOutline = chapterNum !== null
    ? outlines.find((o) => o.chapter_number === chapterNum) ?? null
    : null;

  // 打开某章 = 把 ch 写进 URL(Prose 手改未保存先确认丢弃);视图态清理由 ch 变化 effect 统一做
  async function open(n: number): Promise<boolean> {
    if (n === chapterNum) return true;
    if (proseDirtyRef.current) {
      const ok = await confirmDialog({
        title: "修改未保存,切换将丢弃?",
        body: "当前段落的手改尚未保存,继续将丢弃这些修改。",
        confirmText: "丢弃修改",
        danger: true,
      });
      if (!ok) return false;
    }
    setChapterNum(n);
    return true;
  }

  // 目录抽屉选章:确认打开成功后顺手关掉抽屉
  async function openFromRail(n: number) {
    if (await open(n)) setRailOpen(false);
  }

  // 左右滑切章(仅移动端、仅限已生成章):水平位移 >60px 且纵向位移小于水平的一半才触发,
  // 否则不干预——垂直滚动与文本选择优先(浏览器原生行为照常)。
  function onMainTouchStart(e: TouchEvent) {
    if (!isMobile) return;
    const t = e.touches[0];
    swipeRef.current = { x: t.clientX, y: t.clientY };
  }
  function onMainTouchEnd(e: TouchEvent) {
    if (!isMobile || !swipeRef.current) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - swipeRef.current.x;
    const dy = t.clientY - swipeRef.current.y;
    swipeRef.current = null;
    if (Math.abs(dx) <= 60 || Math.abs(dy) >= Math.abs(dx) / 2) return;
    if (chapterNum === null) return;
    const idx = generatedNums.indexOf(chapterNum);
    if (idx < 0) return; // 未生成的章不参与滑动翻页
    const target = dx < 0 ? generatedNums[idx + 1] : generatedNums[idx - 1];
    if (target !== undefined) void open(target);
  }

  // 阅读器:打开/翻章都走这里(tab/偏好由 Reader 内部管理)
  async function openReader(n: number) {
    setReaderLoading(true); setErr("");
    try {
      setReader(await api.getChapter(pid, n));
    } catch (e) { setErr(errMsg(e)); } finally { setReaderLoading(false); }
  }

  // 上一章/下一章:仅限已生成的章节
  const generatedNums = chapters.map((c) => c.chapter_number);
  const readerIdx = reader ? generatedNums.indexOf(reader.chapter_number) : -1;
  const prevNum = readerIdx > 0 ? generatedNums[readerIdx - 1] : null;
  const nextNum = readerIdx >= 0 && readerIdx < generatedNums.length - 1
    ? generatedNums[readerIdx + 1] : null;
  const readerOutline = reader
    ? outlines.find((o) => o.chapter_number === reader.chapter_number)
    : null;

  // 同步一致性引擎(重抽取 + 重建下游摘要 + 向量库)。非阻塞:仅显示轻量角标,
  // 用户可继续阅读/编辑其他章节。保存后确认、回退版本、挂载重连共用。
  async function triggerSync(num: number) {
    setPendingSync(null);
    if (syncAbortRefs.current.has(num)) return; // 该章已在同步,不重复起(与后端去重一致)
    const ctrl = new AbortController();
    syncAbortRefs.current.set(num, ctrl);
    setSyncStage(num, "启动同步…");
    try {
      const { job_id } = await api.reExtractAsync(pid, num);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setSyncStage(num, stage),
      });
      if (!ctrl.signal.aborted) toast.ok(`第 ${num} 章一致性同步完成`);
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const msg = errMsg(e);
        if (msg.startsWith("任务超时") || msg.startsWith("多次查询")) {
          toast.err("同步进度查询中断", "任务可能仍在后台运行,稍后刷新可见最新状态");
        } else {
          toast.err(`第 ${num} 章同步失败`, msg);
        }
      }
    } finally { if (!ctrl.signal.aborted) clearSync(num); }
  }

  // 轮询生成任务直至完成并落地结果(发起生成与「切走再回来重连」共用)
  async function trackGenerate(n: number, jobId: string, ctrl: AbortController) {
    try {
      // 轮询任务进度(五段:草稿→定稿→检查→抽取→摘要)
      const result = await pollJob<GenerateChapterResponse>(jobId, {
        signal: ctrl.signal,
        onStage: (stage) => setGenJob({ num: n, stage }),
      });
      if (ctrl.signal.aborted) return;
      setGenResult(result);
      setCurrent({
        chapter_number: result.chapter_number, status: result.status,
        word_count: result.word_count, is_stale: result.is_stale,
        draft_content: result.draft_content, final_content: result.final_content,
        outline_version_used: result.outline_version_used,
      });
      await reload();
      // 生成后自动选章(坏味道 #8):当前未选章或选的就是它时把 ch 写进 URL,
      // 消除"结果卡悬空 + 中栏请选择章节";用户中途切到别的章则不拽回
      if (chapterNumRef.current === null || chapterNumRef.current === n) setChapterNum(n);
      // 重写完成:若有旧版快照,自动弹「旧版 vs 新版」对比供选择
      await openVersions(n, true);
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const msg = errMsg(e);
        // 轮询中断(超时/网络抖动):任务可能仍在后台运行,刷新列表让用户看到真实进度
        if (msg.startsWith("任务超时") || msg.startsWith("多次查询")) {
          setErr(`进度查询中断:${msg}`);
          await reload().catch(() => undefined);
        } else {
          setErr(msg);
        }
      }
    } finally { if (!ctrl.signal.aborted) setGenJob(null); }
  }

  async function generate(n: number, revision = "") {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setErr(""); setGenResult(null);
    if (revision) setAct(null); // 带着意见重生成:收起动作卡,结果走生成结果卡
    setGenJob({ num: n, stage: "排队中…" });
    let jobId: string;
    try {
      ({ job_id: jobId } = await api.generateChapterAsync(pid, n, genTendency, revision));
    } catch (e) {
      setErr(errMsg(e));
      setGenJob(null);
      return;
    }
    await trackGenerate(n, jobId, ctrl);
  }

  function closeVersions() {
    setVersionsFor(null); setVersions(null); setCompareVer(null);
  }

  // 打开某章历史版本。auto=true 时(重写刚完成)仅在确有旧版快照时才弹,并自动选中最新一版对比
  async function openVersions(n: number, auto = false) {
    setErr("");
    try {
      const list = await api.listChapterVersions(pid, n);
      if (auto && !list.length) return;  // 首次生成无旧版,不打扰
      setVersions(list); setVersionsFor(n); setCompareVer(null);
      if (auto && list.length) {
        setCompareVer(await api.getChapterVersion(pid, n, list[0].id));
      }
    } catch (e) { setErr(errMsg(e)); }
  }

  async function selectVersion(n: number, v: ChapterVersionBrief) {
    setErr("");
    try { setCompareVer(await api.getChapterVersion(pid, n, v.id)); }
    catch (e) { setErr(errMsg(e)); }
  }

  // 回退到旧版:换回正文 → 自动同步一致性引擎。回退是整段替换(改动大)故不询问,
  // 但同步本身非阻塞,只显角标,不挡操作。
  async function restoreVersion(n: number, vid: number) {
    setErr("");
    try {
      const updated = await api.restoreChapterVersion(pid, n, vid);
      setCurrent(updated);
      closeVersions();
      await reload();
      void triggerSync(n);
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  const genBlocked = !!genJob;
  const genHint = genJob
    ? (genJob.num === 0
      // num=0 是切走重连的连写队列标记,没有具体章号,不给「第 0 章」
      ? "连写任务进行中,完成后可继续操作"
      : genJob.num === chapterNum ? "本章任务进行中" : `第 ${genJob.num} 章任务进行中,完成后可继续操作`)
    : "";

  // ---- 统一动作 dispatch:快捷键/命令面板/Tauri 菜单共用的章级 handler ----
  // 对象每次渲染重建以捕获最新状态;经 ref 交给「挂载时注册一次」的稳定 wrapper,避免闭包过期。
  const actionHandlers: Partial<Record<AppAction, () => void>> = {
    generate: () => {
      if (chapterNum !== null && !currentBrief && !genBlocked) void generate(chapterNum);
    },
    revise: () => { if (current) openDock({ mode: "revise" }); },
    polish: () => {
      if (current) openDock({
        mode: "revise",
        text: "这章文笔/节奏整体不满意,帮我梳理一份整章优化(锁情节、不动情节)的修改意见",
      });
    },
    proofread: () => { if (current) setAct("proofread"); },
    review: () => { if (current) setAct("review"); },
    versions: () => { if (chapterNum !== null) void openVersions(chapterNum); },
    queue: () => { setRailOpen(true); setQueueMode((v) => !v); setQueuePicked(new Set()); },
    "prev-chapter": () => {
      if (chapterNum === null) return;
      const i = generatedNums.indexOf(chapterNum);
      if (i > 0) void open(generatedNums[i - 1]);
    },
    "next-chapter": () => {
      if (chapterNum === null) return;
      const i = generatedNums.indexOf(chapterNum);
      if (i >= 0 && i < generatedNums.length - 1) void open(generatedNums[i + 1]);
    },
    // 目录抽屉(Ctrl+B/命令面板「目录」)
    "toggle-rail": () => setRailOpen((v) => !v),
    immersive: () => setImmersive((v) => !v),
  };
  const actionHandlersRef = useRef(actionHandlers);
  useEffect(() => { actionHandlersRef.current = actionHandlers; });
  useEffect(() => {
    const offs = (Object.keys(actionHandlersRef.current) as AppAction[]).map((name) =>
      registerActionHandler(name, () => actionHandlersRef.current[name]?.()));
    return () => offs.forEach((off) => off());
  }, []);

  // 沉浸模式:Esc 退出(F11/动作「immersive」开合;快捷键监听在 useDesktopHotkeys)
  useEffect(() => {
    if (!immersive) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setImmersive(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [immersive]);

  // 动作卡(act= 进 URL,可刷新/分享):桌面在中栏内联,移动端换全屏 sheet 容器(组件同一份)
  const actCards = (
    <>
      {act === "proofread" && current && chapterNum !== null && (
        <ProofreadCard pid={pid} chapterNum={chapterNum} />
      )}
      {act === "review" && current && chapterNum !== null && (
        <ReviewCard pid={pid} chapterNum={chapterNum}
          onRevise={(text) => openDock({ text, mode: "revise" })} />
      )}
    </>
  );

  return (
    <div className={"write-zone" + (immersive ? " immersive" : "")}>
      {genJob && (
        <div className="gen-banner">
          <span className="spin" />
          <span className="gen-banner-text">
            {genJob.num === 0 || genJob.stage.startsWith("[")
              ? `连写队列进行中(${genJob.stage})`
              : `第 ${genJob.num} 章生成中(${genJob.stage}),完成后可继续操作其他章节`}
          </span>
        </div>
      )}
      {[...syncJobs.entries()].map(([num, stage]) => (
        <div className="sync-badge" key={num}>
          <span className="spin spin-sm" />
          <span>第 {num} 章同步一致性引擎中({stage})· 不影响继续操作</span>
        </div>
      ))}
      {err && <div className="msg-err">{err}</div>}

      {/* ---- 移动端顶栏:←返回项目列表 + 当前章(点击开目录抽屉)+ 阅读入口 ---- */}
      {isMobile && (
        <div className="m-topbar">
          <button type="button" className="m-topbar-btn" title="返回项目列表"
            onClick={() => nav("/")}>←</button>
          <button type="button" className="m-topbar-title" title="打开目录选章"
            onClick={() => setRailOpen(true)}>
            {chapterNum !== null
              ? `第${chapterNum}章${activeOutline?.title ? ` · ${activeOutline.title}` : ""}`
              : "选择章节"}
          </button>
          <button type="button" className="m-topbar-btn" disabled={!currentBrief}
            title={currentBrief ? "阅读本章(全屏阅读器,可选段润色)" : "先在目录选一章已生成的章节"}
            onClick={() => chapterNum !== null && void openReader(chapterNum)}>
            阅读
          </button>
        </div>
      )}

      {/* ---- 主场:正文列 + 右侧 AI 窄栏(P2);移动端 AiDock 为 fixed 元素,位置无关 ---- */}
      <div className="write-body">
      {/* ---- 主场正文列:状态卡 + 蓝图卡 + 正文(段落气泡)+ 章尾下一章卡(移动端支持左右滑切章) ---- */}
      <div className="write-main" onTouchStart={onMainTouchStart} onTouchEnd={onMainTouchEnd}>
        {versionsFor !== null && versions !== null && (
          <VersionCompare
            chapterNumber={versionsFor}
            versions={versions}
            compareVer={compareVer}
            current={current}
            busy={!!genJob}
            onClose={closeVersions}
            onSelectVersion={(v) => selectVersion(versionsFor, v)}
            onRestore={(vid) => restoreVersion(versionsFor, vid)}
          />
        )}

        {genResult && (
          <GenResultCard
            pid={pid}
            result={genResult}
            genBlocked={genBlocked}
            genHint={genHint}
            onClose={() => setGenResult(null)}
            onChanged={() => {
              reload();
              // 修订/放行后同步刷新打开的正文(失效缓存,共享 qk.chapter 自动重拉)
              qc.invalidateQueries({ queryKey: qk.chapter(pid, genResult.chapter_number) });
            }}
            onRewrite={() => {
              // 「去重写本章」:切到该章并打开 AI 栏梳理修改意见
              setChapterNum(genResult.chapter_number);
              openDock({ mode: "revise" });
            }}
          />
        )}

        {/* 「更多」/快捷键打开的动作卡(act= 进 URL);移动端为全屏 sheet */}
        {isMobile && act && current ? (
          <div className="m-sheet-overlay">
            <div className="m-sheet">
              <div className="m-sheet-head">
                <h3 className="grow">{ACT_TITLE[act]}</h3>
                <button className="btn-sm" onClick={() => setAct(null)}>关闭</button>
              </div>
              {actCards}
            </div>
          </div>
        ) : actCards}

        {/* AI 栏③整章优化的对照结果(原文/润色稿,可微调后应用) */}
        {polishCompare && current && (
          <PolishCompareCard
            pid={pid}
            chapterNum={current.chapter_number}
            original={polishCompare.original}
            result={polishCompare.result}
            onApplied={() => setPolishCompare(null)}
            onDiscard={() => setPolishCompare(null)}
          />
        )}

        {/* AI 栏②按批注改的验收卡(逐条 diff,接受/拒绝走 paraEdit 快照守卫写回) */}
        {reviseResult && current && (
          <AnnotatedReviseCard
            pid={pid}
            chapter={current}
            pairs={reviseResult}
            onSaved={(updated) => { setCurrent(updated); void reload(); }}
            onClose={() => setReviseResult(null)}
          />
        )}

        {current ? (
          <>
            {/* 章首状态卡:三态人话(冲突拍板/过目通过/大纲已变)+ 「更多」过渡入口 */}
            <ChapterStatusCard
              pid={pid}
              stage={stage}
              currentBrief={currentBrief}
              current={current}
              genBlocked={genBlocked}
              genHint={genHint}
              onApprove={() => approve(current.chapter_number)}
              onRelease={() => releaseChapter(current.chapter_number)}
              onAct={(a) => {
                // P2:重写/整章优化由 AI 窄栏承接;校对/评分仍走 act= 动作卡
                if (a === "revise") openDock({ mode: "revise" });
                else setAct(a);
              }}
              onVersions={() => { void openVersions(current.chapter_number); }}
              onChanged={() => {
                reload();
                qc.invalidateQueries({ queryKey: qk.chapter(pid, current.chapter_number) });
              }}
            />

            {/* 章首蓝图卡:默认收起一行,展开看摘要/伏笔 */}
            {currentOutline && (
              <details className="card card-info blueprint-card">
                <summary>
                  <b>本章蓝图</b> 第{currentOutline.chapter_number}章《{currentOutline.title}》
                  <span className="badge">{currentOutline.chapter_role}</span>
                </summary>
                <div className="muted mt-1">{currentOutline.summary}</div>
                <div className="meta-line">
                  伏笔:{currentOutline.foreshadowing || "无"}
                </div>
              </details>
            )}

            <div className="card">
              <div className="content-head mb-2">
                <div className="content-head-title">
                  <h2>第{current.chapter_number}章</h2>
                  <span className="content-head-meta">正文 · {current.word_count}字</span>
                  {currentBrief?.is_stale && <span className="badge err">大纲已变</span>}
                </div>
              </div>
              {pendingSync === current.chapter_number && (
                <div className="sync-ask mb-2">
                  <span className="sync-ask-text">
                    第 {current.chapter_number} 章已保存,要同步一致性引擎吗?
                    <b className="hint">同步会更新人物状态、伏笔与后续章节的前情摘要;只改了几处措辞可以跳过。</b>
                  </span>
                  <span className="sync-ask-actions">
                    <button className="primary btn-sm" disabled={syncJobs.has(current.chapter_number)}
                      onClick={() => triggerSync(current.chapter_number)}>立即同步</button>
                    <button className="btn-sm"
                      onClick={() => setPendingSync(null)}>跳过</button>
                  </span>
                </div>
              )}
              {/* 正文即界面:段落点选 → 气泡(改这段/手改);整章 textarea 编辑态已删除 */}
              <Prose
                pid={pid}
                chapter={current}
                genBlocked={genBlocked}
                genHint={genHint}
                onSaved={(updated) => { setCurrent(updated); void reload(); }}
                onSyncAsk={(num) => setPendingSync(num)}
                onDirtyChange={(dirty) => { proseDirtyRef.current = dirty; }}
                onSelectChange={setSelectedPara}
                annotations={annotations}
                onAnnotate={(idx, snapshot, note) =>
                  setAnnotations((prev) => [...prev, { paraIdx: idx, snapshot, note }])}
              />
            </div>

            {/* 章尾下一章卡:唯一常驻的「推进」入口 */}
            <NextChapterCard
              pid={pid}
              nextNum={nextChapterNum}
              nextTitle={nextChapterNum !== null
                ? outlines.find((o) => o.chapter_number === nextChapterNum)?.title
                : undefined}
              genBlocked={genBlocked}
              genHint={genHint}
              onGenerate={(n) => { void generate(n); }}
            />
          </>
        ) : chapterNum !== null ? (
          chapterQuery.isPending ? (
            <div className="card muted"><span className="spin spin-sm" /> 加载正文…</div>
          ) : (
            /* 空态大卡:选中了未写的章 */
            <div className="card empty-chapter">
              <h2>第{chapterNum}章{activeOutline ? `《${activeOutline.title}》` : ""}</h2>
              <div className="muted mt-1">这章还没写。</div>
              {activeOutline && (
                <div className="muted mt-1">{activeOutline.summary}</div>
              )}
              <div className="mt-2">
                <button className="primary" disabled={genBlocked}
                  title={genBlocked ? genHint : "按蓝图生成本章,生成时自动注入:本章蓝图、前情摘要、人物状态、到期伏笔"}
                  onClick={() => void generate(chapterNum)}>
                  让 AI 写这一章
                </button>
              </div>
            </div>
          )
        ) : (
          /* 未选章引导(双端同一份):目录抽屉是唯一选章入口 */
          <div className="card empty-chapter">
            <h2>先选一章</h2>
            <div className="muted mt-1">打开目录挑一章:已写的直接读、点段落就能改;没写的让 AI 写。</div>
            <div className="mt-2">
              <button className="primary" onClick={() => setRailOpen(true)}>打开目录</button>
            </div>
          </div>
        )}
      </div>

      {/* ---- AI 窄栏(P2):桌面右侧 320px/40px 细条;移动端底部输入条/全屏 sheet ---- */}
      {current && chapterNum !== null && (
        <AiDock
          pid={pid}
          chapterNum={chapterNum}
          current={current}
          selectedPara={selectedPara}
          genBlocked={genBlocked}
          genHint={genHint}
          collapsed={dockCollapsed}
          onCollapsedChange={setDockCollapsed}
          prefill={dockPrefill}
          onSaved={(updated) => { setCurrent(updated); void reload(); }}
          onRegenerate={(revision) => {
            // ④:移动端收起 sheet 让出生成横幅;桌面横幅在正文列顶部,无需收
            if (isMobile) setDockCollapsed(true);
            void generate(chapterNum, revision);
          }}
          onPolishResult={(original, result) => {
            if (isMobile) setDockCollapsed(true); // 同上:让出正文区顶部的对照卡
            setPolishCompare({ original, result });
          }}
          annotations={annotations}
          onRemoveAnnotation={(i) =>
            setAnnotations((prev) => prev.filter((_, idx) => idx !== i))}
          onReviseResult={(pairs) => {
            if (isMobile) setDockCollapsed(true); // 让出正文区顶部的验收卡
            setReviseResult(pairs);
            setAnnotations([]); // 已成批发出,批注退场;结果由验收卡逐条处理
          }}
        />
      )}
      </div>

      {/* ---- 目录抽屉(双端同一份):搜索/筛选/连写队列/正文倾向;选章后自动关闭 ---- */}
      {railOpen && (
        <CatalogDrawer onClose={() => setRailOpen(false)}>
          <ChapterRail
            outlines={outlines}
            chapters={chapters}
            genJob={genJob}
            queueMode={queueMode}
            queuePicked={queuePicked}
            onToggleQueueMode={() => { setQueueMode(!queueMode); setQueuePicked(new Set()); }}
            onToggleQueuePick={(n, checked) => {
              const next = new Set(queuePicked);
              if (checked) next.add(n); else next.delete(n);
              setQueuePicked(next);
            }}
            onPickNextBatch={pickNextBatch}
            onStartQueue={startQueue}
            genTendency={genTendency}
            onTendencyChange={setGenTendency}
            onOpen={(n) => { void openFromRail(n); }}
            onClose={() => setRailOpen(false)}
          />
        </CatalogDrawer>
      )}

      {(reader || readerLoading) && (
        <Reader
          loading={readerLoading}
          chapter={reader}
          title={readerOutline?.title}
          hasPrev={prevNum != null}
          hasNext={nextNum != null}
          onPrev={() => prevNum != null && openReader(prevNum)}
          onNext={() => nextNum != null && openReader(nextNum)}
          onClose={() => setReader(null)}
          polishCtx={{
            pid,
            chapterNumber: reader?.chapter_number ?? 0,
            onApplied: (updated) => { setReader(updated); reload(); },
          }}
        />
      )}
    </div>
  );
}
