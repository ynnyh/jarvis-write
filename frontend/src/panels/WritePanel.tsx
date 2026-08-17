// write 区三栏工作台:左=章节轨(唯一选章入口),中=阶段条+当前章正文+蓝图+动作产出卡,
// 右=参考抽屉(ctx= 进 URL)。中栏顶部 StageBar 按章节阶段(deriveStage)摆出 1-2 个主动作,
// 次动作收进「更多动作」下拉(act= 进 URL)——状态驱动流水线,替代旧底部动作条。
// 「当前章」以 URL ch 参数为唯一来源(useChapterContext),正文走 qk.chapter 共享缓存。
// 由原 ChaptersPanel 拆解而来;字数守卫/审校把关设置卡已搬去 settings 区(ProjectSettingsPanel)。
// 移动端壳(交互重构 C 阶段,isMobile 由 useBreakpoint 判定,与 @media (max-width: 767px) 对齐):
// 顶栏(返回/章标题/阅读)+ 底部栏(写/读/参考/本书设置)+ FAB(点按=当前阶段主动作/长按动作扇)
// + 左右滑切章;章节轨/参考/动作卡换成全屏抽屉与 sheet,组件与桌面同一份。
import { useCallback, useEffect, useRef, useState } from "react";
import type { TouchEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  api, ChapterBrief, ChapterDetail, ChapterVersionBrief,
  ChapterVersionDetail, EditorAction, GenerateChapterResponse, Outline, Tendency,
} from "../api";
import { pollJob, errMsg } from "../pollJob";
import { qk, useChapter, useChapters, useInvalidateProject } from "../hooks/queries";
import { useChapterContext } from "../hooks/useChapterContext";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { AppAction, registerActionHandler } from "../ui/actions";
import { emitChapterSaved } from "../desktop";
import { toast } from "../ui/Toaster";
import { confirmDialog } from "../ui/ConfirmDialog";
import Reader, { Paragraphs } from "../components/Reader";
import GenResultCard from "./chapters/GenResultCard";
import { confirmAndReleaseGate } from "./chapters/releaseGate";
import VersionCompare from "./chapters/VersionCompare";
import ChapterRail from "./write/ChapterRail";
import ReviseCard from "./write/ReviseCard";
import ReviewCard from "./write/ReviewCard";
import ProofreadCard from "./write/ProofreadCard";
import RefDrawer from "./write/RefDrawer";
import StageBar from "./write/StageBar";
import { deriveStage } from "./write/chapterStage";
import PolishPanel from "./PolishPanel";
import { useJob } from "../ui/useJob";

interface Props {
  pid: number; outlines: Outline[];
}

// 稳定的空数组引用:RQ 数据未就绪时用它,避免每次渲染新建 [] 触发 effect 抖动
const EMPTY_CHAPTERS: ChapterBrief[] = [];

// 中栏阶段条「更多动作」与动作卡(act= URL 参数):在中栏开对应动作卡
type Act = "revise" | "polish" | "proofread" | "review";
const ACTS: Act[] = ["revise", "polish", "proofread", "review"];
// 移动端动作卡全屏 sheet 的标题
const ACT_TITLE: Record<Act, string> = {
  revise: "重写本章", polish: "润色本章", proofread: "校对本章", review: "主编评分",
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
  // 动作卡与参考抽屉都进 URL(与 ch 共存):act= 中栏动作卡,ctx= 右栏抽屉
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
  const setCtx = useCallback((key: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("ctx", key);
      return next;
    });
  }, [setSearchParams]);
  const clearCtx = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("ctx");
      return next;
    });
  }, [setSearchParams]);

  // ---- 移动端壳(C 阶段)----
  const { isMobile } = useBreakpoint();
  // ---- 桌面壳(D 阶段):章节轨开合(Ctrl+B)与沉浸模式(F11,class 挂在 write-zone) ----
  const [railHidden, setRailHidden] = useState(false);
  const [immersive, setImmersive] = useState(false);
  // 章节轨全屏抽屉(顶栏章标题/底部「写」/无 ch 引导都可触发)
  const [railOpen, setRailOpen] = useState(false);
  // 参考全屏 sheet:底部「参考」或任何写入 ctx= 的入口(如正文卡「审核报告」)都会打开
  const [refSheetOpen, setRefSheetOpen] = useState(false);
  const ctxParam = searchParams.get("ctx");
  const refSheetVisible = refSheetOpen || !!ctxParam;
  const closeRefSheet = useCallback(() => {
    setRefSheetOpen(false);
    clearCtx();
  }, [clearCtx]);
  // FAB 动作扇(重写/润色/校对/评分)
  const [fanOpen, setFanOpen] = useState(false);
  // FAB 长按计时与标记:长按(≥0.5s)展开动作扇,吞掉随后的 click
  const fabTimerRef = useRef<number | null>(null);
  const fabLongRef = useRef(false);
  // 左右滑切章的起点坐标(见 write-main 的 touch 处理器)
  const swipeRef = useRef<{ x: number; y: number } | null>(null);

  // 进行中的「生成/重写」任务:阻塞式(顶部横幅 + 锁住章节列表操作)。
  const [genJob, setGenJob] = useState<{ num: number; stage: string } | null>(null);
  // 进行中的「保存后一致性同步」任务:非阻塞轻量角标,按章号并发(多章各自独立收尾,
  // 互不覆盖清空),不影响阅读/编辑其他章节。key=章号,value=当前阶段文案。
  const [syncJobs, setSyncJobs] = useState<Map<number, string>>(new Map());
  // 保存正文后待用户确认是否同步的章号(null=无待确认)。小幅修改可跳过同步。
  const [pendingSync, setPendingSync] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [genResult, setGenResult] = useState<GenerateChapterResponse | null>(null);
  const [genTendency, setGenTendency] = useState<Tendency>({});
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  // 章节轨行内重写框:当前展开的章号(null=收起)与意见文本(可留空=直接重写)
  const [reviseFor, setReviseFor] = useState<number | null>(null);
  const [reviseText, setReviseText] = useState("");
  // 中栏重写卡(act=revise)的意见文本:评分卡「按此重写」与 localStorage 交接都预填到这里
  const [reviseDraft, setReviseDraft] = useState("");
  // 阅读器(全屏遮罩,共用组件 Reader):当前阅读章节
  const [reader, setReader] = useState<ChapterDetail | null>(null);
  const [readerLoading, setReaderLoading] = useState(false);
  // 正文版本对比:versionsFor=打开历史的章号,versions=该章快照列表,compareVer=选中对比的旧版全文
  const [versionsFor, setVersionsFor] = useState<number | null>(null);
  const [versions, setVersions] = useState<ChapterVersionBrief[] | null>(null);
  const [compareVer, setCompareVer] = useState<ChapterVersionDetail | null>(null);
  // 连写队列:勾选多章 → 后端一个 job 串行生成(状态在此持有,章节轨头部与 StageBar 跟随切换)
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

  // 人工审核通过(docs/08 §5.5):pending_review → approved;quarantined 后端 400 拦截
  const [approving, setApproving] = useState<number | null>(null);
  async function approve(n: number) {
    setApproving(n);
    try {
      const updated = await api.approveChapter(pid, n);
      toast.ok(`第 ${n} 章已通过审核`);
      if (current?.chapter_number === n) setCurrent(updated);
      await reload();
    } catch (e) {
      toast.err("通过审核失败", errMsg(e));
    } finally {
      setApproving(null);
    }
  }

  // quarantined 放行入口上浮(确认+调用逻辑与 GenResultCard 共用 chapters/releaseGate):
  // StageBar blocked 主动作与章节轨行内[放行]都走这里,完成后刷新列表与打开的正文
  const [releasing, setReleasing] = useState<number | null>(null);
  async function releaseChapter(n: number) {
    setReleasing(n);
    try {
      const released = await confirmAndReleaseGate({ pid, n, run });
      if (released) {
        await reload();
        qc.invalidateQueries({ queryKey: qk.chapter(pid, n) });
      }
    } finally {
      setReleasing(null);
    }
  }

  // 重写意见交接(localStorage revise-draft-{pid}):挂载时消费,打开中栏 act=revise 卡并预填
  useEffect(() => {
    const raw = localStorage.getItem(`revise-draft-${pid}`);
    if (!raw) return;
    localStorage.removeItem(`revise-draft-${pid}`);
    try {
      const { num, text } = JSON.parse(raw) as { num: number; text: string };
      if (num && text) {
        setChapterNum(num);
        setReviseDraft(text);
        setAct("revise");
      }
    } catch { /* 损坏的草稿直接丢弃 */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

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
  // (编辑中内容有 confirmDiscardEdit 把守,到这里一定是用户确认过的切换)
  useEffect(() => {
    setErr(""); setGenResult(null); setEditing(false); closeVersions();
    setReviseDraft("");
  }, [chapterNum]);

  // 章节正文拉取失败透传到面板错误区
  useEffect(() => {
    if (chapterQuery.error) setErr(errMsg(chapterQuery.error));
  }, [chapterQuery.error]);

  const byNum = new Map(chapters.map((c) => [c.chapter_number, c]));
  const currentBrief = chapterNum !== null ? byNum.get(chapterNum) : undefined;

  // 章节阶段(状态驱动流水线):StageBar 与移动端 FAB 共用同一份推断(chapterStage.ts)
  const stage = deriveStage({ chapterNum, currentBrief, genJob });
  // approved 阶段的「写下一章」目标:大纲中第一个还没有正文的章(仅选中,不自动生成)
  const nextChapterNum = outlines.find((o) => !byNum.get(o.chapter_number))?.chapter_number ?? null;
  // 生成完成后自动选章要用「当时的」选章状态做守卫,轮询回调里闭包会过期,故走 ref
  const chapterNumRef = useRef(chapterNum);
  useEffect(() => { chapterNumRef.current = chapterNum; }, [chapterNum]);

  // 编辑部预设优化动作(重写意见 chips)
  const [proseActions, setProseActions] = useState<EditorAction[]>([]);
  useEffect(() => {
    api.editorialActions().then((a) => setProseActions(a.prose)).catch(() => undefined);
  }, []);

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
            `先去章节列表通过第 ${paused[1]} 章,再重新排队(或在「设置」关闭「连写要求上一章审核通过」)`);
        }
        await reload().catch(() => undefined);
      }
    } finally { if (!ctrl.signal.aborted) setGenJob(null); }
  }

  const currentOutline = current
    ? outlines.find((o) => o.chapter_number === current.chapter_number) ?? null
    : null;
  // 移动端顶栏用:当前章蓝图(不要求已生成正文,未写的章也能显示标题)
  const activeOutline = chapterNum !== null
    ? outlines.find((o) => o.chapter_number === chapterNum) ?? null
    : null;

  // 编辑中的未保存修改:取消/切章前确认,防手滑丢稿
  const editDirty =
    editing && current !== null &&
    editText !== (current.final_content || current.draft_content);
  async function confirmDiscardEdit(): Promise<boolean> {
    if (!editDirty) return true;
    return confirmDialog({
      title: "修改未保存,切换将丢弃?",
      body: "当前编辑中的正文有未保存修改,继续将丢弃这些修改。",
      confirmText: "丢弃修改",
      danger: true,
    });
  }

  // 打开某章 = 把 ch 写进 URL;视图态清理由上面的 ch 变化 effect 统一做
  async function open(n: number) {
    if (n === chapterNum) return;
    if (!(await confirmDiscardEdit())) return;
    setChapterNum(n);
  }

  // 移动端章节轨抽屉:选章/阅读后顺手关掉抽屉
  async function openFromRail(n: number) {
    await open(n);
    setRailOpen(false);
  }
  function openReaderFromRail(n: number) {
    setRailOpen(false);
    void openReader(n);
  }

  // 移动端 FAB:点按=当前阶段(deriveStage,与 StageBar 共用)的主动作——
  // 未选章→开章节轨;待生成→生成本章;被拦截→开审核 sheet;待审核→通过审核(带确认);
  // 已定稿→选中下一章未写的章(仅选中不自动生成);长按(≥0.5s,仅已有正文时)直接展开动作扇
  function fabTouchStart() {
    fabLongRef.current = false;
    fabTimerRef.current = window.setTimeout(() => {
      fabLongRef.current = true;
      if (currentBrief) setFanOpen(true);
    }, 500);
  }
  function fabTouchEnd() {
    if (fabTimerRef.current !== null) {
      window.clearTimeout(fabTimerRef.current);
      fabTimerRef.current = null;
    }
  }
  function fabTap() {
    if (fabLongRef.current) { fabLongRef.current = false; return; } // 长按已处理
    // 任务锁:不再静默无效,给明确反馈(坏味道:FAB 静默无效 → toast)。
    // approved 的「写下一章」只是选中下一章(不发起生成),不在拦截之列
    if (genBlocked && (stage === "empty" || stage === "review")) {
      toast.err("正在生成其他章节", genHint);
      return;
    }
    switch (stage) {
      case "unselected": setRailOpen(true); return;
      case "empty": if (chapterNum !== null) void generate(chapterNum); return;
      case "blocked": setCtx("review"); return; // ctx= 会触发参考 sheet 打开审核报告
      case "review": if (chapterNum !== null) void approveFromFab(chapterNum); return;
      case "approved":
        if (nextChapterNum !== null) { void open(nextChapterNum); return; }
        setFanOpen(true); return; // 全部写完:FAB 退化为动作扇入口
      default: return; // generating:进度已有横幅/阶段条展示,点按不做事
    }
  }
  // FAB 的「通过审核」带一次确认(移动端误触代价高,与 StageBar 主动作语义一致)
  async function approveFromFab(n: number) {
    const ok = await confirmDialog({
      title: `通过第 ${n} 章审核?`,
      body: "确认本章可定稿,通过后状态变为「已审」。",
      confirmText: "通过审核",
    });
    if (ok) await approve(n);
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

  async function saveEdit() {
    if (!current) return;
    const num = current.chapter_number;
    setSaving(true); setErr("");
    try {
      // 只保存正文(快)。是否同步一致性引擎交给用户定夺,小幅修改可直接跳过。
      const updated = await api.editChapterContent(pid, num, editText);
      setCurrent(updated);
      setEditing(false);
      await reload();
      // 桌面多窗口:广播给对照阅读窗(ReadPanel listen 后失效该章缓存);浏览器 no-op
      void emitChapterSaved(pid, num);
      setPendingSync(num);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setSaving(false); }
  }

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
    setErr(""); setGenResult(null); setReviseFor(null);
    if (revision) setAct(null); // 从重写卡发起:收起卡片,结果走生成结果卡
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

  // 评分卡「按此重写」:就地打开重写卡并预填(不再跳步/走 localStorage)
  function openReviseWith(text: string) {
    setReviseDraft(text);
    setAct("revise");
  }

  const genBlocked = !!genJob;
  const genHint = genJob
    ? (genJob.num === 0
      // num=0 是切走重连的连写队列标记,没有具体章号,不给「第 0 章」
      ? "连写任务进行中,完成后可继续操作"
      : genJob.num === chapterNum ? "本章任务进行中" : `第 ${genJob.num} 章任务进行中,完成后可继续操作`)
    : "";

  // ---- 统一动作 dispatch(D 阶段):快捷键/命令面板/Tauri 菜单共用的章级 handler ----
  // 对象每次渲染重建以捕获最新状态;经 ref 交给「挂载时注册一次」的稳定 wrapper,避免闭包过期。
  const actionHandlers: Partial<Record<AppAction, () => void>> = {
    save: () => { if (editing && !saving && !genBlocked && current) void saveEdit(); },
    generate: () => {
      if (chapterNum !== null && !currentBrief && !genBlocked) void generate(chapterNum);
    },
    revise: () => { if (current && !genBlocked) openReviseWith(reviseDraft); },
    polish: () => { if (current) setAct("polish"); },
    proofread: () => { if (current) setAct("proofread"); },
    review: () => { if (current) setAct("review"); },
    versions: () => { if (chapterNum !== null) void openVersions(chapterNum); },
    queue: () => { setQueueMode((v) => !v); setQueuePicked(new Set()); },
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
    "toggle-rail": () => setRailHidden((v) => !v),
    "toggle-ref": () => { if (searchParams.get("ctx")) clearCtx(); else setCtx("blueprint"); },
    immersive: () => setImmersive((v) => !v),
  };
  const actionHandlersRef = useRef(actionHandlers);
  useEffect(() => { actionHandlersRef.current = actionHandlers; });
  useEffect(() => {
    const offs = (Object.keys(actionHandlersRef.current) as AppAction[]).map((name) =>
      registerActionHandler(name, () => actionHandlersRef.current[name]?.()));
    return () => offs.forEach((off) => off());
  }, []);

  // 沉浸模式:Esc 退出(F11/动作「immersive」开合;快捷键监听在 ProjectPage)
  useEffect(() => {
    if (!immersive) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setImmersive(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [immersive]);

  // 动作卡(act= 进 URL):桌面在中栏内联,移动端换全屏 sheet 容器(组件同一份)
  const actCards = (
    <>
      {act === "revise" && current && (
        <ReviseCard
          pid={pid}
          chapter={current}
          text={reviseDraft}
          proseActions={proseActions}
          genBlocked={genBlocked}
          genHint={genHint}
          onTextChange={setReviseDraft}
          onSubmit={() => generate(current.chapter_number, reviseDraft.trim())}
          onClose={() => setAct(null)}
        />
      )}
      {act === "polish" && current && chapterNum !== null && (
        <PolishPanel pid={pid} chapterNum={chapterNum} />
      )}
      {act === "proofread" && current && chapterNum !== null && (
        <ProofreadCard pid={pid} chapterNum={chapterNum} />
      )}
      {act === "review" && current && chapterNum !== null && (
        <ReviewCard pid={pid} chapterNum={chapterNum} onRevise={openReviseWith} />
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

      {/* ---- 移动端顶栏:←返回项目列表 + 当前章(点击开章节轨抽屉)+ 阅读入口 ---- */}
      {isMobile && (
        <div className="m-topbar">
          <button type="button" className="m-topbar-btn" title="返回项目列表"
            onClick={() => nav("/")}>←</button>
          <button type="button" className="m-topbar-title" title="打开章节轨选章"
            onClick={() => setRailOpen(true)}>
            {chapterNum !== null
              ? `第${chapterNum}章${activeOutline?.title ? ` · ${activeOutline.title}` : ""}`
              : "选择章节"}
          </button>
          <button type="button" className="m-topbar-btn" disabled={!currentBrief}
            title={currentBrief ? "阅读本章(全屏阅读器,可选段润色)" : "先在章节轨选一章已生成的章节"}
            onClick={() => chapterNum !== null && void openReader(chapterNum)}>
            阅读
          </button>
        </div>
      )}

      <div className="write-bench">
        {/* ---- 左栏:章节轨(唯一选章入口;移动端改为全屏抽屉,见 write-zone 末尾;
             桌面端 Ctrl+B 开合,immersive 时由 CSS 隐藏) ---- */}
        {!isMobile && !railHidden && (
          <div className="write-rail">
            <ChapterRail
              pid={pid}
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
              reviseFor={reviseFor}
              reviseText={reviseText}
              proseActions={proseActions}
              onToggleRevise={(n) => {
                setReviseFor(reviseFor === n ? null : n);
                setReviseText("");
              }}
              onReviseTextChange={setReviseText}
              onReviseSubmit={(n) => generate(n, reviseText.trim())}
              onReviseCancel={() => setReviseFor(null)}
              approving={approving}
              onApprove={approve}
              releasing={releasing}
              onRelease={releaseChapter}
              onOpen={open}
              onOpenReader={openReader}
              onGenerate={(n) => generate(n)}
            />
          </div>
        )}

        {/* ---- 中栏:阶段条(顶部)+ 当前章正文 + 蓝图 + 动作产出卡(移动端支持左右滑切章) ---- */}
        <div className="write-main" onTouchStart={onMainTouchStart} onTouchEnd={onMainTouchEnd}>
          {/* 阶段条:状态驱动流水线的唯一动作入口,选章/空态/生成中/拦截/待审/定稿全覆盖 */}
          <StageBar
            stage={stage}
            isMobile={isMobile}
            stale={!!currentBrief?.is_stale}
            genStage={genJob?.stage ?? ""}
            genBlocked={genBlocked}
            genHint={genHint}
            actBusy={approving !== null || releasing !== null}
            hasCurrent={!!current}
            nextNum={nextChapterNum}
            onOpenRail={() => setRailOpen(true)}
            onGenerate={() => { if (chapterNum !== null) void generate(chapterNum); }}
            onApprove={() => { if (chapterNum !== null) void approve(chapterNum); }}
            onRelease={() => { if (chapterNum !== null) void releaseChapter(chapterNum); }}
            onOpenReview={() => setCtx("review")}
            onNextChapter={() => { if (nextChapterNum !== null) void open(nextChapterNum); }}
            onAct={(a) => {
              if (a === "revise") openReviseWith(reviseDraft);
              else setAct(a);
            }}
            onVersions={() => { if (chapterNum !== null) void openVersions(chapterNum); }}
            onOpenSettings={() => nav(`/project/${pid}/settings`)}
          />

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
                // 修订/放行后同步刷新右侧打开的正文(失效缓存,共享 qk.chapter 自动重拉)
                qc.invalidateQueries({ queryKey: qk.chapter(pid, genResult.chapter_number) });
              }}
              onRewrite={() => {
                // 「去重写本章」:切到该章并打开重写卡
                setChapterNum(genResult.chapter_number);
                setReviseDraft("");
                setAct("revise");
              }}
            />
          )}

          {/* 阶段条「更多动作」打开的动作卡(act= 进 URL,可刷新/分享);移动端为全屏 sheet */}
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

          {current ? (
            <>
              {currentOutline && (
                <div className="card card-info">
                  <b>本章蓝图</b> 第{currentOutline.chapter_number}章《{currentOutline.title}》
                  <span className="badge">{currentOutline.chapter_role}</span>
                  <div className="muted mt-1">{currentOutline.summary}</div>
                  <div className="meta-line">
                    伏笔:{currentOutline.foreshadowing || "无"}
                  </div>
                </div>
              )}
              <div className="card">
                <div className="content-head mb-2">
                  <div className="content-head-title">
                    <h2>第{current.chapter_number}章</h2>
                    <span className="content-head-meta">正文 · {current.word_count}字</span>
                  </div>
                  <div className="content-head-actions">
                    {!editing ? (
                      <>
                        <button className="btn-sm" onClick={() => {
                          setEditText(current.final_content || current.draft_content);
                          setEditing(true);
                        }}>编辑正文</button>
                        <button className="btn-sm" disabled={!!genJob}
                          onClick={() => openVersions(current.chapter_number)}>历史版本</button>
                        <button className="btn-sm" title="在右侧参考抽屉打开审核报告"
                          onClick={() => setCtx("review")}>审核报告</button>
                      </>
                    ) : (
                      <>
                        <button className="btn-sm primary" disabled={!!genJob || saving}
                          title={genJob ? `第 ${genJob.num} 章生成中,完成后可保存` : undefined}
                          onClick={saveEdit}>
                          {saving && <span className="spin spin-sm" />}
                          保存正文
                        </button>
                        <button className="btn-sm" onClick={async () => {
                          if (await confirmDiscardEdit()) setEditing(false);
                        }}>取消</button>
                      </>
                    )}
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
                {!editing && <div className="content-head-tip">改文笔?点上方阶段条的「更多动作 → 润色」</div>}
                {editing ? (
                  <textarea
                    className="editor-area"
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                  />
                ) : (
                  <div className="prose">
                    <Paragraphs text={current.final_content || current.draft_content} />
                  </div>
                )}
              </div>
            </>
          ) : isMobile ? (
            /* 移动端无 ch:「选章」引导(不把章节列表塞在正文前面让人滚) */
            <div className="card muted m-pick-guide">
              <div>还没有打开任何章节。</div>
              <button className="primary btn-sm" onClick={() => setRailOpen(true)}>选择章节</button>
            </div>
          ) : (
            <div className="card muted">
              左侧点「生成」写新章,点「阅读」全屏读正文,点章节标题看蓝图/改正文。生成时自动注入:
              本章蓝图、前情摘要、最近章节结尾、人物当前状态(硬约束)、到期伏笔提醒、重复用词避免清单。
            </div>
          )}
        </div>

        {/* ---- 右栏:参考抽屉(默认折叠为图标条;移动端改为全屏 sheet,见 write-zone 末尾) ---- */}
        {!isMobile && (
          <RefDrawer
            pid={pid}
            outlines={outlines}
            chapterNum={chapterNum}
            current={current}
            currentOutline={currentOutline}
            genBlocked={genBlocked}
            genHint={genHint}
            onChanged={() => reload()}
            onRewrite={() => {
              if (chapterNum !== null) openReviseWith("");
            }}
            onOpenVersions={() => { if (chapterNum !== null) void openVersions(chapterNum); }}
          />
        )}
      </div>

      {/* 底部动作条已删除:章级动作由中栏顶部 StageBar(阶段主动作 + 更多动作下拉)承接,
          连写入口保留在左栏 ChapterRail 头部,本书设置入口在 StageBar 右端 ⚙︎ */}

      {/* ---- 移动端:章节轨全屏抽屉(选章/阅读后自动关闭;连写队列也在这里进) ---- */}
      {isMobile && railOpen && (
        <div className="m-overlay">
          <div className="m-rail-drawer">
            <div className="m-sheet-head">
              <div className="grow" />
              <button className="btn-sm" onClick={() => setRailOpen(false)}>关闭</button>
            </div>
            <ChapterRail
              pid={pid}
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
              reviseFor={reviseFor}
              reviseText={reviseText}
              proseActions={proseActions}
              onToggleRevise={(n) => {
                setReviseFor(reviseFor === n ? null : n);
                setReviseText("");
              }}
              onReviseTextChange={setReviseText}
              onReviseSubmit={(n) => generate(n, reviseText.trim())}
              onReviseCancel={() => setReviseFor(null)}
              approving={approving}
              onApprove={approve}
              releasing={releasing}
              onRelease={releaseChapter}
              onOpen={openFromRail}
              onOpenReader={openReaderFromRail}
              onGenerate={(n) => generate(n)}
            />
          </div>
        </div>
      )}

      {/* ---- 移动端:参考全屏 sheet(底部「参考」或任何 ctx= 入口打开,内容组件同一份) ---- */}
      {isMobile && refSheetVisible && (
        <div className="m-sheet-overlay">
          <div className="m-sheet">
            <div className="m-sheet-head">
              <h3 className="grow">参考</h3>
              <button className="btn-sm" onClick={closeRefSheet}>关闭</button>
            </div>
            <RefDrawer
              mobile
              pid={pid}
              outlines={outlines}
              chapterNum={chapterNum}
              current={current}
              currentOutline={currentOutline}
              genBlocked={genBlocked}
              genHint={genHint}
              onChanged={() => reload()}
              onRewrite={() => {
                closeRefSheet();
                if (chapterNum !== null) openReviseWith("");
              }}
              onOpenVersions={() => {
                // 版本对比在中栏(正文后面),先关 sheet 才能看见
                closeRefSheet();
                if (chapterNum !== null) void openVersions(chapterNum);
              }}
            />
          </div>
        </div>
      )}

      {/* ---- 移动端:FAB(点按=最该做的动作,长按=动作扇)+ ⋮ 展开动作扇 ---- */}
      {isMobile && (
        <>
          <button type="button" className="m-fab"
            disabled={stage === "generating"}
            title={stage === "unselected" ? "选择章节"
              : stage === "empty" ? (genBlocked ? genHint : "生成本章")
              : stage === "blocked" ? "去处理门禁拦截"
              : stage === "review" ? "通过审核"
              : stage === "approved" ? (nextChapterNum !== null ? `写下一章(第${nextChapterNum}章)` : "章级动作")
              : "生成中…"}
            onTouchStart={fabTouchStart}
            onTouchEnd={fabTouchEnd}
            onClick={fabTap}>
            {stage === "unselected" ? "选章"
              : stage === "empty" ? "生成"
              : stage === "generating" ? "…"
              : stage === "blocked" ? "处理"
              : stage === "review" ? "通过"
              : nextChapterNum !== null ? "下一章" : "✍"}
          </button>
          <button type="button" className="m-fab-more" title="更多动作"
            onClick={() => setFanOpen(true)}>⋮</button>
          {fanOpen && (
            <>
              <div className="m-fan-backdrop" onClick={() => setFanOpen(false)} />
              <div className="m-fan">
                <button type="button" disabled={!current || genBlocked}
                  title={!current ? "先在章节轨选一章已生成的章节" : genBlocked ? genHint : undefined}
                  onClick={() => { setFanOpen(false); openReviseWith(reviseDraft); }}>
                  重写
                </button>
                <button type="button" disabled={!current || genBlocked}
                  title={!current ? "先在章节轨选一章已生成的章节" : genBlocked ? genHint : undefined}
                  onClick={() => { setFanOpen(false); setAct("polish"); }}>
                  润色
                </button>
                <button type="button" disabled={!current || genBlocked}
                  title={!current ? "先在章节轨选一章已生成的章节" : genBlocked ? genHint : undefined}
                  onClick={() => { setFanOpen(false); setAct("proofread"); }}>
                  校对
                </button>
                <button type="button" disabled={!current || genBlocked}
                  title={!current ? "先在章节轨选一章已生成的章节" : genBlocked ? genHint : undefined}
                  onClick={() => { setFanOpen(false); setAct("review"); }}>
                  评分
                </button>
              </div>
            </>
          )}
        </>
      )}

      {/* ---- 移动端底部栏:写 / 读 / 参考 / 本书设置(吸底,safe-area 垫高) ---- */}
      {isMobile && (
        <nav className="m-bottombar">
          <button type="button" className={"m-bb-item" + (!refSheetVisible ? " on" : "")}
            onClick={() => {
              if (refSheetVisible) closeRefSheet();
              if (chapterNum === null) setRailOpen(true);
            }}>
            <span className="m-bb-glyph">✍</span>写
          </button>
          <button type="button" className="m-bb-item" disabled={!currentBrief}
            title={currentBrief ? "阅读本章(全屏阅读器,可选段润色)" : "先在章节轨选一章已生成的章节"}
            onClick={() => chapterNum !== null && void openReader(chapterNum)}>
            <span className="m-bb-glyph">📖</span>读
          </button>
          <button type="button" className={"m-bb-item" + (refSheetVisible ? " on" : "")}
            onClick={() => (refSheetVisible ? closeRefSheet() : setRefSheetOpen(true))}>
            <span className="m-bb-glyph">🗂</span>参考
          </button>
          <button type="button" className="m-bb-item" title="本书设置:字数守卫 / 审校把关 / 世界观硬规则"
            onClick={() => nav(`/project/${pid}/settings`)}>
            <span className="m-bb-glyph">⚙︎</span>本书设置
          </button>
        </nav>
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
