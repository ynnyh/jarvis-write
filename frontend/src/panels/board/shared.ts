// 一致性看板四子看板共享的类型与常量(拆自 BoardPanel.tsx)。
import { Outline } from "../../api";

export type BoardTab = "overview" | "characters" | "bible" | "timeline" | "foreshadow" | "motifs";

export interface Props { pid: number; outlines: Outline[]; onGotoChapter?: (n: number) => void; }

const FS_CN: Record<string, string> = {
  planted: "已埋设", reinforced: "已强化", paid_off: "已回收", abandoned: "已弃用",
};
const IMP_BADGE: Record<string, string> = { critical: "err", major: "warn", minor: "" };
const FACT_PREVIEW = 3;

export { FS_CN, IMP_BADGE, FACT_PREVIEW };
