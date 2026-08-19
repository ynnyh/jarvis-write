// 可点选段落的键盘/读屏可达性(T0):传 onSelect 时段落是 role=button、可聚焦、aria-pressed 播报选中,
// 回车/空格触发选择(空格 preventDefault 防页面滚动);不传 onSelect(只读阅读)时是纯 <p>,无交互属性。
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, createEvent, cleanup } from "@testing-library/react";
import { Paragraphs } from "../components/reader/paragraphs";

const TEXT = "第一段。\n第二段。\n第三段。";

describe("Paragraphs 可达性", () => {
  afterEach(() => cleanup());

  it("传 onSelect:段落为 role=button、可 Tab 聚焦、aria-pressed 反映选中态", () => {
    render(<Paragraphs text={TEXT} onSelect={vi.fn()} selectedIdx={1} />);
    const btns = screen.getAllByRole("button");
    expect(btns).toHaveLength(3);
    btns.forEach((b) => expect(b).toHaveAttribute("tabindex", "0"));
    // selectedIdx=1 → 第二段 pressed=true,其余 false
    expect(btns[0]).toHaveAttribute("aria-pressed", "false");
    expect(btns[1]).toHaveAttribute("aria-pressed", "true");
    expect(btns[2]).toHaveAttribute("aria-pressed", "false");
  });

  it("回车触发 onSelect(段号)", () => {
    const onSelect = vi.fn();
    render(<Paragraphs text={TEXT} onSelect={onSelect} />);
    fireEvent.keyDown(screen.getByText("第三段。"), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it("空格触发 onSelect 且 preventDefault(防页面滚动)", () => {
    const onSelect = vi.fn();
    render(<Paragraphs text={TEXT} onSelect={onSelect} />);
    const p = screen.getByText("第一段。");
    const ev = createEvent.keyDown(p, { key: " " });
    fireEvent(p, ev);
    expect(onSelect).toHaveBeenCalledWith(0);
    expect(ev.defaultPrevented).toBe(true);
  });

  it("其它按键不触发 onSelect", () => {
    const onSelect = vi.fn();
    render(<Paragraphs text={TEXT} onSelect={onSelect} />);
    fireEvent.keyDown(screen.getByText("第一段。"), { key: "a" });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("不传 onSelect(只读阅读):纯 <p>,无 role/tabindex/交互", () => {
    render(<Paragraphs text={TEXT} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    const p = screen.getByText("第二段。");
    expect(p.tagName).toBe("P");
    expect(p).not.toHaveAttribute("role");
    expect(p).not.toHaveAttribute("tabindex");
    expect(p).not.toHaveAttribute("aria-pressed");
  });
});
