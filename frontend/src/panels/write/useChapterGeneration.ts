// 章节生成中枢(「正文即界面」write 区):按蓝图生成、带意见重写、多章连写队列。
// 阻塞式任务——顶部横幅 + 锁章级动作(genBlocked/genHint 由壳按 chapterNum 派生,不在此)。
// 生成/重写/连写共用一个 abortRef(卸载中止轮询);连写队列另有 queueMode/queuePicked 选章态。
// trackGenerate=轮询到落地(发起与「切走再回来重连」共用);reconnectGenerate 供挂载重连遗留生成任务。
// 回退/自动选章/重写弹版本对比等联动经 deps 注入(hook 不反向依赖父级),genTendency 为生成配置随此内聚。
// 从 WritePanel 状态中枢抽出的自区 hook(拆分技术债,让壳回归编排+布局)。
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, ChapterBrief, ChapterDetail, GenerateChapterResponse, Outline, Tendency,
} from "../../api";
import { pollJob, errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";

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
  const [genTendency, setGenTendency] = useState<Tendency>({});
  // 连写队列:勾选多章 → 后端一个 job 串行生成(状态在此持有,目录抽屉头部跟随切换)
  const [queueMode, setQueueMode] = useState(false);
  const [queuePicked, setQueuePicked] = useState<Set<number>>(new Set());
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
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setErr(""); setGenResult(null);
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
    await trackGenerate(n, jobId, ctrl);
  }, [pid, genTendency, clearAct, setErr, trackGenerate]);

  const startQueue = useCallback(async () => {
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
  }, [pid, queuePicked, genTendency, reload, setErr]);

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
  }, [pid, reload, trackGenerate]);

  return {
    genJob, genResult, setGenResult,
    genTendency, setGenTendency,
    queueMode, setQueueMode, queuePicked, setQueuePicked,
    generate, startQueue, pickNextBatch, reconnectGenerate,
  };
}
