// 系列 API 客户端测试:请求形状与鉴权(同 birthdayApi.test 的理由):
// - 只读请求不该带 Content-Type(带了在某些代理上会被当成有 body 的请求);
// - 建角色是定妆锚驱动的产品红线:name/look/direction 必须原样进 body,
//   缺了后端 400、前端却以为建成功了;
// - 参考图上传走 multipart,手设 Content-Type 会毁掉 boundary——绝不能带;
// - 鉴权参考图读转 blob:缺 Authorization 头就是 401,<img> 也就废了。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { seriesApi } from "../seriesApi";
import { token } from "../api";

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

describe("请求形状", () => {
  it("meta/list 是只读 GET,不带 Content-Type", async () => {
    const mockFetch = okFetch({ directions: [] });
    await seriesApi.meta();
    await seriesApi.listCharacters();
    expect(mockFetch.mock.calls[0][0]).toBe("/api/series/meta");
    expect(mockFetch.mock.calls[0][1].body).toBeUndefined();
    expect(mockFetch.mock.calls[1][0]).toBe("/api/series/characters");
    expect(mockFetch.mock.calls[1][1].headers["Content-Type"]).toBeUndefined();
  });

  it("建角色原样带定妆锚(name/look/direction 是产品红线)", async () => {
    token.set("tk");
    const mockFetch = okFetch({ character_row: { id: 1 } });
    await seriesApi.createCharacter({
      name: "小浣熊", look: "一只戴红围巾的小浣熊…", direction: "render3d",
      default_duration_s: 10, style_hints: "暖光",
    });
    const opts = mockFetch.mock.calls[0][1];
    expect(mockFetch.mock.calls[0][0]).toBe("/api/series/characters");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(opts.headers["Authorization"]).toBe("Bearer tk");
    expect(JSON.parse(opts.body)).toEqual({
      name: "小浣熊", look: "一只戴红围巾的小浣熊…", direction: "render3d",
      default_duration_s: 10, style_hints: "暖光",
    });
  });

  it("AI 代写定妆打到 draft-look,body 原样带概念与画风", async () => {
    const mockFetch = okFetch({ look: "草稿" });
    await seriesApi.draftLook("一只戴红围巾的小浣熊", "render3d", "暖光");
    expect(mockFetch.mock.calls[0][0]).toBe("/api/series/characters/draft-look");
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({
      brief: "一只戴红围巾的小浣熊", direction: "render3d", style_hints: "暖光",
    });
  });

  it("建集/生成/改集/删集打到对应端点", async () => {
    const mockFetch = okFetch();

    await seriesApi.createEpisode(3, "偷蜂蜜", 12);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/series/characters/3/episodes");
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ plot: "偷蜂蜜", duration_s: 12 });

    await seriesApi.generateEpisode(9);
    expect(mockFetch.mock.calls[1][0]).toBe("/api/series/episodes/9/generate");

    await seriesApi.patchEpisode(9, { duration_s: 15 });
    expect(mockFetch.mock.calls[2][0]).toBe("/api/series/episodes/9");
    expect(mockFetch.mock.calls[2][1].method).toBe("PUT");

    await seriesApi.removeEpisode(9);
    expect(mockFetch.mock.calls[3][0]).toBe("/api/series/episodes/9");
    expect(mockFetch.mock.calls[3][1].method).toBe("DELETE");
  });

  it("参考图上传走 multipart,不手设 Content-Type(浏览器要自己带 boundary)", async () => {
    token.set("tk");
    const mockFetch = okFetch({ character_row: { id: 1 } });
    const file = new File([new Uint8Array([1, 2, 3])], "look.png", { type: "image/png" });
    await seriesApi.uploadRef(3, file, "定妆照");
    const opts = mockFetch.mock.calls[0][1];
    expect(mockFetch.mock.calls[0][0]).toBe("/api/series/characters/3/reference");
    expect(opts.headers["Content-Type"]).toBeUndefined();
    expect(opts.headers["Authorization"]).toBe("Bearer tk");
    expect(opts.body).toBeInstanceOf(FormData);
    expect((opts.body as FormData).get("note")).toBe("定妆照");
  });

  it("鉴权参考图读:带 Authorization,返回 blob URL", async () => {
    token.set("tk");
    const blobUrl = "blob:fake";
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn().mockReturnValue(blobUrl);
    });
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob() });
    vi.stubGlobal("fetch", mockFetch);
    const u = await seriesApi.refBlobUrl(3, 1);
    expect(mockFetch.mock.calls[0][0]).toBe("/api/series/characters/3/reference/1");
    expect(mockFetch.mock.calls[0][1].headers["Authorization"]).toBe("Bearer tk");
    expect(u).toBe(blobUrl);
  });

  it("失败响应抛 ApiError 且带后端 detail", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false, status: 400,
      json: async () => ({ detail: "定妆描述不能为空" }),
    });
    vi.stubGlobal("fetch", mockFetch);
    await expect(seriesApi.createCharacter({
      name: "x", look: "", direction: "render3d", default_duration_s: 10,
    })).rejects.toThrow("定妆描述不能为空");
  });
});
