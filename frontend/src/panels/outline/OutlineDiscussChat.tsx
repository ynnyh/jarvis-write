// 单章大纲研讨:和 AI 聊清"这章大纲哪里不对",蒸馏出改写提案(新标题+新简述),
// 确认后走修改指令的 apply 链路落库(版本化 + 正文标失配)。
// 与正文重写研讨(ReviseChat)同构,复用 rd-*/arch-directive/rp-* 样式。
import { useEffect, useRef, useState } from "react";
import { api } from "../../api";

interface Proposal {
  new_title: string | null;
  new_summary: string;
  change_reason: string;
}

interface Props {
  pid: number;
  n: number;
  // 当前标题/简述(提案对比展示用)
  title: string;
  summary: string;
  // 应用提案(父级调 applyEditDirective 落库并刷新);应用后父级收起本组件
  onApply: (p: Proposal) => Promise<void>;
}

export default function OutlineDiscussChat({ pid, n, title, summary, onApply }: Props) {
  const [msgs, setMsgs] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [err, setErr] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // 对话流自动滚到底
  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [msgs, busy]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const next = [...msgs, { role: "user" as const, content: text }];
    setMsgs(next);
    setInput("");
    setBusy(true); setErr("");
    try {
      const r = await api.discussOutline(pid, n, next);
      setMsgs((m) => [...m, { role: "assistant", content: r.reply }]);
      if (r.proposal) setProposal(r.proposal);
    } catch (e) {
      // 失败回退刚发出的那条,方便重发
      setMsgs((m) => m.slice(0, -1));
      setInput(text);
      setErr(String(e));
    } finally { setBusy(false); }
  }

  async function apply() {
    if (!proposal || applying) return;
    setApplying(true); setErr("");
    try {
      await onApply(proposal);
    } catch (e) {
      setErr(String(e));
      setApplying(false);
    }
  }

  return (
    <div className="arch-discuss revise-chat mt-2">
      <div className="rd-log" ref={logRef}>
        {msgs.length === 0 && !busy && (
          <div className="muted rd-empty">
            说说这章大纲哪里不对,比如:「这章和第3章情节重复了」「冲突太弱,想加个反转」
            「节奏不对,这两章应该合并成一章的内容」
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={"rd-msg rd-" + m.role}>
            <div className="rd-bubble">{m.content}</div>
          </div>
        ))}
        {busy && (
          <div className="rd-msg rd-assistant">
            <div className="rd-bubble muted"><span className="spin spin-sm" />结构编辑正在想…</div>
          </div>
        )}
      </div>
      {proposal && (
        <div className="arch-directive">
          <div className="rp-label">AI 整理出的改写提案(应用后版本化落库,本章已有正文会标失配)</div>
          {proposal.change_reason && (
            <div className="muted mb-1">{proposal.change_reason}</div>
          )}
          {proposal.new_title && proposal.new_title !== title && (
            <div className="fact-line">
              <b>标题</b> {title} → <b>{proposal.new_title}</b>
            </div>
          )}
          <div className="fact-line">
            <b>新简述</b>
            <div>{proposal.new_summary}</div>
          </div>
          <details className="issue-ev">
            <summary>当前简述</summary>
            <blockquote>{summary || "(空)"}</blockquote>
          </details>
          <div className="rp-actions">
            <button className="primary btn-sm" disabled={applying} onClick={apply}>
              {applying && <span className="spin spin-sm" />}应用这份改写
            </button>
            <button className="btn-sm" disabled={applying}
              onClick={() => setProposal(null)}>先不应用,继续聊</button>
          </div>
        </div>
      )}
      <div className="rd-input">
        <textarea
          rows={2}
          value={input}
          placeholder="说说这章大纲哪里不对、想改成什么样…(Enter 发送,Shift+Enter 换行)"
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
        />
        <div className="rp-actions">
          <button className="primary btn-sm" disabled={busy || !input.trim()} onClick={send}>
            {busy && <span className="spin" />}发送
          </button>
        </div>
      </div>
      {err && <div className="msg-err mt-2">{err}</div>}
    </div>
  );
}
