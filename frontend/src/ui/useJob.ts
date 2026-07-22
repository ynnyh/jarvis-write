// useJob:面板内发起异步长任务的统一钩子。
// 发起 → 进全局任务中心(切走页面也可见)→ 本组件内轮询到完成拿结果。
// 组件卸载只中止"本地等待",任务本身继续在后台跑(任务中心可见)。
import { useCallback, useEffect, useRef } from "react";
import { pollJob } from "../pollJob";
import { useTaskCenter } from "./TaskCenter";

export function useJob() {
  const { track, refresh } = useTaskCenter();
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  /** start: 调 -async 接口拿 job_id;返回任务结果,本地等待被中止时返回 null */
  const run = useCallback(
    async <T,>(
      start: () => Promise<{ job_id: string }>,
      opts?: { kind?: string; onStage?: (stage: string) => void },
    ): Promise<T | null> => {
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const { job_id } = await start();
      track({ job_id, kind: opts?.kind ?? "job" });
      try {
        const result = await pollJob<T>(job_id, {
          signal: ctrl.signal,
          onStage: opts?.onStage,
        });
        return ctrl.signal.aborted ? null : result;
      } finally {
        refresh();
      }
    },
    [track, refresh],
  );

  return { run, abortRef };
}
