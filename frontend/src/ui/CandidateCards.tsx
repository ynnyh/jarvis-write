// CandidateCards:向导通用的"AI 抽卡"候选卡组。
// 加载期 skeleton(shimmer)→ 内容逐张落定;四操作:选用 / 换一批 / 带一句话反馈重新生成 / 自己写。
// 选用时卡片以 motion layoutId 共享元素过渡"飞入"顶部步骤条缩略位(调用方用同一 layoutId 渲染缩略卡)。
import { ReactNode, useState } from "react";
import { motion, useReducedMotion } from "motion/react";

export interface CandidateCardsProps<T> {
  items: T[] | null;                 // null = 生成中,渲染 skeleton
  skeletonCount?: number;
  keyOf: (item: T) => string;
  renderCard: (item: T) => ReactNode;
  onPick: (item: T) => void;         // 选用
  onRefresh?: () => void;            // 换一批
  onRefine?: (feedback: string) => void; // 带一句话反馈重新生成
  onCustom?: () => void;             // 自己写(展开/聚焦手输区,由调用方渲染)
  busy?: boolean;                    // 外部任务进行中,禁用操作
  layoutIdPrefix: string;            // FLIP 缩略位 layoutId:`wiz-thumb-${layoutIdPrefix}`
  pickedKey?: string | null;         // 已选用项的 key(触发飞出动画)
  pickLabel?: string;                // 选用按钮文案,默认"用这个"
}

export function CandidateCards<T>({
  items, skeletonCount = 3, keyOf, renderCard,
  onPick, onRefresh, onRefine, onCustom,
  busy = false, layoutIdPrefix, pickedKey = null, pickLabel = "用这个",
}: CandidateCardsProps<T>) {
  const reduce = useReducedMotion();
  const [refineOpen, setRefineOpen] = useState(false);
  const [feedback, setFeedback] = useState("");

  // 生成中:skeleton 占位(shimmer 走 CSS)
  if (items === null) {
    return (
      <div className="wiz-cands">
        {Array.from({ length: skeletonCount }, (_, i) => (
          <div key={i} className="wiz-cand wiz-cand-skeleton">
            <div className="wiz-sk-line w60" />
            <div className="wiz-sk-line" />
            <div className="wiz-sk-line" />
            <div className="wiz-sk-line w40" />
          </div>
        ))}
      </div>
    );
  }

  function submitRefine() {
    const f = feedback.trim();
    if (!f || !onRefine) return;
    setRefineOpen(false);
    setFeedback("");
    onRefine(f);
  }

  return (
    <div>
      <div className="wiz-cands">
        {items.map((item, i) => {
          const k = keyOf(item);
          const picked = pickedKey === k;
          const cls = "wiz-cand" + (picked ? " picked" : "");
          const body = (
            <>
              <div className="wiz-cand-body">{renderCard(item)}</div>
              <div className="wiz-cand-foot">
                <button className="primary btn-sm" disabled={busy} onClick={() => onPick(item)}>
                  {pickLabel}
                </button>
              </div>
            </>
          );
          // reduced-motion 下退化为普通卡片,不做位移/FLIP
          if (reduce) return <div key={k} className={cls}>{body}</div>;
          return (
            <motion.div
              key={k}
              className={cls}
              layoutId={picked ? `wiz-thumb-${layoutIdPrefix}` : undefined}
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: pickedKey && !picked ? 0.4 : 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ duration: 0.3, delay: pickedKey ? 0 : i * 0.08 }}
            >
              {body}
            </motion.div>
          );
        })}
      </div>

      <div className="wiz-cand-ops">
        {onRefresh && (
          <button className="btn-sm" disabled={busy} onClick={onRefresh}>↻ 换一批</button>
        )}
        {onRefine && (
          <button className="btn-sm" disabled={busy}
            onClick={() => setRefineOpen((v) => !v)}>
            💬 带反馈重新生成
          </button>
        )}
        {onCustom && (
          <button className="btn-sm" disabled={busy} onClick={onCustom}>✍️ 自己写</button>
        )}
      </div>

      {refineOpen && onRefine && (
        <div className="input-row mt-2">
          <input
            type="text" autoFocus value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="一句话说说哪里不满意,如:太俗了,要冷峻一点"
            onKeyDown={(e) => e.key === "Enter" && submitRefine()}
          />
          <button className="btn-sm primary" disabled={!feedback.trim() || busy}
            onClick={submitRefine}>
            重新生成
          </button>
        </div>
      )}
    </div>
  );
}
