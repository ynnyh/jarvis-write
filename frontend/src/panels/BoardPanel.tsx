// 一致性看板分发器:按 tab 渲染五个子看板(全书概览/人物卡/故事圣经/伏笔/桥段台账)。
// 五个看板各自独立(自带状态与数据拉取),已拆到 panels/board/;此处只做 tab 分发。
// tab 由 book 区经 props 受控传入(tab 进 URL);人物/伏笔/圣经/桥段看板仅此一处入口
// (write 区参考抽屉已随「正文即界面」P1 废除,见 docs/10 §8)。
import { BoardTab, Props } from "./board/shared";
import OverviewBoard from "./board/OverviewBoard";
import CharactersBoard from "./board/CharactersBoard";
import BibleBoard from "./board/BibleBoard";
import ForeshadowBoard from "./board/ForeshadowBoard";
import MotifBoard from "./board/MotifBoard";

export type { BoardTab };

// 看板五 tab 的内容分发(tab 栏在 book 区统一渲染,此处不再自带 chips)
export default function BoardPanel({ pid, outlines, tab, onGotoChapter }: Props & { tab: BoardTab }) {
  if (tab === "characters") return <CharactersBoard pid={pid} />;
  if (tab === "bible") return <BibleBoard pid={pid} outlines={outlines} />;
  if (tab === "foreshadow") return <ForeshadowBoard pid={pid} outlines={outlines} />;
  if (tab === "motifs") return <MotifBoard pid={pid} />;
  return <OverviewBoard pid={pid} onGotoChapter={onGotoChapter} />;
}
