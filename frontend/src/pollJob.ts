// 轮询后端 job 直到完成:统一间隔/超时上限/外部中止(组件卸载时取消)
import { api, ApiError } from "./api";

/** 渲染错误给用户:Error 取 message(去掉 "Error: " 英文前缀),其余兜底 String。 */
export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export interface PollJobOptions {
  intervalMs?: number;               // 轮询间隔,默认 3s
  timeoutMs?: number;                // 超时上限,默认 30 分钟,超时 reject
  signal?: AbortSignal;              // 外部中止(如组件卸载),abort 后 reject AbortError
  onStage?: (stage: string) => void; // 运行中的进度回调
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException("已取消", "AbortError")); return; }
    const timer = setTimeout(() => { cleanup(); resolve(); }, ms);
    const onAbort = () => { cleanup(); reject(new DOMException("已取消", "AbortError")); };
    function cleanup() {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
    signal?.addEventListener("abort", onAbort);
  });
}

/** 轮询 job,完成时 resolve 其 result;失败/超时/中止时 reject。
 *  查询状态的网络抖动(如服务器繁忙导致单次请求超时)会自动重试,
 *  连续 5 次失败才放弃;外部 signal abort 立即取消。 */
export async function pollJob<T = unknown>(jobId: string, opts: PollJobOptions = {}): Promise<T> {
  const { intervalMs = 3000, timeoutMs = 1_800_000, signal, onStage } = opts;
  const deadline = Date.now() + timeoutMs;
  let failures = 0;
  for (;;) {
    if (Date.now() > deadline) throw new Error("任务超时(超过 30 分钟未完成)");
    await sleep(intervalMs, signal);
    let job;
    try {
      job = await api.getJob(jobId);
      failures = 0;
    } catch (e) {
      // 外部取消:立即放弃
      if (signal?.aborted) throw e;
      // 404:任务在后端已不存在(服务重启且任务丢了)——确定性失败,重试无意义,
      // 立刻报清楚原因,不让用户对着 30 分钟超时干等
      if (e instanceof ApiError && e.status === 404) {
        throw new Error("任务已丢失(服务可能重启过),请刷新页面后在任务中心查看或重试");
      }
      // 瞬时故障(网络/服务器繁忙):重试
      if (++failures >= 5) {
        throw new Error("多次查询任务状态失败(网络不稳定),任务可能仍在后台运行,请稍后刷新查看");
      }
      continue;
    }
    if (job.status === "running") { onStage?.(job.stage); continue; }
    if (job.status === "error") throw new Error(job.error ?? "任务失败");
    return job.result as unknown as T;
  }
}
