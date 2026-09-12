// EntityCard(docs/19 M2 追溯层):关系区带证据章引用;出场章可点跳章;降级只读。
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import EntityCard from "../panels/write/EntityCard";
import { CharacterCard } from "../api";

const CARD: CharacterCard = {
  id: 11, name: "林夏", aliases: ["林医生"], entity_type: "character", retired: false,
  profile: "急诊科主治医师",
  key_facts: [{ id: 1, fact_type: "state", content: "左手截肢", valid_from: 3, valid_until: null, importance: "critical" }],
  appearance_chapters: [1, 2, 5],
  relations: [
    {
      other_name: "顾衍", description: "师徒", valid_from: 2, other_retired: false,
      evidence: [{ chapter: 2, content: "顾衍把听诊器放进林夏手里:以后这就是你的武器" }],
    },
  ],
};

describe("EntityCard 追溯", () => {
  it("关系区:对方·关系·起始章 + 证据章引用", () => {
    render(<EntityCard c={CARD} />);
    expect(screen.getByText(/顾衍·师徒/)).toBeTruthy();
    expect(screen.getByText(/自第2章/)).toBeTruthy();
    expect(screen.getByText(/证据·第2章:/)).toBeTruthy();
    expect(screen.getByText(/顾衍把听诊器/)).toBeTruthy();
  });

  it("出场章可点跳章(onJumpChapter);不传则纯文本只读", () => {
    const onJump = vi.fn();
    const ch5 = () => screen.getByText((_, el) => el?.tagName === "SPAN" && el.textContent === "第5章");
    const { rerender } = render(<EntityCard c={CARD} onJumpChapter={onJump} />);
    fireEvent.click(ch5());
    expect(onJump).toHaveBeenCalledWith(5);

    // 只读态:出场章是纯文本(无内层可点 span),点击不触发跳转
    rerender(<EntityCard c={CARD} />);
    expect(screen.queryByText((_, el) => el?.tagName === "SPAN" && el.textContent === "第5章")).toBeNull();
    expect(onJump).toHaveBeenCalledTimes(1);
  });
});
