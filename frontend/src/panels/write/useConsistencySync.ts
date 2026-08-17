// 一致性同步(「正文即界面」write 区):保存正文/回退版本后重抽取 + 重建下游摘要 + 向量库。
// 非阻塞——只显轻量角标,按章号并发(多章各自独立收尾,互不覆盖清空),不挡阅读/编辑其他章节。
// pendingSync=保存后待用户确认是否同步的章号(小幅修改可跳过)。triggerSync 自带去重(同章在跑不重起,
// 与后端去重一致);reconnectSync 供挂载重连遗留任务(切走再回来)。从 WritePanel 状态中枢抽出(拆分技术债)。
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { pollJob, errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";

// running-jobs 单项(api.runningJobs 的 jobs 元素,无导出类型名,此处按结构声明)
type RunningJob = { job_id: string; kind: string; stage: string };

export function useConsistencySync(pid: number) {
  // 进行中的同步任务角标:key=章号,value=当前阶段文案(非阻塞)
  const [syncJobs, setSyncJobs] = useState<Map<number, string>>(new Map());
  // 保存正文后待用户确认是否同步的章号(null=无待确认)
  const [pendingSync, setPendingSync] = useState<number | null>(null);
  // 按章号存中止器,允许多章并发各自独立中止;卸载时全部中止。
  const syncAbortRefs = useRef<Map<number, AbortController>>(new Map());
  useEffect(() => () => {
    syncAbortRefs.current.forEach((c) => c.abort());
  }, []);

  // 同步角标按章号读写(函数式更新,避免并发覆盖);清除时同时移除中止器。
  const setSyncStage = useCallback((num: number, stage: string) => {
    setSyncJobs((m) => new Map(m).set(num, stage));
  }, []);
  const clearSync = useCallback((num: number) => {
    setSyncJobs((m) => { const n = new Map(m); n.delete(num); return n; });
    syncAbortRefs.current.delete(num);
  }, []);

  // 同步一致性引擎(重抽取 + 重建下游摘要 + 向量库)。非阻塞:仅显示轻量角标,
  // 用户可继续阅读/编辑其他章节。保存后确认、回退版本共用。
  const triggerSync = useCallback(async (num: number) => {
    setPendingSync(null);
    if (syncAbortRefs.current.has(num)) return; // 该章已在同步,不重复起(与后端去重一致)
    const ctrl = new AbortController();
    syncAbortRefs.current.set(num, ctrl);
    setSyncStage(num, "启动同步…");
    try {
      const { job_id } = await api.reExtractAsync(pid, num);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setSyncStage(num, stage),
      });
      if (!ctrl.signal.aborted) toast.ok(`第 ${num} 章一致性同步完成`);
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const msg = errMsg(e);
        if (msg.startsWith("任务超时") || msg.startsWith("多次查询")) {
          toast.err("同步进度查询中断", "任务可能仍在后台运行,稍后刷新可见最新状态");
        } else {
          toast.err(`第 ${num} 章同步失败`, msg);
        }
      }
    } finally { if (!ctrl.signal.aborted) clearSync(num); }
  }, [pid, setSyncStage, clearSync]);

  // 挂载重连:接上遗留的多章并发同步任务(切走再回来的场景),已在跟踪的不重复接。
  const reconnectSync = useCallback((jobs: RunningJob[]) => {
    jobs.filter((j) => j.kind.startsWith(`re-extract-${pid}-`)).forEach((sync) => {
      const n = Number(sync.kind.split("-").pop());
      if (syncAbortRefs.current.has(n)) return; // 已在跟踪,不重复接
      const ctrl = new AbortController();
      syncAbortRefs.current.set(n, ctrl);
      setSyncStage(n, sync.stage);
      pollJob(sync.job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setSyncStage(n, stage),
      }).catch(() => undefined)
        .finally(() => { if (!ctrl.signal.aborted) clearSync(n); });
    });
  }, [pid, setSyncStage, clearSync]);

  return { syncJobs, pendingSync, setPendingSync, triggerSync, reconnectSync };
}
