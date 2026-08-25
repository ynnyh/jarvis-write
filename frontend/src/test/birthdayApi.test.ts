// 生日祝福 API 客户端:请求形状与导出下载。
//
// 为什么单测这一层(同 clipsApi.test 的理由):
// - 只读请求不该带 Content-Type(带了在某些代理上会被当成有 body 的请求);
// - 建单体是寿星资料驱动:memories 为空数组/称呼为空是产品红线,发出的 body
//   必须原样带上这些字段,缺了后端 400、前端却以为建成功了;
// - 导出文件名走 `filename*=UTF-8''` 编码,不解码就是一串百分号;缺头要有兜底名;
// - 导出失败必须给出非空错误消息,否则前端 toast 弹一个空白框。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { birthdayApi } from "../birthdayApi";
import { token } from "../api";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});
// stubGlobal 的清理:restoreAllMocks 只还原 spyOn,不还原全局 stub
afterEach(() => vi.unstubAllGlobals());

function okFetch(payload: unknown = { wish_row: { id: 1 } }) {
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
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn().mockReturnValue("blob:fake");
    static revokeObjectURL = vi.fn();
  });
  return created;
}

describe("请求形状", () => {
  it("列表固定走 /api/birthday(无过滤参数形态)", async () => {
    const mockFetch = okFetch({ wishes: [] });
    await birthdayApi.list();
    expect(mockFetch.mock.calls[0][0]).toBe("/api/birthday");
    expect(mockFetch.mock.calls[0][1].body).toBeUndefined();
  });

  it("建单体原样带寿星资料(称呼/关系/回忆点/风格包是产品红线)", async () => {
    token.set("tk");
    const mockFetch = okFetch();
    await birthdayApi.create({
      honoree_name: "老王", relationship: "friend", milestone: "50岁",
      memories: ["大学时一起在天台看流星雨"], sender_desc: "全部门同事",
      duration_s: 30, pack: "hero", direction: "live",
    });
    const opts = mockFetch.mock.calls[0][1];
    expect(mockFetch.mock.calls[0][0]).toBe("/api/birthday");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(opts.headers["Authorization"]).toBe("Bearer tk");
    expect(JSON.parse(opts.body)).toEqual({
      honoree_name: "老王", relationship: "friend", milestone: "50岁",
      memories: ["大学时一起在天台看流星雨"], sender_desc: "全部门同事",
      duration_s: 30, pack: "hero", direction: "live",
    });
  });

  it("只读请求不带 Content-Type,写请求带 JSON 与鉴权头", async () => {
    token.set("tk");
    const mockFetch = okFetch();

    await birthdayApi.get(7);
    expect(mockFetch.mock.calls[0][1].headers["Content-Type"]).toBeUndefined();

    await birthdayApi.pick(7, 2);
    const opts = mockFetch.mock.calls[1][1];
    expect(mockFetch.mock.calls[1][0]).toBe("/api/birthday/7/pick");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opts.body)).toEqual({ index: 2 });
  });

  it("手卡保存/参数补丁打到对应端点", async () => {
    const mockFetch = okFetch();
    await birthdayApi.saveCard(7, {
      take: "x", logline: "", emotion_curve: "", lines: [], shots: [],
      punchline: "", chunks: [], hook_text: "", cautions: [],
    });
    expect(mockFetch.mock.calls[0][0]).toBe("/api/birthday/7/card");
    expect(JSON.parse(mockFetch.mock.calls[0][1].body).card.take).toBe("x");

    await birthdayApi.patch(7, { duration_s: 60 });
    expect(mockFetch.mock.calls[1][0]).toBe("/api/birthday/7");
    expect(mockFetch.mock.calls[1][1].method).toBe("PATCH");
  });

  it("失败时抛后端的人话 detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 400, json: async () => ({ detail: "至少给 1 条回忆点——没有回忆点就没有定制感" }),
    }));
    await expect(birthdayApi.create({
      honoree_name: "老王", relationship: "friend", memories: [],
      duration_s: 30, direction: "live",
    })).rejects.toThrow("没有回忆点就没有定制感");
  });
});

describe("导出下载", () => {
  it("文件名做百分号解码", async () => {
    const created = catchAnchors();
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: { get: () => "attachment; filename*=UTF-8''%E8%80%81%E7%8E%8B%E7%94%9F%E6%97%A5-7-%E6%89%8B%E5%8D%A1.md" },
      blob: async () => new Blob(["x"]),
    });
    vi.stubGlobal("fetch", mockFetch);

    await birthdayApi.export(7, "md");

    expect(mockFetch.mock.calls[0][0]).toBe("/api/birthday/7/export?format=md");
    expect(created[0].download).toBe("老王生日-7-手卡.md");
  });

  it("缺 Content-Disposition 时兜底 wish.<格式>", async () => {
    const created = catchAnchors();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true, headers: { get: () => null }, blob: async () => new Blob(["x"]),
    }));

    await birthdayApi.export(7, "srt");

    expect(created[0].download).toBe("wish.srt");
  });

  it("导出失败的错误消息不许是空串(toast 弹空白框等于没提示)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 500, json: async () => ({}),
    }));
    await expect(birthdayApi.export(7, "json")).rejects.toThrow(/HTTP 500/);
  });
});
