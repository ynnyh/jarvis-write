// Prose 章尾续写 ghost text(Tier 2b):selPara===null 时章尾出现「续写一段」,
// 点/Tab → continueChapter 拿灰字续文 → 接受作为新段落追加写回;放弃则收起不落库。
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
      continueChapter: vi.fn(),
      editChapterContent: vi.fn(),
    },
  };
});
vi.mock("../desktop", () => ({ emitChapterSaved: vi.fn() }));

const BODY = "他很快地跑。\n\n她慢慢地走。";
const CHAPTER: ChapterDetail = {
  chapter_number: 1, status: "approved", word_count: 12, is_stale: false,
  draft_content: "", final_content: BODY, outline_version_used: 1,
};
// 接受追加后写回的整章正文:原文末尾 + 空行 + 续文
const APPENDED = BODY + "\n\n他停在了路口。";

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

describe("Prose 章尾续写 ghost text", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => { vi.restoreAllMocks(); cleanup(); });

  it("章尾有「续写一段」;点它 → continueChapter → 灰字续文出现", async () => {
    vi.mocked(api.continueChapter).mockResolvedValue({ continuation: "他停在了路口。" });
    renderProse();

    fireEvent.click(await screen.findByRole("button", { name: /续写一段/ }));
    await waitFor(() => expect(api.continueChapter).toHaveBeenCalledWith(1, 1));
    // 灰字续文 + 接受/换一段/放弃 三键
    expect(await screen.findByText("他停在了路口。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /接受追加/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /换一段/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /放弃/ })).toBeInTheDocument();
  });

  it("接受追加:续文作为新段落追加到章末并落库", async () => {
    vi.mocked(api.continueChapter).mockResolvedValue({ continuation: "他停在了路口。" });
    vi.mocked(api.editChapterContent).mockResolvedValue({ ...CHAPTER, final_content: APPENDED });
    const { onSaved } = renderProse();

    fireEvent.click(await screen.findByRole("button", { name: /续写一段/ }));
    fireEvent.click(await screen.findByRole("button", { name: /接受追加/ }));
    await waitFor(() =>
      expect(api.editChapterContent).toHaveBeenCalledWith(1, 1, APPENDED));
    expect(onSaved).toHaveBeenCalledTimes(1);
    // 落库后 ghost 收起:回到「续写一段」,接受键消失
    expect(await screen.findByRole("button", { name: /续写一段/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /接受追加/ })).toBeNull();
  });

  it("Tab 键 = 接受追加", async () => {
    vi.mocked(api.continueChapter).mockResolvedValue({ continuation: "他停在了路口。" });
    vi.mocked(api.editChapterContent).mockResolvedValue({ ...CHAPTER, final_content: APPENDED });
    renderProse();

    fireEvent.click(await screen.findByRole("button", { name: /续写一段/ }));
    const accept = await screen.findByRole("button", { name: /接受追加/ });
    fireEvent.keyDown(accept, { key: "Tab" });
    await waitFor(() =>
      expect(api.editChapterContent).toHaveBeenCalledWith(1, 1, APPENDED));
  });

  it("放弃:ghost 收起,不写回正文", async () => {
    vi.mocked(api.continueChapter).mockResolvedValue({ continuation: "他停在了路口。" });
    renderProse();

    fireEvent.click(await screen.findByRole("button", { name: /续写一段/ }));
    fireEvent.click(await screen.findByRole("button", { name: /放弃/ }));
    await waitFor(() => expect(screen.queryByText("他停在了路口。")).toBeNull());
    expect(api.editChapterContent).not.toHaveBeenCalled();
    // 收起后「续写一段」仍在,可再次发起
    expect(screen.getByRole("button", { name: /续写一段/ })).toBeInTheDocument();
  });
});
