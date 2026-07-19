// 润色工作区:选章或贴文本 → 选风格 → 左右对照预览 → 应用
import { useCallback, useEffect, useState } from "react";
import { api, ChapterBrief, PolishResult, Tendency } from "../api";
import TendencySelector from "../components/TendencySelector";

interface Props { pid: number; }

export default function PolishPanel({ pid }: Props) {
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [mode, setMode] = useState<"chapter" | "segment">("chapter");
  const [chapterNum, setChapterNum] = useState<number | null>(null);
  const [original, setOriginal] = useState("");
  const [segment, setSegment] = useState("");
  const [tendency, setTendency] = useState<Tendency>({ polish_style: ["去AI味"] });
  const [result, setResult] = useState<PolishResult | null>(null);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const reload = useCallback(async () => {
    const list = (await api.listChapters(pid)).filter((c) => c.status !== "empty");
    setChapters(list);
    if (list.length && chapterNum === null) setChapterNum(list[0].chapter_number);
  }, [pid, chapterNum]);
  useEffect(() => { reload().catch((e) => setErr(String(e))); }, [reload]);

  useEffect(() => {
    if (mode === "chapter" && chapterNum !== null) {
      api.getChapter(pid, chapterNum)
        .then((c) => setOriginal(c.final_content || c.draft_content))
        .catch((e) => setErr(String(e)));
      setResult(null); setMsg("");
    }
  }, [pid, mode, chapterNum]);

  async function run() {
    const text = mode === "chapter" ? original : segment;
    if (!text.trim()) { setErr("没有可润色的文本"); return; }
    setBusy("润色中(抽事实锁定→润色→校验,约2-6分钟)…"); setErr(""); setMsg(""); setResult(null);
    try {
      const r = mode === "chapter" && chapterNum !== null
        ? await api.polishChapter(pid, chapterNum, tendency)
        : await api.polishSegment(pid, text, tendency);
      setResult(r);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function apply() {
    if (!result || chapterNum === null) return;
    setBusy("写回定稿…");
    try {
      await api.applyPolish(pid, chapterNum, result.polished);
      setOriginal(result.polished);
      setResult(null);
      setMsg(`第 ${chapterNum} 章已更新为润色稿。`);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  return (
    <>
      <div className="card">
        <h2>润色工作台</h2>
        <div className="muted" style={{ marginBottom: 10 }}>
          只改文笔不改剧情:润色前自动抽取情节事实锁定,润色后逐条校验;默认开启「去AI味」。
        </div>
        <div className="chips" style={{ marginBottom: 10 }}>
          <span className={"chip" + (mode === "chapter" ? " on" : "")} onClick={() => { setMode("chapter"); setResult(null); }}>
            润色整章
          </span>
          <span className={"chip" + (mode === "segment" ? " on" : "")} onClick={() => { setMode("segment"); setResult(null); }}>
            润色一段文本
          </span>
        </div>

        {mode === "chapter" ? (
          chapters.length ? (
            <select value={chapterNum ?? ""} onChange={(e) => setChapterNum(Number(e.target.value))}
              style={{ maxWidth: 300 }}>
              {chapters.map((c) => (
                <option key={c.chapter_number} value={c.chapter_number}>
                  第{c.chapter_number}章({c.word_count}字{c.is_stale ? " · 大纲已变" : ""})
                </option>
              ))}
            </select>
          ) : <div className="muted">还没有已生成的章节,先去「写作」生成正文。</div>
        ) : (
          <textarea rows={6} value={segment} onChange={(e) => setSegment(e.target.value)}
            placeholder="把要润色的段落贴进来(最长 12000 字)…" />
        )}

        <label className="fl">润色风格(可多选 + 我要输入)</label>
        <TendencySelector node="polish" value={tendency} onChange={setTendency} compact />
        <div style={{ marginTop: 10 }}>
          <button className="primary" disabled={!!busy} onClick={run}>
            {busy && <span className="spin" />}生成润色预览
          </button>
          {busy && <span className="muted" style={{ marginLeft: 8 }}>{busy}</span>}
          {msg && <span className="msg-ok" style={{ marginLeft: 8 }}>{msg}</span>}
        </div>
        {err && <div className="msg-err" style={{ marginTop: 8 }}>{err}</div>}
      </div>

      {result && (
        <div className="card">
          <div style={{ marginBottom: 10 }}>
            <span className="badge">AI味 {result.flavor_before.score} → {result.flavor_after.score} /千字</span>
            <span className="badge ok">锁定事实 {result.locked_facts.length} 条</span>
            {result.violations.length
              ? <span className="badge err">⚠ 事实违规 {result.violations.length} 处</span>
              : <span className="badge ok">情节零改动 ✓</span>}
          </div>
          {result.violations.map((v, i) => (
            <div key={i} className="msg-err fact-line">「{v.fact}」— {v.problem}</div>
          ))}
          <div className="split" style={{ marginTop: 10 }}>
            <div>
              <div className="pane-title">原文({(mode === "chapter" ? original : segment).length}字)</div>
              <div className="pane prose" style={{ fontSize: 13.5 }}>
                {mode === "chapter" ? original : segment}
              </div>
            </div>
            <div>
              <div className="pane-title">润色稿({result.polished.length}字)</div>
              <div className="pane prose" style={{ fontSize: 13.5, borderColor: "var(--brand)" }}>
                {result.polished}
              </div>
            </div>
          </div>
          <div style={{ marginTop: 12 }}>
            {mode === "chapter" && (
              <button className="primary" disabled={!!busy || !!result.violations.length} onClick={apply}>
                应用(写回第{chapterNum}章定稿)
              </button>
            )}
            <button onClick={() => setResult(null)}>放弃这版</button>
            {mode === "chapter" && !!result.violations.length && (
              <span className="msg-err" style={{ marginLeft: 8 }}>有事实违规,不允许直接应用,请重新润色</span>
            )}
          </div>
        </div>
      )}
    </>
  );
}
