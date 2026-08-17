// 章节阶段模型:write 区「状态驱动流水线」的核心(docs/07 交互重构,工具箱 → 流水线)。
// 纯函数,章首状态卡(ChapterStatusCard 三态人话映射)与快捷键动作共用同一份推断,避免分叉。
// (StageBar/FAB 已随「正文即界面」P1 退场,见 docs/10 §8;六阶段推断保留为内部实现。)
// is_stale 不做独立阶段,由消费方作为修饰文案(「大纲已变,建议重写」)叠加。
import type { ChapterBrief } from "../../api";

export type ChapterStage =
  | "unselected"   // 未选章
  | "empty"        // 已选章,未生成
  | "generating"   // 本章生成中(连写队列实跑时仅队列首章;重连的队列见下)
  | "blocked"      // quarantined,门禁拦截待处理
  | "review"       // pending_review,待人工通过
  | "approved";    // 已定稿

export function deriveStage(args: {
  chapterNum: number | null;
  currentBrief: ChapterBrief | undefined; // byNum.get(chapterNum)
  genJob: { num: number; stage: string } | null;
}): ChapterStage {
  const { chapterNum, currentBrief, genJob } = args;
  if (chapterNum === null) return "unselected";
  // 生成中覆盖一切内容状态。num=章号:本章任务在跑;num=0:切走再回来重连上的
  // 连写队列(reconnect 路径无法从 kind 得知具体章号,统一记 0,任意选中章都算
  // generating)。注意 startQueue 实跑时 num 是队列首章号而非 0——只有选中首章才
  // 推断为 generating,其余章显示真实阶段并靠 genBlocked 任务锁禁用动作。
  if (genJob && (genJob.num === chapterNum || genJob.num === 0)) return "generating";
  if (!currentBrief) return "empty";
  switch (currentBrief.status) {
    case "quarantined": return "blocked";
    case "pending_review": return "review";
    // finalized 为存量旧数据(后端已不产生),按已定稿对待
    case "approved":
    case "finalized": return "approved";
    default: return "empty";
  }
}

// 每阶段的呈现契约:阶段名 + 一句引导文案(单测保有全覆盖;
// 主动作因依赖外部状态由消费方按 stage 自行组装,此处只固定文案)。
export const STAGE_SPEC: Record<ChapterStage, { label: string; guide: string }> = {
  unselected: { label: "未选章", guide: "先选一章" },
  empty: { label: "待生成", guide: "这章还没写" },
  generating: { label: "生成中", guide: "生成中,请稍候" },
  blocked: { label: "被拦截", guide: "门禁拦下,处理矛盾或放行" },
  review: { label: "待审核", guide: "审一眼,没问题就通过" },
  approved: { label: "已定稿", guide: "已定稿,继续下一章" },
};
