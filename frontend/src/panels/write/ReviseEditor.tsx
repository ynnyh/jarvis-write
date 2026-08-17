// 重写意见编辑器:意见输入 + 预设动作 chips + 「先和 AI 聊聊」对话区。
// 章节轨行内重写框与 write 区中栏重写卡(act=revise)共用同一份交互。
import { useState } from "react";
import { EditorAction } from "../../api";
import ReviseChat from "../chapters/ReviseChat";

interface Props {
  pid: number;
  chapterNumber: number;
  // 大纲已变的章:重写完全按新大纲重新构思,意见只是补充要求(提示文案不同)
  isStale: boolean;
  text: string;
  proseActions: EditorAction[];
  // 有生成/重写任务进行中时禁止提交
  genBlocked: boolean;
  genHint: string;
  onTextChange: (text: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}

export default function ReviseEditor({
  pid, chapterNumber, isStale, text, proseActions, genBlocked, genHint,
  onTextChange, onSubmit, onCancel,
}: Props) {
  // 可选对话区开关(「说不清?先和 AI 聊聊怎么改」)
  const [chatOpen, setChatOpen] = useState(false);
  return (
    <div className="revise-box">
      <div className="hint">重写会覆盖当前正文;旧版自动存快照,可随时在「历史版本」对比回退。</div>
      {isStale && (
        <div className="hint" style={{ color: "var(--warn, #b45309)" }}>
          本章大纲已更新:这次重写会完全按新大纲重新构思,不再参照旧正文;补充意见可留空。
        </div>
      )}
      <textarea
        rows={3}
        maxLength={500}
        placeholder={isStale
          ? "对新大纲的补充要求(可留空):比如侧重点、节奏、视角;情节走向以新大纲为准"
          : "哪里不满意?比如:节奏太拖 / 对话不像这个角色 / 结尾太仓促;想要什么方向?比如:加强冲突、多些心理描写(可留空,直接重写)"}
        value={text}
        onChange={(e) => onTextChange(e.target.value)}
      />
      <div className="chips">
        {proseActions.map((a) => (
          <button key={a.key} type="button" className="chip" title={a.directive}
            onClick={() => onTextChange(((text ? text.trimEnd() + ";" : "") + a.directive).slice(0, 500))}>
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
        <ReviseChat pid={pid} n={chapterNumber}
          onApply={(d) => onTextChange(d.slice(0, 500))} />
      )}
      <div className="revise-actions">
        <button className="primary btn-sm" disabled={genBlocked}
          title={genBlocked ? genHint : undefined}
          onClick={onSubmit}>
          开始重写
        </button>
        <button className="btn-sm" onClick={onCancel}>取消</button>
      </div>
    </div>
  );
}
