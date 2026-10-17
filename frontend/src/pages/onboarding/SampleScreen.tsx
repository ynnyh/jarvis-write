// SampleScreen — 样稿屏(docs/30 / docs/32 屏 3):先读三章,再决定整本。
//
// 为什么要有这一屏:旧链最刺眼的体验问题是「铺 150 章蓝图干等 7.4 分钟,而这发生在
// 读到任何正文之前」(docs/29 传国玉玺案例)。现在顺序反过来——确认① 之后后台先跑
// 样稿链(概念→梗卡→核心种子→前三章蓝图→前三章正文,分钟级),作者先读到能判断
// 对错的正文,再点确认②。确认② 之后 full-chain 才去铺其余三层架构 + 骨架 + 全书蓝图。
//
// 四个出口(对应四种「不对」):
//   读着对味,继续写   → full-chain(后台) → 进工作台
//   方向不对,回去改   → 回理解屏(改卡/换方向,重确认后重出样稿)
//   开场不对,重出样稿 → sample-chain restart=blueprint 带一句话重跑(概念/种子不动)
//   第 N 章不对        → 单章带话重写(复用既有 chapter revision 通道,不新造端点)
//
// 本组件自管数据与轮询(仿 SkeletonWall):刷新/回访时从 running-jobs 找回正在跑的
// 样稿链任务接着看,而不是重新起一条。
import { useEffect, useRef, useState } from "react";
import {
  api, ChapterBrief, ChapterDetail, Outline, Project,
} from "../../api";
import { errMsg, pollJob } from "../../pollJob";
import Banner from "../../ui/Banner";
import Reader from "../../components/Reader";
import StepBar, { Step } from "../../ui/StepBar";
import { toast } from "../../ui/Toaster";
import { useJobLive } from "../../ui/useJobLive";

// 样稿链的五步(与后端 chain.py 的 report() 文案对齐)。
// 判据按顺序匹配:先「蓝图」再「章」——「铺前三章蓝图」同时含「章」字。
const SAMPLE_STEPS: { key: string; label: string; todo: string; match: RegExp }[] = [
  { key: "concept", label: "概念", todo: "把确认过的理解卡与方向卡深化成结构化概念",
    match: /概念/ },
  { key: "premise", label: "梗卡", todo: "提炼全书的高概念/兑现机制/边界禁忌",
    match: /梗卡/ },
  { key: "seed", label: "核心种子", todo: "定全书的一句话基因(谁、在什么困境、赌什么)",
    match: /种子/ },
  { key: "blueprint", label: "前三章蓝图", todo: "铺前三章的章节蓝图(不惊动全书卷纲)",
    match: /蓝图/ },
  { key: "prose", label: "前三章正文", todo: "逐章写正文(草稿→定稿→门禁→章后链路)",
    match: /章/ },
];

const SAMPLE_N = 3;   // 与后端 chain._SAMPLE_CHAPTERS 一致

/** 当前阶段文案 → 走到第几步(找不到就返回 -1) */
function stageIndex(stage: string): number {
  if (!stage) return -1;
  return SAMPLE_STEPS.findIndex((s) => s.match.test(stage));
}

interface Props {
  pid: number;
  /** 确认① 返回的样稿 job(刚跳过来时带;刷新回访时为空,从 running-jobs 找) */
  initialJobId?: string | null;
  onBackToIntent: () => void;
  onEnterWorkbench: () => void;
}

export default function SampleScreen({ pid, initialJobId, onBackToIntent, onEnterWorkbench }: Props) {
  const [project, setProject] = useState<Project | null>(null);
  const [outlines, setOutlines] = useState<Outline[]>([]);
  const [briefs, setBriefs] = useState<ChapterBrief[]>([]);
  const [jobId, setJobId] = useState<string | null>(initialJobId ?? null);
  const [stage, setStage] = useState("");
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState("");
  // 全链(整本铺开)进度:点了「继续写」之后后台跑,这一屏与工作台都能看见
  const [fullStage, setFullStage] = useState("");
  const [fullRunning, setFullRunning] = useState(false);
  // 阅读器
  const [readerNum, setReaderNum] = useState<number | null>(null);
  const [chapter, setChapter] = useState<ChapterDetail | null>(null);
  const [chapterLoading, setChapterLoading] = useState(false);
  // 单章带话重写 / 重出样稿
  const [rewriteNum, setRewriteNum] = useState<number | null>(null);
  const [rewriteText, setRewriteText] = useState("");
  const [restartText, setRestartText] = useState("");
  const [busy, setBusy] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const bootRef = useRef<number | null>(null);
  const live = useJobLive(jobId, running);

  useEffect(() => () => abortRef.current?.abort(), []);

  // ---------- 进屏:续接在跑的样稿链,读回已有章节 ----------
  useEffect(() => {
    if (bootRef.current === pid) return;
    bootRef.current = pid;
    (async () => {
      try {
        setProject(await api.getProject(pid));
      } catch { /* 项目态拿不到不拦阅读 */ }
      await refreshContent();
      let jid = initialJobId ?? null;
      if (!jid) {
        const r = await api.runningJobs(pid)
          .catch(() => ({ jobs: [] as { job_id: string; kind: string; stage: string }[] }));
        jid = r.jobs.find((j) => j.kind.startsWith("sample-chain-"))?.job_id ?? null;
        const full = r.jobs.find((j) => j.kind.startsWith("full-chain-"));
        if (full) { setFullRunning(true); void watchFull(full.job_id); }
      }
      if (jid) { setJobId(jid); void watchSample(jid); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  async function refreshContent() {
    try {
      const [outs, bs] = await Promise.all([api.listOutlines(pid), api.listChapters(pid)]);
      setOutlines(outs);
      setBriefs(bs);
    } catch { /* 还没铺到蓝图:保持空态 */ }
  }

  /** 轮询样稿链任务到结束(切走只中止本地等待,任务本身继续在后台跑) */
  async function watchSample(jid: string) {
    setRunning(true); setErr(""); setStage("");
    try {
      await pollJob(jid, { onStage: setStage, intervalMs: 3000 });
      setStage("");
      toast.ok("样稿写好了", "先读前三章,再决定整本怎么写");
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setRunning(false);
      await refreshContent();
    }
  }

  /** 全链任务只做进度可见(不阻塞跳转):这一屏与全局任务中心都看得到 */
  async function watchFull(jid: string) {
    setFullRunning(true); setFullStage("");
    try {
      await pollJob(jid, { onStage: setFullStage, intervalMs: 4000 });
    } catch { /* 全链失败在工作台的任务中心看,这里不抢戏 */ }
    finally { setFullRunning(false); setFullStage(""); }
  }

  // ---------- 阅读 ----------
  async function openReader(n: number) {
    setReaderNum(n); setChapterLoading(true);
    try {
      setChapter(await api.getChapter(pid, n));
    } catch (e) { toast.err("读取失败", errMsg(e)); }
    finally { setChapterLoading(false); }
  }

  /** 有正文的样稿章号(升序) */
  const writtenNums = briefs
    .filter((b) => b.chapter_number <= SAMPLE_N && b.word_count > 0)
    .map((b) => b.chapter_number)
    .sort((a, b) => a - b);
  const readerNums = writtenNums.length ? writtenNums : Array.from({ length: SAMPLE_N }, (_, i) => i + 1);
  const readerIdx = readerNum === null ? -1 : readerNums.indexOf(readerNum);

  // ---------- 四个出口 ----------
  /** 出口 1:读着对味 → full-chain(后台) → 进工作台 */
  async function continueWriting() {
    setBusy("full");
    try {
      // 全链在后台跑:进度在工作台的任务中心与实时正文窗可见,这里不阻塞跳转
      await api.fullChain(pid);
      toast.ok("开始铺整本", "其余架构层、骨架与全书蓝图在后台跑,任务中心能看到进度");
      onEnterWorkbench();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy("");
    }
  }

  /** 出口 3:开场不对 → 重出蓝图与正文(概念/种子不动),可带一句话 */
  async function restartSample() {
    setBusy("restart"); setErr("");
    try {
      const { job_id } = await api.sampleChain(pid, {
        restart: "blueprint", directive: restartText.trim() || undefined,
      });
      setRestartText("");
      setJobId(job_id);
      void watchSample(job_id);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy("");
    }
  }

  /** 出口 4:第 N 章不对 → 单章带话重写(既有通道:generate-async 的 revision) */
  async function rewriteChapter(n: number) {
    setBusy(`ch${n}`); setErr("");
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const { job_id } = await api.generateChapterAsync(
        pid, n, project?.global_tendency ?? {}, rewriteText.trim());
      await pollJob(job_id, { signal: ctrl.signal, onStage: (s) => setStage(s), intervalMs: 3000 });
      toast.ok(`第 ${n} 章已重写`, "读一遍,不对还能再带话");
      setRewriteNum(null); setRewriteText(""); setStage("");
      await refreshContent();
      if (readerNum === n) setChapter(await api.getChapter(pid, n));
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
      setBusy("");
    }
  }

  // 进度:阶段文案与已有产物取较靠后的那个(链跑完但还没刷新时也亮对勾)
  const contentIdx = writtenNums.length ? 4 : outlines.length ? 3 : -1;
  const curIdx = Math.max(stageIndex(stage), contentIdx);
  const steps: Step[] = SAMPLE_STEPS.map((s, i) => ({
    key: s.key, label: s.label, done: i < curIdx, todo: s.todo,
  }));

  const titleOf = (n: number) => outlines.find((o) => o.chapter_number === n)?.title ?? "";
  const summaryOf = (n: number) => outlines.find((o) => o.chapter_number === n)?.summary ?? "";

  return (
    <>
      <div className="card">
        <h2>先写三章给你看</h2>
        <div className="card-desc">
          理解卡确认过了,AI 正在后台把这本书的**开头**写出来:概念 → 梗卡 → 核心种子 →
          前三章蓝图 → 前三章正文,分钟级。读完再决定整本怎么写——不对就回上一步改,
          改完再确认一次,重出的还是这三章。
        </div>

        {running && <div className="mt-2"><Banner stage={stage} text="正在写样稿…" /></div>}
        {fullRunning && (
          <div className="mt-2"><Banner stage={fullStage} text="整本铺开中(其余架构层 → 骨架 → 全书蓝图)…" /></div>
        )}

        <div className="mt-3">
          <StepBar steps={steps} anchorPrefix="sample"
            allDone={<>样稿链已跑完——读一读下面三章,再决定整本怎么走。</>} />
        </div>

        {/* 五步行:既是进度,也是 StepBar 的锚点落点(点步骤条滚到这里) */}
        <div className="sample-steps">
          {SAMPLE_STEPS.map((s, i) => {
            const st = i < curIdx ? "done" : i === curIdx ? "run" : "wait";
            return (
              <div key={s.key} id={`sample-${s.key}`} className={"sample-step " + st}>
                <span className="sample-step-dot">
                  {st === "done" ? "✓" : st === "run" ? <span className="spin spin-sm" /> : i + 1}
                </span>
                <span className="sample-step-label">{s.label}</span>
                {st === "run" && stage && <span className="hint">{stage}</span>}
              </div>
            );
          })}
        </div>

        {/* 实时正文:模型正在写的字逐字滚出来(与全局实时窗同一套 SSE) */}
        {live.text && (
          <pre className="sample-live" data-testid="sample-live">{live.text}</pre>
        )}
        {live.retries > 0 && (
          <div className="hint mt-1">上游重试 {live.retries} 次{live.lastErr ? `:${live.lastErr}` : ""}</div>
        )}
        {err && <div className="msg-err mt-2">{err}</div>}
      </div>

      <div className="card">
        <div className="card-head">
          <h3>前三章正文</h3>
          <span className="grow" />
          {writtenNums.length > 0 && (
            <button className="btn-sm primary" onClick={() => void openReader(writtenNums[0])}>
              读样稿(第 {writtenNums[0]}-{writtenNums[writtenNums.length - 1]} 章)
            </button>
          )}
        </div>

        {writtenNums.length === 0 && !running && (
          <div className="muted mt-2">
            还没有正文。样稿链在跑的话这里会自己长出来;要是没在跑,点下面的「重出样稿」。
          </div>
        )}

        {Array.from({ length: SAMPLE_N }, (_, i) => i + 1).map((n) => {
          const brief = briefs.find((b) => b.chapter_number === n);
          const has = !!brief && brief.word_count > 0;
          return (
            <div className={"sample-chapter" + (has ? "" : " empty")} key={n}>
              <div className="sample-chapter-head">
                <b>第 {n} 章{titleOf(n) ? ` · ${titleOf(n)}` : ""}</b>
                {has && <span className="muted">{brief!.word_count} 字</span>}
                {!has && <span className="muted">还没写</span>}
                <span className="grow" />
                {has && (
                  <button className="btn-sm" onClick={() => void openReader(n)}>读这一章</button>
                )}
                <button className="btn-sm" disabled={!!busy}
                  onClick={() => {
                    setRewriteNum(rewriteNum === n ? null : n);
                    setRewriteText("");
                  }}>
                  {rewriteNum === n ? "收起" : `✎ 第 ${n} 章不对`}
                </button>
              </div>
              {summaryOf(n) && <p className="hint">{summaryOf(n)}</p>}
              {rewriteNum === n && (
                <div className="input-row mt-2">
                  <input type="text" value={rewriteText} maxLength={300} disabled={!!busy}
                    placeholder="这章哪里不对?说一句(如:开场太慢,直接从火场开始)"
                    onChange={(e) => setRewriteText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && rewriteText.trim()) void rewriteChapter(n);
                    }} />
                  <button className="btn-sm primary" disabled={!!busy || !rewriteText.trim()}
                    onClick={() => void rewriteChapter(n)}>
                    {busy === `ch${n}` ? "重写中…" : "按这句重写这一章"}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 确认②:主按钮置底常驻——四出口不藏在滚动尾部(移动端尤其) */}
      <div className="sample-exits">
        <div className="input-row">
          <input type="text" value={restartText} maxLength={500} disabled={!!busy || running}
            placeholder="开场不对?说一句想要的开场(可留空),重出样稿"
            onChange={(e) => setRestartText(e.target.value)} />
          <button className="btn-sm" disabled={!!busy || running} onClick={() => void restartSample()}>
            {busy === "restart" ? "重出中…" : "↻ 开场不对,重出样稿"}
          </button>
        </div>
        <div className="form-actions">
          <button className="primary" disabled={!!busy || running} onClick={() => void continueWriting()}>
            {busy === "full" ? "正在起全链…" : "读着对味,继续写整本 →"}
          </button>
          <button disabled={!!busy} onClick={onBackToIntent}>方向不对,回去改</button>
          <span className="grow" />
          <button className="btn-sm" disabled={!!busy} onClick={onEnterWorkbench}>先进工作台看看</button>
        </div>
        {running && <div className="form-actions-tip">样稿还在写——写完再确认更稳,急着走也可以先进工作台。</div>}
      </div>

      {readerNum !== null && (
        <Reader
          projectId={pid}
          loading={chapterLoading}
          chapter={chapter}
          title={titleOf(readerNum)}
          hasPrev={readerIdx > 0}
          hasNext={readerIdx >= 0 && readerIdx < readerNums.length - 1}
          onPrev={() => readerIdx > 0 && void openReader(readerNums[readerIdx - 1])}
          onNext={() => readerIdx >= 0 && readerIdx < readerNums.length - 1
            && void openReader(readerNums[readerIdx + 1])}
          onClose={() => { setReaderNum(null); setChapter(null); }}
          toc={{
            items: readerNums.map((n) => ({ num: n, label: titleOf(n) || "样稿" })),
            current: readerNum,
            onSelect: (n) => void openReader(n),
            bookTitle: project?.title,
            synopsis: project?.synopsis ?? null,
          }} />
      )}
    </>
  );
}
