// useJobLive 单元测试:SSE 帧 → 直播状态机。
// 锁三件容易回归的事:
//  1) step 帧整屏替换(换步骤不能把上一步的正文糊在后面);
//  2) 收到 done 后不再重连(否则任务结束仍无限刷 SSE);
//  3) 流意外结束(没 done)要带 cursor 续订——长任务被反代掐连接是常态。
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, SseFrame } from "../api";
import { useJobLive } from "../ui/useJobLive";

type Follow = (
  jobId: string,
  cursor: number,
  onFrame: (f: SseFrame) => void,
  signal?: AbortSignal,
) => Promise<void>;

/** 装一个可编排的假 SSE:每次调用返回一个可手动喂帧/结束的会话。 */
function stubFollow() {
  const calls: { cursor: number; emit: (f: SseFrame) => void; end: () => void }[] = [];
  const impl: Follow = (_jobId, cursor, onFrame) =>
    new Promise<void>((resolve) => {
      calls.push({ cursor, emit: onFrame, end: resolve });
    });
  vi.spyOn(api, "followJobLive").mockImplementation(impl as typeof api.followJobLive);
  return calls;
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => vi.restoreAllMocks());

describe("useJobLive", () => {
  it("jobId 为空时不连接", () => {
    const spy = vi.spyOn(api, "followJobLive");
    const { result } = renderHook(() => useJobLive(null));
    expect(spy).not.toHaveBeenCalled();
    expect(result.current.text).toBe("");
  });

  it("enabled=false(窗收起)时不连接:收起就别占着流", () => {
    const spy = vi.spyOn(api, "followJobLive");
    renderHook(() => useJobLive("job1", false));
    expect(spy).not.toHaveBeenCalled();
  });

  it("token 帧逐字拼接,step 帧整屏替换", async () => {
    const calls = stubFollow();
    const { result } = renderHook(() => useJobLive("job1"));
    await waitFor(() => expect(calls.length).toBe(1));

    act(() => {
      calls[0].emit({ event: "step", data: { step: "正在写草稿", text: "", epoch: 1, seq: 0 } });
      calls[0].emit({ event: "token", data: { text: "雪落了", seq: 3 } });
      calls[0].emit({ event: "token", data: { text: "一夜。", seq: 6 } });
    });
    expect(result.current.text).toBe("雪落了一夜。");
    expect(result.current.step).toBe("正在写草稿");
    expect(result.current.streaming).toBe(true);

    // 换步骤:上一步的正文不留在屏上
    act(() => {
      calls[0].emit({ event: "step", data: { step: "正在定稿", text: "定稿开头", epoch: 2, seq: 6 } });
    });
    expect(result.current.text).toBe("定稿开头");
    expect(result.current.step).toBe("正在定稿");
  });

  it("label 帧只换步骤文案,不清屏(蓝图边写边报「已生成 N/M 章」)", async () => {
    const calls = stubFollow();
    const { result } = renderHook(() => useJobLive("job1"));
    await waitFor(() => expect(calls.length).toBe(1));
    act(() => {
      calls[0].emit({ event: "step", data: { step: "正在生成蓝图", text: "", epoch: 1, seq: 0 } });
      calls[0].emit({ event: "token", data: { text: "第一章 雪夜", seq: 6 } });
      calls[0].emit({ event: "label", data: { step: "已生成 1/40 章", seq: 6 } });
      calls[0].emit({ event: "token", data: { text: "第二章 归人", seq: 12 } });
    });
    expect(result.current.step).toBe("已生成 1/40 章");
    expect(result.current.text).toBe("第一章 雪夜第二章 归人");
  });

  it("reset 帧整屏重置(服务端缓冲已滚过,不假装连续)", async () => {
    const calls = stubFollow();
    const { result } = renderHook(() => useJobLive("job1"));
    await waitFor(() => expect(calls.length).toBe(1));
    act(() => {
      calls[0].emit({ event: "token", data: { text: "旧的", seq: 2 } });
      calls[0].emit({ event: "reset", data: { text: "最新一屏", dropped: 500, seq: 999 } });
    });
    expect(result.current.text).toBe("最新一屏");
  });

  it("ping 帧只保活,不动正文", async () => {
    const calls = stubFollow();
    const { result } = renderHook(() => useJobLive("job1"));
    await waitFor(() => expect(calls.length).toBe(1));
    act(() => {
      calls[0].emit({ event: "token", data: { text: "正文", seq: 2 } });
      calls[0].emit({ event: "ping", data: { seq: 2 } });
    });
    expect(result.current.text).toBe("正文");
  });

  it("done 后标记结束且不再重连", async () => {
    vi.useFakeTimers();
    try {
      const calls = stubFollow();
      const { result } = renderHook(() => useJobLive("job1"));
      await vi.waitFor(() => expect(calls.length).toBe(1));
      act(() => {
        calls[0].emit({ event: "done", data: { status: "done", stage: "完成" } });
        calls[0].end();
      });
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(calls.length).toBe(1);              // 没有第二次订阅
      expect(result.current.ended).toBe(true);
      expect(result.current.streaming).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("流没 done 就断了 → 带 cursor 续订,不从头重放", async () => {
    vi.useFakeTimers();
    try {
      const calls = stubFollow();
      renderHook(() => useJobLive("job1"));
      await vi.waitFor(() => expect(calls.length).toBe(1));
      expect(calls[0].cursor).toBe(0);
      act(() => {
        calls[0].emit({ event: "token", data: { text: "写到这里被掐了", seq: 42 } });
        calls[0].end();                          // 反代掐了空闲连接:流结束但没 done
      });
      await act(async () => { await vi.advanceTimersByTimeAsync(2500); });
      expect(calls.length).toBe(2);
      expect(calls[1].cursor).toBe(42);          // 从上次的字数续看
    } finally {
      vi.useRealTimers();
    }
  });

  it("404/401 不重试(任务已清理或登录态失效)", async () => {
    vi.useFakeTimers();
    try {
      const spy = vi
        .spyOn(api, "followJobLive")
        .mockRejectedValue(new ApiError(404, "任务不存在或已被清理"));
      const { result } = renderHook(() => useJobLive("job1"));
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(spy).toHaveBeenCalledTimes(1);
      expect(result.current.ended).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("切换 jobId 时清屏,不把上个任务的正文留在窗里", async () => {
    const calls = stubFollow();
    const { result, rerender } = renderHook(({ id }: { id: string }) => useJobLive(id), {
      initialProps: { id: "job1" },
    });
    await waitFor(() => expect(calls.length).toBe(1));
    act(() => { calls[0].emit({ event: "token", data: { text: "第一个任务的正文", seq: 8 } }); });
    expect(result.current.text).toBe("第一个任务的正文");

    rerender({ id: "job2" });
    expect(result.current.text).toBe("");
    await waitFor(() => expect(calls.length).toBe(2));
    expect(calls[1].cursor).toBe(0);             // 新任务从头看
  });
});
