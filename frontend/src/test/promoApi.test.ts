// 宣传片 API 客户端:SSE 研讨流与导出下载这两处「肉眼看不出对错」的地方。
//
// 为什么单测这一层:
// - 研讨是流式打字机,分片边界完全由网络决定——一个 SSE 帧被切成两次 read 是常态。
//   帧解码器要跨片缓冲,不然用户会看到吞字;这类 bug 在本地快网下几乎复现不出来;
// - 流的三种结局(done / error 帧 / 没等到 done 就断)要给出三种不同的人话,
//   尤其「没等到 done」不能当成功返回空回复(那会把空白气泡落库);
// - 导出的文件名走 `filename*=UTF-8''` 百分号编码,不解码就得到一串 %E8%A5%BF...;
//   缺这个头时还得有兜底名,否则浏览器存成无扩展名文件。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { promoApi } from "../promoApi";
import { token } from "../api";

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});
// stubGlobal 的清理:restoreAllMocks 只还原 spyOn,不还原全局 stub——
// 不补这一句,本文件的 fetch/URL stub 会串进同进程后续所有测试文件
afterEach(() => vi.unstubAllGlobals());

/** 把若干字符串片段做成一个可读流响应(片段边界 = 网络分片边界) */
function sseRes(chunks: string[]) {
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

const frame = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

describe("研讨 SSE 流", () => {
  it("token 帧逐个喂 onToken,done 帧作最终结果;帧被切两半也不吞字", async () => {
    const whole = frame("token", { text: "先" }) + frame("token", { text: "定受众" });
    // 在第一帧中间切开:分片边界落在 JSON 里
    const cut = Math.floor(whole.indexOf("定受众") - 3);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      sseRes([whole.slice(0, cut), whole.slice(cut), frame("done", { reply: "先定受众" })]),
    ));

    const got: string[] = [];
    const turns = await promoApi.chat(5, [{ role: "user", text: "从吃的入手" }], (t) => got.push(t));

    expect(got.join("")).toBe("先定受众");
    expect(turns).toEqual([{ role: "assistant", text: "先定受众" }]);
  });

  it("POST 带鉴权头与 messages(服务端要拿全上下文续聊)", async () => {
    token.set("tk");
    const mockFetch = vi.fn().mockResolvedValue(sseRes([frame("done", { reply: "好" })]));
    vi.stubGlobal("fetch", mockFetch);

    await promoApi.chat(5, [{ role: "user", text: "嗨" }], () => {});

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/promos/5/chat");
    expect(opts.method).toBe("POST");
    expect(opts.headers["Authorization"]).toBe("Bearer tk");
    expect(JSON.parse(opts.body)).toEqual({ messages: [{ role: "user", text: "嗨" }] });
  });

  it("error 帧把后端的人话抛出来", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      sseRes([frame("token", { text: "半" }), frame("error", { detail: "模型未配置" })]),
    ));
    await expect(promoApi.chat(5, [], () => {})).rejects.toThrow("模型未配置");
  });

  it("流断了但没等到 done:算失败,不能返回空回复", async () => {
    // 否则会往聊天记录里落一个空白气泡,用户以为总监「没话说」
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseRes([frame("token", { text: "半" })])));
    await expect(promoApi.chat(5, [], () => {})).rejects.toThrow("对话意外中断");
  });

  it("流还没开始就非 2xx:抛后端 detail 而不是干巴巴的 HTTP 402", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 402, body: null, json: async () => ({ detail: "额度用完了" }),
    }));
    await expect(promoApi.chat(5, [], () => {})).rejects.toThrow("额度用完了");
  });
});

describe("导出下载", () => {
  function stubDownload(headers: Record<string, string>) {
    const clicks = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(clicks);
    // 用子类挂 mock:直接 Object.assign(URL,…) 是在改真实构造器,mock 会永久留在全局
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn().mockReturnValue("blob:fake");
      static revokeObjectURL = vi.fn();
    });
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: { get: (k: string) => headers[k] ?? null },
      blob: async () => new Blob(["# 拍摄手册"]),
    });
    vi.stubGlobal("fetch", mockFetch);
    return { mockFetch, clicks };
  }

  it("文件名做百分号解码(否则存成 %E8%A5%BF%E5%AE%89…)", async () => {
    const { mockFetch } = stubDownload({
      "Content-Disposition": "attachment; filename*=UTF-8''%E8%A5%BF%E5%AE%89.md",
    });
    const created: HTMLAnchorElement[] = [];
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreate(tag);
      if (tag === "a") created.push(el as HTMLAnchorElement);
      return el;
    });

    await promoApi.export(5, "md");

    expect(mockFetch.mock.calls[0][0]).toBe("/api/promos/5/export?format=md");
    expect(created[0].download).toBe("西安.md");
  });

  it("缺 Content-Disposition 时兜底 promo.<格式>,并带鉴权头", async () => {
    token.set("tk");
    const { mockFetch } = stubDownload({});
    const created: HTMLAnchorElement[] = [];
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = realCreate(tag);
      if (tag === "a") created.push(el as HTMLAnchorElement);
      return el;
    });

    await promoApi.export(5, "srt");

    expect(mockFetch.mock.calls[0][1].headers["Authorization"]).toBe("Bearer tk");
    expect(created[0].download).toBe("promo.srt");
  });
});

describe("切段口径", () => {
  it("每段上限走 body 的 chunk_s(后端按它并段,不是前端自己切)", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ job_id: "j1" }) });
    vi.stubGlobal("fetch", mockFetch);

    await promoApi.chunks(5, 10);

    expect(mockFetch.mock.calls[0][0]).toBe("/api/promos/5/chunks");
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ chunk_s: 10 });
  });
});
