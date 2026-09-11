// 侧边栏分组单测(2026-09-11 导航归组):主线独占;创作辅助默认展开、
// 制片工坊默认收起;点组头手动展开;当前路由在组内时自动展开并点亮。
// 这是纯导航壳改动——只钉分组行为,不碰各工坊页面自身(它们各有页面测试)。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Sidebar from "../ui/Sidebar";

// api 仅被 mock(openLink 供 GitHub 链接兜底),断言用不到其值
vi.mock("../api", () => ({ api: { openLink: vi.fn().mockResolvedValue({ ok: true }) } }));
vi.mock("../ui/TaskCenter", () => ({ TaskCenterBadge: () => <div>TASKS</div> }));

const ME = { id: 1, username: "writer", is_admin: false };

function renderAt(path = "/") {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar me={ME} isLocal hasLock={false} tokens=""
        onLock={vi.fn()} onLogout={vi.fn()} />
    </MemoryRouter>,
  );
}

describe("Sidebar 导航分组", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => cleanup());

  it("主线独占首位;制片工坊默认收起,创作辅助默认展开", () => {
    renderAt("/");
    expect(screen.getByText("我的小说")).toBeTruthy();
    // 创作辅助组默认展开 → 组内条目可见
    expect(screen.getByText("灵感工坊")).toBeTruthy();
    expect(screen.getByText("故事工坊")).toBeTruthy();
    // 制片工坊默认收起 → 组头在,组内条目不可见
    expect(screen.getByText("制片工坊")).toBeTruthy();
    expect(screen.queryByText("剧本工坊")).toBeNull();
    expect(screen.queryByText("宣传片工坊")).toBeNull();
    // 功能页沉底,不参与分组
    expect(screen.getByText("使用指南")).toBeTruthy();
    expect(screen.getByText("设置")).toBeTruthy();
  });

  it("点组头展开制片工坊,组内五条入口齐全;再点收起", () => {
    renderAt("/");
    fireEvent.click(screen.getByRole("button", { name: /制片工坊/ }));

    expect(screen.getByText("剧本工坊")).toBeTruthy();
    expect(screen.getByText("宣传片工坊")).toBeTruthy();
    expect(screen.getByText("情绪短片")).toBeTruthy();
    expect(screen.getByText("系列短片")).toBeTruthy();
    expect(screen.getByText("生日祝福")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /制片工坊/ }));
    expect(screen.queryByText("剧本工坊")).toBeNull();
  });

  it("当前路由就在组内时,该组自动展开并点亮(默认收起也不藏位置感)", () => {
    renderAt("/scripts");
    // /scripts 在制片组内 → 组自动展开,组内条目可见,组头点亮
    expect(screen.getByText("剧本工坊")).toBeTruthy();
    expect(screen.getByText("制片工坊").closest(".side-group")?.className).toContain("active");
    // 创作辅助组按默认态(本来就展开),功能页照常
    expect(screen.getByText("灵感工坊")).toBeTruthy();
    expect(screen.getByText("设置")).toBeTruthy();
  });

  it("子路由也算命中:/scripts/9 工作台同样自动展开制片组", () => {
    renderAt("/scripts/9");
    expect(screen.getByText("剧本工坊")).toBeTruthy();
  });
});
