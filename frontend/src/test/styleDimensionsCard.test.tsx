// StyleDimensionsCard 文风画像卡(docs/20 同批):六维笔法可视化+进化。
// 一份真相:projects.style_profile;卡片只是投影,编辑/重分析/回退都写回那一列。
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import StyleDimensionsCard from "../ui/StyleDimensionsCard";
import { api, StyleDimensionsOut } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      getStyleDimensions: vi.fn(),
      saveStyleDimensions: vi.fn(),
      restoreStyleDimensions: vi.fn(),
      reanalyzeStyleDimensionsAsync: vi.fn(),
    },
  };
});

function mkOut(overrides: Partial<StyleDimensionsOut> = {}): StyleDimensionsOut {
  return {
    dims: {
      perspective: { text: "第三人称限知,跟女主", source: "前作分析", at: "2026-09-16T01:00:00Z" },
      rhythm: { text: "", source: "", at: "" },
      dialogue: { text: "", source: "", at: "" },
      rhetoric: { text: "", source: "", at: "" },
      mood: { text: "", source: "", at: "" },
      hook: { text: "章末必留钩", source: "手改", at: "2026-09-16T02:00:00Z" },
    },
    version: 3,
    history: [{ version: 2, dims: {}, at: "2026-09-16T00:00:00Z" }],
    dim_defs: [
      { key: "perspective", label: "叙事视角", hint: "第几人称" },
      { key: "rhythm", label: "句式节奏", hint: "句长偏好" },
      { key: "dialogue", label: "对话密度", hint: "占比" },
      { key: "rhetoric", label: "修辞惯用", hint: "手法" },
      { key: "mood", label: "氛围基调", hint: "调性" },
      { key: "hook", label: "起势与钩法", hint: "钩法" },
    ],
    memo: "",
    ...overrides,
  };
}

function renderCard() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <StyleDimensionsCard pid={1} />
    </QueryClientProvider>,
  );
}

describe("StyleDimensionsCard 文风画像卡", () => {
  afterEach(cleanup);

  it("六维可视化:有内容的维度展示文本与来源,空的标「未提炼」", async () => {
    vi.mocked(api.getStyleDimensions).mockResolvedValue(mkOut());
    renderCard();
    expect(await screen.findByText("文风画像 · v3")).toBeTruthy();
    expect(screen.getByText("第三人称限知,跟女主")).toBeTruthy();
    expect(screen.getByText("前作分析")).toBeTruthy();
    expect(screen.getByText("手改")).toBeTruthy();
    expect(screen.getAllByText(/未提炼/).length).toBe(4);
  });

  it("编辑保存:调 saveStyleDimensions 带六维草稿,旧版进历史的提示可见", async () => {
    vi.mocked(api.getStyleDimensions).mockResolvedValue(mkOut());
    vi.mocked(api.saveStyleDimensions).mockResolvedValue(mkOut({ version: 4 }));
    renderCard();
    fireEvent.click(await screen.findByText("编辑"));
    const boxes = screen.getAllByRole("textbox");
    fireEvent.change(boxes[1]!, { target: { value: "短句为主,紧张段落更碎" } });
    fireEvent.click(screen.getByText(/保存\(旧版进历史\)/));
    await waitFor(() => expect(api.saveStyleDimensions).toHaveBeenCalled());
    const arg = vi.mocked(api.saveStyleDimensions).mock.calls[0]![1];
    expect(arg.rhythm).toBe("短句为主,紧张段落更碎");
  });

  it("历史版本可展开并可回退", async () => {
    vi.mocked(api.getStyleDimensions).mockResolvedValue(mkOut());
    vi.mocked(api.restoreStyleDimensions).mockResolvedValue(mkOut({ version: 4 }));
    renderCard();
    fireEvent.click(await screen.findByText(/历史版本\(1\)/));
    fireEvent.click(screen.getByText("回退到此版"));
    await waitFor(() => expect(api.restoreStyleDimensions).toHaveBeenCalledWith(1, 2));
  });

  it("重新分析走异步任务", async () => {
    vi.mocked(api.getStyleDimensions).mockResolvedValue(mkOut());
    vi.mocked(api.reanalyzeStyleDimensionsAsync).mockResolvedValue({ job_id: "j1" });
    renderCard();
    fireEvent.click(await screen.findByText("重新分析"));
    await waitFor(() => expect(api.reanalyzeStyleDimensionsAsync).toHaveBeenCalledWith(1));
  });
});
