// 进化卡(docs/36):三档旋钮切换 + 记忆列表管理(拍板/忽略/删除/清空)。
// 守的是用户资产承诺:档位点了就存;删除/清空必须过 confirmDialog 二次确认。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmHost } from "../ui/ConfirmDialog";
import { EvolutionCard } from "../pages/settings/EvolutionCard";

const mocks = vi.hoisted(() => ({
  getEvolutionConfig: vi.fn(),
  saveEvolutionConfig: vi.fn(),
  listEvolutionMemories: vi.fn(),
  patchEvolutionMemory: vi.fn(),
  deleteEvolutionMemory: vi.fn(),
  clearEvolutionMemories: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: { ...actual.api, ...mocks } };
});

const mkMem = (over: Record<string, unknown> = {}) => ({
  id: 11, kind: "lesson", content: "段尾停在动作上,不做总结升华",
  evidence: "第1章草稿删掉段尾抒情", confidence: "high",
  status: "pending", project_id: 7, source: {}, created_at: "2026-10-17T00:00:00",
  ...over,
});

function renderCard() {
  return render(<><EvolutionCard /><ConfirmHost /></>);
}

beforeEach(() => {
  mocks.getEvolutionConfig.mockResolvedValue({ level: "confirm" });
  mocks.listEvolutionMemories.mockResolvedValue({ items: [mkMem()] });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("EvolutionCard 档位", () => {
  it("三档齐展示,当前档高亮", async () => {
    renderCard();
    await screen.findByText("确认后生效");
    const on = document.querySelectorAll(".chip.on");
    expect(on).toHaveLength(1);
    expect(on[0].textContent).toBe("确认后生效");
  });

  it("点档位立即保存", async () => {
    mocks.saveEvolutionConfig.mockResolvedValue({ level: "auto" });
    renderCard();
    await screen.findByText("确认后生效");
    fireEvent.click(screen.getByRole("button", { name: "自动生效" }));
    await waitFor(() =>
      expect(mocks.saveEvolutionConfig).toHaveBeenCalledWith("auto"));
  });
});

describe("EvolutionCard 记忆列表", () => {
  it("渲染内容与出处,待确认条目带生效/忽略/删除", async () => {
    renderCard();
    await screen.findByText("段尾停在动作上,不做总结升华");
    expect(screen.getByText("出处:第1章草稿删掉段尾抒情")).toBeTruthy();
    expect(screen.getByRole("button", { name: "生效" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "忽略" })).toBeTruthy();
  });

  it("点生效 → PATCH confirmed", async () => {
    mocks.patchEvolutionMemory.mockResolvedValue(mkMem({ status: "confirmed" }));
    renderCard();
    await screen.findByText("段尾停在动作上,不做总结升华");
    fireEvent.click(screen.getByRole("button", { name: "生效" }));
    await waitFor(() =>
      expect(mocks.patchEvolutionMemory).toHaveBeenCalledWith(11, "confirmed"));
  });

  it("删除必须过二次确认,确认了才真删", async () => {
    mocks.deleteEvolutionMemory.mockResolvedValue({ ok: true });
    renderCard();
    const row = await screen.findByText("段尾停在动作上,不做总结升华");
    const memRow = row.closest(".evo-mem") as HTMLElement;
    fireEvent.click(within(memRow).getByRole("button", { name: "删除" }));
    // 弹层先出现,此时还没删
    expect(mocks.deleteEvolutionMemory).not.toHaveBeenCalled();
    const dlg = await screen.findByRole("dialog");
    fireEvent.click(within(dlg as HTMLElement).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mocks.deleteEvolutionMemory).toHaveBeenCalledWith(11));
  });

  it("清空全部:二次确认后调清空接口", async () => {
    mocks.clearEvolutionMemories.mockResolvedValue({ ok: true, deleted: 1 });
    renderCard();
    await screen.findByText("段尾停在动作上,不做总结升华");
    fireEvent.click(screen.getByRole("button", { name: "清空全部" }));
    const dlg = await screen.findByRole("dialog");
    fireEvent.click(within(dlg as HTMLElement).getByRole("button", { name: "清空" }));
    await waitFor(() => expect(mocks.clearEvolutionMemories).toHaveBeenCalled());
  });

  it("没有记忆时不渲染列表区", async () => {
    mocks.listEvolutionMemories.mockResolvedValue({ items: [] });
    renderCard();
    await screen.findByText("确认后生效");
    expect(document.querySelector(".evo-mems")).toBeNull();
  });
});
