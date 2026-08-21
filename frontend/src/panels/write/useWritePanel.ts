// WritePanel「正文即界面」写作区主场的编排与本地 UI 状态(拆自 WritePanel.tsx,行为逐字一致):
// RQ 章节列表/正文共享缓存、act= 动作卡 URL、目录抽屉/沉浸/移动端壳与滑动切章、AI 窄栏开合与
// ②档批注/③整章优化对照等本地态,并编排 5 个域 hook(useReader/useChapterVersions/
// useConsistencySync/useChapterGeneration/useImmersive)。壳只负责布局渲染,状态与 handler 全在此。
import { useCallback, useEffect, useRef, useState } from "react";
import type { TouchEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  api, ChapterBrief, ChapterDetail,
  Outline, PolishResult, RevisePair,
} from "../../api";
import { errMsg } from "../../pollJob";
import { qk, useChapter, useChapters, useInvalidateProject } from "../../hooks/queries";
import { useChapterContext } from "../../hooks/useChapterContext";
import { useBreakpoint } from "../../hooks/useBreakpoint";
import { AppAction, registerActionHandler } from "../../ui/actions";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { DockMode, DockPrefill } from "./AiDock";
import { Annotation } from "./paraEdit";
import { deriveStage } from "./chapterStage";
import { useImmersive } from "./useImmersive";
import { useReader } from "./useReader";
import { useChapterVersions } from "./useChapterVersions";
import { useConsistencySync } from "./useConsistencySync";
import { useChapterGeneration } from "./useChapterGeneration";

interface UseWritePanelArgs {
  pid: number; outlines: Outline[];
}

// 稳定的空数组引用:RQ 数据未就绪时用它,避免每次渲染新建 [] 触发 effect 抖动
const EMPTY_CHAPTERS: ChapterBrief[] = [];

// 动作卡(act= URL 参数):状态卡「更多」/快捷键/命令面板打开,在中栏展示(未拆技术债,见 WritePanel 头注)。
// P2 后只剩校对/评分两张;重写/整章优化已由 AI 窄栏(AiDock)承接。
export type Act = "proofread" | "review";
const ACTS: Act[] = ["proofread", "review"];
// 移动端动作卡全屏 sheet 的标题
export const ACT_TITLE: Record<Act, string> = {
  proofread: "校对本章", review: "主编评分",
};

export function useWritePanel({ pid, outlines }: UseWritePanelArgs) {
  const invalidateProject = useInvalidateProject(pid);
  const nav = useNavigate();
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
  // 沉浸模式(F11/命令面板/菜单):抽到 useImmersive(class 挂 write-zone,开启时 Esc 退出)
  const { immersive, toggleImmersive } = useImmersive();
  // 目录抽屉(章题按钮/空态引导/Ctrl+B 都可触发,双端同一份 CatalogDrawer)
  const [railOpen, setRailOpen] = useState(false);
  // 故事地图覆盖层(Ctrl+M/命令面板/章首入口唤出,Tier 3:全书脉络纵览+跳章)
  const [mapOpen, setMapOpen] = useState(false);
  // 左右滑切章的起点坐标(见 write-main 的 touch 处理器)
  const swipeRef = useRef<{ x: number; y: number } | null>(null);

  const [err, setErr] = useState("");
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
  // 阅读器(全屏遮罩,共用组件 Reader):抽到 useReader(reader 态/翻章派生/openReader)
  const { reader, readerLoading, setReader, openReader, prevNum, nextNum, readerOutline } =
    useReader(pid, chapters, outlines, setErr);

  // reload 沿用旧调用点:失效 RQ 缓存 → 章节列表与父级顶栏统计一并重拉(消除陈旧)。
  const reload = invalidateProject;
  // 一致性同步(保存/回退后重抽取+重建下游摘要+向量库):抽到 useConsistencySync。
  // triggerSync 供回退版本联动(注入下方 useChapterVersions);reconnectSync 供挂载重连遗留同步。
  const { syncJobs, pendingSync, setPendingSync, triggerSync, reconnectSync } = useConsistencySync(pid);
  // 正文版本对比(打开历史/选版/回退):抽到 useChapterVersions。回退联动写回+刷新+同步,
  // 故注入 setCurrent/reload/triggerSync;openVersions 供生成完成后自动弹对比(见 trackGenerate)。
  const {
    versionsFor, versions, compareVer,
    closeVersions, openVersions, selectVersion, restoreVersion,
  } = useChapterVersions(pid, { setErr, setCurrent, reload, triggerSync });
  // 章节生成(按蓝图生成/带意见重写/多章连写):抽到 useChapterGeneration。注入 openVersions
  // (重写完弹对比)/setChapterNum(生成后自动选章)/clearAct 等联动;genBlocked/genHint 因依赖
  // 当前 chapterNum,留壳做纯派生(见下)。
  const {
    genJob, genResult, setGenResult,
    genTendency, setGenTendency,
    queueMode, setQueueMode, queuePicked, setQueuePicked,
    generate, startQueue, pickNextBatch, reconnectGenerate,
  } = useChapterGeneration(pid, outlines, chapters, {
    setErr, setCurrent, reload, setChapterNum, chapterNum,
    openVersions, clearAct: () => setAct(null),
  });
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

  // 挂载时查有没有还在跑的任务(切走页面再回来的场景),有则接上轮询而不是装作没事。
  // 生成任务 → 阻塞横幅;同步任务 → 非阻塞轻量角标(二者独立,可同时重连)。
  useEffect(() => {
    let cancelled = false;
    api.runningJobs(pid).then(({ jobs }) => {
      if (cancelled) return;
      // 生成任务 → 阻塞横幅;同步任务 → 非阻塞角标(各自 hook 按 kind 过滤 + 去重接线,可同时重连)
      reconnectGenerate(jobs);
      reconnectSync(jobs);
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
  }, [chapterNum, closeVersions, setGenResult]);

  // 章节正文拉取失败透传到面板错误区;未写的章 404 属预期(空态大卡承担引导),不透传
  useEffect(() => {
    if (chapterQuery.error && currentBrief) setErr(errMsg(chapterQuery.error));
  }, [chapterQuery.error, currentBrief]);

  // 章节阶段(deriveStage 纯函数):章首状态卡的三态映射与快捷键动作共用同一份推断
  const stage = deriveStage({ chapterNum, currentBrief, genJob });
  // 「写下一章」目标:大纲中第一个还没有正文的章(章尾卡/空态大卡共用)
  const nextChapterNum = outlines.find((o) => !byNum.get(o.chapter_number))?.chapter_number ?? null;
  // 打开 AI 窄栏并预填(P2):命令面板/快捷键/评分卡/状态卡的「梳理意见」入口都汇聚到这里
  function openDock(p: { text?: string; mode?: DockMode }) {
    dockNonceRef.current += 1;
    setDockPrefill({ text: p.text ?? "", mode: p.mode, nonce: dockNonceRef.current });
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

  // 已生成章号列表(左右滑切章 + 上/下章动作共用;阅读器翻章在 useReader 内另算)
  const generatedNums = chapters.map((c) => c.chapter_number);

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
    // 故事地图(Ctrl+M/命令面板「地图」):全书脉络纵览
    "toggle-map": () => setMapOpen((v) => !v),
    immersive: toggleImmersive,
  };
  const actionHandlersRef = useRef(actionHandlers);
  useEffect(() => { actionHandlersRef.current = actionHandlers; });
  useEffect(() => {
    const offs = (Object.keys(actionHandlersRef.current) as AppAction[]).map((name) =>
      registerActionHandler(name, () => actionHandlersRef.current[name]?.()));
    return () => offs.forEach((off) => off());
  }, []);

  return {
    // RQ / 当前章
    chapters, chapterNum, setChapterNum, current, chapterQuery, currentBrief,
    currentOutline, activeOutline, setCurrent, reload, qc, nav,
    // act= 动作卡
    act, setAct, openDock,
    // 环境 / 壳 / 滑动切章
    isMobile, immersive, railOpen, setRailOpen, mapOpen, setMapOpen, onMainTouchStart, onMainTouchEnd,
    // 顶部错误 & 派生
    err, stage, nextChapterNum, genBlocked, genHint,
    // 本地 UI 态:AI 窄栏 / 选段 / 对照 / 批注 / 脏标记
    dockCollapsed, setDockCollapsed, dockPrefill,
    selectedPara, setSelectedPara,
    polishCompare, setPolishCompare,
    annotations, setAnnotations,
    reviseResult, setReviseResult,
    proseDirtyRef,
    // 审核(放行已下沉到 ChapterStatusCard 的 GateResolve,不再从壳导出)
    approve,
    // useConsistencySync
    syncJobs, pendingSync, setPendingSync, triggerSync,
    // useChapterVersions
    versionsFor, versions, compareVer, closeVersions, openVersions, selectVersion, restoreVersion,
    // useChapterGeneration
    genJob, genResult, setGenResult, genTendency, setGenTendency,
    queueMode, setQueueMode, queuePicked, setQueuePicked,
    generate, startQueue, pickNextBatch,
    // useReader
    reader, readerLoading, setReader, openReader, prevNum, nextNum, readerOutline,
    // 目录抽屉选章
    openFromRail,
  };
}
