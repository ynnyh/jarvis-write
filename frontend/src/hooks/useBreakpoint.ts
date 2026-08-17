// hooks/useBreakpoint.ts
// 窄屏(手机)判定:matchMedia 监听,与 styles.css 末尾的 @media (max-width: 767px) 对齐。
// 仅浏览器端使用(无 SSR);断点变化(resize/旋转)即触发重渲染。
import { useEffect, useState } from "react";

// 移动端断点:新增样式集中在 styles.css「移动端壳」一节的同名媒体查询里
export const MOBILE_QUERY = "(max-width: 767px)";

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia(query).matches
      : false,
  );
  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    setMatches(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

export function useBreakpoint() {
  const isMobile = useMediaQuery(MOBILE_QUERY);
  return { isMobile };
}
