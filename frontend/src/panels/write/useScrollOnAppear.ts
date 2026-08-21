// 结果卡出现即滚动到视野:重写/润色/按批注改/版本对比等异步任务完成后,结果卡插在正文列顶部,
// 而用户此刻常停在章节中部选段/批注——不滚动就看不到,得手动往上翻找(交互重灾区)。
// 当 active 由 假→真(卡片出现)时,把承载卡片的元素平滑滚进视野;只在「出现」这一跳变滚一次,
// 卡片内容后续变化不反复打扰。用法:const ref = useScrollOnAppear(someState); <div ref={ref}>…卡片…</div>
import { useEffect, useRef } from "react";

export function useScrollOnAppear(active: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  const was = useRef(false);
  const now = !!active;
  useEffect(() => {
    if (now && !was.current) {
      ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    was.current = now;
  }, [now]);
  return ref;
}
