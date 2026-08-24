// api 客户端单元测试:token 管理、请求头注入、401 处理、错误提取
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { token, setUnauthorizedHandler, api, createSseDecoder } from "../api";

// 用真实的 localStorage(jsdom 提供)
beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

afterEach(() => {
  localStorage.clear();
});

describe("token", () => {
  it("get 无 token 时返回空字符串", () => {
    expect(token.get()).toBe("");
  });

  it("set/get 读写一致", () => {
    token.set("abc123");
    expect(token.get()).toBe("abc123");
    expect(localStorage.getItem("jarvis_token")).toBe("abc123");
  });

  it("clear 清除 token", () => {
    token.set("abc123");
    token.clear();
    expect(token.get()).toBe("");
    expect(localStorage.getItem("jarvis_token")).toBeNull();
  });
});

describe("req 请求行为", () => {
  it("有 token 时注入 Authorization 头", async () => {
    token.set("my-token");
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: 1 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await api.me();
    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.headers["Authorization"]).toBe("Bearer my-token");
  });

  it("无 token 时不带 Authorization 头", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: 1 }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await api.me();
    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.headers["Authorization"]).toBeUndefined();
  });

  it("401 时清除 token 并触发 unauthorized 回调", async () => {
    token.set("expired-token");
    const onUnauth = vi.fn();
    setUnauthorizedHandler(onUnauth);

    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "未授权" }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await expect(api.me()).rejects.toThrow("未授权");
    expect(token.get()).toBe("");
    expect(onUnauth).toHaveBeenCalledTimes(1);
  });

  it("非 401 错误提取 detail 字段", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: "用户名已存在" }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await expect(api.register("a", "b", "c")).rejects.toThrow("用户名已存在");
  });

  it("非 JSON 错误体回退到 HTTP 状态码", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => { throw new Error("not json"); },
    });
    vi.stubGlobal("fetch", mockFetch);

    await expect(api.me()).rejects.toThrow("HTTP 500");
  });

  // fetch 对断网/连接被掐一律只给 "Failed to fetch",原样上屏用户无从判断;
  // 线上起名就吃过这个(见 api.ts 的 netError)。
  it("fetch 抛错(断网/连接被掐)翻成人话,不是 Failed to fetch", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(api.me()).rejects.toThrow("网络请求失败");
    await expect(api.me()).rejects.not.toThrow("Failed to fetch");
  });

  it("超时(自己 abort)给出等了多久的提示", async () => {
    // 真实 fetch 在 abort 时抛 AbortError;这里模拟成 signal.aborted 已置位后抛错
    vi.stubGlobal("fetch", vi.fn().mockImplementation((_url, opts) => {
      Object.defineProperty(opts.signal, "aborted", { value: true, configurable: true });
      return Promise.reject(new DOMException("The operation was aborted.", "AbortError"));
    }));

    await expect(api.me()).rejects.toThrow("请求超时");
  });

  it("正文读一半断了 → 网络错误;服务端返非 JSON → 可解析提示", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => { throw new TypeError("network error"); },
    }));
    await expect(api.me()).rejects.toThrow("网络请求失败");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => { throw new SyntaxError("Unexpected token <"); },
    }));
    await expect(api.me()).rejects.toThrow("无法解析");
  });

  it("POST 请求带 JSON body 和 Content-Type", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ token: "t", username: "u", is_admin: false }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await api.login("user", "pass");
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/auth/login");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(opts.body)).toEqual({ username: "user", password: "pass" });
  });
});

describe("createSseDecoder(SSE 帧解析)", () => {
  it("单个完整帧:解析 event + data(JSON)", () => {
    const feed = createSseDecoder();
    expect(feed('event: token\ndata: {"text":"你好"}\n\n')).toEqual([
      { event: "token", data: { text: "你好" } },
    ]);
  });

  it("跨 chunk 的帧只在补全后吐出一次(网络分块的核心场景)", () => {
    const feed = createSseDecoder();
    expect(feed('event: token\ndata: {"text":"A"')).toEqual([]); // 半截,先不吐
    expect(feed("}\n\n")).toEqual([{ event: "token", data: { text: "A" } }]);
  });

  it("一次喂入多帧全部解析;非 JSON 的 data 原样保留为字符串", () => {
    const feed = createSseDecoder();
    const out = feed('event: token\ndata: {"text":"x"}\n\nevent: note\ndata: hello\n\n');
    expect(out).toEqual([
      { event: "token", data: { text: "x" } },
      { event: "note", data: "hello" },
    ]);
  });

  it("CRLF 代理(\\r\\n)也能正确切帧", () => {
    const feed = createSseDecoder();
    expect(feed('event: done\r\ndata: {"ok":true}\r\n\r\n')).toEqual([
      { event: "done", data: { ok: true } },
    ]);
  });

  it("无 data 行的注释/心跳帧跳过", () => {
    const feed = createSseDecoder();
    expect(feed(": keep-alive\n\n")).toEqual([]);
  });
});

// 把字符串分块伪装成 fetch 的 SSE 响应体(res.body.getReader())
function streamResponse(chunks: string[]) {
  const enc = new TextEncoder();
  let i = 0;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { value: enc.encode(chunks[i++]), done: false }
            : { value: undefined, done: true },
      }),
    },
  };
}

describe("流式对话客户端", () => {
  it("discussFragmentStream:逐帧回调 token,done 收尾出 reply + suggestion", async () => {
    const mockFetch = vi.fn().mockResolvedValue(streamResponse([
      'event: token\ndata: {"text":"你好"}\n\n',
      'event: token\ndata: {"text":",世界"}\n\n',
      'event: done\ndata: {"reply":"你好,世界","suggestion":"改写版"}\n\n',
    ]));
    vi.stubGlobal("fetch", mockFetch);

    const tokens: string[] = [];
    const r = await api.discussFragmentStream(
      3, 5, [{ role: "user", content: "hi" }], "原文段", (t) => tokens.push(t));

    expect(tokens.join("")).toBe("你好,世界");
    expect(r.reply).toBe("你好,世界");
    expect(r.suggestion).toBe("改写版");
    // 打到流式端点,body 带 messages + target
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/projects/3/chapters/5/discuss-stream");
    expect(JSON.parse(opts.body)).toEqual({
      messages: [{ role: "user", content: "hi" }], target: "原文段",
    });
  });

  it("discussRevisionStream:done 出 directive + 档位建议", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse([
      'event: token\ndata: {"text":"节奏确实拖"}\n\n',
      'event: done\ndata: {"reply":"节奏确实拖","directive":"开头砍一半","suggested_level":"polish"}\n\n',
    ])));
    const r = await api.discussRevisionStream(1, 1, [{ role: "user", content: "拖" }], () => {});
    expect(r.reply).toBe("节奏确实拖");
    expect(r.directive).toBe("开头砍一半");
    expect(r.suggested_level).toBe("polish");
  });

  it("error 帧 → 抛 ApiError(带后端 detail)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse([
      'event: error\ndata: {"detail":"请先说点什么"}\n\n',
    ])));
    await expect(
      api.discussFragmentStream(1, 1, [], "", () => {}),
    ).rejects.toThrow("请先说点什么");
  });

  it("流未开始的 HTTP 错误(404)→ 抛 ApiError 读 detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 404, json: async () => ({ detail: "第 1 章尚未生成" }),
    }));
    await expect(
      api.discussRevisionStream(1, 1, [{ role: "user", content: "x" }], () => {}),
    ).rejects.toThrow("第 1 章尚未生成");
  });

  it("流中断没有 done 帧 → 抛错(不把半截结果当成功)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse([
      'event: token\ndata: {"text":"半截"}\n\n',
    ])));
    await expect(
      api.discussFragmentStream(1, 1, [{ role: "user", content: "x" }], "", () => {}),
    ).rejects.toThrow("中断");
  });
});
