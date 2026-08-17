// read 区:单章纯阅读页(/project/:id/read?ch=N),给桌面多窗口对照与移动端用。
// 只读展示当前章 + 上一章/下一章(仅限已生成章),无编辑入口;正文走 qk.chapter 共享缓存。
// 桌面多窗口:listen 主窗保存广播(chapter-saved),命中当前章则失效缓存重拉。
import { useEffect } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Outline } from "../api";
import { qk, useChapter, useChapters } from "../hooks/queries";
import { useChapterContext } from "../hooks/useChapterContext";
import { isDesktop, onChapterSaved } from "../desktop";
import { Paragraphs, STATUS_BADGE, STATUS_CN } from "../components/Reader";

interface Props { pid: number; outlines: Outline[]; }

export default function ReadPanel({ pid, outlines }: Props) {
  const { chapterNum, setChapterNum } = useChapterContext();
  const chaptersQuery = useChapters(pid);
  const chapters = chaptersQuery.data ?? [];
  const chapterQuery = useChapter(pid, chapterNum);
  const current = chapterQuery.data ?? null;
  const qc = useQueryClient();

  // 桌面对照阅读窗:主窗保存正文后 emit chapter-saved,命中当前章即 invalidate 重拉。
  // 浏览器环境 isDesktop() 为 false,不挂监听。
  useEffect(() => {
    if (!isDesktop()) return;
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    onChapterSaved((p) => {
      if (p.pid === pid && chapterNum !== null && p.ch === chapterNum) {
        qc.invalidateQueries({ queryKey: qk.chapter(pid, p.ch) });
      }
    })
      .then((u) => { if (!cancelled) unlisten = u; })
      .catch(() => {});
    return () => { cancelled = true; unlisten?.(); };
  }, [pid, chapterNum, qc]);

  // 已生成章号(翻页范围);URL 没带 ch 或 ch 未生成时,落到第一章已生成的
  const genNums = chapters.map((c) => c.chapter_number);
  const valid = chapterNum !== null && genNums.includes(chapterNum);
  useEffect(() => {
    if (!chaptersQuery.data || valid) return;
    if (genNums.length) setChapterNum(genNums[0], { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chaptersQuery.data, valid]);

  const idx = valid ? genNums.indexOf(chapterNum) : -1;
  const prevNum = idx > 0 ? genNums[idx - 1] : null;
  const nextNum = idx >= 0 && idx < genNums.length - 1 ? genNums[idx + 1] : null;
  const outline = chapterNum !== null
    ? outlines.find((o) => o.chapter_number === chapterNum)
    : undefined;

  if (chaptersQuery.data && !genNums.length) {
    return <div className="card muted">还没有已生成的章节。</div>;
  }
  if (!valid) return <div className="muted">加载中…</div>;

  return (
    <div className="read-zone">
      <div className="read-bar">
        <Link to={`/project/${pid}/write?ch=${chapterNum}`} className="linkbtn">← 回写作区</Link>
        <div className="grow" />
        <button className="btn-sm" disabled={prevNum === null}
          onClick={() => prevNum !== null && setChapterNum(prevNum)}>← 上一章</button>
        <button className="btn-sm" disabled={nextNum === null}
          onClick={() => nextNum !== null && setChapterNum(nextNum)}>下一章 →</button>
      </div>
      <div className="card">
        {chapterQuery.isLoading && <div className="muted"><span className="spin" />加载中…</div>}
        {chapterQuery.error && <div className="msg-err">{String(chapterQuery.error)}</div>}
        {current && (
          <>
            <div className="content-head mb-2">
              <div className="content-head-title">
                <h2>第{current.chapter_number}章 {outline?.title ?? ""}</h2>
                <span className="content-head-meta">
                  <span className={"badge " + (current.is_stale ? "err" : STATUS_BADGE[current.status] ?? "")}>
                    {current.is_stale ? "大纲已变" : STATUS_CN[current.status] ?? current.status}
                  </span>
                  {" "}{current.word_count}字
                </span>
              </div>
            </div>
            <div className="prose">
              <Paragraphs text={current.final_content || current.draft_content} />
            </div>
          </>
        )}
      </div>
      <div className="read-bar">
        <button className="btn-sm" disabled={prevNum === null}
          onClick={() => prevNum !== null && setChapterNum(prevNum)}>← 上一章</button>
        <div className="grow" />
        <button className="btn-sm" disabled={nextNum === null}
          onClick={() => nextNum !== null && setChapterNum(nextNum)}>下一章 →</button>
      </div>
    </div>
  );
}
