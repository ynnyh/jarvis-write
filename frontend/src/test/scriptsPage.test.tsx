// 剧本工坊列表页单测:加载/空态、新建(成功跳工作台 + 失败提示)、删除确认、
// 「从小说改编」入口触发 adapt。工作台(带 :id)不在本文件范围。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ScriptsPage from "../pages/ScriptsPage";
import { scriptsApi } from "../scriptsApi";
import { api } from "../api";
import { confirmDialog } from "../ui/ConfirmDialog";
import { toast } from "../ui/Toaster";

vi.mock("../scriptsApi", () => ({
  scriptsApi: {
    list: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    adapt: vi.fn(),
  },
}));
vi.mock("../api", () => ({ api: { listProjects: vi.fn() } }));
vi.mock("../ui/ConfirmDialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("../ui/Toaster", () => ({ toast: { ok: vi.fn(), err: vi.fn(), info: vi.fn() } }));

function script(over: Record<string, unknown> = {}) {
  return {
    id: 1, title: "长夜灯", genre: "悬疑", logline: "灯灭那夜,他回了村",
    target_episodes: 12, status: "outlined", style_memo: "", source_project_id: null,
    ...over,
  };
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/scripts"]}>
      <Routes>
        <Route path="/scripts" element={<ScriptsPage />} />
        <Route path="/scripts/:id" element={<div>WORKBENCH</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ScriptsPage 列表", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listProjects).mockResolvedValue([]);
    vi.mocked(scriptsApi.list).mockResolvedValue([]);
  });
  afterEach(() => cleanup());

  it("加载列表:渲染剧名、类型与状态中文", async () => {
    vi.mocked(scriptsApi.list).mockResolvedValue([script(), script({ id: 2, title: "雾港", status: "empty" })]);
    renderPage();

    expect(await screen.findByText("长夜灯")).toBeTruthy();
    expect(screen.getByText("雾港")).toBeTruthy();
    expect(screen.getByText("已有大纲")).toBeTruthy();
    expect(screen.getByText("未开工")).toBeTruthy();
  });

  it("列表为空:显示空态引导", async () => {
    renderPage();
    expect(await screen.findByText(/还没有剧本/)).toBeTruthy();
  });

  it("新建成功:create 用 trim 后的值,提示并跳到工作台", async () => {
    vi.mocked(scriptsApi.create).mockResolvedValue(script({ id: 9 }));
    renderPage();

    fireEvent.change(await screen.findByLabelText(/剧名/), { target: { value: "  长夜灯  " } });
    fireEvent.change(screen.getByLabelText(/类型/), { target: { value: "悬疑" } });
    fireEvent.click(screen.getByRole("button", { name: "创建剧本" }));

    await waitFor(() => expect(scriptsApi.create).toHaveBeenCalledWith({
      title: "长夜灯", genre: "悬疑", logline: "", target_episodes: 12,
    }));
    expect(vi.mocked(toast.ok)).toHaveBeenCalled();
    expect(await screen.findByText("WORKBENCH")).toBeTruthy();
  });

  it("新建失败:提示错误,留在列表页", async () => {
    vi.mocked(scriptsApi.create).mockRejectedValue(new Error("服务繁忙"));
    renderPage();

    fireEvent.change(await screen.findByLabelText(/剧名/), { target: { value: "长夜灯" } });
    fireEvent.click(screen.getByRole("button", { name: "创建剧本" }));

    await waitFor(() => expect(vi.mocked(toast.err)).toHaveBeenCalled());
    expect(screen.queryByText("WORKBENCH")).toBeNull();
  });

  it("删除:确认后调 remove 并重新拉列表", async () => {
    vi.mocked(scriptsApi.list).mockResolvedValue([script()]);
    vi.mocked(confirmDialog).mockResolvedValue(true);
    vi.mocked(scriptsApi.remove).mockResolvedValue({ deleted: true });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    await waitFor(() => expect(scriptsApi.remove).toHaveBeenCalledWith(1));
    // 初次加载 + 删除后重载 = 2 次
    await waitFor(() => expect(scriptsApi.list).toHaveBeenCalledTimes(2));
  });

  it("有定稿小说时出现改编入口,点击走 adapt 并跳工作台", async () => {
    vi.mocked(api.listProjects).mockResolvedValue([{ id: 7, title: "破封纪" } as never]);
    vi.mocked(scriptsApi.adapt).mockResolvedValue({ script_id: 21, title: "破封纪·剧", episodes: 12, logline: "" });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "改编成剧本" }));

    await waitFor(() => expect(scriptsApi.adapt).toHaveBeenCalledWith(7, { target_episodes: 12 }));
    expect(await screen.findByText("WORKBENCH")).toBeTruthy();
  });
});
