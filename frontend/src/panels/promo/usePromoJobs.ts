// 宣传片工作台的任务闸门:同一份企划,同一时刻只跑一个 AI 任务。
// 为什么集中管而不是各区各管一份 busy:六步背后是同一条流水线
//(简报 → 视觉锚 → 解说词 → 分镜/提示词 → 切段 → 成片包),后一步吃前一步的产物。
// 两个任务并行跑同一个 plan,写库时后写的赢,用户看到的是「分镜莫名换了一版」。
// 所以 busy 一亮全体禁用,横幅与错误也集中显示在正在跑的那一区。
import { useCallback, useState } from "react";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";

export type PromoJobKind =
  | "brief" | "style" | "landmarks" | "script" | "board" | "prompts" | "chunks" | "pack";

export interface PromoJobs {
  /** 正在跑的那一步(空串 = 空闲);各区拿它禁用自己的按钮 */
  busy: PromoJobKind | "";
  /** 后端 progress 文案(「AI 正在写分段视频提示词…」) */
  stage: string;
  err: string;
  act: (kind: PromoJobKind, start: () => Promise<{ job_id: string }>, ok: string) => Promise<void>;
}

export function usePromoJobs(pid: number, reload: () => Promise<void>): PromoJobs {
  const { run } = useJob();
  const [busy, setBusy] = useState<PromoJobKind | "">("");
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  const act = useCallback<PromoJobs["act"]>(async (kind, start, ok) => {
    setBusy(kind); setErr(""); setStage("");
    try {
      await run(start, { kind: `promo-${kind}-${pid}`, onStage: setStage });
      await reload();
      toast.ok(ok);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(""); setStage("");
    }
  }, [pid, reload, run]);

  return { busy, stage, err, act };
}
