// 情绪短片 API 客户端:query 拼装与导出下载。
//
// 为什么单测这一层:
// - 列表有两种用法——通用入口(全部)与小说项目内的「投流」页签(只看这本书衍生的)。
//   project_id 漏了/多了都不报错,只是默默列错东西,所以两种形态都钉住;
// - 只读请求不该带 Content-Type(带了在某些代理上会被当成有 body 的请求);
// - 导出文件名走 `filename*=UTF-8''` 编码,不解码就是一串百分号;缺头要有兜底名;
// - 导出失败必须给出非空错误消息,否则前端 toast 弹一个空白框(等于什么都没说)。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clipsApi } from "../clipsApi";
import { token } from "../api";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});
// stubGlobal 的清理:restoreAllMocks 只还原 spyOn,不还原全局 stub——
// 不补这一句,本文件的 fetch/URL stub 会串进同进程后续所有测试文件
afterEach(() => vi.unstubAllGlobals());

function okFetch(payload: unknown = { clip_row: { id: 1 } }) {
  const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => payload });
  vi.stubGlobal("fetch", mockFetch);
  return mockFetch;
}

/** 捕获导出时创建的 <a>(download 属性是唯一能验证文件名的地方) */
function catchAnchors() {
  const created: HTMLAnchorElement[] = [];
  const realCreate = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
    const el = realCreate(tag);
    if (tag === "a") created.push(el as HTMLAnchorElement);
    return el;
  });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  // 用子类挂 mock:直接 Object.assign(URL,…) 是在改真实构造器,mock 会永久留在全局
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn().mockReturnValue("blob:fake");
    static revokeObjectURL = vi.fn();
  });
  return created;
}

describe("列表与请求形状", () => {
  it("通用入口不带 project_id,项目内「投流」页签带上(否则列成别人的)", async () => {
    const mockFetch = okFetch({ clips: [] });

    await clipsApi.list();
    expect(mockFetch.mock.calls[0][0]).toBe("/api/clips");

    await clipsApi.list(3);
    expect(mockFetch.mock.calls[1][0]).toBe("/api/clips?project_id=3");
  });

  it("只读请求不带 Content-Type,写请求带 JSON 与鉴权头", async () => {
    token.set("tk");
    const mockFetch = okFetch();

    await clipsApi.get(7);
    expect(mockFetch.mock.calls[0][1].headers["Content-Type"]).toBeUndefined();
    expect(mockFetch.mock.calls[0][1].body).toBeUndefined();

    await clipsApi.create({ theme: "regret", duration_s: 15, direction: "anime" });
    const opts = mockFetch.mock.calls[1][1];
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(opts.headers["Authorization"]).toBe("Bearer tk");
    expect(JSON.parse(opts.body)).toEqual({ theme: "regret", duration_s: 15, direction: "anime" });
  });

  it("选定本子把下标发给后端(三选一的定稿口径在服务端)", async () => {
    const mockFetch = okFetch();
    await clipsApi.pick(7, 2);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/clips/7/pick");
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ index: 2 });
  });

  it("失败时抛后端的人话 detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 400, json: async () => ({ detail: "先产本子再选定" }),
    }));
    await expect(clipsApi.pick(7, 0)).rejects.toThrow("先产本子再选定");
  });
});

describe("导出下载", () => {
  it("文件名做百分号解码", async () => {
    const created = catchAnchors();
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: { get: () => "attachment; filename*=UTF-8''%E5%90%8E%E6%82%94.md" },
      blob: async () => new Blob(["x"]),
    });
    vi.stubGlobal("fetch", mockFetch);

    await clipsApi.export(7, "md");

    expect(mockFetch.mock.calls[0][0]).toBe("/api/clips/7/export?format=md");
    expect(created[0].download).toBe("后悔.md");
  });

  it("缺 Content-Disposition 时兜底 clip.<格式>", async () => {
    const created = catchAnchors();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true, headers: { get: () => null }, blob: async () => new Blob(["x"]),
    }));

    await clipsApi.export(7, "srt");

    expect(created[0].download).toBe("clip.srt");
  });

  it("导出失败的错误消息不许是空串(toast 弹空白框等于没提示)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 500, json: async () => ({}), // 后端没给 detail 的情况
    }));
    await expect(clipsApi.export(7, "json")).rejects.toThrow(/HTTP 500/);
  });
});
