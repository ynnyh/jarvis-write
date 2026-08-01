// 生成结果卡片(章节审核面板):字数/AI味、门禁状态、五维审校分、
// 写前提醒(preflight)与可操作问题清单(按建议修订/人工解决/忽略)
import { useCallback, useEffect, useState } from "react";
import {
  api, ChapterIssue, flavorTitle, GenerateChapterResponse,
} from "../../api";
import { errMsg } from "../../pollJob";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { confirmDialog } from "../../ui/ConfirmDialog";

// 审校五维分中文标签;旧快照无 continuity 键,Object.entries 遍历天然不渲染该行
const SCORE_LABEL: Record<string, string> = {
  plot: "情节", prose: "文笔", pacing: "节奏", character: "人物", continuity: "连续性",
};
// 问题严重度:徽标配色 + 中文
const SEV_BADGE: Record<string, string> = { blocker: "err", major: "warn", minor: "" };
const SEV_CN: Record<string, string> = { blocker: "致命", major: "重要", minor: "次要" };
// 问题来源中文
const SOURCE_CN: Record<string, string> = {
  gate: "门禁", preflight: "预审", diag: "诊断", review: "审校",
};
const ISSUE_STATUS_CN: Record<string, string> = { resolved: "已人工解决", ignored: "已忽略" };

interface Props {
  pid: number;
  result: GenerateChapterResponse;
  // 章节数据有变(修订完成/放行后状态变化):刷新章节列表与打开的正文
  onChanged: () => void;
  // 「重写」引导:展开本章的行内重写框
  onRewrite: () => void;
  // 历史模式:从章节列表「审核报告」打开(非刚生成完)。标题换为「审核报告」,
  // 无当次一致性检查数据时不显示"检查通过"徽标;拦截状态按章节 status 推导
  historical?: boolean;
}

export default function GenResultCard({ pid, result, onChanged, onRewrite, historical }: Props) {
  const { run } = useJob();
  const n = result.chapter_number;
  // 问题清单:挂载后按章拉取(与生成响应里的 consistency_issues 互补,这份可操作)
  const [issues, setIssues] = useState<ChapterIssue[] | null>(null);
  const [issuesErr, setIssuesErr] = useState("");
  // 单条问题操作进行中(禁用该条按钮);门禁放行进行中
  const [busyIssue, setBusyIssue] = useState<number | null>(null);
  const [gateBusy, setGateBusy] = useState(false);
  // 契约重提进行中(docs/08 §8:契约错了会导致门禁误报/下章衔接错位)
  const [contractBusy, setContractBusy] = useState(false);

  const reloadIssues = useCallback(async () => {
    try {
      setIssues(await api.listChapterIssues(pid, n));
      setIssuesErr("");
    } catch (e) {
      setIssuesErr(errMsg(e));
    }
  }, [pid, n]);

  useEffect(() => {
    setIssues(null); setIssuesErr("");
    let cancelled = false;
    api.listChapterIssues(pid, n)
      .then((list) => { if (!cancelled) setIssues(list); })
      .catch((e) => { if (!cancelled) setIssuesErr(errMsg(e)); });
    return () => { cancelled = true; };
  }, [pid, n]);

  // open → resolved(已人工改完)/ ignored(确认忽略)
  async function markIssue(issue: ChapterIssue, status: "resolved" | "ignored") {
    setBusyIssue(issue.id);
    try {
      await api.patchChapterIssue(pid, n, issue.id, status);
      toast.ok(status === "resolved" ? "已标记为人工解决" : "已忽略该问题");
      await reloadIssues();
    } catch (e) {
      toast.err("问题状态更新失败", errMsg(e));
    } finally {
      setBusyIssue(null);
    }
  }

  // 按建议修订:异步重写 job(受理即标 resolved,门禁重跑后未消除的问题会重新 open);
  // 409 = 本章/队列已有任务在跑
  async function applyRevision(issue: ChapterIssue) {
    setBusyIssue(issue.id);
    try {
      const res = await run<GenerateChapterResponse & { applied_issue_id?: number }>(
        () => api.applyIssueRevision(pid, n, issue.id),
        { kind: `chapter-${pid}-${n}` },
      );
      if (res) {
        toast.ok(`第 ${n} 章已按建议修订`, "正文已更新,一致性门禁已重跑");
        await reloadIssues();
        onChanged();
      }
    } catch (e) {
      toast.err("修订失败", errMsg(e));
      await reloadIssues();
    } finally {
      setBusyIssue(null);
    }
  }

  // quarantined 放行:确认忽略全部 blocker → 异步补走圣经/摘要链路,状态回 pending_review
  async function releaseGate() {
    const blockers = result.gate?.blockers?.length ?? 0;
    const ok = await confirmDialog({
      title: `放行第 ${n} 章?`,
      body: `将忽略全部${blockers ? ` ${blockers} 个` : ""}致命矛盾,并补走圣经抽取/滚动摘要等被跳过的同步。矛盾仍留在正文里,后续章节可能继续触发提醒。`,
      confirmText: "放行(忽略全部)",
      danger: true,
    });
    if (!ok) return;
    setGateBusy(true);
    try {
      const res = await run(() => api.gateRelease(pid, n), { kind: `gate-release-${pid}-${n}` });
      if (res !== null) {
        toast.ok(`第 ${n} 章已放行`, "状态变为「待审」,圣经/摘要已补齐");
        await reloadIssues();
        onChanged();
      }
    } catch (e) {
      toast.err("放行失败", errMsg(e));
    } finally {
      setGateBusy(false);
    }
  }

  // 契约重提:按当前正文重提上一章+本章契约,并重检本章门禁(gate 清单重建)。
  // quarantined 章重检干净后状态不变,仍需放行(后端语义,结果里只报数字)。
  async function reextractContract() {
    setContractBusy(true);
    try {
      const res = await run<{
        contract_status: string; contract_error?: string;
        issues: number; blockers: number;
      }>(
        () => api.reextractContract(pid, n),
        { kind: `contract-${pid}-${n}` },
      );
      if (res) {
        if (res.contract_status !== "ok") {
          toast.err("本章契约提取失败", res.contract_error || "已留痕,可稍后重试");
        } else if (res.blockers > 0) {
          toast.info("契约已重提,门禁重检仍有硬矛盾",
            `共 ${res.issues} 个问题(${res.blockers} 个致命),见下方清单`);
        } else {
          toast.ok("契约已重提,门禁重检通过",
            res.issues ? `仍有 ${res.issues} 个非致命问题` : "未发现一致性问题");
        }
        await reloadIssues();
        onChanged();
      }
    } catch (e) {
      toast.err("契约重提失败", errMsg(e));
    } finally {
      setContractBusy(false);
    }
  }

  const gate = result.gate;
  // 历史模式无当次 gate 数据:按章节 status 推导拦截态(blocker 明细在下方问题清单里)
  const quarantined = gate?.status === "quarantined" || result.status === "quarantined";
  const blockerCount = gate?.blockers?.length ?? 0;
  const warnings = result.preflight?.warnings ?? [];
  const openIssues = (issues ?? []).filter((i) => i.status === "open");
  const doneIssues = (issues ?? []).filter((i) => i.status !== "open");

  return (
    <div className={"card " + (quarantined ? "card-warn" : "card-ok")}>
      <b>{historical ? "审核报告" : "生成完成"}</b> {result.word_count} 字
      {result.ai_flavor && (
        <span className="badge" title={flavorTitle(result.ai_flavor)}>
          AI味 {result.ai_flavor.score} /千字
        </span>
      )}
      {result.ai_flavor && (
        <span className="muted"> 偏高可去「编辑部」,选「去AI味」方向</span>
      )}

      {quarantined && (
        <div className="notice notice-err gate-banner mt-2">
          <b>门禁拦截:{blockerCount ? `${blockerCount} 个` : "存在"}致命矛盾,本章未进圣经/摘要</b>
          <div className="mt-1">
            正文已保存,状态为「被拦截」。建议先按下方问题清单修订或重写;
            确认矛盾可接受时可放行(忽略全部),放行后进入「待审」。
          </div>
          <div className="issue-actions">
            <button className="danger btn-sm" disabled={gateBusy} onClick={releaseGate}>
              {gateBusy && <span className="spin spin-sm" />}放行(忽略全部)
            </button>
            <button className="btn-sm" disabled={gateBusy} onClick={onRewrite}>去重写本章</button>
          </div>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="mt-2">
          <span className="badge warn">写前提醒 {warnings.length}</span>
          <span className="muted"> 写前审核发现的疑似矛盾,只提醒不阻断</span>
          {warnings.map((w, k) => (
            <div key={k} className="fact-line">
              <b>[{w.type === "timeline" ? "时间线" : "状态"}]</b> {w.description}
              {w.evidence && <div className="muted">证据: {w.evidence}</div>}
              {w.conflicting_fact && <div className="muted">冲突设定: {w.conflicting_fact}</div>}
              {w.suggestion && <div className="muted">建议: {w.suggestion}</div>}
            </div>
          ))}
        </div>
      )}

      {result.consistency_issues.length
        ? <div className="mt-2">
            <span className="badge err">一致性问题 {result.consistency_issues.length}</span>
            {result.consistency_issues.map((i, k) => (
              <div key={k} className="fact-line">
                <b>[{i.severity}]</b> {i.description}
                <div className="muted">建议: {i.suggestion}</div>
              </div>
            ))}
          </div>
        // 历史模式没有当次一致性检查数据,不显示"检查通过"(避免误导)
        : (!historical && <span className="badge ok">一致性检查通过</span>)}

      <div className="mt-2">
        <b>问题清单</b>
        {" "}
        <button className="btn-sm" disabled={contractBusy}
          title="契约提取错了会导致本章门禁误报(对照上章契约)或下章衔接错位(注入本章契约)。点此按当前正文重提两章契约并重检本章门禁"
          onClick={reextractContract}>
          {contractBusy && <span className="spin spin-sm" />}重新提取契约
        </button>
        {issues === null && !issuesErr && (
          <span className="muted"> 加载中…</span>
        )}
        {issuesErr && (
          <span className="msg-err"> 加载失败:{issuesErr}
            <button className="btn-sm" onClick={reloadIssues}>重试</button>
          </span>
        )}
        {issues !== null && !issues.length && (
          <span className="badge ok"> 暂无未处理问题</span>
        )}
        {openIssues.map((issue) => (
          <IssueRow key={issue.id} issue={issue} busy={busyIssue === issue.id}
            onApply={() => applyRevision(issue)}
            onResolve={() => markIssue(issue, "resolved")}
            onIgnore={() => markIssue(issue, "ignored")} />
        ))}
        {doneIssues.length > 0 && (
          <details className="issue-done-box mt-1">
            <summary className="muted">已处理 {doneIssues.length} 条(已解决/已忽略)</summary>
            {doneIssues.map((issue) => (
              <IssueRow key={issue.id} issue={issue} busy={false} done />
            ))}
          </details>
        )}
      </div>

      {result.review && (
        <div className="mt-2">
          <span className={"badge " + (result.review.passed ? "ok" : "err")}>
            {result.review.passed ? "审校达标" : "审校未达标"}
          </span>
          <span className="muted">
            {" "}{Object.entries(result.review.scores)
              .map(([k, v]) => `${SCORE_LABEL[k] ?? k}${v}`)
              .join("·")}
            （达标线{result.review.threshold}）
          </span>
          {result.review.revision_rounds > 0 && (
            <span className="badge"> 自动回炉 {result.review.revision_rounds} 轮</span>
          )}
          {result.review.comment && (
            <div className="muted">主审:{result.review.comment}</div>
          )}
        </div>
      )}
      {result.word_guard_action === "compressed" && (
        <div className="mt-2">
          <span className="badge">字数守卫:已压缩至目标范围</span>
        </div>
      )}
      {result.word_guard_action === "split" && result.split_info && (
        <div className="mt-2">
          <span className="badge err">字数守卫:已自动拆章</span>
          <div className="fact-line">
            原第{result.split_info.original_chapter}章 →
            第{result.split_info.original_chapter}章({result.split_info.part_a_words}字)
            + 第{result.split_info.new_chapter}章《{result.split_info.new_title}》({result.split_info.part_b_words}字)
          </div>
          {result.split_info.reason && (
            <div className="muted">断点:{result.split_info.reason}</div>
          )}
        </div>
      )}
    </div>
  );
}

// 单条问题:severity 徽标 + 来源 + 描述 + 可折叠证据 + 建议;open 状态给三个操作
function IssueRow({ issue, busy, done, onApply, onResolve, onIgnore }: {
  issue: ChapterIssue;
  busy: boolean;
  done?: boolean;
  onApply?: () => void;
  onResolve?: () => void;
  onIgnore?: () => void;
}) {
  return (
    <div className={"fact-line issue-item" + (done ? " issue-done" : "")}>
      <div className="issue-head">
        <span className={"badge " + (SEV_BADGE[issue.severity] ?? "")}>
          {SEV_CN[issue.severity] ?? issue.severity}
        </span>
        <span className="badge">{SOURCE_CN[issue.source] ?? issue.source}</span>
        {done && <span className="badge">{ISSUE_STATUS_CN[issue.status] ?? issue.status}</span>}
        <span>{issue.description}</span>
      </div>
      {issue.evidence && (
        <details className="issue-ev">
          <summary>证据(原文引用)</summary>
          <blockquote>{issue.evidence}</blockquote>
        </details>
      )}
      {issue.suggestion && <div className="muted">建议: {issue.suggestion}</div>}
      {!done && (
        <div className="issue-actions">
          <button className="primary btn-sm" disabled={busy}
            title="把该问题的修正建议交给 AI 走重写链路(受理即标记解决,门禁会重跑验证)"
            onClick={onApply}>
            {busy && <span className="spin spin-sm" />}按建议修订
          </button>
          <button className="btn-sm" disabled={busy} onClick={onResolve}>已人工解决</button>
          <button className="btn-sm" disabled={busy} onClick={onIgnore}>忽略</button>
        </div>
      )}
    </div>
  );
}
