// 章节列表单行:状态徽标、生成/重写按钮、行内重写意见区(编辑器抽自 write/ReviseEditor)
import { ChapterBrief, EditorAction, Outline } from "../../api";
import { STATUS_BADGE, STATUS_CN } from "../../components/Reader";
import ReviseEditor from "../write/ReviseEditor";

interface Props {
  pid: number;
  outline: Outline;
  chapter: ChapterBrief | undefined;
  queueMode: boolean;
  queuePicked: boolean;
  generating: boolean;
  genBlocked: boolean;
  genHint: string;
  genStage: string;
  reviseOpen: boolean;
  reviseText: string;
  proseActions: EditorAction[];
  // 人工审核通过(docs/08 §5.5):仅 pending_review 章显示行内「通过」按钮
  approving: boolean;
  onApprove: () => void;
  onOpen: () => void;
  onOpenReader: () => void;
  onToggleQueue: (checked: boolean) => void;
  onToggleRevise: () => void;
  onReviseTextChange: (text: string) => void;
  onGenerate: () => void;
  onReviseSubmit: () => void;
  onReviseCancel: () => void;
}

export default function ChapterListItem({
  pid, outline: o, chapter: ch, queueMode, queuePicked, generating, genBlocked,
  genHint, genStage, reviseOpen, reviseText, proseActions, approving, onApprove,
  onOpen, onOpenReader, onToggleQueue, onToggleRevise,
  onReviseTextChange, onGenerate, onReviseSubmit, onReviseCancel,
}: Props) {
  const st = ch?.status ?? "empty";
  return (
    <>
      <div className="fact-line fact-row">
        {queueMode && (
          <input type="checkbox" className="queue-check"
            checked={queuePicked}
            disabled={!!ch && !ch.is_stale}
            title={ch && !ch.is_stale ? "已写好的章不用排队" : undefined}
            onChange={(e) => onToggleQueue(e.target.checked)} />
        )}
        {ch ? (
          <button type="button" className="fact-title linkish" onClick={onOpen}>
            <b>第{o.chapter_number}章</b> {o.title}
            <span className={"badge " + (ch.is_stale ? "err" : STATUS_BADGE[st] ?? "")}>
              {ch.is_stale ? "大纲已变" : STATUS_CN[st] ?? st}
            </span>
            <span className="muted"> {ch.word_count}字</span>
            {generating && (
              <span className="gen-stage"><span className="spin" />{genStage}</span>
            )}
          </button>
        ) : (
          <span className="fact-title">
            <b>第{o.chapter_number}章</b> {o.title}
            <span className={"badge " + (STATUS_BADGE[st] ?? "")}>
              {STATUS_CN[st] ?? st}
            </span>
            {generating && (
              <span className="gen-stage"><span className="spin" />{genStage}</span>
            )}
          </span>
        )}
        {ch && st === "pending_review" && (
          <button className="btn-sm" disabled={approving}
            title="人工审核通过:确认本章可定稿,批准后状态变为「已审」"
            onClick={onApprove}>
            {approving && <span className="spin spin-sm" />}通过
          </button>
        )}
        {ch && (
          <button className="btn-sm" onClick={onOpenReader}>阅读</button>
        )}
        <button className="btn-sm"
          title={genBlocked ? genHint : (ch ? "重写前旧版会自动存快照,可回退" : undefined)}
          disabled={genBlocked}
          onClick={() => {
            if (ch) onToggleRevise();
            else onGenerate();
          }}>
          {ch ? "重写" : "生成"}
        </button>
      </div>
      {reviseOpen && (
        <div className="fact-line">
          <ReviseEditor
            pid={pid}
            chapterNumber={o.chapter_number}
            isStale={!!ch?.is_stale}
            text={reviseText}
            proseActions={proseActions}
            genBlocked={genBlocked}
            genHint={genHint}
            onTextChange={onReviseTextChange}
            onSubmit={onReviseSubmit}
            onCancel={onReviseCancel}
          />
        </div>
      )}
    </>
  );
}
