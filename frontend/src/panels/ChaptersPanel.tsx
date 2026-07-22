// 写作面板:逐章生成 / 阅读;本章蓝图上下文置顶;润色移步「润色」工作区
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, ChapterBrief, ChapterDetail, ChapterVersionBrief, ChapterVersionDetail,
  EditorAction, GenerateChapterResponse, Outline, Project, Tendency,
} from "../api";
import { pollJob } from "../pollJob";
import { useInvalidateProject } from "../hooks/queries";
import { toast } from "../ui/Toaster";
import TendencySelector from "../components/TendencySelector";
import Reader, { Paragraphs } from "../components/Reader";
import GenResultCard from "./chapters/GenResultCard";
import VersionCompare from "./chapters/VersionCompare";
import ChapterListItem from "./chapters/ChapterListItem";

interface Props {
  pid: number; project: Project; outlines: Outline[];
  // 看板「概览」点章节格子跳来:聚焦章号(已生成则打开正文),消费后经回调清空
  focusChapter?: number | null;
  onFocusConsumed?: () => void;
}

export default function ChaptersPanel({ pid, project, outlines, focusChapter, onFocusConsumed }: Props) {
  const invalidateProject = useInvalidateProject(pid);
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [current, setCurrent] = useState<ChapterDetail | null>(null);
  // 进行中的章节任务:生成(kind=generate)或保存后同步一致性引擎(kind=sync)。
  // 只锁「生成/重写」「保存」这类会起新任务的操作;阅读/打开/编辑不锁。
  const [genJob, setGenJob] = useState<{ num: number; kind: "generate" | "sync"; stage: string } | null>(null);
  const [err, setErr] = useState("");
  const [genResult, setGenResult] = useState<GenerateChapterResponse | null>(null);
  const [genTendency, setGenTendency] = useState<Tendency>({});
  const [showTendency, setShowTendency] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  // 行内重写意见输入区:当前展开的章号(null=收起)与意见文本(可留空=直接重写)
  const [reviseFor, setReviseFor] = useState<number | null>(null);
  const [reviseText, setReviseText] = useState("");
  // 阅读器(全屏遮罩,共用组件 Reader):当前阅读章节
  const [reader, setReader] = useState<ChapterDetail | null>(null);
  const [readerLoading, setReaderLoading] = useState(false);
  // 正文版本对比:versionsFor=打开历史的章号,versions=该章快照列表,compareVer=选中对比的旧版全文
  const [versionsFor, setVersionsFor] = useState<number | null>(null);
  const [versions, setVersions] = useState<ChapterVersionBrief[] | null>(null);
  const [compareVer, setCompareVer] = useState<ChapterVersionDetail | null>(null);
  // 组件卸载时中止轮询,防止卸载后继续 setState
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const reload = useCallback(async () => {
    setChapters(await api.listChapters(pid));
  }, [pid]);
  useEffect(() => { reload().catch((e) => setErr(String(e))); }, [reload]);

  // 字数守卫开关:超标自动压缩/拆章。一个开关同时管压缩与拆章,默认关闭。
  async function toggleGuard(on: boolean) {
    try {
      await api.patchProject(pid, { word_guard_enabled: on, auto_split_enabled: on });
      await invalidateProject();
      toast.ok(on ? "已开启字数守卫" : "已关闭字数守卫",
        on ? "章节超出目标字数较多时会自动压缩或拆章" : "字数只做宽松参考,不再自动压缩/拆章");
    } catch (e) { toast.err("开关保存失败", String(e)); }
  }

  // 编辑部「按此重写」交接:挂载时消费预填的重写意见,展开对应章的重写框
  useEffect(() => {
    const raw = localStorage.getItem(`revise-draft-${pid}`);
    if (!raw) return;
    localStorage.removeItem(`revise-draft-${pid}`);
    try {
      const { num, text } = JSON.parse(raw) as { num: number; text: string };
      if (num && text) { setReviseFor(num); setReviseText(text); }
    } catch { /* 损坏的草稿直接丢弃 */ }
  }, [pid]);

  // 挂载时查有没有还在跑的章节任务(切走页面再回来的场景),有则接上轮询而不是装作没事
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
          setGenJob({ num: 0, kind: "generate", stage: gen.stage });
          pollJob(gen.job_id, {
            signal: ctrl.signal,
            onStage: (stage) => setGenJob({ num: 0, kind: "generate", stage }),
          }).then(() => reload())
            .catch(() => reload().catch(() => undefined))
            .finally(() => { if (!ctrl.signal.aborted) setGenJob(null); });
        } else {
          const n = Number(tail);
          setGenJob({ num: n, kind: "generate", stage: gen.stage });
          trackGenerate(n, gen.job_id, ctrl);
        }
        return;
      }
      const sync = jobs.find((j) => j.kind.startsWith(`re-extract-${pid}-`));
      if (sync) {
        const n = Number(sync.kind.split("-").pop());
        const ctrl = new AbortController();
        abortRef.current = ctrl;
        setGenJob({ num: n, kind: "sync", stage: sync.stage });
        pollJob(sync.job_id, {
          signal: ctrl.signal,
          onStage: (stage) => setGenJob({ num: n, kind: "sync", stage }),
        }).catch(() => undefined)
          .finally(() => { if (!ctrl.signal.aborted) setGenJob(null); });
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  // 看板「概览」跳章:章节列表就绪后打开该章正文;未生成的章只落到写作列表
  useEffect(() => {
    if (focusChapter == null || !chapters.length) return;
    if (chapters.some((c) => c.chapter_number === focusChapter)) {
      setErr(""); setGenResult(null); setEditing(false);
      setVersionsFor(null); setVersions(null); setCompareVer(null);
      api.getChapter(pid, focusChapter)
        .then(setCurrent)
        .catch((e) => setErr(String(e)));
    }
    onFocusConsumed?.();
  }, [focusChapter, chapters, pid, onFocusConsumed]);

  const byNum = new Map(chapters.map((c) => [c.chapter_number, c]));

  // 连写队列:勾选多章 → 后端一个 job 串行生成
  const [queueMode, setQueueMode] = useState(false);
  const [queuePicked, setQueuePicked] = useState<Set<number>>(new Set());
  // 列表筛选(长书用):文本 + 状态
  const [filterText, setFilterText] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  // 编辑部预设优化动作(重写意见 chips)
  const [proseActions, setProseActions] = useState<EditorAction[]>([]);
  useEffect(() => {
    api.editorialActions().then((a) => setProseActions(a.prose)).catch(() => undefined);
  }, []);

  const shownOutlines = outlines.filter((o) => {
    const ch = byNum.get(o.chapter_number);
    if (filterText.trim()) {
      const q = filterText.trim();
      if (!o.title.includes(q) && String(o.chapter_number) !== q.replace(/^第|章$/g, "")) return false;
    }
    if (filterStatus === "unwritten" && ch) return false;
    if (filterStatus === "finalized" && (!ch || ch.is_stale)) return false;
    if (filterStatus === "stale" && !ch?.is_stale) return false;
    return true;
  });

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
    setGenJob({ num: nums[0], kind: "generate", stage: `队列 ${nums.length} 章:排队中…` });
    setQueueMode(false); setQueuePicked(new Set());
    try {
      const { job_id } = await api.generateQueue(pid, nums, genTendency);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setGenJob({ num: nums[0], kind: "generate", stage }),
      });
      if (ctrl.signal.aborted) return;
      await reload();
    } catch (e) {
      if (!ctrl.signal.aborted) {
        setErr(e instanceof Error ? e.message : String(e));
        await reload().catch(() => undefined);
      }
    } finally { if (!ctrl.signal.aborted) setGenJob(null); }
  }

  const currentOutline = current
    ? outlines.find((o) => o.chapter_number === current.chapter_number)
    : null;

  async function open(n: number) {
    setErr(""); setGenResult(null); setEditing(false); closeVersions();
    try { setCurrent(await api.getChapter(pid, n)); } catch (e) { setErr(String(e)); }
  }

  // 阅读器:打开/翻章都走这里(tab/偏好由 Reader 内部管理)
  async function openReader(n: number) {
    setReaderLoading(true); setErr("");
    try {
      setReader(await api.getChapter(pid, n));
    } catch (e) { setErr(String(e)); } finally { setReaderLoading(false); }
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
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setGenJob({ num, kind: "sync", stage: "保存正文…" }); setErr("");
    try {
      const updated = await api.editChapterContent(pid, num, editText);
      setCurrent(updated);
      setEditing(false);
      await reload();
      // 手改后同步一致性引擎:重抽取 + 重建下游摘要 + 向量库
      const { job_id } = await api.reExtractAsync(pid, num);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setGenJob({ num, kind: "sync", stage }),
      });
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const msg = e instanceof Error ? e.message : String(e);
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

  // 轮询生成任务直至完成并落地结果(发起生成与「切走再回来重连」共用)
  async function trackGenerate(n: number, jobId: string, ctrl: AbortController) {
    try {
      // 轮询任务进度(五段:草稿→定稿→检查→抽取→摘要)
      const result = await pollJob<GenerateChapterResponse>(jobId, {
        signal: ctrl.signal,
        onStage: (stage) => setGenJob({ num: n, kind: "generate", stage }),
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
      // 重写完成:若有旧版快照,自动弹「旧版 vs 新版」对比供选择
      await openVersions(n, true);
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const msg = e instanceof Error ? e.message : String(e);
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
    setGenJob({ num: n, kind: "generate", stage: "排队中…" });
    let jobId: string;
    try {
      ({ job_id: jobId } = await api.generateChapterAsync(pid, n, genTendency, revision));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
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
    } catch (e) { setErr(String(e)); }
  }

  async function selectVersion(n: number, v: ChapterVersionBrief) {
    setErr("");
    try { setCompareVer(await api.getChapterVersion(pid, n, v.id)); }
    catch (e) { setErr(String(e)); }
  }

  // 回退到旧版:换回正文 → 同步一致性引擎(重抽取+摘要+向量,同 saveEdit)
  async function restoreVersion(n: number, vid: number) {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setGenJob({ num: n, kind: "sync", stage: "回退正文…" }); setErr("");
    try {
      const updated = await api.restoreChapterVersion(pid, n, vid);
      setCurrent(updated);
      closeVersions();
      await reload();
      const { job_id } = await api.reExtractAsync(pid, n);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setGenJob({ num: n, kind: "sync", stage }),
      });
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const msg = e instanceof Error ? e.message : String(e);
        if (msg.startsWith("任务超时") || msg.startsWith("多次查询")) {
          setErr(`进度查询中断:${msg}`);
          await reload().catch(() => undefined);
        } else {
          setErr(msg);
        }
      }
    } finally { if (!ctrl.signal.aborted) setGenJob(null); }
  }

  return (
    <div className="two-col">
      <div className="two-col-side">
        {genJob && (
          <div className="gen-banner">
            <span className="spin" />
            <span className="gen-banner-text">
              {genJob.kind === "generate"
                ? genJob.num === 0 || genJob.stage.startsWith("[")
                  ? `连写队列进行中(${genJob.stage})`
                  : `第 ${genJob.num} 章生成中(${genJob.stage}),完成后可继续操作其他章节`
                : `第 ${genJob.num} 章保存后同步一致性引擎(${genJob.stage}),完成后可继续其他操作`}
            </span>
          </div>
        )}
        {err && <div className="msg-err">{err}</div>}
        <div className="card card-compact">
          <div className="card-head mb-2">
            <h3 className="grow">章节</h3>
            <button className="btn-sm" onClick={() => { setQueueMode(!queueMode); setQueuePicked(new Set()); }}>
              {queueMode ? "取消连写" : "连写多章"}
            </button>
            <button className="btn-sm" onClick={() => setShowTendency(!showTendency)}>
              {showTendency ? "收起" : "正文倾向"}
            </button>
          </div>
          {showTendency && (
            <div className="mb-3">
              <TendencySelector node="chapter" value={genTendency} onChange={setGenTendency} compact />
            </div>
          )}
          {queueMode && (
            <div className="queue-bar mb-2">
              <span className="hint">勾选要连写的章(按章号顺序串行生成,失败即停)</span>
              <button className="btn-sm" onClick={pickNextBatch}>选未写的前 5 章</button>
              <button className="primary btn-sm" disabled={!queuePicked.size || !!genJob}
                onClick={startQueue}>
                排队生成 {queuePicked.size || ""} 章
              </button>
            </div>
          )}
          {outlines.length > 12 && (
            <div className="input-row mb-2">
              <input type="text" value={filterText} onChange={(e) => setFilterText(e.target.value)}
                placeholder="搜章名/章号…" />
              <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
                <option value="">全部状态</option>
                <option value="unwritten">未写</option>
                <option value="finalized">已定稿</option>
                <option value="stale">大纲已变</option>
              </select>
            </div>
          )}
          {shownOutlines.map((o) => {
            const ch = byNum.get(o.chapter_number);
            const generating = genJob?.num === o.chapter_number;
            const genBlocked = !!genJob;
            const genHint = generating
              ? "本章任务进行中"
              : `第 ${genJob?.num} 章任务进行中,完成后可继续操作`;
            return (
              <ChapterListItem
                key={o.chapter_number}
                outline={o}
                chapter={ch}
                queueMode={queueMode}
                queuePicked={queuePicked.has(o.chapter_number)}
                generating={generating}
                genBlocked={genBlocked}
                genHint={genHint}
                genStage={genJob?.stage ?? ""}
                reviseOpen={reviseFor === o.chapter_number}
                reviseText={reviseText}
                proseActions={proseActions}
                onOpen={() => open(o.chapter_number)}
                onOpenReader={() => openReader(o.chapter_number)}
                onToggleQueue={(checked) => {
                  const next = new Set(queuePicked);
                  if (checked) next.add(o.chapter_number);
                  else next.delete(o.chapter_number);
                  setQueuePicked(next);
                }}
                onToggleRevise={() => {
                  setReviseFor(reviseFor === o.chapter_number ? null : o.chapter_number);
                  setReviseText("");
                }}
                onReviseTextChange={setReviseText}
                onGenerate={() => generate(o.chapter_number)}
                onReviseSubmit={() => generate(o.chapter_number, reviseText.trim())}
                onReviseCancel={() => setReviseFor(null)}
              />
            );
          })}
        </div>
        <div className="card card-compact mt-2">
          <label className="guard-toggle">
            <input type="checkbox" checked={!!project.word_guard_enabled}
              onChange={(e) => toggleGuard(e.target.checked)} />
            <span>
              字数守卫
              <b className="hint">超标自动压缩/拆章,默认关闭</b>
            </span>
          </label>
        </div>
      </div>

      <div className="two-col-main">
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

        {genResult && <GenResultCard result={genResult} />}

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
                    </>
                  ) : (
                    <>
                      <button className="btn-sm primary" disabled={!!genJob}
                        title={genJob ? `第 ${genJob.num} 章任务进行中,完成后可保存` : undefined}
                        onClick={saveEdit}>
                        {genJob?.kind === "sync" && genJob.num === current.chapter_number && <span className="spin" />}
                        保存(自动同步一致性引擎)
                      </button>
                      <button className="btn-sm" onClick={() => setEditing(false)}>取消</button>
                    </>
                  )}
                </div>
              </div>
              {!editing && <div className="content-head-tip">改文笔?去「润色」</div>}
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
        ) : (
          <div className="card muted">
            左侧点「生成」写新章,点「阅读」全屏读正文,点章节标题看蓝图/改正文。生成时自动注入:
            本章蓝图、前情摘要、最近章节结尾、人物当前状态(硬约束)、到期伏笔提醒、重复用词避免清单。
          </div>
        )}
      </div>

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
