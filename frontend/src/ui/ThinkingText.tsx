// ThinkingText:"AI 构思中"轮换微文案。
// 生成等待期替代静态 loading,给用户"AI 在为我想"的体感;reduced-motion 时退化为静态文本。
import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

export function ThinkingText({ phrases, interval = 2400, className = "" }: {
  phrases: string[];   // 轮换文案,单条时不轮转
  interval?: number;   // 轮换间隔 ms
  className?: string;
}) {
  const [i, setI] = useState(0);
  const reduce = useReducedMotion();
  useEffect(() => {
    if (phrases.length <= 1) return;
    const t = setInterval(() => setI((v) => (v + 1) % phrases.length), interval);
    return () => clearInterval(t);
  }, [phrases.length, interval]);
  if (!phrases.length) return null;
  const text = phrases[i % phrases.length];
  if (reduce) return <span className={className}>{text}</span>;
  return (
    <span className={("wiz-thinking " + className).trim()}>
      <AnimatePresence mode="wait">
        <motion.span
          key={text}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.25 }}
          style={{ display: "inline-block" }}
        >
          {text}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}
