// 一键复制的三层兜底:HTTP 页面(没有 Clipboard API)照样要复制得动,
// 全失败也不能把用户丢在「请手动选中文本复制」的死胡同里。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CopyBtn, CopyHost, copyOrPrompt, copyText } from "../ui/copy";

/** 装/卸 navigator.clipboard(jsdom 默认没有,正好等于 HTTP 页面的情形)。 */
function setClipboard(writeText: ((t: string) => Promise<void>) | null) {
  if (writeText === null) {
    Reflect.deleteProperty(navigator, "clipboard");
    return;
  }
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText }, configurable: true, writable: true,
  });
}

let execResult = true;
let execSeen: string[] = [];

beforeEach(() => {
  execResult = true;
  execSeen = [];
  // jsdom 没有 execCommand;这里同时充当「复制时选中的是什么」的探针
  (document as unknown as { execCommand: (c: string) => boolean }).execCommand = (cmd) => {
    if (cmd !== "copy") return false;
    const el = document.activeElement as HTMLTextAreaElement | null;
    if (el && el.tagName === "TEXTAREA") execSeen.push(el.value);
    return execResult;
  };
});

afterEach(() => {
  setClipboard(null);
  vi.restoreAllMocks();
  cleanup();   // vitest 未开 globals,testing-library 的自动清理不生效
});

describe("copyText", () => {
  it("有 Clipboard API 就直接用", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);
    expect(await copyText("整段提示词")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("整段提示词");
    expect(execSeen).toEqual([]);   // 不必走兜底
  });

  it("没有 Clipboard API(http:// 打开)时回落 execCommand,而不是报失败", async () => {
    setClipboard(null);
    expect(await copyText("单人正面半身,纯色浅灰背景")).toBe(true);
    expect(execSeen).toEqual(["单人正面半身,纯色浅灰背景"]);
  });

  it("Clipboard API 抛异常(权限策略拒绝)也回落兜底", async () => {
    setClipboard(vi.fn().mockRejectedValue(new Error("NotAllowedError")));
    expect(await copyText("负面词")).toBe(true);
    expect(execSeen).toEqual(["负面词"]);
  });

  it("两层都不行才返回 false,并且把临时 textarea 清干净", async () => {
    setClipboard(null);
    execResult = false;
    expect(await copyText("x")).toBe(false);
    expect(document.querySelectorAll("textarea")).toHaveLength(0);
  });
});

describe("兜底弹层", () => {
  it("复制不成就弹出已全选的文本,不留死胡同", async () => {
    setClipboard(null);
    execResult = false;
    render(<CopyHost />);
    expect(await copyOrPrompt("拿不进剪贴板的长提示词", "整段")).toBe(false);
    const ta = await screen.findByDisplayValue("拿不进剪贴板的长提示词");
    expect(ta).toBeInTheDocument();
    expect((ta as HTMLTextAreaElement).selectionEnd).toBe("拿不进剪贴板的长提示词".length);
    expect(screen.getByText(/手动复制/)).toBeInTheDocument();
    // 点「好了」关掉:弹层状态是模块级的,不关会漏进下一条用例
    fireEvent.click(screen.getByRole("button", { name: "好了" }));
    expect(screen.queryByText(/手动复制/)).toBeNull();
  });

  it("成功时不弹任何东西", async () => {
    setClipboard(vi.fn().mockResolvedValue(undefined));
    render(<CopyHost />);
    expect(await copyOrPrompt("好复制的文本")).toBe(true);
    expect(screen.queryByText(/手动复制/)).toBeNull();
  });
});

describe("CopyBtn", () => {
  it("点一下即复制并给出「已复制」反馈", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);
    render(<CopyBtn text="提示词整段" label="复制整段" />);
    fireEvent.click(screen.getByRole("button", { name: "复制整段" }));
    expect(await screen.findByRole("button", { name: "✓ 已复制" })).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledWith("提示词整段");
  });
});
