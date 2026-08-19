// write/StoryMapOverlay.tsx — 可召唤的故事地图(Tier 3,「正文即界面」后续):
// 写正文时随时召唤(Ctrl+M / 命令面板「地图」/ 章首蓝图卡「看全书脉络」),纵览全书每章的
// 定位·摘要·伏笔,把握全局、追伏笔线,点章即跳(跳转由父级顺手关本层)。
// 与目录抽屉分工:目录=选章导航(搜索/筛选/连写),故事地图=只读纵览脉络。
// 数据全用 WritePanel 已在手的 outlines,纯前端零后端;遮罩+Esc 复用 CatalogDrawer 同款。
import { useEffect, useMemo, useRef, useState } from "react";
import { ChapterBrief, Outline } from "../../api";

interface Props {
  outlines: Outline[];
  chapters: ChapterBrief[];
  currentNum: number | null;
  onOpen: (n: number) => void;
  onClose: () => void;
}

export default function StoryMapOverlay({ outlines, chapters, currentNum, onOpen, onClose }: Props) {
  // 只看有伏笔的章:追伏笔线用(哪章埋、哪章收)
  const [foreOnly, setForeOnly] = useState(false);
  const byNum = useMemo(() => new Map(chapters.map((c) => [c.chapter_number, c])), [chapters]);
  const currentRef = useRef<HTMLButtonElement>(null);

  // Esc 关闭(与点遮罩等价,复用 CatalogDrawer 同款)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 打开即定位到当前正在写的章(长书免翻);?. 兜底 jsdom 无 scrollIntoView
  useEffect(() => { currentRef.current?.scrollIntoView?.({ block: "center" }); }, []);

  const foreCount = useMemo(
    () => outlines.filter((o) => o.foreshadowing?.trim()).length, [outlines]);
  const shown = useMemo(
    () => (foreOnly ? outlines.filter((o) => o.foreshadowing?.trim()) : outlines),
    [outlines, foreOnly]);

  return (
    <div className="m-overlay story-map-overlay" onClick={onClose}>
      <div className="story-map" onClick={(e) => e.stopPropagation()}>
        <div className="story-map-head">
          <h3 className="grow">故事地图</h3>
          <span className="hint">全书 {outlines.length} 章 · 点章跳转</span>
          {foreCount > 0 && (
            <label className="story-map-filter">
              <input type="checkbox" checked={foreOnly}
                onChange={(e) => setForeOnly(e.target.checked)} />
              只看有伏笔的章({foreCount})
            </label>
          )}
          <button className="btn-sm" title="关闭(Esc)" onClick={onClose}>关闭</button>
        </div>

        {shown.length === 0 ? (
          <div className="muted story-map-empty">
            {outlines.length === 0 ? "还没有大纲。先去开书区生成蓝图。" : "没有埋伏笔的章节。"}
          </div>
        ) : (
          <div className="story-map-list">
            {shown.map((o) => {
              const ch = byNum.get(o.chapter_number);
              const isCurrent = o.chapter_number === currentNum;
              return (
                <button key={o.chapter_number} type="button"
                  ref={isCurrent ? currentRef : undefined}
                  className={"story-map-row" + (isCurrent ? " on" : "")}
                  onClick={() => onOpen(o.chapter_number)}>
                  <div className="smr-head">
                    <span className="smr-num">第{o.chapter_number}章</span>
                    <span className="smr-title">{o.title}</span>
                    {o.chapter_role && <span className="badge">{o.chapter_role}</span>}
                    {!ch && <span className="badge">未写</span>}
                    {ch?.is_stale && <span className="badge err">大纲已变</span>}
                  </div>
                  {o.summary && <div className="smr-summary">{o.summary}</div>}
                  {o.foreshadowing?.trim() && (
                    <div className="smr-fore">伏笔:{o.foreshadowing}</div>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
