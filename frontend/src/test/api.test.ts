// api 客户端单元测试:token 管理、请求头注入、401 处理、错误提取
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { token, setUnauthorizedHandler, api } from "../api";

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
