// hooks/useJobReconnect.ts
// 挂载时接回还在后台跑的任务(切走页面再回来的场景):查 runningJobs → 按 kind 命中 →
// 存 AbortController 到共享 abortRef → 轮询进度回填 busy → 完成走 onDone / 失败报错。
// ArchPanel(架构生成)、OutlinePanel(蓝图生成/展开下一卷)、WritePanel 同一模式,从中抽出。
import { MutableRefObject, useEffect } from "react";
import { api } from "../api";
import { pollJob, errMsg } from "../pollJob";

interface JobReconnectOpts<T> {
  pid: number;
  kind: string;                 // 命中判定的任务 kind(如 `architecture-${pid}`)
  busyPrefix: string;           // 进度文案前缀(如 "架构生成中:")
  abortRef: MutableRefObject<AbortController | null>; // 与主动生成共用:卸载/切走时中止
  setBusy: (s: string) => void;
  setErr: (s: string) => void;
  onDone: (result: T) => void | Promise<void>;
}

export function useJobReconnect<T = unknown>({
  pid, kind, busyPrefix, abortRef, setBusy, setErr, onDone,
}: JobReconnectOpts<T>) {
  useEffect(() => {
    let cancelled = false;
    api.runningJobs(pid).then(({ jobs }) => {
      if (cancelled) return;
      const gen = jobs.find((j) => j.kind === kind);
      if (!gen) return;
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setBusy(`${busyPrefix}${gen.stage}`);
      pollJob<T>(gen.job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`${busyPrefix}${stage}`),
      }).then(async (r) => {
        if (ctrl.signal.aborted) return;
        await onDone(r);
      }).catch((e) => {
        if (!ctrl.signal.aborted) setErr(errMsg(e));
      }).finally(() => { if (!ctrl.signal.aborted) setBusy(""); });
    }).catch(() => undefined);
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);
}
