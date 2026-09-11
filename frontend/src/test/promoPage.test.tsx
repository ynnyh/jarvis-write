// 宣传片工坊列表页单测:加载/空态、新建(缺主题拦截 + 成功后跳工作台)、删除确认。
// 工作台整块 mock 掉,只验列表页的分发与表单编排。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import PromoPage from "../pages/PromoPage";
import { promoApi } from "../promoApi";
import { confirmDialog } from "../ui/ConfirmDialog";
import { toast } from "../ui/Toaster";

vi.mock("../promoApi", () => ({
  promoApi: { meta: vi.fn(), list: vi.fn(), create: vi.fn(), remove: vi.fn() },
  PROMO_STATUS_CN: { draft: "企划中", briefed: "简报已定", scripted: "解说词已定", storyboarded: "分镜已定", ready: "提示词就绪" },
}));
vi.mock("../panels/promo/PromoWorkbench", () => ({ default: () => <div>PROMO-WB</div> }));
vi.mock("../ui/ConfirmDialog", () => ({ confirmDialog: vi.fn() }));
vi.mock("../ui/Toaster", () => ({ toast: { ok: vi.fn(), err: vi.fn(), info: vi.fn() } }));

const META = {
  angles: [{ key: "food", label: "美食", directive: "" }],
  directions: [{ key: "live", label: "实拍风", tip: "" }],
};

function plan(over: Record<string, unknown> = {}) {
  return {
    id: 1, subject: "西安", title: "", angles: ["food"], duration_s: 90,
    direction: "live", direction_label: "实拍风", status: "draft", ...over,
  };
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/promo"]}>
      <Routes>
        <Route path="/promo" element={<PromoPage />} />
        <Route path="/promo/:id" element={<PromoPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("PromoPage 列表", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(promoApi.meta).mockResolvedValue(META);
    vi.mocked(promoApi.list).mockResolvedValue({ plans: [] });
  });
  afterEach(() => cleanup());

  it("加载列表:渲染主题、时长、画风与状态中文", async () => {
    vi.mocked(promoApi.list).mockResolvedValue({ plans: [plan(), plan({ id: 2, title: "大唐", duration_s: 60, status: "briefed" })] });
    renderPage();

    expect(await screen.findByText("西安")).toBeTruthy();
    expect(screen.getByText("大唐")).toBeTruthy();
    // 两条都是实拍风 → 两颗徽标;另有一份出现在画风下拉的 option 里
    expect(screen.getAllByText("实拍风").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("简报已定")).toBeTruthy();
  });

  it("没有企划:显示空态", async () => {
    renderPage();
    expect(await screen.findByText(/还没有企划/)).toBeTruthy();
  });

  it("缺主题时拦在本地,不调 create", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /建企划/ }));

    expect(vi.mocked(toast.err)).toHaveBeenCalledWith("先填主题", expect.any(String));
    expect(promoApi.create).not.toHaveBeenCalled();
  });

  it("新建成功:create 带上表单值并跳工作台", async () => {
    vi.mocked(promoApi.create).mockResolvedValue({ plan: plan({ id: 5 }) });
    renderPage();

    fireEvent.change(await screen.findByLabelText(/主题/), { target: { value: "  西安  " } });
    fireEvent.click(screen.getByRole("button", { name: /建企划/ }));

    await waitFor(() => expect(promoApi.create).toHaveBeenCalledWith({
      subject: "西安", angles: ["food"], duration_s: 90, direction: "live",
    }));
    expect(await screen.findByText("PROMO-WB")).toBeTruthy();
  });

  it("删除:确认后调 remove 并重新拉列表", async () => {
    vi.mocked(promoApi.list).mockResolvedValue({ plans: [plan()] });
    vi.mocked(confirmDialog).mockResolvedValue(true);
    vi.mocked(promoApi.remove).mockResolvedValue({ ok: true });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    await waitFor(() => expect(promoApi.remove).toHaveBeenCalledWith(1));
    // 初次加载 + 删除后重载 = 2 次
    await waitFor(() => expect(promoApi.list).toHaveBeenCalledTimes(2));
  });
});
