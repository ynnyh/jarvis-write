// hooks/useDiscussChat.ts
// 单通道 AI 研讨对话循环:乐观追加用户消息 → 调接口 → 追加回复(onReply 取额外载荷),
// 失败回退刚发出的那条并恢复输入框 + 报错。架构研讨 / 单章大纲研讨 / 段落研讨三处同构,
// 从各面板抽出(AiDock 是聊天/重写双通道变体,不走此 hook)。
import { useState } from "react";
import { errMsg } from "../pollJob";

export interface DiscussMsg {
  role: "user" | "assistant";
  content: string;
}

interface DiscussOpts<R> {
  onReply?: (r: R) => void;   // 回复到手后取额外载荷(directive / proposal / suggestion…)
  onSendStart?: () => void;   // 乐观发送前的清理(如清掉上一条改写建议)
}

export function useDiscussChat<R extends { reply: string }>(
  call: (msgs: DiscussMsg[]) => Promise<R>,
  opts?: DiscussOpts<R>,
) {
  const [msgs, setMsgs] = useState<DiscussMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    const next = [...msgs, { role: "user" as const, content: text }];
    setMsgs(next);
    setInput("");
    setBusy(true); setErr("");
    opts?.onSendStart?.();
    try {
      const r = await call(next);
      setMsgs((m) => [...m, { role: "assistant", content: r.reply }]);
      opts?.onReply?.(r);
    } catch (e) {
      // 失败回退刚发出的那条,方便重发
      setMsgs((m) => m.slice(0, -1));
      setInput(text);
      setErr(errMsg(e));
    } finally { setBusy(false); }
  }

  // 清空对话(换章 / 收起研讨时复位)
  function reset() {
    setMsgs([]);
    setInput("");
    setErr("");
  }

  return { msgs, input, setInput, busy, err, setErr, send, reset };
}
