// TitleSuggest — 「✨ AI 起名」按钮 + 候选书名下拉(新建项目表单 / 重命名共用)
import { useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Props {
  topic: string;                  // 主题/灵感,为空时禁用按钮
  genre?: string;                 // 类型(可空)
  onPick: (title: string) => void;
}

export default function TitleSuggest({ topic, genre, onPick }: Props) {
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
      const r = await api.suggestTitle(topic.trim(), (genre ?? "").trim());
      setTitles(r.titles);
      setOpen(true);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  }

  const disabled = !topic.trim();

  return (
    <div className="ts-wrap" ref={wrapRef}>
      <button
        type="button"
        className="btn-sm"
        disabled={busy || disabled}
        title={disabled ? "先填写主题/灵感,再让 AI 起名" : "让 AI 起几个候选书名"}
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
