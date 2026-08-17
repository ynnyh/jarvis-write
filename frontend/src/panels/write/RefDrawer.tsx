// write 区右栏「参考抽屉」:默认折叠为竖排图标条,点开 360px 抽屉,ctx= 进 URL(与 ch 共存)。
// 项:蓝图(当前章 outline 详情)/人物/伏笔(复用 BoardPanel 子板)/审核报告(当前章 review 快照)/
// 历史版本(入口,实际在中栏打开 VersionCompare)。世界观规则在 settings 区,不在此抽屉。
// 移动端(mobile=true):同一组件换容器——由 WritePanel 的全屏 sheet 包裹,
// 图标条横排在 sheet 顶部,内容区平铺(样式见 styles.css 移动端壳一节)。
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  api, ChapterDetail, ChapterReview, GenerateChapterResponse, Outline,
} from "../../api";
import { qk } from "../../hooks/queries";
import GenResultCard from "../chapters/GenResultCard";
import { CharactersBoard, ForeshadowBoard } from "../BoardPanel";

// 抽屉项:key=ctx 参数值(versions 不设 ctx,点击直接在中栏开对比)
const ITEMS: { key: string; icon: string; label: string }[] = [
  { key: "blueprint", icon: "🧭", label: "蓝图" },
  { key: "characters", icon: "👥", label: "人物" },
  { key: "foreshadow", icon: "🧵", label: "伏笔" },
  { key: "review", icon: "🛡", label: "审核" },
  { key: "versions", icon: "🕘", label: "版本" },
];
const CTX_TITLE: Record<string, string> = {
  blueprint: "本章蓝图", characters: "人物", foreshadow: "伏笔", review: "审核报告",
};

interface Props {
  pid: number;
  outlines: Outline[];
  chapterNum: number | null;
  current: ChapterDetail | null;
  currentOutline: Outline | null;
  // 审核报告卡内操作(修订/放行)后:刷新章节列表与正文缓存
  onChanged: (chapterNum: number) => void;
  // 「去重写本章」:中栏打开 act=revise 卡
  onRewrite: () => void;
  // 「历史版本」:中栏打开 VersionCompare
  onOpenVersions: () => void;
  // 移动端:图标条横排、内容平铺(全屏 sheet 容器由 WritePanel 提供)
  mobile?: boolean;
}

export default function RefDrawer({
  pid, outlines, chapterNum, current, currentOutline, onChanged, onRewrite, onOpenVersions,
  mobile = false,
}: Props) {
  const [searchParams, setSearchParams] = useSearchParams();
  const qc = useQueryClient();
  const ctx = searchParams.get("ctx");
  // 审核报告快照(正文改过则后端返 null,自动不展示过期评分)
  const [reviewSnap, setReviewSnap] = useState<ChapterReview | null>(null);

  const setCtx = (key: string | null) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (key === null) next.delete("ctx");
      else next.set("ctx", key);
      return next;
    });
  };

  // 打开审核报告(或切章)时拉取当前章主审快照
  useEffect(() => {
    if (ctx !== "review" || chapterNum === null || !current) { setReviewSnap(null); return; }
    let cancelled = false;
    api.getReview(pid, chapterNum)
      .then((r) => { if (!cancelled) setReviewSnap(r.review); })
      .catch(() => { if (!cancelled) setReviewSnap(null); });
    return () => { cancelled = true; };
  }, [ctx, pid, chapterNum, current]);

  // 历史模式合成生成响应(与原 ChaptersPanel toggleReviewReport 那套一致):
  // 门禁态由 status 推导,问题清单由卡片内自取,主审分用快照
  const reviewReportResult: GenerateChapterResponse | null =
    ctx === "review" && current ? {
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

  return (
    <div className={"ref-wrap" + (mobile ? " ref-wrap-m" : "")}>
      <div className={"ref-strip" + (mobile ? " ref-strip-m" : "")}>
        {ITEMS.map((it) => (
          <button key={it.key} type="button"
            className={"ref-icon" + (ctx === it.key ? " on" : "")}
            title={it.key === "versions" ? "历史版本(在中栏打开对比)" : it.label}
            onClick={() => {
              if (it.key === "versions") { onOpenVersions(); return; }
              setCtx(ctx === it.key ? null : it.key);
            }}>
            <span className="ref-icon-glyph">{it.icon}</span>
            <span className="ref-icon-label">{it.label}</span>
          </button>
        ))}
      </div>
      {/* 移动端 sheet 未选 ctx 时给引导;桌面端 ctx 为 null 即收起,不渲染内容 */}
      {mobile && !ctx && (
        <div className="muted m-ref-empty">点上方图标查看本章蓝图、人物、伏笔或审核报告。</div>
      )}
      {ctx && (
        <div className={"ref-drawer" + (mobile ? " ref-drawer-m" : "")}>
          <div className="card-head mb-2">
            <h3 className="grow">{CTX_TITLE[ctx] ?? ctx}</h3>
            <button className="btn-sm" onClick={() => setCtx(null)}>收起 »</button>
          </div>

          {ctx === "blueprint" && (
            currentOutline ? (
              <div className="ref-body">
                <b>第{currentOutline.chapter_number}章《{currentOutline.title}》</b>
                <span className="badge">{currentOutline.chapter_role}</span>
                <div className="muted mt-1">{currentOutline.summary}</div>
                <div className="meta-line">伏笔:{currentOutline.foreshadowing || "无"}</div>
                {currentOutline.characters_involved?.length ? (
                  <div className="meta-line">出场:{currentOutline.characters_involved.join("、")}</div>
                ) : null}
                {currentOutline.scene_location && (
                  <div className="meta-line">场景:{currentOutline.scene_location}</div>
                )}
              </div>
            ) : <div className="muted">先在左侧章节轨选一章。</div>
          )}

          {ctx === "characters" && <CharactersBoard pid={pid} />}
          {ctx === "foreshadow" && <ForeshadowBoard pid={pid} outlines={outlines} />}

          {ctx === "review" && (
            chapterNum === null || !current ? (
              <div className="muted">先在左侧章节轨选一章。</div>
            ) : reviewReportResult ? (
              <GenResultCard
                pid={pid}
                result={reviewReportResult}
                historical
                onChanged={() => {
                  onChanged(reviewReportResult.chapter_number);
                  // 修订/放行后同步刷新主审快照
                  qc.invalidateQueries({ queryKey: qk.chapter(pid, reviewReportResult.chapter_number) });
                  api.getReview(pid, reviewReportResult.chapter_number)
                    .then((r) => setReviewSnap(r.review))
                    .catch(() => undefined);
                }}
                onRewrite={onRewrite}
              />
            ) : null
          )}
        </div>
      )}
    </div>
  );
}
