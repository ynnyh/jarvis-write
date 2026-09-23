// 起步流的步骤定义:枚举 / 顺序 / 中文标签 / 服务端 setup_state 映射 / 路由解析。
// 拆自 OnboardingFlow.tsx —— 纯常量与纯函数,零行为变化。
//
// 确认链重构(2026-09)后的主线收敛为四步:
//   想法(idea) → 概念(concept:选卡+打磨拍板) → 配置(setup:题材/口味/篇幅/书名/总检) → 点火(launch:架构闸门→骨架墙→铺章)
// 旧八步路由(genre/tone/title/scale/confirm)由 parseStep 兼容映射进 setup,
// setup_state 老值照常识别,存量书回访不断链。
export type SetupStep = "idea" | "concept" | "setup" | "launch";

export const STEP_ORDER: SetupStep[] = ["idea", "concept", "setup", "launch"];

export const STEP_LABEL: Record<SetupStep, string> = {
  idea: "想法", concept: "概念", setup: "配置", launch: "点火",
};

// setup_state(服务端字符串字段,直接扩展取值):launch 屏记为 generating,语义更准
export const SETUP_STATE: Record<SetupStep, string> = {
  idea: "idea", concept: "concept", setup: "setup", launch: "generating",
};

// 旧八步 → 新四步:配置类屏全部落 setup;generating(旧流水线屏)落 launch
const LEGACY_STEP: Record<string, SetupStep> = {
  genre: "setup", tone: "setup", title: "setup", scale: "setup",
  confirm: "setup", generating: "launch",
};

// 路由 step → 屏;兼容历史取值(旧八步与新四步都认)
export function parseStep(p?: string): SetupStep {
  if (LEGACY_STEP[p ?? ""]) return LEGACY_STEP[p ?? ""];
  return (STEP_ORDER as string[]).includes(p ?? "") ? (p as SetupStep) : "idea";
}
