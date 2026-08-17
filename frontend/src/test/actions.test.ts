// 统一动作 dispatch(ui/actions.ts)单测:
//   注册/分发/注销;同动作栈「后者优先、卸载回落」;非法动作名拦截。
import { describe, it, expect, vi } from "vitest";
import { dispatchAction, isAppAction, registerActionHandler } from "../ui/actions";

describe("dispatch 注册表", () => {
  it("注册后可 dispatch;注销后无人认领返回 false", () => {
    const fn = vi.fn();
    const off = registerActionHandler("generate", fn);
    expect(dispatchAction("generate")).toBe(true);
    expect(fn).toHaveBeenCalledTimes(1);
    off();
    expect(dispatchAction("generate")).toBe(false);
  });

  it("同一动作重复注册:栈顶(后注册者)优先,卸载后回落", () => {
    const a = vi.fn();
    const b = vi.fn();
    const offA = registerActionHandler("immersive", a);
    const offB = registerActionHandler("immersive", b);
    dispatchAction("immersive");
    expect(b).toHaveBeenCalledTimes(1);
    expect(a).not.toHaveBeenCalled();
    offB(); // 栈顶注销 → 回落到 a
    dispatchAction("immersive");
    expect(a).toHaveBeenCalledTimes(1);
    offA();
    expect(dispatchAction("immersive")).toBe(false);
  });

  it("注销幂等:重复调用不报错也不误删他人 handler", () => {
    const a = vi.fn();
    const off = registerActionHandler("generate", a);
    off();
    off();
    expect(dispatchAction("generate")).toBe(false);
  });

  it("isAppAction:只认已知动作名(Tauri 菜单事件 payload 校验用)", () => {
    expect(isAppAction("command-palette")).toBe(true);
    expect(isAppAction("export-txt")).toBe(true);
    expect(isAppAction("rm-rf")).toBe(false);
    expect(isAppAction(42)).toBe(false);
    expect(isAppAction(null)).toBe(false);
  });
});
