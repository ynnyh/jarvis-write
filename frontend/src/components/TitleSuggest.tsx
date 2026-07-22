// TitleSuggest — 「✨ AI 起名」按钮 + 候选书名下拉(新建项目表单 / 重命名共用)
// 主题为空也可用:后端会按类型/自由发挥盲出候选
import { useEffect, useRef, useState } from "react";
import { api, Concept } from "../api";

interface Props {
  topic: string;                  // 主题/灵感(可空)
  genre?: string;                 // 类型(可空)
  concept?: Concept | null;       // 已捏出的结构化概念(可空,给起名更多上下文)
  onPick: (title: string) => void;
}

export default function TitleSuggest({ topic, genre, concept, onPick }: Props) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [titles, setTitles] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);

  // 点击组件外部时收起候选列表
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  async function suggest() {
    if (busy) return;
    setBusy(true); setErr("");
    try {
      const r = await api.suggestTitle(topic.trim(), (genre ?? "").trim(), concept);
      setTitles(r.titles);
      setOpen(true);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ts-wrap" ref={wrapRef}>
      <button
        type="button"
        className="btn-sm"
        disabled={busy}
        title={topic.trim() ? "让 AI 起几个候选书名" : "没填主题也行,AI 会自由发挥起名"}
        onClick={suggest}
      >
        {busy && <span className="spin" />}✨ AI 起名
      </button>
      {err && <span className="msg-err">{err}</span>}
      {open && titles.length > 0 && (
        <div className="ts-list">
          {titles.map((t) => (
            <div
              key={t}
              className="ts-item"
              onClick={() => { onPick(t); setOpen(false); }}
            >
              {t}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
