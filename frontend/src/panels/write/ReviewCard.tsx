// 主编评分卡(write 区 act=review):从原 EditorialPanel review 页签拆出。
// 「按此重写/全部意见转为重写指令」P2 起改为打开 AI 窄栏 revise 通道并预填意见文本。
import { useEffect, useState } from "react";
import { api, ChapterReview, ReviewSuggestion } from "../../api";
import { useJob } from "../../ui/useJob";
import GateRepairDetails from "../../ui/GateRepairDetails";
import { errMsg } from "../../pollJob";

const SCORE_LABEL: Record<string, string> = {
  plot: "情节", prose: "文笔", pacing: "节奏", character: "人物", continuity: "连续性",
};

interface Props {
  pid: number;
  chapterNum: number;
  // 把建议打包成重写指令文本,交给 write 区的 AI 窄栏(预填意见文本)
  onRevise: (text: string) => void;
}

export default function ReviewCard({ pid, chapterNum, onRevise }: Props) {
  const { run: runJob } = useJob();
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [review, setReview] = useState<ChapterReview | null>(null);

  // 回显:生成时/手动审校的结果都存在章节上,打开直接读最近一次;
  // 正文改动后指纹失配后端返回 null,不会展示过期评分
  useEffect(() => {
    let cancelled = false;
    setReview(null);
    api.getReview(pid, chapterNum).then((r) => {
      if (!cancelled && r.review) setReview(r.review);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [pid, chapterNum]);

  async function runReview() {
    setBusy("主编审读中(约 1 分钟)…"); setErr(""); setReview(null);
    try {
      const r = await runJob<ChapterReview>(
        () => api.reviewChapterAsync(pid, chapterNum),
        { kind: `review-${pid}-${chapterNum}` },
      );
      if (r) setReview(r);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  // 建议 → 重写指令文本(与旧 EditorialPanel 交接格式一致,上限 500 字)
  function sugText(sugs: ReviewSuggestion[]) {
    return sugs
      .map((s) => (s.evidence ? `"${s.evidence}"这里` : "") + s.issue + (s.fix ? `,改法:${s.fix}` : ""))
      .join(";")
      .slice(0, 500);
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">主编评分 · 第{chapterNum}章</h3>
        <button className="primary" disabled={!!busy} onClick={runReview}>
          {busy && <span className="spin" />}请主编审读
        </button>
      </div>
      <div className="card-desc mt-1">情节/文笔/节奏/人物四维打分 + 修改建议。</div>
      {busy && <div className="muted mt-2">{busy}(可切到别处,进度看右上角任务)</div>}
      {err && <div className="msg-err mt-2">{err}</div>}
      {review && (
        <div className="mt-3">
          <div className="review-meta">
            <span className="badge">{review.source === "generation" ? "生成时审校" : "手动审校"}</span>
            {review.reviewed_at && (
              <span className="hint">{new Date(review.reviewed_at).toLocaleString("zh-CN", {
                month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
              })}</span>
            )}
            {!!review.revision_rounds && <span className="hint">回炉 {review.revision_rounds} 轮</span>}
            {!!review.repair_rounds && (
              <span className="hint" title="一致性矛盾由 AI 定点改句消除,未整章重写">
                门禁定点修复 {review.repairs?.applied.length ?? 0} 处
              </span>
            )}
            {!!review.proofread_fixed && <span className="hint">已自动修复 {review.proofread_fixed} 处硬伤</span>}
          </div>
          <GateRepairDetails repairs={review.repairs} />
          <div className="score-row">
            {Object.entries(review.scores).map(([k, v]) => (
              <div key={k} className={"score-item" + (v > 0 && v < 6 ? " low" : "")}>
                <b>{v || "—"}</b>
                <span>{SCORE_LABEL[k] ?? k}</span>
              </div>
            ))}
          </div>
          {review.passed !== undefined && (
            <div className="mt-2">
              <span className={"badge " + (review.passed ? "ok" : "err")}>
                {review.passed ? "已达达标线" : "未达达标线"}
                {review.threshold ? `(四维均需 ≥${review.threshold})` : ""}
              </span>
            </div>
          )}
          {review.comment && <div className="notice notice-info mt-3">{review.comment}</div>}
          {review.suggestions.length > 0 && (
            <div className="mt-3">
              <label className="fl">最该改的三件事(可一键转成本章重写指令)</label>
              {review.suggestions.map((s, i) => (
                <div key={i} className="review-sug">
                  <div className="rs-head">
                    <b>{i + 1}. {s.issue}</b>
                    <button className="btn-sm" title="带着这条意见打开 AI 栏梳理"
                      onClick={() => onRevise(sugText([s]))}>→ 按此重写</button>
                  </div>
                  {s.evidence && <blockquote className="rs-quote">"{s.evidence}"</blockquote>}
                  {s.fix && <div className="rs-fix">改法:{s.fix}</div>}
                </div>
              ))}
              <div className="actions mt-2">
                <button className="primary btn-sm" onClick={() => onRevise(sugText(review.suggestions))}>
                  全部意见转为重写指令
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
