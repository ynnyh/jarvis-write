// http 层错误翻译:非字符串 detail(典型:FastAPI 422 校验错误是数组)也要翻成人话,
// 不许 "[object Object]" 上屏——2026-10-20 方向卡页实踩的病根就在 failResponse。
// NO_PROVIDER 内联引导:撞上「未配置写作渠道」→ 弹配置弹窗 → 保存后自动重放原请求。
import { describe, it, expect, vi, afterEach } from "vitest";
import {
  ApiError,
  NO_PROVIDER_MARKER,
  ProviderSetupCancelled,
  req,
  requestProviderSetup,
  setProviderSetupHandler,
} from "../http";

afterEach(() => {
  vi.unstubAllGlobals();
  setProviderSetupHandler(null as unknown as () => Promise<void>);
});

function noKeyResponse(): Response {
  return new Response(
    JSON.stringify({ detail: { code: "NO_PROVIDER", message: "还没有可用的写作渠道:配好一个模型的 API Key 就能开始。" } }),
    { status: 400, headers: { "Content-Type": "application/json" } },
  );
}

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

  it("结构化业务错误 {code, message}:message 上屏,code 可程序化分流", async () => {
    let err: ApiError | null = null;
    vi.stubGlobal("fetch", vi.fn(async () => noKeyResponse()));
    try { await req("POST", "/api/projects/1/blueprint", {}); } catch (e) { err = e as ApiError; }
    expect(err).toBeInstanceOf(ApiError);
    expect(err?.code).toBe("NO_PROVIDER");
    expect(err?.message).toContain("API Key");
  });

  it("非 JSON 错误体回落到 HTTP 状态码", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Bad Gateway", { status: 502 })));
    await expect(req("GET", "/api/y")).rejects.toThrow("HTTP 502");
  });
});

describe("NO_PROVIDER 内联引导", () => {
  it("弹窗保存成功 → 自动重放原请求,拿到结果", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(noKeyResponse())
      .mockResolvedValueOnce(Response.json({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    setProviderSetupHandler(() => Promise.resolve());
    await expect(req("POST", "/api/projects/1/blueprint", {})).resolves.toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("用户关掉弹窗 → 原错误文案照常上屏", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => noKeyResponse()));
    setProviderSetupHandler(() => Promise.reject(new ProviderSetupCancelled()));
    await expect(req("POST", "/api/projects/1/blueprint", {}))
      .rejects.toThrow(/还没有可用的写作渠道/);
  });

  it("配了但再次 NO_PROVIDER → 只重放一次,不无限循环", async () => {
    const fetchMock = vi.fn(async () => noKeyResponse());
    vi.stubGlobal("fetch", fetchMock);
    setProviderSetupHandler(() => Promise.resolve());
    await expect(req("POST", "/api/projects/1/blueprint", {}))
      .rejects.toThrow(/还没有可用的写作渠道/);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("并发请求共用同一次弹窗(in-flight 复用)", async () => {
    const handler = vi.fn(() => Promise.resolve());
    setProviderSetupHandler(handler);
    await Promise.all([requestProviderSetup(), requestProviderSetup()]);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("任务路径标记:pollJob 拿到的 [NO_PROVIDER] 前缀可剥出人话", () => {
    const raw = `${NO_PROVIDER_MARKER}还没有可用的写作渠道`;
    expect(raw.startsWith(NO_PROVIDER_MARKER)).toBe(true);
    expect(raw.slice(NO_PROVIDER_MARKER.length)).toBe("还没有可用的写作渠道");
  });
});
