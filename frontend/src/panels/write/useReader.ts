// 全屏阅读器(「正文即界面」write 区):打开某章=拉全文进遮罩层(共用 Reader 组件),
// 上一章/下一章仅在已生成章节间跳。从 WritePanel 状态中枢抽出的自区 hook(拆分技术债)。
// 错误沿用面板统一 err 横幅(传入 setErr),不自持错误区;setReader 暴露给阅读器内润色回写。
import { useCallback, useState } from "react";
import { api, ChapterBrief, ChapterDetail, Outline } from "../../api";
import { errMsg } from "../../pollJob";

export function useReader(
  pid: number,
  chapters: ChapterBrief[],
  outlines: Outline[],
  setErr: (msg: string) => void,
) {
  // 当前阅读章节(null=阅读器关闭);readerLoading=正在拉全文
  const [reader, setReader] = useState<ChapterDetail | null>(null);
  const [readerLoading, setReaderLoading] = useState(false);

  // 打开/翻章都走这里(tab/偏好由 Reader 内部管理)
  const openReader = useCallback(async (n: number) => {
    setReaderLoading(true); setErr("");
    try {
      setReader(await api.getChapter(pid, n));
    } catch (e) { setErr(errMsg(e)); } finally { setReaderLoading(false); }
  }, [pid, setErr]);

  // 上一章/下一章:仅限已生成的章节
  const generatedNums = chapters.map((c) => c.chapter_number);
  const readerIdx = reader ? generatedNums.indexOf(reader.chapter_number) : -1;
  const prevNum = readerIdx > 0 ? generatedNums[readerIdx - 1] : null;
  const nextNum = readerIdx >= 0 && readerIdx < generatedNums.length - 1
    ? generatedNums[readerIdx + 1] : null;
  const readerOutline = reader
    ? outlines.find((o) => o.chapter_number === reader.chapter_number)
    : null;

  return { reader, readerLoading, setReader, openReader, prevNum, nextNum, readerOutline };
}
