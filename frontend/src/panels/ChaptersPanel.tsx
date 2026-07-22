// 写作面板:逐章生成 / 阅读;本章蓝图上下文置顶;润色移步「润色」工作区
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import {
  api, ChapterBrief, ChapterDetail, ChapterVersionBrief, ChapterVersionDetail,
  EditorAction, flavorTitle, GenerateChapterResponse, Outline, Project, Tendency, VERSION_SOURCE_CN,
} from "../api";
import { pollJob } from "../pollJob";
import TendencySelector from "../components/TendencySelector";
import Reader, { Paragraphs, STATUS_CN } from "../components/Reader";

interface Props {
  pid: number; project: Project; outlines: Outline[];
  // 看板「概览」点章节格子跳来:聚焦章号(已生成则打开正文),消费后经回调清空
  focusChapter?: number | null;
  onFocusConsumed?: () => void;
}

export default function ChaptersPanel({ pid, outlines, focusChapter, onFocusConsumed }: Props) {
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
            const st = ch?.status ?? "empty";
            const generating = genJob?.num === o.chapter_number;
            const genBlocked = !!genJob;
            const genHint = generating
              ? "本章任务进行中"
              : `第 ${genJob?.num} 章任务进行中,完成后可继续操作`;
            return (
              <Fragment key={o.chapter_number}>
                <div className="fact-line fact-row">
                  {queueMode && (
                    <input type="checkbox" className="queue-check"
                      checked={queuePicked.has(o.chapter_number)}
                      disabled={!!ch && !ch.is_stale}
                      title={ch && !ch.is_stale ? "已写好的章不用排队" : undefined}
                      onChange={(e) => {
                        const next = new Set(queuePicked);
                        if (e.target.checked) next.add(o.chapter_number);
                        else next.delete(o.chapter_number);
                        setQueuePicked(next);
                      }} />
                  )}
                  <span className={"fact-title" + (ch ? " linkish" : "")}
                    onClick={() => ch && open(o.chapter_number)}>
                    <b>第{o.chapter_number}章</b> {o.title}
                    <span className={"badge " + (ch?.is_stale ? "err" : st === "finalized" ? "ok" : "")}>
                      {ch?.is_stale ? "大纲已变" : STATUS_CN[st] ?? st}
                    </span>
                    {ch && <span className="muted"> {ch.word_count}字</span>}
                    {generating && (
                      <span className="gen-stage"><span className="spin" />{genJob.stage}</span>
                    )}
                  </span>
                  {ch && (
                    <button className="btn-sm" onClick={() => openReader(o.chapter_number)}>
                      阅读
                    </button>
                  )}
                  <button className="btn-sm" disabled={genBlocked} title={genBlocked ? genHint : undefined}
                    onClick={() => {
                      if (ch) {
                        // 重写:先展开行内意见输入区,不直接开跑
                        setReviseFor(reviseFor === o.chapter_number ? null : o.chapter_number);
                        setReviseText("");
                      } else {
                        generate(o.chapter_number);
                      }
                    }}>
                    {ch ? "重写" : "生成"}
                  </button>
                </div>
                {reviseFor === o.chapter_number && (
                  <div className="fact-line revise-box">
                    <textarea
                      rows={3}
                      maxLength={500}
                      placeholder="哪里不满意?比如:节奏太拖 / 对话不像这个角色 / 结尾太仓促;想要什么方向?比如:加强冲突、多些心理描写(可留空,直接重写)"
                      value={reviseText}
                      onChange={(e) => setReviseText(e.target.value)}
                    />
                    <div className="chips">
                      {proseActions.map((a) => (
                        <span key={a.key} className="chip" title={a.directive}
                          onClick={() => setReviseText((t) => ((t ? t.trimEnd() + ";" : "") + a.directive).slice(0, 500))}>
                          {a.label}
                        </span>
                      ))}
                    </div>
                    <div className="revise-actions">
                      <button className="primary btn-sm" disabled={genBlocked}
                        title={genBlocked ? genHint : undefined}
                        onClick={() => generate(o.chapter_number, reviseText.trim())}>
                        开始重写
                      </button>
                      <button className="btn-sm" onClick={() => setReviseFor(null)}>取消</button>
                    </div>
                  </div>
                )}
              </Fragment>
            );
          })}
        </div>
      </div>

      <div className="two-col-main">
        {versionsFor !== null && versions !== null && (
          <div className="card">
            <div className="card-head mb-2">
              <h3 className="grow">第{versionsFor}章 · 历史版本对比</h3>
              <button className="btn-sm" onClick={closeVersions}>关闭</button>
            </div>
            {versions.length === 0 ? (
              <div className="muted">
                暂无历史版本。以后重写 / 润色 / 手改正文时,被覆盖的旧版会自动存到这里,可随时对比回退。
              </div>
            ) : (
              <>
                <div className="muted mb-2">
                  选一个旧版和「当前版」左右对照。满意当前版就关掉;想要旧版点「回退」。
                </div>
                <div className="chips mb-2">
                  {versions.map((v) => (
                    <span key={v.id}
                      className={"chip" + (compareVer?.id === v.id ? " on" : "")}
                      onClick={() => selectVersion(versionsFor, v)}>
                      v{v.version} · {VERSION_SOURCE_CN[v.source] ?? v.source} · {v.word_count}字
                    </span>
                  ))}
                </div>
                {compareVer && current && (
                  <>
                    <div className="split mt-2">
                      <div>
                        <div className="pane-title">
                          旧版 v{compareVer.version}
                          ({VERSION_SOURCE_CN[compareVer.source] ?? compareVer.source} · {compareVer.word_count}字)
                        </div>
                        <div className="pane pane-prose prose">
                          <Paragraphs text={compareVer.final_content} />
                        </div>
                      </div>
                      <div>
                        <div className="pane-title">当前版({current.word_count}字)</div>
                        <div className="pane pane-prose prose">
                          <Paragraphs text={current.final_content || current.draft_content} />
                        </div>
                      </div>
                    </div>
                    <div className="actions mt-3">
                      <button className="primary" disabled={!!genJob}
                        title={genJob ? "有任务进行中,完成后可回退" : undefined}
                        onClick={() => restoreVersion(versionsFor, compareVer.id)}>
                        回退到旧版 v{compareVer.version}(覆盖当前版并同步一致性引擎)
                      </button>
                      <button onClick={closeVersions}>保留当前版</button>
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        )}

        {genResult && (
          <div className="card card-ok">
            <b>生成完成</b> {genResult.word_count} 字
            {genResult.ai_flavor && (
              <span className="badge" title={flavorTitle(genResult.ai_flavor)}>
                AI味 {genResult.ai_flavor.score} /千字
              </span>
            )}
            {genResult.ai_flavor && (
              <span className="muted"> 偏高可去「润色」,选「去AI味」方向</span>
            )}
            {genResult.consistency_issues.length
              ? <div className="mt-2">
                  <span className="badge err">一致性问题 {genResult.consistency_issues.length}</span>
                  {genResult.consistency_issues.map((i, k) => (
                    <div key={k} className="fact-line">
                      <b>[{i.severity}]</b> {i.description}
                      <div className="muted">建议: {i.suggestion}</div>
                    </div>
                  ))}
                </div>
              : <span className="badge ok">一致性检查通过</span>}
            {genResult.word_guard_action === "compressed" && (
              <div className="mt-2">
                <span className="badge">字数守卫:已压缩至目标范围</span>
              </div>
            )}
            {genResult.word_guard_action === "split" && genResult.split_info && (
              <div className="mt-2">
                <span className="badge err">字数守卫:已自动拆章</span>
                <div className="fact-line">
                  原第{genResult.split_info.original_chapter}章 →
                  第{genResult.split_info.original_chapter}章({genResult.split_info.part_a_words}字)
                  + 第{genResult.split_info.new_chapter}章《{genResult.split_info.new_title}》({genResult.split_info.part_b_words}字)
                </div>
                {genResult.split_info.reason && (
                  <div className="muted">断点:{genResult.split_info.reason}</div>
                )}
              </div>
            )}
          </div>
        )}

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
              <div className="card-head mb-2">
                <h2 className="grow">
                  第{current.chapter_number}章 正文
                  <span className="hint"> {current.word_count}字</span>
                </h2>
                {!editing ? (
                  <>
                    <button onClick={() => {
                      setEditText(current.final_content || current.draft_content);
                      setEditing(true);
                    }}>编辑正文</button>
                    <button className="btn-sm" disabled={!!genJob}
                      onClick={() => openVersions(current.chapter_number)}>历史版本</button>
                    <span className="muted">改文笔?去「润色」</span>
                  </>
                ) : (
                  <>
                    <button className="primary" disabled={!!genJob}
                      title={genJob ? `第 ${genJob.num} 章任务进行中,完成后可保存` : undefined}
                      onClick={saveEdit}>
                      {genJob?.kind === "sync" && genJob.num === current.chapter_number && <span className="spin" />}
                      保存(自动同步一致性引擎)
                    </button>
                    <button onClick={() => setEditing(false)}>取消</button>
                  </>
                )}
              </div>
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
