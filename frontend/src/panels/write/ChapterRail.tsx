// 目录(「正文即界面」P1 瘦身版):原左栏「章节轨」改为 CatalogDrawer 的内容体,双端同一份。
// 只保留:搜索/状态筛选、连写队列(头部「连写多章」+ 队列条,逻辑不动)、正文倾向、
// 状态徽标与生成中 spinner;行内操作(生成/阅读/通过/放行/行内重写框)全部删除,点行即开章。
import { useState } from "react";
import { ChapterBrief, Outline, Tendency } from "../../api";
import TendencySelector from "../../components/TendencySelector";
import ChapterListItem from "../chapters/ChapterListItem";

interface Props {
  outlines: Outline[];
  chapters: ChapterBrief[];
  // 进行中的「生成/重写」任务:null=空闲(连写排队时禁用,行内 spinner 跟随)
  genJob: { num: number; stage: string } | null;
  // 连写队列(状态由 WritePanel 持有)
  queueMode: boolean;
  queuePicked: Set<number>;
  onToggleQueueMode: () => void;
  onToggleQueuePick: (n: number, checked: boolean) => void;
  onPickNextBatch: () => void;
  onStartQueue: () => void;
  // 正文倾向(生成参数,WritePanel 持有)
  genTendency: Tendency;
  onTendencyChange: (t: Tendency) => void;
  onOpen: (n: number) => void;
  // 头部「关闭」(抽屉本身也可 Esc/点遮罩关,见 CatalogDrawer)
  onClose: () => void;
}

export default function ChapterRail({
  outlines, chapters, genJob,
  queueMode, queuePicked, onToggleQueueMode, onToggleQueuePick, onPickNextBatch, onStartQueue,
  genTendency, onTendencyChange, onOpen, onClose,
}: Props) {
  const byNum = new Map(chapters.map((c) => [c.chapter_number, c]));
  // 正文倾向选择器展开态(纯视图态,留在本组件)
  const [showTendency, setShowTendency] = useState(false);
  // 列表筛选(长书用):文本 + 状态
  const [filterText, setFilterText] = useState("");
  const [filterStatus, setFilterStatus] = useState("");

  const shownOutlines = outlines.filter((o) => {
    const ch = byNum.get(o.chapter_number);
    if (filterText.trim()) {
      const q = filterText.trim();
      if (!o.title.includes(q) && String(o.chapter_number) !== q.replace(/^第|章$/g, "")) return false;
    }
    if (filterStatus === "unwritten" && ch) return false;
    if (filterStatus === "finalized" && (!ch || ch.is_stale)) return false;
    if (filterStatus === "stale" && !ch?.is_stale) return false;
    return true;
  });

  return (
    <div className="card card-compact">
      <div className="card-head mb-2">
        <h3 className="grow">目录</h3>
        <button className="btn-sm" onClick={onToggleQueueMode}>
          {queueMode ? "取消连写" : "连写多章"}
        </button>
        <button className="btn-sm" onClick={() => setShowTendency(!showTendency)}>
          {showTendency ? "收起" : "正文倾向"}
        </button>
        <button className="btn-sm" title="关闭目录(Esc)" onClick={onClose}>关闭</button>
      </div>
      {showTendency && (
        <div className="mb-3">
          <TendencySelector node="chapter" value={genTendency} onChange={onTendencyChange} compact />
        </div>
      )}
      {queueMode && (
        <div className="queue-bar mb-2">
          <span className="hint">勾选要连写的章(按章号顺序串行生成,失败即停)</span>
          <button className="btn-sm" onClick={onPickNextBatch}>选未写的前 5 章</button>
          <button className="primary btn-sm" disabled={!queuePicked.size || !!genJob}
            onClick={onStartQueue}>
            排队生成 {queuePicked.size || ""} 章
          </button>
        </div>
      )}
      {outlines.length > 12 && (
        <div className="input-row mb-2">
          <input type="text" value={filterText} onChange={(e) => setFilterText(e.target.value)}
            placeholder="搜章名/章号…" />
          <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
            <option value="">全部状态</option>
            <option value="unwritten">未写</option>
            <option value="finalized">已成文</option>
            <option value="stale">大纲已变</option>
          </select>
        </div>
      )}
      {shownOutlines.map((o) => {
        const ch = byNum.get(o.chapter_number);
        const generating = genJob?.num === o.chapter_number;
        return (
          <ChapterListItem
            key={o.chapter_number}
            outline={o}
            chapter={ch}
            queueMode={queueMode}
            queuePicked={queuePicked.has(o.chapter_number)}
            generating={generating}
            genStage={genJob?.stage ?? ""}
            onToggleQueue={(checked) => onToggleQueuePick(o.chapter_number, checked)}
            onOpen={() => onOpen(o.chapter_number)}
          />
        );
      })}
    </div>
  );
}
