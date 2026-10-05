// ThinkingText:"AI 构思中"轮换微文案。
// 生成等待期替代静态 loading,给用户"AI 在为我想"的体感;reduced-motion 时退化为静态文本。
import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

// 等待超过该时长仍未返回 → 追加一条慢模型提示。用户接了推理类/小众慢模型时,
// 长时间只有轮转文案没有任何解释,等超时了也不知道是慢还是挂了(2026-10-05
// 月哥实测:书生卡 ~11 tok/s,三问要一分多钟)。25s 取值:快模型几乎都在
// 30s 内返回(不会误伤),慢模型此时已明显偏慢。
const SLOW_HINT_AFTER_MS = 25_000;

export function ThinkingText({ phrases, interval = 2400, className = "" }: {
  phrases: string[];   // 轮换文案,单条时不轮转
  interval?: number;   // 轮换间隔 ms
  className?: string;
}) {
  const [i, setI] = useState(0);
  const [slow, setSlow] = useState(false);
  const reduce = useReducedMotion();
  useEffect(() => {
    if (phrases.length <= 1) return;
    const t = setInterval(() => setI((v) => (v + 1) % phrases.length), interval);
    return () => clearInterval(t);
  }, [phrases.length, interval]);
  // 慢等待提示:挂载计时一次,卸载清理。刻意不把 phrases 放依赖——多处调用方
  // 传的是内联字面量(每次渲染新引用),进依赖会让计时反复重置,慢提示永不出现。
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), SLOW_HINT_AFTER_MS);
    return () => clearTimeout(t);
  }, []);
  if (!phrases.length) return null;
  const text = phrases[i % phrases.length];
  if (reduce) {
    return (
      <span className={className}>
        {text}
        {slow && <SlowHint />}
      </span>
    );
  }
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
      {slow && <SlowHint />}
    </span>
  );
}

function SlowHint() {
  return (
    <span className="muted" style={{ display: "block", fontSize: "0.85em", marginTop: 6 }}>
      模型回话比平时慢——推理类或小众模型可能要几分钟,请耐心等待;
      若总是很慢,可在「模型设置」里换更快的模型,或把该配置的超时/max_tokens 调大。
    </span>
  );
}
