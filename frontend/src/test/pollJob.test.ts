// pollJob 单元测试:轮询逻辑、重试、超时、中止
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { pollJob } from "../pollJob";
import { api } from "../api";

vi.mock("../api", () => ({
  api: { getJob: vi.fn() },
}));

const getJob = vi.mocked(api.getJob);

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("pollJob", () => {
  it("job 完成时 resolve 其 result", async () => {
    getJob.mockResolvedValueOnce({ status: "running", stage: "草稿", result: null, error: null });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    getJob.mockResolvedValueOnce({ status: "done", stage: "完成", result: { word_count: 3000 }, error: null } as any);

    const result = await pollJob("j1", { intervalMs: 10, timeoutMs: 5000 });
    expect(result).toEqual({ word_count: 3000 });
    expect(getJob).toHaveBeenCalledTimes(2);
  });

  it("运行中调用 onStage 回调", async () => {
    const stages: string[] = [];
    getJob.mockResolvedValueOnce({ status: "running", stage: "草稿", result: null, error: null });
    getJob.mockResolvedValueOnce({ status: "running", stage: "定稿", result: null, error: null });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    getJob.mockResolvedValueOnce({ status: "done", stage: "完成", result: "ok", error: null } as any);

    await pollJob("j2", { intervalMs: 10, timeoutMs: 5000, onStage: (s) => stages.push(s) });
    expect(stages).toEqual(["草稿", "定稿"]);
  });

  it("job 失败时 reject 错误信息", async () => {
    getJob.mockResolvedValueOnce({ status: "error", stage: "失败", result: null, error: "LLM 调用超时" });

    await expect(pollJob("j3", { intervalMs: 10, timeoutMs: 5000 }))
      .rejects.toThrow("LLM 调用超时");
  });

  it("job 失败无 error 字段时给出默认消息", async () => {
    getJob.mockResolvedValueOnce({ status: "error", stage: "失败", result: null, error: null });

    await expect(pollJob("j4", { intervalMs: 10, timeoutMs: 5000 }))
      .rejects.toThrow("任务失败");
  });

  it("超时后 reject", async () => {
    getJob.mockResolvedValue({ status: "running", stage: "草稿", result: null, error: null });

    await expect(pollJob("j5", { intervalMs: 10, timeoutMs: 50 }))
      .rejects.toThrow("任务超时");
  });

  it("signal abort 后 reject AbortError", async () => {
    const ctrl = new AbortController();
    getJob.mockImplementation(async () => {
      ctrl.abort();
      return { status: "running", stage: "草稿", result: null, error: null };
    });

    await expect(pollJob("j6", { intervalMs: 10, timeoutMs: 5000, signal: ctrl.signal }))
      .rejects.toThrow();
  });

  it("网络抖动自动重试,恢复后继续", async () => {
    getJob
      .mockRejectedValueOnce(new Error("network"))
      .mockRejectedValueOnce(new Error("network"))
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .mockResolvedValueOnce({ status: "done", stage: "完成", result: 42, error: null } as any);

    const result = await pollJob("j7", { intervalMs: 10, timeoutMs: 5000 });
    expect(result).toBe(42);
    expect(getJob).toHaveBeenCalledTimes(3);
  });

  it("连续 5 次查询失败后放弃", async () => {
    getJob.mockRejectedValue(new Error("network"));

    await expect(pollJob("j8", { intervalMs: 10, timeoutMs: 5000 }))
      .rejects.toThrow("多次查询任务状态失败");
    expect(getJob).toHaveBeenCalledTimes(5);
  });

  it("已 abort 的 signal 不重试直接抛出", async () => {
    const ctrl = new AbortController();
    ctrl.abort();
    getJob.mockRejectedValue(new Error("network"));

    await expect(pollJob("j9", { intervalMs: 10, timeoutMs: 5000, signal: ctrl.signal }))
      .rejects.toThrow();
    // sleep 会在第一次就 reject,不会走到 getJob
    expect(getJob).not.toHaveBeenCalled();
  });
});
