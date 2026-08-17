// hooks/useChapterContext.ts
// 「当前章」的唯一来源:URL searchParams(ch=37)。
// 写作/编辑部/润色共用同一个 ch 参数——切步骤不丢、刷新不丢、可收藏可分享,
// 取代原先三个面板各自独立的 useState(交互重构阶段 A 地基)。
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

export function useChapterContext() {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get("ch");
  const n = raw ? Number(raw) : NaN;
  // 非法值(非正整数)当作未指定
  const chapterNum = Number.isInteger(n) && n > 0 ? n : null;

  /** 显式选章才写 URL;传 null 清除。默认 replace=false,选章可后退。 */
  const setChapterNum = useCallback(
    (num: number | null, opts?: { replace?: boolean }) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (num === null) next.delete("ch");
          else next.set("ch", String(num));
          return next;
        },
        { replace: opts?.replace ?? false },
      );
    },
    [setSearchParams],
  );

  return { chapterNum, setChapterNum };
}
