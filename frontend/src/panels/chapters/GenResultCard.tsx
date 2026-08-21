// 生成结果卡片(章节审核面板):字数/AI味、门禁状态、五维审校分、
// 写前提醒(preflight)与可操作问题清单(按建议修订/人工解决/忽略)。
// 门禁拦截时(仅刚生成完的非历史卡)顶部出统一处理卡 GateResolve(说人话三选一:让 AI 按这条
// 重写 / 去改设定 / 就这样忽略继续 + 重新检查);历史模式由章首 ChapterStatusCard 的 GateResolve
// 承接,这里只做完整报告明细,不重复出卡。
import { useCallback, useEffect, useState } from "react";
import {
  api, ChapterIssue, flavorTitle, GenerateChapterResponse,
} from "../../api";
import { errMsg } from "../../pollJob";
import { dispatchAction } from "../../ui/actions";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import GateResolve from "./GateResolve";

// 审校五维分中文标签;旧快照无 continuity 键,Object.entries 遍历天然不渲染该行
const SCORE_LABEL: Record<string, string> = {
  plot: "情节", prose: "文笔", pacing: "节奏", character: "人物", continuity: "连续性",
};
// 问题严重度:徽标配色 + 中文
const SEV_BADGE: Record<string, string> = { blocker: "err", major: "warn", minor: "" };
const SEV_CN: Record<string, string> = { blocker: "致命", major: "重要", minor: "次要" };
// 问题来源中文
const SOURCE_CN: Record<string, string> = {
  gate: "门禁", preflight: "预审", diag: "诊断", review: "审校", rules: "规则",
};
const ISSUE_STATUS_CN: Record<string, string> = { resolved: "已人工解决", ignored: "已忽略" };
// AI 味偏高分界线(/千字,加权命中+统计罚分;经验值,可调):超过即提示一键去味
const FLAVOR_HIGH = 6;

interface Props {
  pid: number;
  result: GenerateChapterResponse;
  // 章节数据有变(修订完成/放行后状态变化):刷新章节列表与打开的正文
  onChanged: () => void;
  // 「重写」引导:展开本章的行内重写框
  onRewrite: () => void;
  // 关闭按钮(右上角 ×):仅刚生成完的结果卡传入(WritePanel setGenResult(null));
  // 历史模式(审核报告)由抽屉/sheet 容器负责关闭,不传
  onClose?: () => void;
  // 任务锁(坏味道 #9 统一):有生成/重写任务在跑时禁用「按建议修订」与门禁横幅
  // 「放行」(gate-release 后端对进行中的章节任务返回 409),title 给原因
  genBlocked?: boolean;
  genHint?: string;
  // 历史模式:从章节列表「审核报告」打开(非刚生成完)。标题换为「审核报告」,
  // 无当次一致性检查数据时不显示"检查通过"徽标;拦截状态按章节 status 推导
  historical?: boolean;
}

export default function GenResultCard({ pid, result, onChanged, onRewrite, onClose, genBlocked, genHint, historical }: Props) {
  const { run } = useJob();
  const n = result.chapter_number;
  // 问题清单:挂载后按章拉取(与生成响应里的 consistency_issues 互补,这份可操作)
  const [issues, setIssues] = useState<ChapterIssue[] | null>(null);
  const [issuesErr, setIssuesErr] = useState("");
  // 单条问题操作进行中(禁用该条按钮)
  const [busyIssue, setBusyIssue] = useState<number | null>(null);

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

  const gate = result.gate;
  // 历史模式无当次 gate 数据:按章节 status 推导拦截态(blocker 明细在下方问题清单里)
  const quarantined = gate?.status === "quarantined" || result.status === "quarantined";
  const warnings = result.preflight?.warnings ?? [];
  const openIssues = (issues ?? []).filter((i) => i.status === "open");
  const doneIssues = (issues ?? []).filter((i) => i.status !== "open");

  return (
    <div className={"card " + (quarantined ? "card-warn" : "card-ok")}>
      <div className="card-head">
        <b className="grow">{historical ? "审核报告" : "生成完成"}</b>
        {/* 关闭入口(引导断裂修复):结果卡此前只能切章才消失,给用户明确的退出路径 */}
        {onClose && (
          <button className="btn-sm" title="关闭结果卡(正文已保存,可随时从章首状态卡「查看审核报告」复查)"
            onClick={onClose}>×</button>
        )}
      </div>
      {result.word_count} 字
      {result.ai_flavor && (
        <span className={"badge" + (result.ai_flavor.score >= FLAVOR_HIGH ? " warn" : "")}
          title={flavorTitle(result.ai_flavor)}>
          AI味 {result.ai_flavor.score} /千字
        </span>
      )}
      {result.ai_flavor && result.ai_flavor.score >= FLAVOR_HIGH && (
        <>
          <span className="muted"> 腔调偏重,建议过一遍去味</span>
          <button className="btn-sm" title="打开 AI 栏梳理整章优化意见(默认去AI味方向)"
            onClick={() => dispatchAction("polish")}>
            一键去味
          </button>
        </>
      )}

      {/* 门禁拦截(仅刚生成完的非历史卡):顶部出统一处理卡,说人话三选一。
          历史模式由章首 ChapterStatusCard 的 GateResolve 承接,这里不重复出卡。 */}
      {quarantined && !historical && (
        <div className="gate-banner mt-2">
          <GateResolve
            pid={pid}
            n={n}
            genBlocked={genBlocked}
            genHint={genHint}
            onChanged={() => { reloadIssues(); onChanged(); }}
            onRewriteFallback={onRewrite}
          />
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
            genBlocked={genBlocked} genHint={genHint}
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
function IssueRow({ issue, busy, done, genBlocked, genHint, onApply, onResolve, onIgnore }: {
  issue: ChapterIssue;
  busy: boolean;
  done?: boolean;
  // 任务锁:生成/重写任务在跑时禁用「按建议修订」(它也是一条重写链路,409 会被后端拦)
  genBlocked?: boolean;
  genHint?: string;
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
          <button className="primary btn-sm" disabled={busy || genBlocked}
            title={genBlocked ? genHint : "把该问题的修正建议交给 AI 走重写链路(受理即标记解决,门禁会重跑验证)"}
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
