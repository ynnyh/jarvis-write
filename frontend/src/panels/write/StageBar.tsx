// write 区中栏顶部的阶段条:状态驱动流水线的呈现层(替代已删除的底部动作条)。
// 阶段由 chapterStage.deriveStage 纯函数推断(与移动端 FAB 共用同一份),
// 只摆当前阶段的 1-2 个主动作,其余章级动作收进右侧「更多动作 ▾」下拉。
// 主动作回调全部由 WritePanel 注入(复用 generate/approve/releaseChapter/openReviseWith/setAct/openVersions)。
import { useState } from "react";
import { ChapterStage, STAGE_SPEC } from "./chapterStage";

// 「更多动作」下拉的章级动作(与原底部动作条一一对应,顺序不变)
type MoreAct = "revise" | "polish" | "proofread" | "review";

interface Props {
  stage: ChapterStage;
  isMobile: boolean;      // unselected 阶段:移动端给「选择章节」主动作,桌面只给提示文字(左栏即章节轨)
  stale: boolean;         // is_stale 修饰:叠加「大纲已变,建议重写」,不另立阶段
  genStage: string;       // generating 阶段的进度文案(genJob.stage)
  genBlocked: boolean;    // 任务锁:有生成/重写任务在跑时锁住章级动作(坏味道 #9 统一)
  genHint: string;
  actBusy: boolean;       // 通过审核/放行进行中(禁用对应主动作防连点)
  hasCurrent: boolean;    // 当前章正文已加载(重写/润色/校对/评分/历史版本的 disabled 依据)
  nextNum: number | null; // 下一个未写章号(approved 主动作;null=全部写完)
  onOpenRail: () => void;
  onGenerate: () => void;
  onApprove: () => void;
  onRelease: () => void;
  onOpenReview: () => void;
  onNextChapter: () => void;
  onAct: (act: MoreAct) => void;
  onVersions: () => void;
  onOpenSettings: () => void;
}

export default function StageBar({
  stage, isMobile, stale, genStage, genBlocked, genHint, actBusy, hasCurrent, nextNum,
  onOpenRail, onGenerate, onApprove, onRelease, onOpenReview, onNextChapter,
  onAct, onVersions, onOpenSettings,
}: Props) {
  const spec = STAGE_SPEC[stage];
  const [moreOpen, setMoreOpen] = useState(false);
  // 章级动作的通用 disabled/title(与原底部动作条规则一致 + genBlocked 任务锁)
  const actDisabled = !hasCurrent || genBlocked;
  const actTitle = !hasCurrent
    ? "先选一章已生成的章节"
    : genBlocked ? genHint : undefined;

  const fire = (fn: () => void) => () => { setMoreOpen(false); fn(); };

  return (
    <div className="stage-bar">
      <span className={"stage-dot st-" + stage} />
      <b className="stage-name">{spec.label}</b>
      <span className="stage-guide">
        {stage === "generating" ? genStage || spec.guide : spec.guide}
        {stale && stage !== "generating" && (
          <span className="badge warn"> 大纲已变,建议重写</span>
        )}
      </span>

      {/* ---- 主动作:每阶段 1-2 个,由阶段模型决定(见 chapterStage.ts) ---- */}
      {stage === "unselected" && isMobile && (
        <button className="primary btn-sm" onClick={onOpenRail}>选择章节</button>
      )}
      {stage === "empty" && (
        <button className="primary btn-sm" disabled={genBlocked}
          title={genBlocked ? genHint : undefined}
          onClick={onGenerate}>
          生成本章
        </button>
      )}
      {stage === "generating" && (
        <button className="primary btn-sm" disabled>
          <span className="spin spin-sm" />生成中…
        </button>
      )}
      {stage === "blocked" && (
        <>
          <button className="primary btn-sm" onClick={onOpenReview}>去处理</button>
          <button className="danger btn-sm" disabled={actBusy || genBlocked}
            title={genBlocked ? genHint : "忽略全部致命矛盾,补走圣经/摘要链路,状态回「待审」"}
            onClick={onRelease}>
            {actBusy && <span className="spin spin-sm" />}放行
          </button>
        </>
      )}
      {stage === "review" && (
        <button className="primary btn-sm" disabled={actBusy || genBlocked}
          title={genBlocked ? genHint : "人工审核通过:确认本章可定稿"}
          onClick={onApprove}>
          {actBusy && <span className="spin spin-sm" />}通过审核
        </button>
      )}
      {stage === "approved" && (
        nextNum !== null ? (
          <button className="primary btn-sm"
            title={`选中第 ${nextNum} 章(看一眼蓝图再点生成)`}
            onClick={onNextChapter}>
            写下一章(第{nextNum}章)
          </button>
        ) : (
          <span className="muted">已全部写完</span>
        )
      )}

      <div className="grow" />

      {/* ---- 次动作:重写/润色/校对/评分/历史版本,收进下拉 ---- */}
      <div className="stage-more">
        <button className="btn-sm" onClick={() => setMoreOpen((v) => !v)}>更多动作 ▾</button>
        {moreOpen && (
          <>
            <div className="stage-more-backdrop" onClick={() => setMoreOpen(false)} />
            <div className="stage-more-menu">
              <button type="button" disabled={actDisabled}
                title={actTitle ?? "重写前旧版会自动存快照,可回退"}
                onClick={fire(() => onAct("revise"))}>重写</button>
              <button type="button" disabled={actDisabled} title={actTitle}
                onClick={fire(() => onAct("polish"))}>润色</button>
              <button type="button" disabled={actDisabled} title={actTitle}
                onClick={fire(() => onAct("proofread"))}>校对</button>
              <button type="button" disabled={actDisabled} title={actTitle}
                onClick={fire(() => onAct("review"))}>评分</button>
              <button type="button" disabled={actDisabled} title={actTitle}
                onClick={fire(onVersions)}>历史版本</button>
            </div>
          </>
        )}
      </div>
      <button className="btn-sm stage-settings" title="本书设置:字数守卫 / 审校把关 / 世界观硬规则"
        onClick={onOpenSettings}>⚙︎</button>
    </div>
  );
}
