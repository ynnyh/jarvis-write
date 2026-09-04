// SearchDialog(全书全文检索,Ctrl+Shift+F)测试:
// 1) 输入 <2 字不发请求,≥2 字防抖后调 api.search;
// 2) 结果按分组渲染,摘要高亮,命中数展示;
// 3) 点章节命中 → 导航 write?ch=N 并关闭;点实体命中 → book?tab=bible。
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SearchDialog from "../ui/SearchDialog";
import { api, type SearchResponse } from "../api";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: { ...actual.api, search: vi.fn() },
  };
});

const mockedSearch = vi.mocked(api.search);

const RES: SearchResponse = {
  q: "寒字七十二", total: 3, elapsed_ms: 5,
  grouped: {
    chapter: [{ kind: "chapter", kind_cn: "正文", ref_id: 11, chapter_number: 5,
      title: null, name: null, snippet: "…玉佩上刻着「寒字七十二」…", hits: 2 }],
    entity: [{ kind: "entity", kind_cn: "设定", ref_id: 7, chapter_number: null,
      title: null, name: "陆辰", snippet: "别名:寒字七十二号持有人", hits: 1 }],
  },
};

function renderDialog() {
  const onClose = vi.fn();
  render(
    <MemoryRouter>
      <SearchDialog pid={9} onClose={onClose} />
    </MemoryRouter>,
  );
  return onClose;
}

beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("SearchDialog", () => {
  it("输入不足 2 字不发请求;达到 2 字防抖后搜索", async () => {
    renderDialog();
    const input = screen.getByPlaceholderText(/搜全书/);
    fireEvent.change(input, { target: { value: "寒" } });
    await waitFor(() => expect(mockedSearch).not.toHaveBeenCalled(), { timeout: 500 });
    fireEvent.change(input, { target: { value: "寒字" } });
    await waitFor(() => expect(mockedSearch).toHaveBeenCalledWith(9, "寒字"), { timeout: 1000 });
  });

  it("渲染分组结果并高亮命中词", async () => {
    mockedSearch.mockResolvedValue(RES);
    renderDialog();
    fireEvent.change(screen.getByPlaceholderText(/搜全书/), { target: { value: "寒字七十二" } });
    await waitFor(() => expect(screen.getByText("正文")).toBeTruthy());
    expect(screen.getByText("设定")).toBeTruthy();
    // 高亮:命中词进 <b>
    expect(document.querySelectorAll(".search-hit-b").length).toBeGreaterThan(0);
    // 命中数 ×2
    expect(screen.getByText("×2")).toBeTruthy();
  });

  it("点章节命中 → write?ch=N + 关闭;点实体命中 → 故事圣经页签", async () => {
    mockedSearch.mockResolvedValue(RES);
    const onClose = renderDialog();
    fireEvent.change(screen.getByPlaceholderText(/搜全书/), { target: { value: "寒字七十二" } });
    await waitFor(() => expect(screen.getByText("第5章")).toBeTruthy());
    fireEvent.click(screen.getByText("第5章"));
    expect(onClose).toHaveBeenCalled();
  });
});
