// ReconciliationBlock 交稿对账(docs/19 M3):待确认关系逐条确认/否决;梗兑现只读。
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReconciliationBlock from "../ui/ReconciliationBlock";
import { api, ChapterReconciliation } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: { ...actual.api, getReconciliation: vi.fn(), confirmReconciliation: vi.fn() },
  };
});

const DATA: ChapterReconciliation = {
  chapter_number: 4,
  pending_relations: [
    { id: 7, from_name: "林夏", to_name: "灰衣人", relation: "猫鼠游戏",
      evidence_chapter: 4, evidence_text: "走廊尽头,穿灰风衣的男人安静地看她" },
  ],
  ledger: { fulfilled: true, beat: "第1拍·代价显形", note: "首次救人后倒计时延长", strength: 4, evidence: "" },
  foreshadow_changes: [{ description: "腕上倒计时纸条", op: "planted" }],
  confirmed: false,
};

function renderBlock() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ReconciliationBlock pid={1} n={4} />
    </QueryClientProvider>,
  );
}

describe("ReconciliationBlock 交稿对账", () => {
  afterEach(cleanup);

  it("展示待确认关系(带证据)、梗兑现、伏笔变动", async () => {
    vi.mocked(api.getReconciliation).mockResolvedValue(DATA);
    renderBlock();
    expect(await screen.findByText(/林夏 → 灰衣人:猫鼠游戏/)).toBeTruthy();
    expect(screen.getByText(/证据\(第4章\)/)).toBeTruthy();
    expect(screen.getByText("梗已兑现")).toBeTruthy();
    expect(screen.getByText(/第1拍·代价显形/)).toBeTruthy();
    expect(screen.getByText("本章埋设")).toBeTruthy();
  });

  it("确认按钮调 confirmReconciliation;全部处理后只剩摘要", async () => {
    const EMPTY: ChapterReconciliation = { ...DATA, pending_relations: [], confirmed: true };
    vi.mocked(api.getReconciliation)
      .mockResolvedValueOnce(DATA)        // 首查:有待确认
      .mockResolvedValue(EMPTY);          // 确认并失效缓存后:已清
    vi.mocked(api.confirmReconciliation).mockResolvedValue({ confirmed: 1, rejected: 0 });
    renderBlock();
    fireEvent.click(await screen.findByText("确认"));
    await waitFor(() => expect(api.confirmReconciliation).toHaveBeenCalled());
    expect(await screen.findByText(/无待确认项/)).toBeTruthy();
  });
});
