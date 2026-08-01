// 锁屏组件单测:解锁成功回调 onUnlocked;密码错误就地提示并清空输入;空密码禁提交;
// 忘记密码:输入「重置」二字确认后调 reset 并直接进入(视为已解锁)
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import LockScreen from "../pages/LockScreen";
import { api } from "../api";

vi.mock("../api", () => ({
  api: { appLockUnlock: vi.fn(), appLockReset: vi.fn() },
}));

describe("LockScreen", () => {
  beforeEach(() => vi.clearAllMocks());
  // vitest 未开 globals,testing-library 的自动清理不生效,需手动 cleanup
  afterEach(() => cleanup());

  it("解锁成功调用 onUnlocked", async () => {
    vi.mocked(api.appLockUnlock).mockResolvedValue({ ok: true });
    const onUnlocked = vi.fn();
    render(<LockScreen onUnlocked={onUnlocked} />);

    fireEvent.change(screen.getByPlaceholderText("输入应用锁密码"), {
      target: { value: "lock123" },
    });
    fireEvent.click(screen.getByRole("button", { name: /解锁/ }));

    await waitFor(() => expect(onUnlocked).toHaveBeenCalledTimes(1));
    expect(api.appLockUnlock).toHaveBeenCalledWith("lock123");
  });

  it("密码错误显示后端 detail 并清空输入,不触发 onUnlocked", async () => {
    vi.mocked(api.appLockUnlock).mockRejectedValue(new Error("密码不正确"));
    const onUnlocked = vi.fn();
    render(<LockScreen onUnlocked={onUnlocked} />);

    const input = screen.getByPlaceholderText("输入应用锁密码");
    fireEvent.change(input, { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /解锁/ }));

    await screen.findByText("密码不正确");
    expect(onUnlocked).not.toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("");
  });

  it("空密码时解锁按钮禁用", () => {
    render(<LockScreen onUnlocked={vi.fn()} />);
    expect(screen.getByRole("button", { name: /解锁/ })).toBeDisabled();
  });

  it("忘记密码:输入「重置」确认后调 reset 并直接进入", async () => {
    vi.mocked(api.appLockReset).mockResolvedValue({ ok: true });
    const onUnlocked = vi.fn();
    render(<LockScreen onUnlocked={onUnlocked} />);

    fireEvent.click(screen.getByText("忘记密码?"));
    // 未输入「重置」二字时确认按钮禁用
    const confirmBtn = screen.getByRole("button", { name: /确认重置/ });
    expect(confirmBtn).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("重置"), {
      target: { value: "重置" },
    });
    expect(confirmBtn).not.toBeDisabled();
    fireEvent.click(confirmBtn);

    await waitFor(() => expect(onUnlocked).toHaveBeenCalledTimes(1));
    expect(api.appLockReset).toHaveBeenCalledWith("重置");
  });
});
