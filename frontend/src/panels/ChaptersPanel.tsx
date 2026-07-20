// 写作面板:逐章生成 / 阅读;本章蓝图上下文置顶;润色移步「润色」工作区
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api, ChapterBrief, ChapterDetail, GenerateChapterResponse, Outline, Project, Tendency,
} from "../api";
import { pollJob } from "../pollJob";
import TendencySelector from "../components/TendencySelector";
import Reader, { Paragraphs, STATUS_CN } from "../components/Reader";

interface Props { pid: number; project: Project; outlines: Outline[]; }

export default function ChaptersPanel({ pid, outlines }: Props) {
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [current, setCurrent] = useState<ChapterDetail | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [genResult, setGenResult] = useState<GenerateChapterResponse | null>(null);
  const [genTendency, setGenTendency] = useState<Tendency>({});
  const [showTendency, setShowTendency] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  // 阅读器(全屏遮罩,共用组件 Reader):当前阅读章节
  const [reader, setReader] = useState<ChapterDetail | null>(null);
  const [readerLoading, setReaderLoading] = useState(false);
  // 组件卸载时中止轮询,防止卸载后继续 setState
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const reload = useCallback(async () => {
    setChapters(await api.listChapters(pid));
  }, [pid]);
  useEffect(() => { reload().catch((e) => setErr(String(e))); }, [reload]);

  const byNum = new Map(chapters.map((c) => [c.chapter_number, c]));
  const currentOutline = current
    ? outlines.find((o) => o.chapter_number === current.chapter_number)
    : null;

  async function open(n: number) {
    setErr(""); setGenResult(null); setEditing(false);
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
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy("保存正文…"); setErr("");
    try {
      const updated = await api.editChapterContent(pid, current.chapter_number, editText);
      setCurrent(updated);
      setEditing(false);
      await reload();
      // 手改后同步一致性引擎:重抽取 + 重建下游摘要 + 向量库
      const { job_id } = await api.reExtractAsync(pid, current.chapter_number);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`同步一致性引擎:${stage}`),
      });
    } catch (e) {
      if (!ctrl.signal.aborted) setErr(String(e));
    } finally { if (!ctrl.signal.aborted) setBusy(""); }
  }

  async function generate(n: number) {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setErr(""); setGenResult(null);
    setBusy(`第 ${n} 章:排队中…`);
    try {
      const { job_id } = await api.generateChapterAsync(pid, n, genTendency);
      // 轮询任务进度(五段:草稿→定稿→检查→抽取→摘要)
      const result = await pollJob<GenerateChapterResponse>(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`第 ${n} 章:${stage}`),
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
    } catch (e) {
      if (!ctrl.signal.aborted) setErr(String(e));
    } finally { if (!ctrl.signal.aborted) setBusy(""); }
  }

  return (
    <div className="two-col">
      <div className="two-col-side">
        <div className="card card-compact">
          <div className="card-head mb-2">
            <h3 className="grow">章节</h3>
            <button className="btn-sm" onClick={() => setShowTendency(!showTendency)}>
              {showTendency ? "收起" : "正文倾向"}
            </button>
          </div>
          {showTendency && (
            <div className="mb-3">
              <TendencySelector node="chapter" value={genTendency} onChange={setGenTendency} compact />
            </div>
          )}
          {outlines.map((o) => {
            const ch = byNum.get(o.chapter_number);
            const st = ch?.status ?? "empty";
            return (
              <div key={o.chapter_number} className="fact-line fact-row">
                <span className={"fact-title" + (ch ? " linkish" : "")}
                  onClick={() => ch && open(o.chapter_number)}>
                  <b>第{o.chapter_number}章</b> {o.title}
                  <span className={"badge " + (ch?.is_stale ? "err" : st === "finalized" ? "ok" : "")}>
                    {ch?.is_stale ? "大纲已变" : STATUS_CN[st] ?? st}
                  </span>
                  {ch && <span className="muted"> {ch.word_count}字</span>}
                </span>
                {ch && (
                  <button className="btn-sm" disabled={!!busy} onClick={() => openReader(o.chapter_number)}>
                    阅读
                  </button>
                )}
                <button className="btn-sm" disabled={!!busy} onClick={() => generate(o.chapter_number)}>
                  {ch ? "重写" : "生成"}
                </button>
              </div>
            );
          })}
        </div>
        {busy && <div className="card muted"><span className="spin" />{busy}</div>}
        {err && <div className="msg-err">{err}</div>}
      </div>

      <div className="two-col-main">
        {genResult && (
          <div className="card card-ok">
            <b>生成完成</b> {genResult.word_count} 字
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
                    <span className="muted">改文笔?去「润色」</span>
                  </>
                ) : (
                  <>
                    <button className="primary" disabled={!!busy} onClick={saveEdit}>
                      {busy && <span className="spin" />}保存(自动同步一致性引擎)
                    </button>
                    <button disabled={!!busy} onClick={() => setEditing(false)}>取消</button>
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
        />
      )}
    </div>
  );
}
