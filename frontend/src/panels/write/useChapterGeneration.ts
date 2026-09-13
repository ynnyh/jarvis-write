// 章节生成中枢(「正文即界面」write 区):按蓝图生成、带意见重写、多章连写队列。
// 阻塞式任务——顶部横幅 + 锁章级动作(genBlocked/genHint 由壳按 chapterNum 派生,不在此)。
// 生成/重写/连写共用一个 abortRef(卸载中止轮询);连写队列另有 queueMode/queuePicked 选章态。
// trackGenerate=轮询到落地(发起与「切走再回来重连」共用);reconnectGenerate 供挂载重连遗留生成任务。
// 回退/自动选章/重写弹版本对比等联动经 deps 注入(hook 不反向依赖父级),genTendency 为生成配置随此内聚。
// 从 WritePanel 状态中枢抽出的自区 hook(拆分技术债,让壳回归编排+布局)。
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, ChapterBrief, ChapterDetail, GenerateChapterResponse, GenerateQueueResult, Outline, Tendency,
} from "../../api";
import { pollJob, errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { chapterEstimateMin, recordGenDuration } from "./genDuration";
import { confirmPeakPricing, ackPeakPricing, peakPricingNotice } from "../../peakPricing";

// running-jobs 单项(api.runningJobs 的 jobs 元素,无导出类型名,此处按结构声明)
type RunningJob = { job_id: string; kind: string; stage: string };

interface Deps {
  setErr: (msg: string) => void;
  setCurrent: (c: ChapterDetail) => void;
  reload: () => Promise<void>;
  setChapterNum: (n: number) => void;
  chapterNum: number | null; // 生成后自动选章的守卫读它(hook 内建 ref 防闭包过期)
  openVersions: (n: number, auto?: boolean) => Promise<boolean>;
  clearAct: () => void; // 带意见重生成时收起动作卡(壳传 () => setAct(null),hook 不依赖 Act 类型)
}

export function useChapterGeneration(
  pid: number,
  outlines: Outline[],
  chapters: ChapterBrief[],
  deps: Deps,
) {
  const { setErr, setCurrent, reload, setChapterNum, chapterNum, openVersions, clearAct } = deps;
  // 进行中的「生成/重写」任务:阻塞式(顶部横幅 + 锁住章级动作)。
  const [genJob, setGenJob] = useState<{ num: number; stage: string } | null>(null);
  const [genResult, setGenResult] = useState<GenerateChapterResponse | null>(null);
  // 本次生成实耗(秒,P1 成本透明:结果卡展示"用时 X 分钟");null=无当次数据
  const [genDurSec, setGenDurSec] = useState<number | null>(null);
  const [genTendency, setGenTendency] = useState<Tendency>({});
  // 连写队列:勾选多章 → 后端一个 job 串行生成(状态在此持有,目录抽屉头部跟随切换)
  const [queueMode, setQueueMode] = useState(false);
  const [queuePicked, setQueuePicked] = useState<Set<number>>(new Set());
  // 连写中断后的待续跑章号(402 欠费/门禁拦截/严格模式暂停):非空时壳层挂「一键续跑」,
  // 用户不必手动重选剩余区间(402 场景:充值后点一下就接着写)
  const [queueResume, setQueueResume] = useState<number[] | null>(null);
  // 连写停止原因(P1-B):与 remaining 同源落 localStorage,切走再回来恢复条还能说清「为什么停」
  const [queueResumeReason, setQueueResumeReason] = useState<string | null>(null);
  // 生成/重写任务卸载时中止轮询,防止卸载后继续 setState(生成/重写/连写共用一个)
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => { abortRef.current?.abort(); }, []);
  // 生成完成后自动选章要用「当时的」选章状态做守卫,轮询回调里闭包会过期,故走 ref
  const chapterNumRef = useRef(chapterNum);
  useEffect(() => { chapterNumRef.current = chapterNum; }, [chapterNum]);

  // 轮询生成任务直至完成并落地结果(发起生成与「切走再回来重连」共用)
  const trackGenerate = useCallback(async (n: number, jobId: string, ctrl: AbortController) => {
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
      // 重写完成:若有旧版快照,自动弹「旧版 vs 新版」对比供选择,并提示"旧版都留着",
      // 直接回应用户"重写后第一版还在吗"的担忧(首次生成无旧版 openVersions 返回 false,不提示)
      const opened = await openVersions(n, true);
      if (opened) {
        toast.ok(`第 ${n} 章新版已生成`,
          "旧版都留着——不满意可在正文顶部「历史版本对比」里回退到任意一版(含最初稿)");
      }
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
  }, [setErr, setCurrent, reload, setChapterNum, openVersions]);

  const generate = useCallback(async (n: number, revision = "") => {
    // 官方 DeepSeek 峰时(计费 ×2)弹一次确认;非峰时/中转站/会话内已确认则直通
    if (!(await confirmPeakPricing(1))) return;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setErr(""); setGenResult(null); setGenDurSec(null);
    if (revision) clearAct(); // 带着意见重生成:收起动作卡,结果走生成结果卡
    setGenJob({ num: n, stage: "排队中…" });
    let jobId: string;
    try {
      ({ job_id: jobId } = await api.generateChapterAsync(pid, n, genTendency, revision));
    } catch (e) {
      setErr(errMsg(e));
      setGenJob(null);
      return;
    }
    // 实耗记账(P1):完成(非中断)才入账,异常值由 recordGenDuration 护栏过滤
    const t0 = Date.now();
    await trackGenerate(n, jobId, ctrl);
    if (!ctrl.signal.aborted) {
      const sec = Math.round((Date.now() - t0) / 1000);
      setGenDurSec(sec);
      recordGenDuration(pid, sec);
    }
  }, [pid, genTendency, clearAct, setErr, trackGenerate]);

  // 连写发起前的确认弹窗(成本预估 + 峰时提示合一个框,不连弹);开始与续跑共用
  const confirmQueueStart = useCallback(async (nums: number[]) => {
    const per = chapterEstimateMin(pid);
    const total = per * nums.length;
    const peak = await peakPricingNotice(nums.length);
    const ok = await confirmDialog({
      title: `开始连写 ${nums.length} 章?`,
      body: (peak ? `${peak}\n\n` : "")
        + `按本书最近的生成速度,约需 ${total} 分钟(每章约 ${per} 分钟)。`
        + "期间可以离开页面,回来后随时查看进度;中途被门禁拦截或中断时会自动暂停,可一键续跑。",
      confirmText: "开始连写",
    });
    if (ok && peak) ackPeakPricing(); // 用户已看过峰时成本并确认,本会话不再重复弹
    return ok;
  }, [pid]);

  // 连写执行体(开始与续跑共用):轮询到结构化结果,中断时挂出待续跑章号
  const runQueue = useCallback(async (nums: number[]) => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setErr(""); setGenResult(null);
    setGenJob({ num: nums[0], stage: `队列 ${nums.length} 章:排队中…` });
    setQueueMode(false); setQueuePicked(new Set());
    setQueueResume(null);
    const t0 = Date.now();
    try {
      const { job_id } = await api.generateQueue(pid, nums, genTendency);
      const r = await pollJob<GenerateQueueResult>(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setGenJob({ num: nums[0], stage }),
      });
      if (ctrl.signal.aborted) return;
      await reload();
      if (r.error) {
        // 结构化中断:已完成章已各自落库,剩余章挂「一键续跑」(remaining 含失败章本尊,重跑即续;
        // 门禁拦截不含该章——它要先去写作页处理)。402 欠费场景:充值后点一下就接着写。
        setErr(r.error);
        setQueueResume(r.remaining || []);
        setQueueResumeReason(r.error || null);
        try { localStorage.setItem(`queue-paused:${pid}`, JSON.stringify({ remaining: r.remaining || [], reason: r.error || "" })); } catch { /* 隐私模式忽略 */ }
        if (!r.quarantined) {
          toast.err("连写队列中断",
            `已完成 ${r.completed?.length ?? 0}/${r.total} 章,进度已保存。`
            + (r.remaining?.length ? `点「从第 ${r.remaining[0]} 章继续」接着写。` : ""));
        }
        return;
      }
      // 总耗时按章数均摊入账(连写里单章无独立计时,均值口径够用)
      recordGenDuration(pid, (Date.now() - t0) / 1000 / nums.length);
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const msg = errMsg(e);
        setErr(msg);
        // 兜底:旧版后端/轮询层抛错时按消息文本引导(结构化中断走不到这里)
        const paused = /第\s*(\d+)\s*章尚未人工审核通过/.exec(msg);
        if (paused) {
          toast.err("连写队列已暂停",
            `先去目录选中第 ${paused[1]} 章并通过审核,再重新排队(或在「设置」关闭「连写要求上一章审核通过」)`);
        }
        await reload().catch(() => undefined);
      }
    } finally { if (!ctrl.signal.aborted) setGenJob(null); }
  }, [pid, genTendency, reload, setErr]);

  const startQueue = useCallback(async () => {
    const nums = [...queuePicked].sort((a, b) => a - b);
    if (!nums.length) return;
    if (!(await confirmQueueStart(nums))) return;
    await runQueue(nums);
  }, [queuePicked, confirmQueueStart, runQueue]);

  // 一键续跑:从上次中断处接着写(章号由后端 remaining 给出,不需要用户重选)
  const resumeQueue = useCallback(async () => {
    if (!queueResume?.length) return;
    if (!(await confirmQueueStart(queueResume))) return;
    await runQueue(queueResume);
  }, [queueResume, confirmQueueStart, runQueue]);

  const dismissQueueResume = useCallback(() => {
    setQueueResume(null);
    setQueueResumeReason(null);
    try { localStorage.removeItem(`queue-paused:${pid}`); } catch { /* 隐私模式忽略 */ }
  }, [pid]);
  // 挂载恢复:切走再回来,恢复条(连同停止原因)仍在
  useEffect(() => {
    try {
      const raw = localStorage.getItem(`queue-paused:${pid}`);
      if (!raw) return;
      const saved = JSON.parse(raw) as { remaining?: number[]; reason?: string };
      if (saved.remaining?.length) {
        setQueueResume(saved.remaining);
        setQueueResumeReason(saved.reason || null);
      }
    } catch { /* 脏数据忽略 */ }
  }, [pid]);

  const pickNextBatch = useCallback(() => {
    const written = new Set(chapters.map((c) => c.chapter_number));
    const unwritten = outlines
      .filter((o) => !written.has(o.chapter_number))
      .map((o) => o.chapter_number)
      .slice(0, 5);
    setQueuePicked(new Set(unwritten));
  }, [outlines, chapters]);

  // 挂载重连:接上遗留的生成/连写任务(切走再回来的场景)。连写(尾巴 queue)走通用轮询+刷新列表,
  // 单章生成/重写复用 trackGenerate。生成任务是阻塞式,重连即恢复顶部横幅。
  const reconnectGenerate = useCallback((jobs: RunningJob[]) => {
    const gen = jobs.find((j) => j.kind.startsWith(`chapter-${pid}-`));
    if (!gen) return;
    const tail = gen.kind.split("-").pop()!;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    if (tail === "queue") {
      setGenJob({ num: 0, stage: gen.stage });
      pollJob<GenerateQueueResult>(gen.job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setGenJob({ num: 0, stage }),
      }).then((r) => {
        if (r?.error) {
          // 重连后才发现队列已中断:同样挂出一键续跑,不让用户对着一句报错手动重选
          setErr(r.error);
          setQueueResume(r.remaining || []);
          setQueueResumeReason(r.error || null);
          try { localStorage.setItem(`queue-paused:${pid}`, JSON.stringify({ remaining: r.remaining || [], reason: r.error || "" })); } catch { /* 隐私模式忽略 */ }
        }
      }).catch(() => undefined)
        .finally(() => reload().catch(() => undefined))
        .finally(() => { if (!ctrl.signal.aborted) setGenJob(null); });
    } else {
      const n = Number(tail);
      setGenJob({ num: n, stage: gen.stage });
      trackGenerate(n, gen.job_id, ctrl);
    }
  }, [pid, reload, trackGenerate]);

  return {
    genJob, genResult, setGenResult, genDurSec,
    genTendency, setGenTendency,
    queueMode, setQueueMode, queuePicked, setQueuePicked,
    queueResume, queueResumeReason, resumeQueue, dismissQueueResume,
    generate, startQueue, pickNextBatch, reconnectGenerate,
  };
}
