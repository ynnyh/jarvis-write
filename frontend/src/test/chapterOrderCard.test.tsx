// ChapterOrderCard 章节订单卡(docs/20 订单制):六单预填/编辑、确认即按单生成、
// 对账订单区随 ReconciliationBlock 渲染的偏差逐条亮出。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ChapterOrderCard from "../ui/ChapterOrderCard";
import { api, ChapterOrder } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getChapterOrder: vi.fn(),
      saveChapterOrder: vi.fn(),
      confirmChapterOrder: vi.fn(),
      unconfirmChapterOrder: vi.fn(),
    },
  };
});

const PREFILL = {
  order: null,
  prefill: {
    cast: { entering: [], present: ["陈默"], exiting: [] },
    relations: [],
    beats: ["蓝图节拍一"],
    hooks: { carry_in: [{ text: "上章脚步声", must: false }], leave: "" },
    foreshadow: { due: [], plant: [] },
    scenes: [],
    free_directive: "",
  },
};

const CONFIRMED = {
  id: 1, chapter_number: 4, version: 2, status: "confirmed" as const,
  beat_check: [{ beat: "接到信", hit: true, note: "开头即写" }],
  updated_at: null,
  payload: {
    cast: {
      entering: [{ name: "林晚", reason: "带线索登场" }],
      present: ["陈默"],
      exiting: [{ name: "老赵", mode: "远行", threads: "账本" }],
    },
    relations: [{ from: "林晚", to: "陈默", before: "陌生", after: "合作", event: "交易" }],
    beats: ["接到匿名信", "亮出账本"],
    hooks: { carry_in: [{ text: "脚步声", must: true }], leave: "账本少一页" },
    foreshadow: { plant: ["缺页秘密"] },
    scenes: [],
    free_directive: "全章小雨",
  },
} satisfies ChapterOrder;

function renderCard(onGenerate?: (n: number) => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ChapterOrderCard pid={1} n={4} onGenerate={onGenerate} />
    </QueryClientProvider>,
  );
}

describe("ChapterOrderCard 章节订单", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.mocked(api.saveChapterOrder).mockReset();
    vi.mocked(api.confirmChapterOrder).mockReset();
  });

  it("无订单时如实说「未建单」,收起态展示预填要点", async () => {
    vi.mocked(api.getChapterOrder).mockResolvedValue(PREFILL);
    renderCard();
    expect(await screen.findByText(/未建单/)).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("chapter-order").textContent).toContain("在场:陈默"));
    expect(screen.getByTestId("chapter-order").textContent).toContain("节拍 1 拍");
  });

  it("确认订单调 confirm 并触发按单生成", async () => {
    vi.mocked(api.getChapterOrder).mockResolvedValue({
      order: null,
      prefill: PREFILL.prefill,
    });
    vi.mocked(api.confirmChapterOrder).mockResolvedValue({
      ...CONFIRMED, status: "confirmed", version: 1,
    });
    const onGenerate = vi.fn();
    renderCard(onGenerate);
    fireEvent.click(await screen.findByText("编辑订单"));
    fireEvent.click(screen.getByText(/确认订单 · 按单生成/));
    await waitFor(() => expect(api.confirmChapterOrder).toHaveBeenCalled());
    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith(4));
  });

  it("已确认订单展示版本与按单生成标记,摘要可核对六单要点", async () => {
    vi.mocked(api.getChapterOrder).mockResolvedValue({ order: CONFIRMED, prefill: null });
    renderCard();
    expect(await screen.findByText(/已确认 v2 · 按单生成/)).toBeTruthy();
    expect(screen.getByText(/必登场:林晚/)).toBeTruthy();
    expect(screen.getByText(/退场:老赵/)).toBeTruthy();
    expect(screen.getByText(/承上必收 1 条/)).toBeTruthy();
    expect(screen.getByText(/含作者指令/)).toBeTruthy();
  });

  it("撤回确认走 unconfirm", async () => {
    vi.mocked(api.getChapterOrder).mockResolvedValue({ order: CONFIRMED, prefill: null });
    vi.mocked(api.unconfirmChapterOrder).mockResolvedValue(CONFIRMED);
    renderCard();
    fireEvent.click(await screen.findByText("编辑订单"));
    fireEvent.click(screen.getByText("撤回确认"));
    await waitFor(() => expect(api.unconfirmChapterOrder).toHaveBeenCalledWith(1, 4));
  });
});
