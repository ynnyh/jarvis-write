// 起步流的步骤定义:枚举 / 顺序 / 中文标签 / 服务端 setup_state 映射 / 路由解析。
// 拆自 OnboardingFlow.tsx —— 纯常量与纯函数。
//
// 开书前置重构(docs/30 / docs/32,2026-10-17)后的主线四步:
//   开哪种书(mode)→ 想法(idea:灵感/流派/皮肤/参考方向)
//   → 理解(intent:理解卡一次确认 + 方向卡墙)→ 样稿(sample:前三章试读,二次确认)
// 为什么从六屏砍到四屏:旧链有五道签字(mode/brief/concept/setup/launch),每一道都
// 要作者在**还没读到任何正文**时点头,而铺 150 章蓝图的 7.4 分钟就发生在这中间
// (docs/29 传国玉玺案例)。现在只留两处确认:理解卡(我听到的对不对)与样稿(写出来
// 对不对),产物层(概念/架构/骨架/大纲)退场为「可看可改」,在工作台里随手改。
export type SetupStep = "mode" | "idea" | "intent" | "sample";

export const STEP_ORDER: SetupStep[] = ["mode", "idea", "intent", "sample"];

export const STEP_LABEL: Record<SetupStep, string> = {
  mode: "开书", idea: "想法", intent: "理解", sample: "样稿",
};

// setup_state(服务端字符串字段,直接扩展取值):写库的就是这几步本身
export const SETUP_STATE: Record<SetupStep, string> = {
  mode: "mode", idea: "idea", intent: "intent", sample: "sample",
};

// 旧步骤 → 新四步。存量书回访、列表页「继续创建」、ProjectPage 的 /new/:pid/:step
// 都走 parseStep,这张表保证不断链:
//   concept/setup/launch/generating = 旧链的产物层与点火屏 → 样稿屏
//     (作者已经过了理解这一步,直接看/读样稿;旧书的样稿链按需手动补跑)
//   brief = 旧的方向卡屏 → 理解屏(同一件事:定方向)
//   genre/tone/title/scale/confirm = 更早的八步路由 → 理解屏
//     (它们都是「生成前的配置」,理解屏正是改题材/换方向的地方)
const LEGACY_STEP: Record<string, SetupStep> = {
  concept: "sample", setup: "sample", launch: "sample", generating: "sample",
  brief: "intent",
  genre: "intent", tone: "intent", title: "intent", scale: "intent", confirm: "intent",
};

// 路由 step → 屏;兼容历史取值(旧八步/旧六步都认),未知或空 → 想法屏
export function parseStep(p?: string): SetupStep {
  if (LEGACY_STEP[p ?? ""]) return LEGACY_STEP[p ?? ""];
  return (STEP_ORDER as string[]).includes(p ?? "") ? (p as SetupStep) : "idea";
}
