// 章首交稿单(「正文即界面」P4,docs/10 §5「章首交稿单(AI 自检)」;沿用 §0「说人话」):
// deriveStage 的内部六阶段在此映射成三态用户语言,并以一句话结论开头(数据全部来自现有信号,
// 不新增自动 job):校对快照(GET /proofread)+ 一致性门禁(quarantined + open issues 计数)。
//   ——「AI 味」不在此展示:它不是廉价单章信号(仅持久化在全书 overview 聚合;单章要另算需
//     on-demand LLM 调用=新 job,§5 明令不新增),生成后已由 GenResultCard 即时给出。故交稿单
//     的一句话只用「校对 + 冲突」两项组合,这是相对 §5 示例的一处有意取舍。
// 三态:
//   review  → 「第 N 章写好了——{校对结论},和设定没冲突。」+ 有小错时[查看并修复]+ [通过审核]。
//   blocked → 「第 N 章写好了,但有 X 处和设定冲突,等你拍板。」+ [查看审核报告](展开 GenResultCard
//     historical,按建议修订在卡内)+ [放行](confirmAndReleaseGate 已含确认);另有校对小错时给校对子行。
//   is_stale → 徽标「大纲已变,建议重写」+ [和 AI 梳理](打开 AI 窄栏 revise 通道)。
// 无事不打扰:approved 且无 stale 时不渲染交稿单。校对「按建议修复」→ 复用 ProofreadCard(act=proofread)
// 的逐条 diff + 修复流(§5:把校对问题带进 diff 验收流)。
// 底部「更多」保留评分/校对/历史版本全量入口:交稿单已把当前章的校对/冲突提到显眼处,这里覆盖 approved
// 章的可达性(§8:动作卡外壳最终退场,本轮 ProofreadCard/ReviewCard 作为明细面板经 act= 复用,故不删)。
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  api, ChapterBrief, ChapterDetail, ChapterReview, GenerateChapterResponse, ProofreadSnapshot,
} from "../../api";
import { qk } from "../../hooks/queries";
import { confirmDialog } from "../../ui/ConfirmDialog";
import GenResultCard from "../chapters/GenResultCard";
import type { ChapterStage } from "./chapterStage";

// 章级动作:revise=打开 AI 窄栏梳理意见;proofread/review=act= 明细面板(与 WritePanel 的 Act 一致)
type Act = "revise" | "proofread" | "review";

interface Props {
  pid: number;
  stage: ChapterStage;
  currentBrief: ChapterBrief | undefined;
  current: ChapterDetail;
  genBlocked: boolean;
  genHint: string;
  onApprove: () => Promise<void> | void;
  onRelease: () => Promise<void> | void;
  onAct: (act: Act) => void;
  onVersions: () => void;
  // 审核报告卡内操作(修订/放行)后:父级刷新章节列表与正文缓存
  onChanged: () => void;
}

export default function ChapterStatusCard({
  pid, stage, currentBrief, current, genBlocked, genHint,
  onApprove, onRelease, onAct, onVersions, onChanged,
}: Props) {
  const qc = useQueryClient();
  const n = current.chapter_number;
  // 主审快照(仅 blocked 时拉取;正文改过则后端返 null,自动不展示过期评分)
  const [reviewSnap, setReviewSnap] = useState<ChapterReview | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [approving, setApproving] = useState(false);
  const [releasing, setReleasing] = useState(false);
  // 交稿单自检信号(现有信号,不新增 job):校对快照 + blocked 时的冲突条数
  const [proof, setProof] = useState<ProofreadSnapshot | null>(null);
  const [conflictCount, setConflictCount] = useState(0);
  const stale = !!currentBrief?.is_stale;

  useEffect(() => {
    setReportOpen(false);
    if (stage !== "blocked") { setReviewSnap(null); return; }
    let cancelled = false;
    api.getReview(pid, n)
      .then((r) => { if (!cancelled) setReviewSnap(r.review); })
      .catch(() => { if (!cancelled) setReviewSnap(null); });
    return () => { cancelled = true; };
  }, [stage, pid, n]);

  // 校对自检快照(现有信号,GET /proofread;正文改过后端返 null → 不展示,不重算)。
  // 只在写好待过目/被拦下时取——一句话结论要用到;stale-only 与 approved 不需要。
  useEffect(() => {
    if (stage !== "review" && stage !== "blocked") { setProof(null); return; }
    let cancelled = false;
    api.getProofread(pid, n)
      .then((r) => { if (!cancelled) setProof(r.proofread); })
      .catch(() => { if (!cancelled) setProof(null); });
    return () => { cancelled = true; };
  }, [stage, pid, n]);

  // blocked 冲突条数(现有信号,GET /issues 中仍 open 的条目;拉取失败回落 0 → 文案不带数字)
  useEffect(() => {
    if (stage !== "blocked") { setConflictCount(0); return; }
    let cancelled = false;
    api.listChapterIssues(pid, n)
      .then((issues) => {
        if (!cancelled) setConflictCount(issues.filter((i) => i.status === "open").length);
      })
      .catch(() => { if (!cancelled) setConflictCount(0); });
    return () => { cancelled = true; };
  }, [stage, pid, n]);

  // 历史模式合成生成响应(照抄旧 RefDrawer.tsx 的 reviewReportResult 那套):
  // 门禁态由 status 推导,问题清单由卡片内自取,主审分用快照
  const reviewReportResult: GenerateChapterResponse | null =
    stage === "blocked" ? {
      ...current,
      consistency_issues: [],
      extraction_stats: {},
      ai_flavor: null,
      review: reviewSnap ? {
        scores: reviewSnap.scores,
        comment: reviewSnap.comment,
        suggestions: reviewSnap.suggestions,
        passed: reviewSnap.passed ?? false,
        revision_rounds: reviewSnap.revision_rounds ?? 0,
        threshold: reviewSnap.threshold ?? 7,
      } : undefined,
    } : null;

  // 「通过审核」带一次确认(沿用旧 StageBar/FAB 语义,误触代价高)
  async function approveWithConfirm() {
    const ok = await confirmDialog({
      title: `通过第 ${n} 章审核?`,
      body: "确认本章可定稿,通过后状态变为「已审」。",
      confirmText: "通过审核",
    });
    if (!ok) return;
    setApproving(true);
    try { await onApprove(); } finally { setApproving(false); }
  }

  // 「放行」走父级 releaseChapter(confirmAndReleaseGate 已含确认)
  async function releaseWithBusy() {
    setReleasing(true);
    try { await onRelease(); } finally { setReleasing(false); }
  }

  // 校对一句话结论:没问题 / 已修掉 M 处 / 还有 N 处待修(proof 为 null=正文改过,不出结论)
  const proofLine = proof
    ? (proof.issues.length === 0
        ? (proof.fixed > 0 ? `校对已修掉 ${proof.fixed} 处小错` : "校对没发现问题")
        : `校对还有 ${proof.issues.length} 处小错待修`)
    : "";
  const proofHasIssues = !!proof && proof.issues.length > 0;

  // 一切正常(已定稿且无大纲变动)时状态块不渲染;「更多」过渡入口始终保留(act= 可达性红线)
  const showBlock = stage === "blocked" || stage === "review" || stale;

  return (
    <>
      {showBlock && (
        <div className={"card chapter-status"
          + (stage === "blocked" ? " card-warn" : stage === "review" ? " card-info" : "")}>
          {stage === "blocked" && (
            <>
              <div className="chapter-status-line">
                <b className="grow">
                  第 {n} 章写好了,但{conflictCount > 0 ? `有 ${conflictCount} 处` : ""}和设定冲突,等你拍板。
                </b>
                <button className="btn-sm" onClick={() => setReportOpen((v) => !v)}>
                  {reportOpen ? "收起审核报告" : "查看审核报告"}
                </button>
                <button className="danger btn-sm" disabled={releasing || genBlocked}
                  title={genBlocked ? genHint : "忽略全部致命矛盾,补走圣经/摘要链路,状态回「待审」"}
                  onClick={releaseWithBusy}>
                  {releasing && <span className="spin spin-sm" />}放行
                </button>
              </div>
              {proofHasIssues && (
                <div className="chapter-status-line chapter-status-sub">
                  <span className="muted grow">{proofLine}</span>
                  <button className="btn-sm" title="逐条查看校对建议,按建议一键修复(diff 验收)"
                    onClick={() => onAct("proofread")}>查看并修复</button>
                </div>
              )}
              {reportOpen && reviewReportResult && (
                <GenResultCard
                  pid={pid}
                  result={reviewReportResult}
                  historical
                  genBlocked={genBlocked}
                  genHint={genHint}
                  onChanged={() => {
                    onChanged();
                    // 修订/放行后同步刷新主审快照
                    qc.invalidateQueries({ queryKey: qk.chapter(pid, n) });
                    api.getReview(pid, n)
                      .then((r) => setReviewSnap(r.review))
                      .catch(() => undefined);
                  }}
                  onRewrite={() => onAct("revise")}
                />
              )}
            </>
          )}
          {stage === "review" && (
            <div className="chapter-status-line">
              <b className="grow">
                第 {n} 章写好了{proofLine ? `——${proofLine},和设定没冲突` : ",等你过目"}。
              </b>
              {proofHasIssues && (
                <button className="btn-sm" title="逐条查看校对建议,按建议一键修复(diff 验收)"
                  onClick={() => onAct("proofread")}>查看并修复</button>
              )}
              <button className="primary btn-sm" disabled={approving || genBlocked}
                title={genBlocked ? genHint : "人工审核通过:确认本章可定稿"}
                onClick={approveWithConfirm}>
                {approving && <span className="spin spin-sm" />}通过审核
              </button>
            </div>
          )}
          {stale && (
            <div className="chapter-status-line">
              <span className="badge warn grow">大纲已变,建议重写</span>
              <button className="btn-sm" disabled={genBlocked}
                title={genBlocked ? genHint : "打开 AI 栏梳理修改意见,再按新大纲重跑本章"}
                onClick={() => onAct("revise")}>
                和 AI 梳理
              </button>
            </div>
          )}
        </div>
      )}

      {/* 评分/校对/历史版本全量入口:交稿单已把当前章校对/冲突提到显眼处,这里覆盖 approved 章的可达性
          (§8:动作卡外壳最终退场,本轮作为明细面板经 act= 复用;重写/整章优化 P2 起由 AI 窄栏承接) */}
      <details className="chapter-status-more">
        <summary>更多</summary>
        <div className="chapter-status-links">
          <button className="linkish" onClick={() => onAct("proofread")}>校对</button>
          <button className="linkish" onClick={() => onAct("review")}>给这章打个分</button>
          <button className="linkish" disabled={genBlocked}
            title={genBlocked ? genHint : undefined}
            onClick={onVersions}>历史版本</button>
        </div>
      </details>
    </>
  );
}
