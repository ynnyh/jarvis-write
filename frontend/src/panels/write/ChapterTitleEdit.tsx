// 章节标题一键改名(写作页章首):手动改 + 「让 AI 换个不夸张的」候选挑选。
// 纯展示性改动:走 editOutline 只改 title —— 后端不标正文失配、不触发级联、不烧精判
// (见 backend cascade/differ._COSMETIC_FIELDS)。改完 onRenamed() 刷新大纲缓存即回显新名。
import { useState } from "react";
import { api } from "../../api";

interface Props {
  pid: number;
  chapterNumber: number;
  title: string;
  onRenamed: () => void;
}

export default function ChapterTitleEdit({ pid, chapterNumber, title, onRenamed }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(title);
  const [cands, setCands] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [asking, setAsking] = useState(false);
  const [err, setErr] = useState("");
  const busy = saving || asking;

  function start() {
    setDraft(title);
    setCands(null);
    setErr("");
    setOpen(true);
  }
  function cancel() {
    setOpen(false);
    setCands(null);
    setErr("");
  }

  async function save(next: string) {
    const t = next.trim();
    if (!t || t === title) { cancel(); return; }
    setSaving(true); setErr("");
    try {
      await api.editOutline(pid, chapterNumber, { title: t });
      onRenamed();
      cancel();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "改名失败,请重试");
    } finally {
      setSaving(false);
    }
  }

  async function askAi() {
    setAsking(true); setErr(""); setCands(null);
    try {
      const { titles } = await api.retitleChapter(pid, chapterNumber);
      setCands(titles);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "生成候选失败,请重试");
    } finally {
      setAsking(false);
    }
  }

  if (!open) {
    return (
      <div className="title-edit">
        <span className="title-edit-label">章节名称</span>
        <span className="title-edit-cur">《{title}》</span>
        <button className="btn-sm" onClick={start} title="手动改名,或让 AI 换个不夸张的">改名</button>
      </div>
    );
  }

  return (
    <div className="title-edit title-edit-open">
      <div className="title-edit-row">
        <input
          className="title-edit-input"
          type="text"
          value={draft}
          maxLength={30}
          placeholder="输入新的章节名称"
          disabled={saving}
          autoFocus
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void save(draft);
            else if (e.key === "Escape") cancel();
          }}
        />
        <button className="primary btn-sm" disabled={busy || !draft.trim()} onClick={() => void save(draft)}>
          {saving ? "保存中…" : "保存"}
        </button>
        <button className="btn-sm" disabled={busy} onClick={cancel}>取消</button>
      </div>
      <div className="title-edit-row">
        <button className="btn-sm" disabled={busy} onClick={() => void askAi()}>
          {asking ? "AI 思考中…" : "让 AI 换个不夸张的"}
        </button>
        <span className="muted title-edit-hint">现在叫《{title}》,觉得太夸张就换掉</span>
      </div>
      {err && <div className="msg-err title-edit-err">{err}</div>}
      {cands !== null && (
        cands.length > 0 ? (
          <div className="title-edit-cands">
            {cands.map((c) => (
              <button key={c} className="title-cand" disabled={busy}
                title="用这个名字" onClick={() => void save(c)}>{c}</button>
            ))}
            <button className="btn-sm title-cand-refresh" disabled={busy}
              onClick={() => void askAi()}>换一批</button>
          </div>
        ) : (
          <div className="muted title-edit-hint">AI 没给出候选,点「换一批」重试或手动改。</div>
        )
      )}
    </div>
  );
}
