// 工作台书名入口(ProjectSettingsPanel)用例。
// 背景:开书时书名由确认① 选中的方向卡自动落库,成书后此前只能回项目列表页重命名——
// 在写作区里想改个名字得先退出工作台(用户 2026-10-17 反馈)。这条用例钉住四件事:
// 回显当前书名、保存走去空白后的 patchProject、空值/未改不发请求、后端报错不假装成功。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Project } from "../api";

// vi.hoisted:vi.mock 的工厂会被提升到 import 之前执行,普通 const 那时还在 TDZ
const mocks = vi.hoisted(() => ({
  patchProject: vi.fn(),
  getPremise: vi.fn(),
}));
vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: { ...actual.api, patchProject: mocks.patchProject, getPremise: mocks.getPremise },
  };
});
// 面板里另外三张子卡各自带查询与网络副作用,与本次被测的书名行无关:
// 打桩掉,用例才盯得住一件事(与 projectsPage.test 打桩 TitleSuggest 同一做法)
vi.mock("../ui/PremiseCard", () => ({ default: () => null }));
vi.mock("../ui/StyleDimensionsCard", () => ({ default: () => null }));
vi.mock("../ui/CreativeReferenceCard", () => ({ default: () => null }));

import ProjectSettingsPanel from "../panels/ProjectSettingsPanel";

function mkProject(over: Partial<Project> = {}): Project {
  return {
    id: 7, title: "未命名新书", topic: "", genre: "",
    target_chapters: 30, target_words_per_chapter: 3000,
    global_tendency: {}, status: "draft",
    ...over,
  } as Project;
}

function renderPanel(project: Project) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ProjectSettingsPanel pid={project.id} project={project} />
    </QueryClientProvider>,
  );
}

// 面板里有两个文字都是「保存」的按钮(书名行 / 每章目标字数行,后者是既有的),
// 所以断言收窄到书名那一行里找——这也正是要验的关联:输入框旁边那个按钮才是它的保存键
const titleRow = () => screen.getByLabelText("书名").closest(".input-row") as HTMLElement;
const saveBtn = () => within(titleRow()).getByRole("button", { name: /保存/ });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getPremise.mockResolvedValue(null);
  mocks.patchProject.mockResolvedValue(mkProject({ title: "裁缝铺里的十年账" }));
});
afterEach(() => cleanup());

describe("工作台书名入口", () => {
  it("回显当前书名;保存走 patchProject 且去掉首尾空白", async () => {
    renderPanel(mkProject({ title: "番茄炒蛋与冷面主厨" }));
    const input = screen.getByLabelText("书名");
    expect(input).toHaveValue("番茄炒蛋与冷面主厨");

    fireEvent.change(input, { target: { value: "  裁缝铺里的十年账  " } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(mocks.patchProject).toHaveBeenCalledWith(
      7, { title: "裁缝铺里的十年账" }));
  });

  it("书名清空时前端拦住,不发请求", () => {
    renderPanel(mkProject({ title: "旧名" }));
    fireEvent.change(screen.getByLabelText("书名"), { target: { value: "   " } });
    fireEvent.click(saveBtn());
    expect(mocks.patchProject).not.toHaveBeenCalled();
  });

  it("没改过就不发请求(避免无谓 PATCH)", () => {
    renderPanel(mkProject({ title: "旧名" }));
    fireEvent.click(saveBtn());
    expect(mocks.patchProject).not.toHaveBeenCalled();
  });

  it("回车即保存", async () => {
    renderPanel(mkProject({ title: "旧名" }));
    const input = screen.getByLabelText("书名");
    fireEvent.change(input, { target: { value: "新名" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(mocks.patchProject).toHaveBeenCalledWith(7, { title: "新名" }));
  });

  it("后端报错时不假装成功:输入原样留着,可以再改", async () => {
    mocks.patchProject.mockRejectedValue(new Error("书名不能为空"));
    renderPanel(mkProject({ title: "旧名" }));
    const input = screen.getByLabelText("书名");
    fireEvent.change(input, { target: { value: "新名" } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(mocks.patchProject).toHaveBeenCalled());
    expect(screen.getByLabelText("书名")).toHaveValue("新名");
  });

  it("书名输入框限长 100(与后端 ProjectPatch 同口径)", () => {
    renderPanel(mkProject({ title: "旧名" }));
    expect(screen.getByLabelText("书名")).toHaveAttribute("maxLength", "100");
  });
});
