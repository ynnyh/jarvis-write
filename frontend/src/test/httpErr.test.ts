// http 层错误翻译:非字符串 detail(典型:FastAPI 422 校验错误是数组)也要翻成人话,
// 不许 "[object Object]" 上屏——2026-10-20 方向卡页实踩的病根就在 failResponse。
import { describe, it, expect, vi, afterEach } from "vitest";
import { req } from "../http";

afterEach(() => vi.unstubAllGlobals());

describe("req 错误翻译", () => {
  it("422 校验错误(detail 为数组)reject 的 message 是可读 JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: [{ loc: ["body", "answers"], msg: "Input should be a valid string" }] }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    )));
    await expect(req("POST", "/api/projects/1/book-plans", {}))
      .rejects.toThrow(/Input should be a valid string/);
  });

  it("字符串 detail 原样透传", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: "类型不在目录里,请从下拉里选。" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    )));
    await expect(req("POST", "/api/x", {})).rejects.toThrow("类型不在目录里");
  });

  it("非 JSON 错误体回落到 HTTP 状态码", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Bad Gateway", { status: 502 })));
    await expect(req("GET", "/api/y")).rejects.toThrow("HTTP 502");
  });
});
