// 章节状态的中文映射与徽标配色。阅读器与各处章节列表/命令面板共用。拆自 Reader.tsx。

// 章节状态中文映射。drafting/drafted 已删(后端不再产生,坏味道 #10 死枚举清理);
// finalized 为存量旧数据保留映射(后端已不产生,老书可能还有)
export const STATUS_CN: Record<string, string> = {
  empty: "未生成",
  pending_review: "待审", approved: "已审", quarantined: "被拦截",
  finalized: "已定稿", stale: "大纲已变",
};
// 章节状态徽标配色(badge class):待审 warn / 已审 ok / 被拦截 err(finalized 同为存量旧数据)
export const STATUS_BADGE: Record<string, string> = {
  pending_review: "warn", approved: "ok", quarantined: "err", finalized: "ok",
};
