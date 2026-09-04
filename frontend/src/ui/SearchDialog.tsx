// ui/SearchDialog.tsx — 全书全文检索(Ctrl+Shift+F,后端 FTS5 trigram 倒排):
// 输入即搜(300ms 防抖),结果按 正文/大纲/设定/事实/伏笔 分组,摘要就地裁剪;
// 章节类命中点击直达该章(write?ch=N),设定类命中跳故事圣经(book?tab=bible)。
// 复用命令面板的 cmdk-overlay/panel/input 外观(见 12-mobile-shell.css)。
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type SearchKind, type SearchResponse } from "../api";

const GROUP_ORDER: SearchKind[] = ["chapter", "outline", "entity", "fact", "foreshadowing"];
const GROUP_LABEL: Record<SearchKind, string> = {
  chapter: "正文", outline: "大纲", entity: "设定", fact: "事实", foreshadowing: "伏笔",
};

interface Props { pid: number; onClose: () => void }

/** 摘要里把命中词加粗(SQLITE 摘要由后端裁剪,这里只做高亮)。 */
function Highlight({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  const lower = text.toLowerCase(), needle = q.toLowerCase();
  const parts: React.ReactNode[] = [];
  let from = 0, at = lower.indexOf(needle), i = 0;
  while (at >= 0) {
    if (at > from) parts.push(text.slice(from, at));
    parts.push(<b key={i++} className="search-hit-b">{text.slice(at, at + needle.length)}</b>);
    from = at + needle.length;
    at = lower.indexOf(needle, from);
  }
  if (from < text.length) parts.push(text.slice(from));
  return <>{parts}</>;
}

export default function SearchDialog({ pid, onClose }: Props) {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => { inputRef.current?.focus(); }, []);

  // 300ms 防抖搜索;<2 字清结果(后端单字也能搜,但前端少打一半字就出全量噪音)
  useEffect(() => {
    window.clearTimeout(timer.current);
    const query = q.trim();
    if (query.length < 2) { setRes(null); return; }
    setBusy(true);
    timer.current = window.setTimeout(async () => {
      try { setRes(await api.search(pid, query)); }
      catch { setRes(null); }  // 失败静默:输入还在变,不打断
      finally { setBusy(false); }
    }, 300);
    return () => window.clearTimeout(timer.current);
  }, [q, pid]);

  const goto = (kind: SearchKind, ch: number | null) => {
    if (ch) nav(`/project/${pid}/write?ch=${ch}`);
    else if (kind === "entity" || kind === "fact" || kind === "foreshadowing")
      nav(`/project/${pid}/book?tab=bible`);
    onClose();
  };

  const groups = res
    ? GROUP_ORDER.map((k) => ({ kind: k, hits: res.grouped[k] ?? [] })).filter((g) => g.hits.length)
    : [];

  return (
    <div className="cmdk-overlay" onClick={onClose}>
      <div className="cmdk-panel search-panel" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef} className="cmdk-input" value={q}
          placeholder="搜全书:一句台词、一个设定、一处伏笔…(至少 2 字)"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}
        />
        <div className="cmdk-list search-list">
          {busy && <div className="cmdk-hint">搜索中…</div>}
          {!busy && q.trim().length < 2 && (
            <div className="cmdk-hint">输入至少 2 个字开始搜全书正文、大纲与设定。</div>
          )}
          {!busy && res && res.total === 0 && (
            <div className="cmdk-hint">全书没有命中「<Highlight text={res.q} q={res.q} />」。</div>
          )}
          {!busy && res && groups.map((g) => (
            <div key={g.kind}>
              <div className="search-group-head">
                {GROUP_LABEL[g.kind]}<span className="search-group-n">{g.hits.length}</span>
              </div>
              {g.hits.map((h) => (
                <button key={`${h.kind}-${h.ref_id}`} className="search-hit" onClick={() => goto(h.kind, h.chapter_number)}>
                  <span className="search-hit-loc">
                    {h.kind === "chapter" && `第${h.chapter_number}章`}
                    {h.kind === "outline" && `第${h.chapter_number}章大纲${h.title ? ` · ${h.title}` : ""}`}
                    {h.kind === "entity" && `实体 · ${h.name ?? ""}`}
                    {h.kind === "fact" && `事实 · 第${h.chapter_number}章起`}
                    {h.kind === "foreshadowing" && `伏笔 · 第${h.chapter_number}章埋`}
                  </span>
                  <span className="search-hit-snippet"><Highlight text={h.snippet} q={res.q} /></span>
                  {h.hits > 1 && <span className="search-group-n">×{h.hits}</span>}
                </button>
              ))}
            </div>
          ))}
          {!busy && res && res.total > 0 && (
            <div className="search-foot muted">共 {res.total} 处命中 · {res.elapsed_ms}ms · Esc 关闭</div>
          )}
        </div>
      </div>
    </div>
  );
}
