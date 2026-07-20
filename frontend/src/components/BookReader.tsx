// 全书阅读:从 ProjectPage「阅读全书」进入,目录 + 连续翻章,复用 Reader 的视觉与偏好。
// 目录列出全部大纲章节,未生成正文的置灰;翻页只在已生成章节间进行。
// 按项目记住阅读位置(localStorage reader-pos-{pid}):打开时恢复到上次的章节与滚动位置,
// 章节已不存在则从第一章有正文的开始。
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ChapterBrief, ChapterDetail, Outline, Project } from "../api";
import Reader, { ReaderTocItem } from "./Reader";

interface Props {
  pid: number;
  project: Project;
  outlines: Outline[];
  chapters: ChapterBrief[];
  onClose: () => void;
}

/** 阅读位置记忆:与 reader-prefs 同风格,读写失败静默忽略 */
interface ReaderPos { chapter: number; scroll: number; }

function loadPos(key: string): ReaderPos | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const pos = JSON.parse(raw);
    if (typeof pos?.chapter !== "number" || typeof pos?.scroll !== "number") return null;
    return pos;
  } catch {
    return null;
  }
}

function savePos(key: string, pos: ReaderPos) {
  try { localStorage.setItem(key, JSON.stringify(pos)); } catch { /* ignore */ }
}

export default function BookReader({ pid, project, outlines, chapters, onClose }: Props) {
  const [chapter, setChapter] = useState<ChapterDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  // 打开时要恢复的滚动位置(仅首章生效,Reader 内部只应用一次)
  const [restoreScroll, setRestoreScroll] = useState<number | null>(null);
  const posKey = `reader-pos-${pid}`;

  // 已生成正文的章节号(翻页范围),按章号排序
  const genNums = useMemo(
    () => chapters.map((c) => c.chapter_number).sort((a, b) => a - b),
    [chapters],
  );
  const genSet = useMemo(() => new Set(genNums), [genNums]);

  const open = useCallback(async (n: number, scroll = 0) => {
    savePos(posKey, { chapter: n, scroll });
    setLoading(true); setErr("");
    try { setChapter(await api.getChapter(pid, n)); }
    catch (e) { setErr(String(e)); }
    finally { setLoading(false); }
  }, [pid, posKey]);

  // 进入即恢复上次的阅读位置;记忆的章节不存在(被删/未生成)则从第一章有正文的开始
  useEffect(() => {
    if (!genNums.length) return;
    const saved = loadPos(posKey);
    if (saved && genSet.has(saved.chapter)) {
      setRestoreScroll(saved.scroll);
      open(saved.chapter, saved.scroll);
    } else {
      open(genNums[0]);
    }
  }, [genNums, genSet, open, posKey]);

  // Reader 滚动防抖后上报:记住当前章 + 滚动位置
  const handleScrollPos = useCallback((n: number, scroll: number) => {
    savePos(posKey, { chapter: n, scroll });
  }, [posKey]);

  const idx = chapter ? genNums.indexOf(chapter.chapter_number) : -1;
  const prevNum = idx > 0 ? genNums[idx - 1] : null;
  const nextNum = idx >= 0 && idx < genNums.length - 1 ? genNums[idx + 1] : null;

  // 目录:全部大纲章节,未生成的置灰;无大纲时兜底只列已生成章节
  const tocItems: ReaderTocItem[] = outlines.length
    ? outlines.map((o) => ({
        num: o.chapter_number, label: o.title, disabled: !genSet.has(o.chapter_number),
      }))
    : genNums.map((n) => ({ num: n, label: "" }));

  return (
    <>
      <Reader
        loading={loading}
        chapter={chapter}
        title={chapter
          ? outlines.find((o) => o.chapter_number === chapter.chapter_number)?.title
          : undefined}
        hasPrev={prevNum != null}
        hasNext={nextNum != null}
        onPrev={() => prevNum != null && open(prevNum)}
        onNext={() => nextNum != null && open(nextNum)}
        onClose={onClose}
        toc={{
          items: tocItems,
          current: chapter?.chapter_number ?? null,
          onSelect: open,
          bookTitle: project.title,
          synopsis: project.synopsis,
        }}
        restoreScroll={restoreScroll}
        onScrollPos={handleScrollPos}
        polishCtx={{
          pid,
          chapterNumber: chapter?.chapter_number ?? 0,
          onApplied: (updated) => setChapter(updated),
        }}
      />
      {err && <div className="msg-err book-err">{err}</div>}
    </>
  );
}
