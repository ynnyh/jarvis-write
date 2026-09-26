// 起步流的静态预设与流水线步骤类型。拆自 OnboardingFlow.tsx。

// 篇幅预设卡
export const SCALE_PRESETS = [
  { key: "short", label: "短篇", chapters: 20, words: 3000, desc: "约 6 万字,适合练手或中短故事" },
  { key: "mid", label: "中篇", chapters: 60, words: 3000, desc: "约 18 万字,完整起承转合" },
  { key: "long", label: "长篇", chapters: 150, words: 3000, desc: "约 45 万字,单卷完整大故事" },
  { key: "serial", label: "连载", chapters: 500, words: 3000, desc: "百万字级连载体量;按卷滚动规划,写到卷尾自动展开下一卷,章数随时可调" },
];

// 篇幅显示(本书档案/总检墙/步骤条缩略共用):按章数×字数反推档位。
// 建库默认 30×3000 不匹配任何预设——那是「还没人选过」,不是「定了 30 章」,
// 如实显示未定,配置屏 AI 推荐档应用或用户点选后预设值落库才会命中档位。
export interface ScaleDisplay { tag: string; text: string; decided: boolean; }
export function scaleDisplay(chapters: number, words: number): ScaleDisplay {
  const total = chapters * words;
  const wan = total >= 10000 ? `≈ ${Math.round(total / 10000)} 万字` : `≈ ${total} 字`;
  const preset = SCALE_PRESETS.find((p) => p.chapters === chapters && p.words === words);
  if (preset) {
    return preset.key === "serial"
      ? { tag: preset.label, text: `连载 · ${chapters} 章,按卷滚动规划`, decided: true }
      : { tag: preset.label, text: `${preset.label} · ${chapters} 章 × ${words} 字 ${wan}`, decided: true };
  }
  if (chapters === 30 && words === 3000) {
    return { tag: "未定", text: "未定 · 配置屏 AI 按概念推荐", decided: false };
  }
  return { tag: "自定", text: `自定 · ${chapters} 章 × ${words} 字 ${wan}`, decided: true };
}

// "AI 构思中"轮换微文案
export const THINK_CONCEPT = [
  "正在揣摩题材气质…", "正在搭建核心冲突…", "正在给主角找困境…", "正在埋藏反转的种子…",
];
export const THINK_TITLE = [
  "正在咀嚼故事的味儿…", "正在掂量每个字的分量…", "正在试着念出声来…",
];

export type PipeStatus = "wait" | "run" | "done" | "err";
export interface PipeStep { status: PipeStatus; stage: string; error: string; }
export const PIPE_WAIT: PipeStep = { status: "wait", stage: "", error: "" };
