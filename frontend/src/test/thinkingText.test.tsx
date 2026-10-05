// ThinkingText 慢等待提示:等待超过 25s 追加「模型回话比平时慢」——慢模型接入
// 者此前只有轮转文案,等超时了也不知道是慢还是挂了(2026-10-05 月哥实测)。
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThinkingText } from "../ui/ThinkingText";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("ThinkingText 慢等待提示", () => {
  it("25 秒内不显示慢提示", () => {
    render(<ThinkingText phrases={["正在构思…"]} />);
    vi.advanceTimersByTime(24_999);
    expect(screen.queryByText(/模型回话比平时慢/)).toBeNull();
  });

  it("超过 25 秒后显示慢提示(推理类/慢模型场景)", () => {
    render(<ThinkingText phrases={["正在构思…"]} />);
    act(() => { vi.advanceTimersByTime(25_000); });
    expect(screen.getByText(/模型回话比平时慢/)).toBeTruthy();
    expect(screen.getByText(/换更快的模型/)).toBeTruthy();
  });
});
