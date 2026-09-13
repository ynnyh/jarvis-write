// 项目列表页单测:列表加载/空态/加载失败、重命名(成功 + 空标题拦截)、
// 删除(确认 + 取消)、完本锁定(重命名/删除置灰,标完本走 patch)。
// 依赖的全局弹层与 toast 都 mock 掉,只测页面自己的编排。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ProjectsPage from "../pages/ProjectsPage";
import { api } from "../api";
import type { Project } from "../api";
import { confirmDialog } from "../ui/ConfirmDialog";
import { toast } from "../ui/Toaster";

vi.mock("../api", () => ({
  api: {
    listProjects: vi.fn(),
    listChapters: vi.fn(),
    renameProject: vi.fn(),
    deleteProject: vi.fn(),
    patchProject: vi.fn(),
    dashboard: vi.fn().mockResolvedValue({ projects: [] }),  // 驾驶舱:测试聚焦列表本身
  },
}));
vi.mock("../ui/ConfirmDialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("../ui/Toaster", () => ({ toast: { ok: vi.fn(), err: vi.fn(), info: vi.fn() } }));
// AI 起名按钮与列表页无关,去掉它免得依赖未被 mock 的 suggestTitleAsync
vi.mock("../components/TitleSuggest", () => ({ default: () => null }));

function proj(over: Partial<Project> = {}): Project {
  return {
    id: 1,
    title: "破封纪",
    topic: "修仙",
    genre: "修仙",
    target_chapters: 100,
    target_words_per_chapter: 3000,
    global_tendency: {},
    status: "writing",
    written_chapters: 10,
    total_words: 30000,
    ...over,
  } as Project;
}

function renderPage() {
  render(<MemoryRouter><ProjectsPage /></MemoryRouter>);
}

describe("ProjectsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listChapters).mockResolvedValue([]);
  });
  afterEach(() => cleanup());

  it("加载列表:渲染书名与进度", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([
      proj(),
      proj({ id: 2, title: "长夜灯", written_chapters: 3, target_chapters: 60 }),
    ]);
    renderPage();

    expect(await screen.findByText(/破封纪/)).toBeTruthy();
    expect(screen.getByText(/长夜灯/)).toBeTruthy();
    expect(screen.getByText(/10\/100 章/)).toBeTruthy();
    expect(screen.getByText(/3\/60 章/)).toBeTruthy();
  });

  it("列表为空:显示空态引导", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/还没有项目/)).toBeTruthy();
  });

  it("加载失败:显示错误文案", async () => {
    vi.mocked(api.listProjects).mockRejectedValue(new Error("服务不可用"));
    renderPage();
    expect(await screen.findByText("服务不可用")).toBeTruthy();
  });

  it("重命名:保存后调 renameProject 并就地更新标题", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([proj()]);
    vi.mocked(api.renameProject).mockResolvedValue(proj({ title: "封天记" }));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "重命名" }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "封天记" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(api.renameProject).toHaveBeenCalledWith(1, "封天记"));
    expect(await screen.findByText(/封天记/)).toBeTruthy();
  });

  it("重命名为空:前端拦下并提示,不调接口", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([proj()]);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("标题不能为空")).toBeTruthy();
    expect(api.renameProject).not.toHaveBeenCalled();
  });

  it("删除:确认后调 deleteProject 并从列表移除", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([proj()]);
    vi.mocked(api.listChapters).mockResolvedValue([
      { chapter_number: 1, status: "approved", word_count: 3000, is_stale: false },
      { chapter_number: 2, status: "approved", word_count: 2800, is_stale: false },
    ]);
    vi.mocked(confirmDialog).mockResolvedValue(true);
    vi.mocked(api.deleteProject).mockResolvedValue({ ok: true, deleted_chapters: 2 });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    await waitFor(() => expect(api.deleteProject).toHaveBeenCalledWith(1));
    // 确认弹层拿真实章节数做文案
    expect(vi.mocked(confirmDialog).mock.calls[0][0].title).toContain("删除《破封纪》");
    expect(vi.mocked(toast.ok)).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText(/破封纪/)).toBeNull());
  });

  it("删除取消:不调 deleteProject,列表不动", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([proj()]);
    vi.mocked(confirmDialog).mockResolvedValue(false);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
    expect(api.deleteProject).not.toHaveBeenCalled();
    expect(screen.getByText(/破封纪/)).toBeTruthy();
  });

  it("完本锁定:重命名/删除置灰且删除显示「已锁定」", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([proj({ finished: true })]);
    renderPage();

    expect(await screen.findByRole("button", { name: "重命名" })).toBeDisabled();
    const locked = screen.getByRole("button", { name: "已锁定" });
    expect(locked).toBeDisabled();
    // 完本徽标与「取消完本」入口都在
    expect(screen.getByText("完本")).toBeTruthy();
    expect(screen.getByRole("button", { name: "取消完本" })).toBeTruthy();
  });

  it("标完本:确认后 patch finished=true 并提示锁定", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([proj()]);
    vi.mocked(confirmDialog).mockResolvedValue(true);
    vi.mocked(api.patchProject).mockResolvedValue(proj({ finished: true }));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "标完本" }));

    await waitFor(() => expect(api.patchProject).toHaveBeenCalledWith(1, { finished: true }));
    expect(vi.mocked(toast.ok)).toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "已锁定" })).toBeTruthy();
  });

  it("取消完本:不上确认弹层,直接 patch finished=false", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([proj({ finished: true })]);
    vi.mocked(api.patchProject).mockResolvedValue(proj({ finished: false }));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "取消完本" }));

    await waitFor(() => expect(api.patchProject).toHaveBeenCalledWith(1, { finished: false }));
    expect(confirmDialog).not.toHaveBeenCalled();
  });
});
