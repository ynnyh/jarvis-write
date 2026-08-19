// 整章优化对照卡:409 并发冲突的显性化处理(护住"静默吞手改"这条高危回归)。
// 核心断言:①应用带优化基线 original 作并发校验;②后端 409 时浮出冲突条(不静默、不当普通报错),
// 且不触发 onApplied;③点「仍用这版覆盖」重发应用且不带 base_content(强制),成功后回调 onApplied。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PolishCompareCard from "../panels/write/PolishCompareCard";
import { ApiError, api, FlavorInfo, PolishResult } from "../api";

// 只替换 api.applyPolish;ApiError / flavorTitle 用真实实现(instanceof 与渲染都依赖它们)
vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: { ...actual.api, applyPolish: vi.fn() } };
});

const PID = 7;
const CH = 3;
const ORIGINAL = "优化开始时的正文第一段。";
const POLISHED = "润色后的正文第一段。";

const flavor = (score: number): FlavorInfo => ({ score, summary: `AI味${score}` });
const makeResult = (over: Partial<PolishResult> = {}): PolishResult => ({
  polished: POLISHED,
  locked_facts: ["锁定事实一"],
  violations: [],
  flavor_before: flavor(5),
  flavor_after: flavor(2),
  ...over,
});

function renderCard(over: Partial<Parameters<typeof PolishCompareCard>[0]> = {}) {
  const onApplied = vi.fn();
  const onDiscard = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <PolishCompareCard
        pid={PID}
        chapterNum={CH}
        original={ORIGINAL}
        result={makeResult()}
        onApplied={onApplied}
        onDiscard={onDiscard}
        {...over}
      />
    </QueryClientProvider>,
  );
  return { onApplied, onDiscard };
}

describe("PolishCompareCard 并发冲突", () => {
  beforeEach(() => vi.clearAllMocks());
  // vitest 未开 globals,需手动 cleanup
  afterEach(() => cleanup());

  it("应用时带优化基线 original 作并发校验,成功回调 onApplied", async () => {
    vi.mocked(api.applyPolish).mockResolvedValue({ status: "applied" });
    const { onApplied } = renderCard();

    fireEvent.click(screen.getByRole("button", { name: /应用/ }));

    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
    // 第 4 参 = 优化基线,后端据此判断是否被手改
    expect(api.applyPolish).toHaveBeenCalledWith(PID, CH, POLISHED, ORIGINAL);
  });

  it("后端 409 时浮出冲突条(不静默、不触发 onApplied)", async () => {
    vi.mocked(api.applyPolish).mockRejectedValue(new ApiError(409, "正文被改动过"));
    const { onApplied } = renderCard();

    fireEvent.click(screen.getByRole("button", { name: /应用/ }));

    // 冲突条浮出(而非静默成功或当普通红字报错)
    await screen.findByText(/正文在优化期间被改动过/);
    expect(screen.getByRole("button", { name: /仍用这版覆盖/ })).toBeInTheDocument();
    expect(onApplied).not.toHaveBeenCalled();
  });

  it("冲突条点「仍用这版覆盖」重发应用且不带 base_content(强制),成功回调 onApplied", async () => {
    vi.mocked(api.applyPolish)
      .mockRejectedValueOnce(new ApiError(409, "正文被改动过"))
      .mockResolvedValueOnce({ status: "applied" });
    const { onApplied } = renderCard();

    // 第一次应用 → 409 → 冲突条
    fireEvent.click(screen.getByRole("button", { name: /应用/ }));
    await screen.findByText(/正文在优化期间被改动过/);

    // 强制覆盖:重发应用
    fireEvent.click(screen.getByRole("button", { name: /仍用这版覆盖/ }));

    await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
    expect(api.applyPolish).toHaveBeenCalledTimes(2);
    // 强制那次第 4 参为 undefined(跳过并发校验)
    expect(api.applyPolish).toHaveBeenNthCalledWith(2, PID, CH, POLISHED, undefined);
  });
});
