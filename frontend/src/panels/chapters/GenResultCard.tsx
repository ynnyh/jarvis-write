// 生成结果卡片:字数、AI味、一致性问题、字数守卫动作
import { flavorTitle, GenerateChapterResponse } from "../../api";

export default function GenResultCard({ result }: { result: GenerateChapterResponse }) {
  return (
    <div className="card card-ok">
      <b>生成完成</b> {result.word_count} 字
      {result.ai_flavor && (
        <span className="badge" title={flavorTitle(result.ai_flavor)}>
          AI味 {result.ai_flavor.score} /千字
        </span>
      )}
      {result.ai_flavor && (
        <span className="muted"> 偏高可去「润色」,选「去AI味」方向</span>
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
        : <span className="badge ok">一致性检查通过</span>}
      {result.review && (
        <div className="mt-2">
          <span className={"badge " + (result.review.passed ? "ok" : "err")}>
            {result.review.passed ? "审校达标" : "审校未达标"}
          </span>
          <span className="muted">
            {" "}情节{result.review.scores.plot}·文笔{result.review.scores.prose}·
            节奏{result.review.scores.pacing}·人物{result.review.scores.character}
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
