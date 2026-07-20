// 全书阅读:从 ProjectPage「阅读全书」进入,目录 + 连续翻章,复用 Reader 的视觉与偏好。
// 目录列出全部大纲章节,未生成正文的置灰;翻页只在已生成章节间进行;默认从第一章有正文的开始。
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ChapterBrief, ChapterDetail, Outline } from "../api";
import Reader, { ReaderTocItem } from "./Reader";

interface Props {
  pid: number;
  outlines: Outline[];
  chapters: ChapterBrief[];
  onClose: () => void;
}

export default function BookReader({ pid, outlines, chapters, onClose }: Props) {
  const [chapter, setChapter] = useState<ChapterDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  // 已生成正文的章节号(翻页范围),按章号排序
  const genNums = useMemo(
    () => chapters.map((c) => c.chapter_number).sort((a, b) => a - b),
    [chapters],
  );
  const genSet = useMemo(() => new Set(genNums), [genNums]);

  const open = useCallback(async (n: number) => {
    setLoading(true); setErr("");
    try { setChapter(await api.getChapter(pid, n)); }
    catch (e) { setErr(String(e)); }
    finally { setLoading(false); }
  }, [pid]);

  // 进入即打开第一章有正文的章节
  useEffect(() => { if (genNums.length) open(genNums[0]); }, [genNums, open]);

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
        toc={{ items: tocItems, current: chapter?.chapter_number ?? null, onSelect: open }}
      />
      {err && <div className="msg-err book-err">{err}</div>}
    </>
  );
}
