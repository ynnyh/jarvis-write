// SkillPacksCard 创作 Skill 包管理卡(docs/21):列表投影 + 启停 + 条目 JSON 编辑 +
// 历史回退。一份真相:skill_packs 表;卡片只是投影,注入逻辑在后端 packs 引擎。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SkillPacksCard } from "../pages/settings/SkillPacksCard";
import { api, SkillPack } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listSkillPacks: vi.fn(),
      updateSkillPack: vi.fn(),
      restoreSkillPack: vi.fn(),
    },
  };
});

function mkPack(overrides: Partial<SkillPack> = {}): SkillPack {
  return {
    id: 1,
    pack_key: "storyboard-basics",
    name: "分镜功底包",
    description: "分镜工序的工艺下限",
    scope: ["anime"],
    version: 1,
    entries: [
      { node: "shots", kind: "directive", directive: "每镜只安排一个主动作" },
      { node: "shots", kind: "param", params: { 单镜时长上限: "5 秒" } },
    ],
    history: [{ version: 1, entries: [] }],
    enabled: true,
    is_builtin: true,
    ...overrides,
  };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SkillPacksCard />
    </QueryClientProvider>,
  );
}

describe("SkillPacksCard 创作 Skill 包管理卡", () => {
  beforeEach(() => {
    vi.clearAllMocks(); // 清上一用例的调用记录,「未调用」断言才可信
    vi.mocked(api.listSkillPacks).mockResolvedValue([mkPack()]);
  });
  afterEach(cleanup);

  it("列出包:名称/版本/适用线/条目内容可见", async () => {
    renderCard();
    expect(await screen.findByText(/分镜功底包/)).toBeTruthy();
    expect(screen.getByText(/适用:动画短剧/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "条目与历史" }));
    expect(await screen.findByText(/每镜只安排一个主动作/)).toBeTruthy();
    expect(screen.getByText(/单镜时长上限:5 秒/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "回到 v1" })).toBeTruthy();
  });

  it("启停开关走 updateSkillPack(enabled)", async () => {
    vi.mocked(api.updateSkillPack).mockResolvedValue(mkPack({ enabled: false }));
    renderCard();
    const checkbox = await screen.findByRole("checkbox");
    fireEvent.click(checkbox);
    await waitFor(() =>
      expect(api.updateSkillPack).toHaveBeenCalledWith(1, { enabled: false }));
  });

  it("编辑条目:JSON 解析失败不发请求,合法才保存", async () => {
    vi.mocked(api.updateSkillPack).mockResolvedValue(mkPack({ version: 2 }));
    renderCard();
    await screen.findByText(/分镜功底包/);
    fireEvent.click(screen.getByRole("button", { name: "条目与历史" }));
    fireEvent.click(screen.getByRole("button", { name: "编辑条目" }));
    const box = await screen.findByRole("textbox");
    fireEvent.change(box, { target: { value: "{不是合法 JSON" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(api.updateSkillPack).not.toHaveBeenCalled();
    fireEvent.change(box, {
      target: { value: JSON.stringify([{ node: "shots", kind: "directive", directive: "改" }]) },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.updateSkillPack).toHaveBeenCalledTimes(1));
  });

  it("历史回退走 restoreSkillPack", async () => {
    vi.mocked(api.restoreSkillPack).mockResolvedValue(mkPack({ version: 2 }));
    renderCard();
    await screen.findByText(/分镜功底包/);
    fireEvent.click(screen.getByRole("button", { name: "条目与历史" }));
    fireEvent.click(screen.getByRole("button", { name: "回到 v1" }));
    await waitFor(() =>
      expect(api.restoreSkillPack).toHaveBeenCalledWith(1, 1));
  });
});
