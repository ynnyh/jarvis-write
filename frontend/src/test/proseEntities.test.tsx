// 正文实体链接(Tier 1b):故事圣经人物名在正文里高亮成 .entity-link,hover 浮出人物卡(简介/事实);
// 关键回归:高亮 span 插入段内后,拖选偏移仍按段落文本计算(offsetInPara 量文本长度,不依赖节点结构)。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Prose from "../panels/write/Prose";
import { api, ChapterDetail, CharacterCard } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      editorialActions: vi.fn().mockResolvedValue({ prose: [] }),
      characters: vi.fn(),
      polishFragment: vi.fn(),
      editChapterContent: vi.fn(),
    },
  };
});
vi.mock("../desktop", () => ({ emitChapterSaved: vi.fn() }));

// 段0: 林(0)渊(1)拔(2)出(3)了(4)剑(5)。(6)
const CHAPTER: ChapterDetail = {
  chapter_number: 1, status: "approved", word_count: 12, is_stale: false,
  draft_content: "", final_content: "林渊拔出了剑。\n\n然后他离开了。", outline_version_used: 1,
};
const LINYUAN: CharacterCard = {
  id: 1, name: "林渊", aliases: ["小渊"], entity_type: "character", retired: false,
  profile: "少年剑客,性格孤僻。",
  key_facts: [{ id: 9, fact_type: "trait", content: "擅长御剑术", valid_from: 1, valid_until: null, importance: "major" }],
  appearance_chapters: [1], relations: [],
};

function renderProse() {
  const onSaved = vi.fn();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <Prose pid={1} chapter={CHAPTER} genBlocked={false} genHint=""
        onSaved={onSaved} onSyncAsk={vi.fn()} />
    </QueryClientProvider>,
  );
  return { onSaved, ...utils };
}

describe("Prose 正文实体链接", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => { vi.restoreAllMocks(); cleanup(); });

  it("命中人物名高亮成 entity-link,hover 浮出人物卡(简介/事实);无关文字不高亮", async () => {
    vi.mocked(api.characters).mockResolvedValue({ characters: [LINYUAN], other_entities_count: 0 });
    const { container } = renderProse();

    // 数据到位后,正文中的「林渊」被包成 .entity-link(且全篇只此一处命中)
    const link = await screen.findByText("林渊");
    expect(link).toHaveClass("entity-link");
    expect(container.querySelectorAll(".entity-link")).toHaveLength(1);

    // hover 浮出人物卡:含简介与关键事实与类型徽章
    fireEvent.mouseEnter(link);
    expect(await screen.findByText("少年剑客,性格孤僻。")).toBeInTheDocument();
    expect(screen.getByText("擅长御剑术")).toBeInTheDocument();
    expect(screen.getByText("人物")).toBeInTheDocument();
  });

  it("高亮 span 插入后,段内拖选偏移仍按段落文本计算(选到的是「拔出」而非串位)", async () => {
    vi.mocked(api.characters).mockResolvedValue({ characters: [LINYUAN], other_entities_count: 0 });
    vi.mocked(api.polishFragment).mockResolvedValue({ polished: "抽出", notes: null });
    vi.mocked(api.editChapterContent).mockResolvedValue({
      ...CHAPTER, final_content: "林渊抽出了剑。\n\n然后他离开了。",
    });
    const { container, onSaved } = renderProse();

    await screen.findByText("林渊"); // 等高亮渲染完:段0 = [span「林渊」, 文本「拔出了剑。」]
    const p0 = container.querySelectorAll("p")[0];
    const textNode = p0.childNodes[1]; // 「林渊」之后的纯文本节点
    const range = document.createRange();
    range.setStart(textNode, 0); // 段内偏移 2(「林渊」之后)
    range.setEnd(textNode, 2);   // 段内偏移 4 → 选中「拔出」
    vi.spyOn(window, "getSelection").mockReturnValue({
      isCollapsed: false, rangeCount: 1, anchorNode: textNode,
      getRangeAt: () => range, toString: () => range.toString(),
    } as unknown as Selection);
    fireEvent.mouseUp(p0);

    await screen.findByText(/选中 2 字/);
    fireEvent.click(screen.getByRole("button", { name: /开始润色/ }));
    // 选中子串是「拔出」——证明高亮 span 未使偏移串位
    await waitFor(() => expect(api.polishFragment).toHaveBeenCalledWith(1, 1, "拔出", ""));

    fireEvent.click(await screen.findByRole("button", { name: /接受替换/ }));
    await waitFor(() =>
      expect(api.editChapterContent).toHaveBeenCalledWith(1, 1, "林渊抽出了剑。\n\n然后他离开了。"));
    expect(onSaved).toHaveBeenCalledTimes(1);
  });
});
