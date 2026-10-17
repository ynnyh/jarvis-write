// 复盘弹窗(docs/36 §4):off 档给引导不给入口;confirm 档跑完逐条拍板;失败可重试。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import RetrospectiveDialog from "../panels/RetrospectiveDialog";

const mocks = vi.hoisted(() => ({
  getEvolutionConfig: vi.fn(),
  runRetrospective: vi.fn(),
  patchEvolutionMemory: vi.fn(),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: { ...actual.api, ...mocks } };
});

const RETRO_RESULT = {
  level: "confirm" as const,
  summary: "冷面细节控,讨厌段尾抒情",
  memories: [
    { id: 21, kind: "style" as const, content: "段尾停在动作上,不做总结升华",
      evidence: "第1章草稿删掉段尾抒情", confidence: "high",
      status: "pending" as const, project_id: 7, source: {}, created_at: "2026-10-17T00:00:00" },
  ],
};

function renderDialog() {
  return render(
    <MemoryRouter>
      <RetrospectiveDialog pid={7} title="账房" onClose={vi.fn()} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mocks.getEvolutionConfig.mockResolvedValue({ level: "confirm" });
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("off 档:给引导,不提供复盘入口", async () => {
  mocks.getEvolutionConfig.mockResolvedValue({ level: "off" });
  renderDialog();
  expect(await screen.findByText(/进化开关还没打开/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "开始复盘" })).toBeNull();
});

it("confirm 档:跑复盘 → 候选带出处展示 → 拍板生效", async () => {
  mocks.runRetrospective.mockResolvedValue(RETRO_RESULT);
  mocks.patchEvolutionMemory.mockResolvedValue({ ...RETRO_RESULT.memories[0], status: "confirmed" });
  renderDialog();
  fireEvent.click(await screen.findByRole("button", { name: "开始复盘" }));
  expect(await screen.findByText("冷面细节控,讨厌段尾抒情")).toBeTruthy();
  expect(screen.getByText("段尾停在动作上,不做总结升华")).toBeTruthy();
  expect(screen.getByText("出处:第1章草稿删掉段尾抒情")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "生效" }));
  await waitFor(() => expect(mocks.patchEvolutionMemory).toHaveBeenCalledWith(21, "confirmed"));
});

it("复盘失败:显示原因,可再试一次", async () => {
  mocks.runRetrospective.mockRejectedValue(new Error("模型没给出可用的复盘"));
  renderDialog();
  fireEvent.click(await screen.findByRole("button", { name: "开始复盘" }));
  expect(await screen.findByText(/模型没给出可用的复盘/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "再试一次" })).toBeTruthy();
});
