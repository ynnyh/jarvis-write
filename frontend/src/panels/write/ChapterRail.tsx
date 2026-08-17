// write 区左栏「章节轨」:唯一选章入口。
// 从原 ChaptersPanel 侧栏抽出:搜索/状态筛选/连写勾选/正文倾向/每章操作(生成·阅读·通过·放行·重写框)。
import { useState } from "react";
import { ChapterBrief, EditorAction, Outline, Tendency } from "../../api";
import TendencySelector from "../../components/TendencySelector";
import ChapterListItem from "../chapters/ChapterListItem";

interface Props {
  pid: number;
  outlines: Outline[];
  chapters: ChapterBrief[];
  // 进行中的「生成/重写」任务:null=空闲(阻塞式,锁住列表操作)
  genJob: { num: number; stage: string } | null;
  // 连写队列(状态由 WritePanel 持有:StageBar 阶段条也要跟随 genJob 切换)
  queueMode: boolean;
  queuePicked: Set<number>;
  onToggleQueueMode: () => void;
  onToggleQueuePick: (n: number, checked: boolean) => void;
  onPickNextBatch: () => void;
  onStartQueue: () => void;
  // 正文倾向(生成参数,WritePanel 持有)
  genTendency: Tendency;
  onTendencyChange: (t: Tendency) => void;
  // 行内重写框(每章快捷入口;中栏 act=revise 卡是当前章的提升版)
  reviseFor: number | null;
  reviseText: string;
  proseActions: EditorAction[];
  onToggleRevise: (n: number) => void;
  onReviseTextChange: (text: string) => void;
  onReviseSubmit: (n: number) => void;
  onReviseCancel: () => void;
  // 人工审核通过(仅 pending_review 章显示)
  approving: number | null;
  onApprove: (n: number) => void;
  // quarantined 放行(仅被拦截章显示行内[放行],与[通过]同位)
  releasing: number | null;
  onRelease: (n: number) => void;
  onOpen: (n: number) => void;
  onOpenReader: (n: number) => void;
  onGenerate: (n: number) => void;
}

export default function ChapterRail({
  pid, outlines, chapters, genJob,
  queueMode, queuePicked, onToggleQueueMode, onToggleQueuePick, onPickNextBatch, onStartQueue,
  genTendency, onTendencyChange,
  reviseFor, reviseText, proseActions, onToggleRevise, onReviseTextChange, onReviseSubmit, onReviseCancel,
  approving, onApprove, releasing, onRelease, onOpen, onOpenReader, onGenerate,
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
        <h3 className="grow">章节</h3>
        <button className="btn-sm" onClick={onToggleQueueMode}>
          {queueMode ? "取消连写" : "连写多章"}
        </button>
        <button className="btn-sm" onClick={() => setShowTendency(!showTendency)}>
          {showTendency ? "收起" : "正文倾向"}
        </button>
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
        const genBlocked = !!genJob;
        const genHint = generating
          ? "本章任务进行中"
          : `第 ${genJob?.num} 章任务进行中,完成后可继续操作`;
        return (
          <ChapterListItem
            key={o.chapter_number}
            pid={pid}
            outline={o}
            chapter={ch}
            queueMode={queueMode}
            queuePicked={queuePicked.has(o.chapter_number)}
            generating={generating}
            genBlocked={genBlocked}
            genHint={genHint}
            genStage={genJob?.stage ?? ""}
            reviseOpen={reviseFor === o.chapter_number}
            reviseText={reviseText}
            proseActions={proseActions}
            approving={approving === o.chapter_number}
            onApprove={() => onApprove(o.chapter_number)}
            releasing={releasing === o.chapter_number}
            onRelease={() => onRelease(o.chapter_number)}
            onOpen={() => onOpen(o.chapter_number)}
            onOpenReader={() => onOpenReader(o.chapter_number)}
            onToggleQueue={(checked) => onToggleQueuePick(o.chapter_number, checked)}
            onToggleRevise={() => onToggleRevise(o.chapter_number)}
            onReviseTextChange={onReviseTextChange}
            onGenerate={() => onGenerate(o.chapter_number)}
            onReviseSubmit={() => onReviseSubmit(o.chapter_number)}
            onReviseCancel={onReviseCancel}
          />
        );
      })}
    </div>
  );
}
