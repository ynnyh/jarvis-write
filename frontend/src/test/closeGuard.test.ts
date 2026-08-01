// 关闭守卫决策单测:
//   有任务 → 一律弹确认框(即便开了托盘偏好,避免误杀任务);
//   无任务 → 按「关闭时最小化到托盘」偏好进托盘或直接关。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("../api", () => ({ api: { myJobs: vi.fn() } }));
vi.mock("../desktop", () => ({
  isDesktop: () => true,
  enableCloseGuard: vi.fn(),
  closeApp: vi.fn(),
  hideToTray: vi.fn(),
  onCloseRequested: vi.fn(),
}));

import { decideCloseAction, getCloseToTrayPref, setCloseToTrayPref } from "../ui/CloseGuard";

describe("decideCloseAction", () => {
  it("有任务进行中:一律弹确认框,不管托盘偏好", () => {
    expect(decideCloseAction(2, false)).toBe("ask");
    expect(decideCloseAction(1, true)).toBe("ask");
  });

  it("无任务:按偏好进托盘或直接关", () => {
    expect(decideCloseAction(0, true)).toBe("tray");
    expect(decideCloseAction(0, false)).toBe("close");
  });
});

describe("关闭进托盘偏好(localStorage)", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  it("默认关", () => {
    expect(getCloseToTrayPref()).toBe(false);
  });

  it("写入后可读回;关掉的值不会被当成开", () => {
    setCloseToTrayPref(true);
    expect(getCloseToTrayPref()).toBe(true);
    setCloseToTrayPref(false);
    expect(getCloseToTrayPref()).toBe(false);
  });
});
