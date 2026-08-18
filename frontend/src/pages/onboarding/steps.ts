// 起步流的步骤定义:枚举 / 顺序 / 中文标签 / 服务端 setup_state 映射 / 路由解析。
// 拆自 OnboardingFlow.tsx —— 纯常量与纯函数,零行为变化。
export type SetupStep =
  | "idea" | "concept" | "genre" | "tone" | "title" | "scale" | "confirm" | "launch";

export const STEP_ORDER: SetupStep[] = [
  "idea", "concept", "genre", "tone", "title", "scale", "confirm", "launch",
];

export const STEP_LABEL: Record<SetupStep, string> = {
  idea: "想法", concept: "概念", genre: "题材", tone: "倾向",
  title: "书名", scale: "篇幅", confirm: "确认", launch: "点火",
};

// setup_state(服务端字符串字段,直接扩展取值):launch 屏记为 generating,语义更准
export const SETUP_STATE: Record<SetupStep, string> = {
  idea: "idea", concept: "concept", genre: "genre", tone: "tone",
  title: "title", scale: "scale", confirm: "confirm", launch: "generating",
};

// 路由 step → 屏;兼容历史取值(generating = 流水线屏)
export function parseStep(p?: string): SetupStep {
  if (p === "generating") return "launch";
  return (STEP_ORDER as string[]).includes(p ?? "") ? (p as SetupStep) : "idea";
}
