// 跨章标记/全书批修 + 桥段台账/雷区 API 客户端测试:请求形状与鉴权(同 seriesApi.test 的理由):
// - 记标记的 chapter_number/para_idx/snapshot 是失效判定的产品红线,必须原样进 body;
// - 全书批修的 directive 是唯一的统一指令入口,丢字 = 全书按错误方向改;
// - 雷区标签走 query/path 时要 encodeURIComponent(中文标签不编码会打出乱码请求)。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, token } from "../api";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});
afterEach(() => vi.unstubAllGlobals());

function okFetch(payload: unknown = { ok: true }) {
  const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
  vi.stubGlobal("fetch", mockFetch);
  return mockFetch;
}

describe("跨章标记 + 全书批修", () => {
  it("marks 清单是只读 GET,不带 Content-Type", async () => {
    const mockFetch = okFetch([]);
    await api.marks(7);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/projects/7/marks");
    expect(mockFetch.mock.calls[0][1].body).toBeUndefined();
  });

  it("addMark 原样带 chapter_number/para_idx/snapshot/note(失效判定的依据)", async () => {
    token.set("tk");
    const mockFetch = okFetch({ id: 1, chapter_number: 3, para_idx: 2, snapshot: "s", note: "n" });
    await api.addMark(7, { chapter_number: 3, para_idx: 2, snapshot: "段落原文", note: "意象重复" });
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/projects/7/marks");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Authorization"]).toBe("Bearer tk");
    expect(JSON.parse(opts.body)).toEqual({
      chapter_number: 3, para_idx: 2, snapshot: "段落原文", note: "意象重复",
    });
  });

  it("removeMark 走 DELETE /marks/{id}", async () => {
    const mockFetch = okFetch({ ok: true });
    await api.removeMark(7, 42);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/projects/7/marks/42");
    expect(mockFetch.mock.calls[0][1].method).toBe("DELETE");
  });

  it("marksReviseAsync 原样带 directive(全书批修的唯一统一指令)", async () => {
    const mockFetch = okFetch({ job_id: "j1" });
    await api.marksReviseAsync(7, "所有铁锈玫瑰的描写全部换掉");
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/projects/7/marks/revise-async");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ directive: "所有铁锈玫瑰的描写全部换掉" });
  });
});

describe("桥段台账 + 雷区", () => {
  it("addBannedMotif 原样带 label/detail;promote 带 label", async () => {
    const mockFetch = okFetch({ id: 1, label: "铁锈玫瑰", detail: "" });
    await api.addBannedMotif(7, "铁锈玫瑰", "写烦了");
    await api.promoteMotif(7, "躺下等天亮");
    expect(mockFetch.mock.calls[0][0]).toBe("/api/projects/7/motifs/banned");
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ label: "铁锈玫瑰", detail: "写烦了" });
    expect(mockFetch.mock.calls[1][0]).toBe("/api/projects/7/motifs/banned/promote");
    expect(JSON.parse(mockFetch.mock.calls[1][1].body)).toEqual({ label: "躺下等天亮" });
  });

  it("clearLedgerMotif 的中文标签要 encodeURIComponent", async () => {
    const mockFetch = okFetch({ removed: 2 });
    await api.clearLedgerMotif(7, "铁锈玫瑰");
    expect(mockFetch.mock.calls[0][0]).toBe(
      `/api/projects/7/motifs/ledger?label=${encodeURIComponent("铁锈玫瑰")}`,
    );
    expect(mockFetch.mock.calls[0][1].method).toBe("DELETE");
  });

  it("scanMotifsAsync 走 scan-async POST", async () => {
    const mockFetch = okFetch({ job_id: "j2" });
    await api.scanMotifsAsync(7);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/projects/7/motifs/scan-async");
    expect(mockFetch.mock.calls[0][1].method).toBe("POST");
  });
});
