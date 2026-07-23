// 重写研讨:重写框里的可选对话区。和 AI 聊清"这章到底哪里不满意",
// 后台把对话蒸馏成一条修改意见,点「填入重写意见」回填到上方文本框,再开始重写。
// 与架构研讨(ArchPanel)同构,复用 rd-*/arch-directive/rp-* 样式。
import { useEffect, useRef, useState } from "react";
import { api } from "../../api";

interface Props {
  pid: number;
  n: number;
  // 把蒸馏出的修改意见回填进重写文本框(父级负责截断到 500 字)
  onApply: (directive: string) => void;
}

export default function ReviseChat({ pid, n, onApply }: Props) {
  const [msgs, setMsgs] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [directive, setDirective] = useState(""); // AI 蒸馏出的修改意见
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
      const r = await api.discussRevision(pid, n, next);
      setMsgs((m) => [...m, { role: "assistant", content: r.reply }]);
      if (r.directive) setDirective(r.directive);
    } catch (e) {
      // 失败回退刚发出的那条,方便重发
      setMsgs((m) => m.slice(0, -1));
      setInput(text);
      setErr(String(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="arch-discuss revise-chat">
      <div className="rd-log" ref={logRef}>
        {msgs.length === 0 && !busy && (
          <div className="muted rd-empty">
            说说这章哪里不对,比如:「开头铺垫太长,进冲突太慢」「主角这里不该哭,他是个隐忍的人」「结尾太突然,想留个钩子」
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={"rd-msg rd-" + m.role}>
            <div className="rd-bubble">{m.content}</div>
          </div>
        ))}
        {busy && (
          <div className="rd-msg rd-assistant">
            <div className="rd-bubble muted"><span className="spin spin-sm" />编辑正在想…</div>
          </div>
        )}
      </div>
      {directive && (
        <div className="arch-directive">
          <div className="rp-label">AI 整理出的修改意见(填入后重写会高优先级遵循)</div>
          <textarea rows={Math.min(8, Math.max(3, directive.split("\n").length + 1))}
            value={directive} onChange={(e) => setDirective(e.target.value)} />
          <div className="rp-actions">
            <button className="primary btn-sm" onClick={() => onApply(directive)}>
              填入上方重写意见
            </button>
            <button className="btn-sm" onClick={() => setDirective("")}>清空,继续聊</button>
          </div>
        </div>
      )}
      <div className="rd-input">
        <textarea
          rows={2}
          value={input}
          placeholder="说说哪里不满意、想要什么…(Enter 发送,Shift+Enter 换行)"
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
