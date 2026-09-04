// FreeWriteEditor(自由改稿,CodeMirror 6)冒烟测试:
// 1) 挂载后编辑器里有本章正文;
// 2) 改文档 → 保存走 editChapterContent 写回链路 + 触发 sync-ask;
// 3) genBlocked 时保存按钮禁用。
// CM6 在 jsdom 下可正常建 view(测量类插件降级为 0 尺寸,不影响文档操作)。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import FreeWriteEditor from "../panels/write/FreeWriteEditor";
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
  chapter_number: 3, status: "approved", word_count: 10, is_stale: false,
  draft_content: "", final_content: "第一段。\n\n第二段。", outline_version_used: 1,
};

function renderEditor(overrides?: Partial<Parameters<typeof FreeWriteEditor>[0]>) {
  const props = {
    pid: 1, chapter: CHAPTER, genBlocked: false, genHint: "",
    onSaved: vi.fn(), onSyncAsk: vi.fn(), onDirtyChange: vi.fn(),
    onExit: vi.fn(), ...overrides,
  };
  render(<FreeWriteEditor {...props} />);
  return props;
}

describe("FreeWriteEditor", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => { cleanup(); });

  it("挂载后编辑器载入本章正文", () => {
    renderEditor();
    const content = document.querySelector(".free-write-host .cm-content")!;
    expect(content.textContent).toContain("第二段。");
    expect(screen.getByText(/字/)).toBeTruthy();
  });

  it("改文档后保存:走写回 API 并触发 sync-ask 与 onSaved", async () => {
    const props = renderEditor();
    vi.mocked(api.editChapterContent).mockResolvedValue(CHAPTER);
    // CM 官方通路:EditorView.findFromDOM 从 .cm-editor 元素找回 view 实例
    const editorDom = document.querySelector(".free-write-host .cm-editor") as HTMLElement;
    const view = EditorView.findFromDOM(editorDom)!;
    expect(view).toBeTruthy();
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: "重写后的整章。" } });
    await waitFor(() => expect(props.onDirtyChange).toHaveBeenCalledWith(true));
    fireEvent.click(screen.getByText("保存本章"));
    await waitFor(() => expect(api.editChapterContent).toHaveBeenCalledWith(1, 3, "重写后的整章。"));
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(CHAPTER));
    expect(props.onSyncAsk).toHaveBeenCalledWith(3);
    expect(props.onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it("genBlocked 时保存按钮禁用且出现只读提示", () => {
    renderEditor({ genBlocked: true, genHint: "生成中,稍等" });
    const saveBtn = screen.getByText("保存本章").closest("button") as HTMLButtonElement;
    expect(saveBtn.disabled).toBe(true);
    expect(screen.getByText(/生成中,稍等/)).toBeTruthy();
  });
});
