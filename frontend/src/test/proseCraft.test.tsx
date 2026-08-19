// Prose 选区 craft 微工具(Tier 2a):改这段工作台顶部工具行(润色/描写/扩写/脑暴)。
// - describe/expand:调 craftFragment 拿 rewrite → 复用 diff→接受→子串写回链路;
// - brainstorm:调 craftFragment 拿 ideas → 列表展示,不写回正文。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Prose from "../panels/write/Prose";
import { api, ChapterDetail } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      editorialActions: vi.fn().mockResolvedValue({ prose: [] }),
      characters: vi.fn().mockResolvedValue({ characters: [], other_entities_count: 0 }),
      craftFragment: vi.fn(),
      polishFragment: vi.fn(),
      editChapterContent: vi.fn(),
    },
  };
});
vi.mock("../desktop", () => ({ emitChapterSaved: vi.fn() }));

const CHAPTER: ChapterDetail = {
  chapter_number: 1, status: "approved", word_count: 12, is_stale: false,
  draft_content: "", final_content: "他很快地跑。\n\n她慢慢地走。", outline_version_used: 1,
};

function renderProse() {
  const onSaved = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Prose pid={1} chapter={CHAPTER} genBlocked={false} genHint=""
        onSaved={onSaved} onSyncAsk={vi.fn()} />
    </QueryClientProvider>,
  );
  return { onSaved };
}

// 拖选 startP 段内 [from,to) 文字,stub getSelection 指向真实 Range
function stubSelection(startP: HTMLElement, from: number, to: number) {
  const range = document.createRange();
  range.setStart(startP.firstChild!, from);
  range.setEnd(startP.firstChild!, to);
  vi.spyOn(window, "getSelection").mockReturnValue({
    isCollapsed: false, rangeCount: 1, anchorNode: startP.firstChild,
    getRangeAt: () => range, toString: () => range.toString(),
  } as unknown as Selection);
}

// 选中「很快」后进入工作台
function selectHenKuai() {
  const p0 = screen.getByText("他很快地跑。");
  stubSelection(p0, 1, 3); // 「很快」
  fireEvent.mouseUp(p0);
}

describe("Prose 选区 craft 微工具", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => { vi.restoreAllMocks(); cleanup(); });

  it("工作台顶部有 润色/描写/扩写/脑暴 工具行", async () => {
    renderProse();
    selectHenKuai();
    await screen.findByText(/选中 2 字/);
    for (const label of ["润色", "描写", "扩写", "脑暴"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    // 默认停在润色:方向输入 + 开始润色仍在(不破坏 Tier 1a)
    expect(screen.getByRole("button", { name: /开始润色/ })).toBeInTheDocument();
  });

  it("扩写:craftFragment 拿改写稿 → diff → 接受只写回选中子串", async () => {
    vi.mocked(api.craftFragment).mockResolvedValue({
      mode: "expand", rewrite: "非常迅速", ideas: null, notes: null,
    });
    vi.mocked(api.editChapterContent).mockResolvedValue({
      ...CHAPTER, final_content: "他非常迅速地跑。\n\n她慢慢地走。",
    });
    const { onSaved } = renderProse();
    selectHenKuai();
    await screen.findByText(/选中 2 字/);

    // 切到「扩写」→ 生成扩写(mode=expand,note 空)
    fireEvent.click(screen.getByRole("button", { name: "扩写" }));
    fireEvent.click(screen.getByRole("button", { name: /生成扩写/ }));
    await waitFor(() =>
      expect(api.craftFragment).toHaveBeenCalledWith(1, 1, "很快", "expand", ""));

    // diff 屏 → 接受替换:段内 [1,3) 换成「非常迅速」,其余原样
    fireEvent.click(await screen.findByRole("button", { name: /接受替换/ }));
    await waitFor(() =>
      expect(api.editChapterContent).toHaveBeenCalledWith(1, 1, "他非常迅速地跑。\n\n她慢慢地走。"));
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("脑暴:craftFragment 拿点子列表 → 展示,不写回正文", async () => {
    vi.mocked(api.craftFragment).mockResolvedValue({
      mode: "brainstorm", rewrite: null, ideas: ["让他摔一跤", "补一句心理描写"], notes: null,
    });
    renderProse();
    selectHenKuai();
    await screen.findByText(/选中 2 字/);

    fireEvent.click(screen.getByRole("button", { name: "脑暴" }));
    fireEvent.click(screen.getByRole("button", { name: /想点子/ }));
    await waitFor(() =>
      expect(api.craftFragment).toHaveBeenCalledWith(1, 1, "很快", "brainstorm", ""));

    // 点子逐条展示,且不触发任何写回
    expect(await screen.findByText("让他摔一跤")).toBeInTheDocument();
    expect(screen.getByText("补一句心理描写")).toBeInTheDocument();
    expect(api.editChapterContent).not.toHaveBeenCalled();
    // 脑暴不写回:无「接受替换」按钮
    expect(screen.queryByRole("button", { name: /接受替换/ })).toBeNull();
  });
});
