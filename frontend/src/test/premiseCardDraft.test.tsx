// PremiseCard 草稿上报(P0-1):AI 预填就绪即上报;编辑变更上报;保存后报 null。
// 向导点火前用 onDraftChange 拿最新值兜底落库,保证「确认过的梗卡」必进数据库。
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import PremiseCard from "../ui/PremiseCard";
import { api, Premise } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: { ...actual.api, suggestPremise: vi.fn(), savePremise: vi.fn() },
  };
});
vi.mock("../ui/Toaster", () => ({
  toast: { ok: vi.fn(), err: vi.fn() },
}));

const SUGGESTED: Premise = {
  high_concept: "救人一次,寿命减一年", payoff: "代价累积", beats: ["代价显形"],
  boundaries: ["能力无代价"], hook_plan: { opening: "首救" }, source: "ai",
};

function renderCard(onDraftChange: (d: Premise | null) => void) {
  render(<PremiseCard pid={1} initial={null} autoSuggest onDraftChange={onDraftChange} />);
}

describe("PremiseCard 草稿上报", () => {
  afterEach(cleanup);

  it("AI 预填就绪即上报(未编辑也可整体落库)", async () => {
    vi.mocked(api.suggestPremise).mockResolvedValue(SUGGESTED);
    const onDraft = vi.fn();
    renderCard(onDraft);
    await waitFor(() => expect(onDraft).toHaveBeenCalledWith(SUGGESTED));
  });

  it("编辑高概念 → 上报最新草稿;点保存 → 上报 null 且调 savePremise", async () => {
    vi.mocked(api.suggestPremise).mockResolvedValue(SUGGESTED);
    vi.mocked(api.savePremise).mockResolvedValue({ ...SUGGESTED, source: "human" });
    const onDraft = vi.fn();
    renderCard(onDraft);
    await screen.findByText("改梗卡");
    fireEvent.click(screen.getByText("改梗卡"));

    const input = screen.getAllByRole("textbox")[0];
    fireEvent.change(input, { target: { value: "救人一次,寿命减两年" } });
    await waitFor(() => {
      const last = onDraft.mock.calls[onDraft.mock.calls.length - 1]?.[0];
      expect(last?.high_concept).toBe("救人一次,寿命减两年");
    });

    fireEvent.click(screen.getByText("保存梗卡"));
    await waitFor(() => expect(api.savePremise).toHaveBeenCalled());
    // 保存成功后必须有一次 null 上报(清掉未保存草稿;中间可能有其它上报)
    await waitFor(() => expect(onDraft.mock.calls.some((c) => c[0] === null)).toBe(true));
  });
});
