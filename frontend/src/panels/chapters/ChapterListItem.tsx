// 章节列表单行:状态徽标、生成/重写按钮、行内重写意见区
import { ChapterBrief, EditorAction, Outline } from "../../api";
import { STATUS_CN } from "../../components/Reader";

interface Props {
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
  outline: o, chapter: ch, queueMode, queuePicked, generating, genBlocked,
  genHint, genStage, reviseOpen, reviseText, proseActions,
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
        <span className={"fact-title" + (ch ? " linkish" : "")} onClick={() => ch && onOpen()}>
          <b>第{o.chapter_number}章</b> {o.title}
          <span className={"badge " + (ch?.is_stale ? "err" : st === "finalized" ? "ok" : "")}>
            {ch?.is_stale ? "大纲已变" : STATUS_CN[st] ?? st}
          </span>
          {ch && <span className="muted"> {ch.word_count}字</span>}
          {generating && (
            <span className="gen-stage"><span className="spin" />{genStage}</span>
          )}
        </span>
        {ch && (
          <button className="btn-sm" onClick={onOpenReader}>阅读</button>
        )}
        <button className="btn-sm" disabled={genBlocked} title={genBlocked ? genHint : undefined}
          onClick={() => {
            if (ch) onToggleRevise();
            else onGenerate();
          }}>
          {ch ? "重写" : "生成"}
        </button>
      </div>
      {reviseOpen && (
        <div className="fact-line revise-box">
          <textarea
            rows={3}
            maxLength={500}
            placeholder="哪里不满意?比如:节奏太拖 / 对话不像这个角色 / 结尾太仓促;想要什么方向?比如:加强冲突、多些心理描写(可留空,直接重写)"
            value={reviseText}
            onChange={(e) => onReviseTextChange(e.target.value)}
          />
          <div className="chips">
            {proseActions.map((a) => (
              <span key={a.key} className="chip" title={a.directive}
                onClick={() => onReviseTextChange(((reviseText ? reviseText.trimEnd() + ";" : "") + a.directive).slice(0, 500))}>
                {a.label}
              </span>
            ))}
          </div>
          <div className="revise-actions">
            <button className="primary btn-sm" disabled={genBlocked}
              title={genBlocked ? genHint : undefined}
              onClick={onReviseSubmit}>
              开始重写
            </button>
            <button className="btn-sm" onClick={onReviseCancel}>取消</button>
          </div>
        </div>
      )}
    </>
  );
}
