// Prose 任意选区内联润色(Tier 1a):拖选单段内文字 → 直接进 polish 模式对选区润色 →
// 接受时只把段内选中子串写回(applySelectionReplacement),整段其余原样;跨段选区忽略。
// jsdom 支持真实 Range(文本节点 setEnd/toString),仅 stub window.getSelection 指向真实 DOM。
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
const DIR_PLACEHOLDER = "如:更紧张一些 / 去掉 AI 腔";

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

// 在指定 <p> 内构造真实 Range 并 stub getSelection(可跨到另一段的文本节点上验证跨段忽略)
function stubSelection(startP: HTMLElement, from: number, to: number, endP: HTMLElement = startP) {
  const range = document.createRange();
  range.setStart(startP.firstChild!, from);
  range.setEnd(endP.firstChild!, to);
  vi.spyOn(window, "getSelection").mockReturnValue({
    isCollapsed: false, rangeCount: 1, anchorNode: startP.firstChild,
    getRangeAt: () => range, toString: () => range.toString(),
  } as unknown as Selection);
}

describe("Prose 任意选区内联润色", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => { vi.restoreAllMocks(); cleanup(); });

  it("拖选段内文字 → 润色该选区 → 接受只写回子串,整段其余原样", async () => {
    vi.mocked(api.polishFragment).mockResolvedValue({ polished: "飞速", notes: null });
    vi.mocked(api.editChapterContent).mockResolvedValue({
      ...CHAPTER, final_content: "他飞速地跑。\n\n她慢慢地走。",
    });
    const { onSaved } = renderProse();

    const p0 = screen.getByText("他很快地跑。");
    stubSelection(p0, 1, 3); // 选中「很快」
    fireEvent.mouseUp(p0);

    // 进入选区润色:标签显示「选中 2 字」,方向输入框出现(而非整段 pick 菜单)
    await screen.findByText(/选中 2 字/);
    fireEvent.click(screen.getByRole("button", { name: /开始润色/ }));

    // polish-fragment 拿到的是选中的子串,不是整段
    await waitFor(() =>
      expect(api.polishFragment).toHaveBeenCalledWith(1, 1, "很快", ""));

    // diff 屏 → 接受替换:只把段内 [1,3) 换成「飞速」,其余字与段落分隔原样
    fireEvent.click(await screen.findByRole("button", { name: /接受替换/ }));
    await waitFor(() =>
      expect(api.editChapterContent).toHaveBeenCalledWith(1, 1, "他飞速地跑。\n\n她慢慢地走。"));
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("跨段选区忽略:不弹内联润色气泡", async () => {
    renderProse();
    const p0 = screen.getByText("他很快地跑。");
    const p1 = screen.getByText("她慢慢地走。");
    stubSelection(p0, 1, 2, p1); // 从第一段跨到第二段
    fireEvent.mouseUp(p0);

    // 没有进入 polish 模式(方向输入框不出现)
    expect(screen.queryByPlaceholderText(DIR_PLACEHOLDER)).toBeNull();
    expect(api.polishFragment).not.toHaveBeenCalled();
  });
});
