// 「章被拦了怎么办」统一处理块(说人话·一处逻辑两处复用)。
// 章首交稿单(blocked 态)与生成结果卡(门禁横幅)、翻新命中拦截,全部复用这一张卡:
// 一句话说清最该处理的那条硬矛盾 + 三个人话按钮,吸收掉「放行 / 契约重提 / 重检 /
// 按建议修订」等黑话与散落各处的重复入口。三个决定 + 一条次要出口:
//   ▸ 让 AI 按这条重写(默认高亮)—— 拿最该处理的那条致命矛盾走重写链路(applyIssueRevision)
//   ▸ 去改设定 —— 跳故事圣经看板,改掉与本章冲突的既有事实
//   ▸ 就这样,忽略继续 —— 即原「放行」(gate-release),沿用其危险确认
//   · 改好设定了?重新检查 —— 重检门禁;干净则后端自动补链路放行(不必再懂「放行 / 契约重提」)
// 自取 open issues(单点计算 topBlocker/blockerCount,两处调用点零 prop 线缆、措辞零漂移)。
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ChapterIssue } from "../../api";
import { errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";
import { useJob } from "../../ui/useJob";
import { confirmAndReleaseGate } from "./releaseGate";

interface Props {
  pid: number;
  n: number;
  // 处理后(重写受理 / 放行 / 重检自动放行)刷新章节列表与打开的正文;拦截解除后父级会自然收起本卡
  onChanged: () => void;
  // 拿不到具体 blocker 时的兜底重写(打开 AI 窄栏梳理);由章首卡传入
  onRewriteFallback?: () => void;
  // 任务锁:有生成/重写任务在跑时禁用重写/放行(后端也会 409),title 给原因
  genBlocked?: boolean;
  genHint?: string;
}

export default function GateResolve({ pid, n, onChanged, onRewriteFallback, genBlocked, genHint }: Props) {
  const nav = useNavigate();
  const { run } = useJob();
  const [issues, setIssues] = useState<ChapterIssue[] | null>(null);
  const [busy, setBusy] = useState<"" | "rewrite" | "release" | "recheck">("");

  const reload = useCallback(() => {
    api.listChapterIssues(pid, n).then(setIssues).catch(() => setIssues([]));
  }, [pid, n]);

  useEffect(() => {
    let cancelled = false;
    setIssues(null);
    api.listChapterIssues(pid, n)
      .then((l) => { if (!cancelled) setIssues(l); })
      .catch(() => { if (!cancelled) setIssues([]); });
    return () => { cancelled = true; };
  }, [pid, n]);

  const open = (issues ?? []).filter((i) => i.status === "open");
  const blockers = open.filter((i) => i.severity === "blocker");
  // 最该处理的那条:优先致命矛盾,退而取任一未处理项;都没有时为 null(走兜底重写 / 靠重检自动放行)。
  // 排除 source=canon 的宪法建议——它是「采纳进宪法」的增值提案,不是本章硬矛盾,不该被当成重写目标。
  const topBlocker = blockers[0] ?? open.find((i) => i.source !== "canon") ?? null;
  const blockerCount = blockers.length;

  // ▸ 让 AI 按这条重写:有具体矛盾走「按建议修订」链路;拿不到则回落到 AI 窄栏梳理
  async function rewrite() {
    if (!topBlocker) { onRewriteFallback?.(); return; }
    setBusy("rewrite");
    try {
      const res = await run<{ applied_issue_id?: number }>(
        () => api.applyIssueRevision(pid, n, topBlocker.id),
        { kind: `chapter-${pid}-${n}` },
      );
      if (res) {
        toast.ok(`第 ${n} 章已按这条矛盾重写`, "正文已更新,门禁已重跑核对");
        reload();
        onChanged();
      }
    } catch (e) {
      toast.err("重写失败", errMsg(e));
      reload();
    } finally {
      setBusy("");
    }
  }

  // ▸ 去改设定:跳故事圣经看板,改掉冲突的既有事实(改完回本章点「重新检查」)
  function goFixSettings() {
    toast.info("去故事圣经改掉冲突的设定", "改完回到本章,点「改好设定了?重新检查」即可");
    nav(`/project/${pid}/book?tab=bible`);
  }

  // ▸ 就这样,忽略继续:原「放行」,沿用危险确认
  async function ignore() {
    setBusy("release");
    try {
      const released = await confirmAndReleaseGate({ pid, n, blockerCount, run });
      if (released) { reload(); onChanged(); }
    } finally {
      setBusy("");
    }
  }

  // · 改好设定了?重新检查:重检门禁;干净且原为拦截 → 后端自动补链路放行
  async function recheck() {
    setBusy("recheck");
    try {
      const res = await run<{ blockers: number; issues: number; auto_released?: boolean }>(
        () => api.reextractContract(pid, n),
        { kind: `contract-${pid}-${n}` },
      );
      if (res) {
        if (res.auto_released)
          toast.ok(`第 ${n} 章设定已无冲突,自动通过`, "已补齐圣经/摘要,状态回「待审」");
        else if (res.blockers > 0)
          toast.info(`重新检查:仍有 ${res.blockers} 处硬矛盾`, "可继续改设定,或让 AI 按矛盾重写、或忽略继续");
        else
          toast.ok("重新检查完成", res.issues ? `仍有 ${res.issues} 个非致命问题` : "未发现问题");
        reload();
        onChanged();
      }
    } catch (e) {
      toast.err("重新检查失败", errMsg(e));
    } finally {
      setBusy("");
    }
  }

  const lock = !!busy || genBlocked;
  return (
    <div className="gate-resolve">
      <div className="gate-resolve-head">
        先别急着往下写——和你的设定撞了{blockerCount > 1 ? `(共 ${blockerCount} 处,先处理最要紧的一条)` : ""}:
      </div>
      <div className="gate-resolve-what">
        {issues === null ? "读取冲突详情…" : (topBlocker?.description || "本章与既有设定存在硬矛盾")}
      </div>
      {topBlocker?.suggestion && <div className="muted gate-resolve-sug">建议:{topBlocker.suggestion}</div>}
      <div className="gate-resolve-q">想怎么办?</div>
      <div className="gate-resolve-actions">
        <button className="primary btn-sm" disabled={lock}
          title={genBlocked ? genHint : "把这条矛盾交给 AI 走重写链路(重写后门禁自动重跑核对)"}
          onClick={rewrite}>
          {busy === "rewrite" && <span className="spin spin-sm" />}让 AI 按这条重写
        </button>
        <button className="btn-sm" disabled={!!busy}
          title="跳到故事圣经,改掉与本章冲突的既有设定;改完回来点「改好设定了?重新检查」"
          onClick={goFixSettings}>
          去改设定
        </button>
        <button className="btn-sm" disabled={lock}
          title={genBlocked ? genHint : "忽略全部致命矛盾继续(原「放行」):矛盾仍留在正文里,后续章节可能继续提醒"}
          onClick={ignore}>
          {busy === "release" && <span className="spin spin-sm" />}就这样,忽略继续
        </button>
      </div>
      <button className="linkish gate-resolve-recheck" disabled={!!busy}
        title="改完设定后点这里重检;若不再有硬矛盾,本章会自动通过并补齐圣经/摘要"
        onClick={recheck}>
        {busy === "recheck" && <span className="spin spin-sm" />}改好设定了?重新检查
      </button>
    </div>
  );
}
