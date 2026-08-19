// 故事地图覆盖层(Tier 3):纵览全书每章脉络(标题/摘要/伏笔),点章跳转,可只看有伏笔的章。
// 纯 props 组件,不触后端;这里验渲染、当前章高亮、跳章回调、伏笔筛选、关闭。
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import StoryMapOverlay from "../panels/write/StoryMapOverlay";
import { ChapterBrief, Outline } from "../api";

function mk(n: number, title: string, summary: string, role: string, fore = ""): Outline {
  return {
    id: n, chapter_number: n, title, chapter_role: role,
    chapter_purpose: "", suspense_level: "", foreshadowing: fore,
    plot_twist_level: "", summary, characters_involved: null,
    key_items: [], scene_location: "", current_version: 1,
  };
}

const OUTLINES: Outline[] = [
  mk(1, "少年离乡", "他背起行囊上路。", "起"),
  mk(2, "山中遇险", "遇狼群,神秘人出手相救。", "承", "神秘人身份成谜"),
  mk(3, "初入王城", "抵达王城,城门紧闭。", "转"),
];
// 第1章成文、第3章大纲已变、第2章未写(无 brief)
const CHAPTERS: ChapterBrief[] = [
  { chapter_number: 1, status: "approved", word_count: 100, is_stale: false },
  { chapter_number: 3, status: "approved", word_count: 80, is_stale: true },
];

function renderMap(over: Partial<Parameters<typeof StoryMapOverlay>[0]> = {}) {
  const onOpen = vi.fn();
  const onClose = vi.fn();
  render(
    <StoryMapOverlay outlines={OUTLINES} chapters={CHAPTERS} currentNum={2}
      onOpen={onOpen} onClose={onClose} {...over} />,
  );
  return { onOpen, onClose };
}

describe("StoryMapOverlay 故事地图", () => {
  afterEach(() => { vi.restoreAllMocks(); cleanup(); });

  it("列出各章标题/摘要/伏笔,并高亮当前章", () => {
    renderMap();
    // 三章的标题与摘要都在
    expect(screen.getByText("少年离乡")).toBeInTheDocument();
    expect(screen.getByText("他背起行囊上路。")).toBeInTheDocument();
    expect(screen.getByText("初入王城")).toBeInTheDocument();
    // 伏笔单独醒目行
    expect(screen.getByText("伏笔:神秘人身份成谜")).toBeInTheDocument();
    // 当前章(第2章)行高亮
    expect(screen.getByRole("button", { name: /山中遇险/ }).className).toContain("on");
    // 非当前章不高亮
    expect(screen.getByRole("button", { name: /少年离乡/ }).className).not.toContain("on");
  });

  it("点某章 → onOpen(该章号)", () => {
    const { onOpen } = renderMap();
    fireEvent.click(screen.getByRole("button", { name: /初入王城/ }));
    expect(onOpen).toHaveBeenCalledWith(3);
  });

  it("勾「只看有伏笔的章」→ 只剩有伏笔的章", () => {
    renderMap();
    // 初始三章都在
    expect(screen.getByText("少年离乡")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox"));
    // 无伏笔的第1、3章隐藏,有伏笔的第2章保留
    expect(screen.queryByText("少年离乡")).toBeNull();
    expect(screen.queryByText("初入王城")).toBeNull();
    expect(screen.getByText("山中遇险")).toBeInTheDocument();
  });

  it("关闭按钮 / Esc 都触发 onClose", () => {
    const { onClose } = renderMap();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    // Esc(监听挂在 window)
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("无大纲时给空态引导", () => {
    renderMap({ outlines: [] });
    expect(screen.getByText(/还没有大纲/)).toBeInTheDocument();
  });
});
