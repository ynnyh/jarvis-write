// 章节列表单行:状态徽标、生成/重写按钮、行内重写意见区
import { useEffect, useState } from "react";
import { ChapterBrief, EditorAction, Outline } from "../../api";
import { STATUS_BADGE, STATUS_CN } from "../../components/Reader";
import ReviseChat from "./ReviseChat";

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
  // 重写框里的可选对话区开关;重写框收起时一并复位
  const [chatOpen, setChatOpen] = useState(false);
  useEffect(() => {
    if (!reviseOpen) setChatOpen(false);
  }, [reviseOpen]);
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
        <div className="fact-line revise-box">
          <div className="hint">重写会覆盖当前正文;旧版自动存快照,可随时在「历史版本」对比回退。</div>
          <textarea
            rows={3}
            maxLength={500}
            placeholder="哪里不满意?比如:节奏太拖 / 对话不像这个角色 / 结尾太仓促;想要什么方向?比如:加强冲突、多些心理描写(可留空,直接重写)"
            value={reviseText}
            onChange={(e) => onReviseTextChange(e.target.value)}
          />
          <div className="chips">
            {proseActions.map((a) => (
              <button key={a.key} type="button" className="chip" title={a.directive}
                onClick={() => onReviseTextChange(((reviseText ? reviseText.trimEnd() + ";" : "") + a.directive).slice(0, 500))}>
                {a.label}
              </button>
            ))}
          </div>
          <div className="revise-chat-toggle">
            <button type="button" className="linkish-btn" onClick={() => setChatOpen((v) => !v)}>
              {chatOpen ? "收起对话 ↑" : "说不清?先和 AI 聊聊怎么改 ↓"}
            </button>
          </div>
          {chatOpen && (
            <ReviseChat pid={pid} n={o.chapter_number}
              onApply={(d) => onReviseTextChange(d.slice(0, 500))} />
          )}
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
