// quarantined 放行(确认 + 异步 job + toast)的共用逻辑:
// GenResultCard 门禁横幅与 WritePanel.releaseChapter(StageBar blocked 主动作 / 章节轨行内按钮)共用,
// 提取于此避免两处确认文案/调用漂移。返回 true=已放行(调用方自行刷新各自视图)。
import { api } from "../../api";
import { errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";

interface ReleaseOpts {
  pid: number;
  n: number;
  // 致命矛盾个数,仅用于确认文案;历史模式无当次 gate 数据时可不传
  blockerCount?: number;
  // useJob().run:进全局任务中心并轮询到完成;返回 null=本地等待被中止(任务仍在后台)
  run: <T>(start: () => Promise<{ job_id: string }>, opts?: { kind?: string }) => Promise<T | null>;
}

export async function confirmAndReleaseGate({ pid, n, blockerCount, run }: ReleaseOpts): Promise<boolean> {
  const ok = await confirmDialog({
    title: `放行第 ${n} 章?`,
    body: `将忽略全部${blockerCount ? ` ${blockerCount} 个` : ""}致命矛盾,并补走圣经抽取/滚动摘要等被跳过的同步。矛盾仍留在正文里,后续章节可能继续触发提醒。`,
    confirmText: "放行(忽略全部)",
    danger: true,
  });
  if (!ok) return false;
  try {
    const res = await run(() => api.gateRelease(pid, n), { kind: `gate-release-${pid}-${n}` });
    if (res === null) return false;
    toast.ok(`第 ${n} 章已放行`, "状态变为「待审」,圣经/摘要已补齐");
    return true;
  } catch (e) {
    toast.err("放行失败", errMsg(e));
    return false;
  }
}
