// 公开分享阅读页单测:免登录 fetch(/api/public/shares/:token)。
// 钉住三态(加载中 / 成功 / 失败)+ 多章目录切换 + 请求地址带 token。
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import SharePage from "../pages/SharePage";

function renderAt(tok = "tok-1") {
  render(
    <MemoryRouter initialEntries={[`/share/${tok}`]}>
      <Routes>
        <Route path="/share/:token" element={<SharePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function jsonRes(body: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => body } as unknown as Response;
}

describe("SharePage", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("请求未回来时显示加载态", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    renderAt();
    expect(screen.getByText("加载中…")).toBeTruthy();
  });

  it("成功:渲染书名/正文,多章时目录可切换章节", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonRes({
      scope: "book",
      book_title: "破封纪",
      genre: "修仙",
      chapters: [
        { number: 1, title: "破封", content: "第一段\n第二段" },
        { number: 2, title: "入山", content: "第二章正文" },
      ],
    }));
    vi.stubGlobal("fetch", fetchMock);
    renderAt("tok-9");

    expect(await screen.findByText("破封纪")).toBeTruthy();
    // 请求打的是免登录公开接口,token 来自路由参数
    expect(fetchMock).toHaveBeenCalledWith("/api/public/shares/tok-9");
    expect(screen.getByText("第一段")).toBeTruthy();
    expect(screen.getByText("第二段")).toBeTruthy();

    // 目录两项 → 点第二章,正文切换
    fireEvent.click(screen.getByRole("button", { name: /第2章/ }));
    await waitFor(() => expect(screen.getByText("第二章正文")).toBeTruthy());
    expect(screen.queryByText("第一段")).toBeNull();
  });

  it("失败:透出后端 detail 文案", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonRes({ detail: "分享不存在或已撤销" }, false, 404),
    ));
    renderAt();
    expect(await screen.findByText(/分享不存在或已撤销/)).toBeTruthy();
  });
});
