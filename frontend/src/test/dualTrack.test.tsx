// DualTrackEditor(同文双轨)测试:
// 1) 右栏空笔起稿,左栏渲染定稿参照;
// 2) 右栏输入 → 逐段差异统计出现(改动/新增计数 + 字数迁移);
// 3) 载入当前定稿 → 右栏 = 定稿,与定稿无差异;
// 4) 写回:走 editChapterContent 链路 + sync-ask;与定稿相同时拒写回。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import DualTrackEditor from "../panels/write/DualTrackEditor";
import { EditorView } from "@codemirror/view";
import { api, ChapterDetail } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: { ...actual.api, editChapterContent: vi.fn() },
  };
});

const CHAPTER: ChapterDetail = {
  chapter_number: 4, status: "approved", word_count: 12, is_stale: false,
  outline_version_used: 1,
  draft_content: "", final_content: "陆辰摸了摸左耳。\n\n玉佩上刻着寒字七十二。",
};

function renderEditor(overrides?: Partial<Parameters<typeof DualTrackEditor>[0]>) {
  const props = {
    pid: 1, chapter: CHAPTER, genBlocked: false, genHint: "",
    onSaved: vi.fn(), onSyncAsk: vi.fn(), onDirtyChange: vi.fn(), onExit: vi.fn(),
    ...overrides,
  };
  render(<DualTrackEditor {...props} />);
  return props;
}

function getRightView(): EditorView {
  const editorDom = document.querySelector(".dual-right .free-write-host .cm-editor") as HTMLElement;
  const view = EditorView.findFromDOM(editorDom)!;
  expect(view).toBeTruthy();
  return view;
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); });

describe("DualTrackEditor", () => {
  it("右栏空笔起稿,左栏渲染定稿参照", () => {
    renderEditor();
    expect(screen.getByText("陆辰摸了摸左耳。")).toBeTruthy();
    expect(screen.getByText("玉佩上刻着寒字七十二。")).toBeTruthy();
    expect(getRightView().state.doc.toString()).toBe("");
  });

  it("右栏改动后统计出现:逐段差异 + 字数迁移", async () => {
    renderEditor();
    const view = getRightView();
    view.dispatch({ changes: { from: 0, insert: "陆辰摸了摸左耳。\n\n左耳后玉佩刻着「寒字七十二」。\n\n多出来的一段。" } });
    await waitFor(() => expect(screen.getByText(/右稿 3 段/)).toBeTruthy(), { timeout: 800 });
    expect(screen.getByText(/改动 1 /)).toBeTruthy();
    expect(screen.getByText(/新增 1 /)).toBeTruthy();
  });

  it("载入当前定稿:右栏 = 定稿全文,显示无差异", async () => {
    renderEditor();
    fireEvent.click(screen.getByText("载入当前定稿"));
    await waitFor(() => expect(getRightView().state.doc.toString()).toBe(CHAPTER.final_content));
    await waitFor(() => expect(screen.getByText("与定稿无差异")).toBeTruthy(), { timeout: 800 });
  });

  it("写回:改右栏后保存走写回 API + sync-ask + onSaved", async () => {
    const props = renderEditor();
    vi.mocked(api.editChapterContent).mockResolvedValue(CHAPTER);
    const view = getRightView();
    view.dispatch({ changes: { from: 0, insert: "重写后的整章。" } });
    await waitFor(() => expect(props.onDirtyChange).toHaveBeenCalledWith(true));
    fireEvent.click(screen.getByText("写回定稿"));
    await waitFor(() => expect(api.editChapterContent).toHaveBeenCalledWith(1, 4, "重写后的整章。"));
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(CHAPTER));
    expect(props.onSyncAsk).toHaveBeenCalledWith(4);
  });
});
