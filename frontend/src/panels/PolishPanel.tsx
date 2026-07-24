// 润色工作区:选章或贴文本 → 选风格 → 左右对照预览(润色稿可微调) → 应用;原文支持手动编辑
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ChapterBrief, flavorTitle, PolishResult, Tendency } from "../api";
import { pollJob } from "../pollJob";
import TendencySelector from "../components/TendencySelector";
import { useJob } from "../ui/useJob";

interface Props { pid: number; }

export default function PolishPanel({ pid }: Props) {
  const { run: runAsyncJob } = useJob();
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [mode, setMode] = useState<"chapter" | "segment">("chapter");
  const [chapterNum, setChapterNum] = useState<number | null>(null);
  const [original, setOriginal] = useState("");
  const [segment, setSegment] = useState("");
  const [tendency, setTendency] = useState<Tendency>({ polish_style: ["去AI味"] });
  const [result, setResult] = useState<PolishResult | null>(null);
  // 润色稿可编辑副本:应用时以此为准(用户可在 AI 结果上手动微调)
  const [polishedDraft, setPolishedDraft] = useState("");
  // 原文手动编辑态(与润色稿编辑互不影响)
  const [editingOriginal, setEditingOriginal] = useState(false);
  const [editText, setEditText] = useState("");
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  // 组件卸载时中止保存后的同步轮询,防止卸载后继续 setState
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

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
      // 切换章节:清空对照结果与两侧编辑态
      setResult(null); setPolishedDraft(""); setEditingOriginal(false); setEditText(""); setMsg("");
    }
  }, [pid, mode, chapterNum]);

  async function run() {
    const text = mode === "chapter" ? original : segment;
    if (!text.trim()) { setErr("没有可润色的文本"); return; }
    setBusy("润色中(抽事实锁定→润色→校验,约2-6分钟,可切到别处,进度看右上角任务)…");
    setErr(""); setMsg(""); setResult(null);
    try {
      const r = mode === "chapter" && chapterNum !== null
        ? await runAsyncJob<PolishResult>(
            () => api.polishChapterAsync(pid, chapterNum, tendency),
            { kind: `polish-${pid}-${chapterNum}`, onStage: (s) => setBusy(`${s}…`) })
        : await runAsyncJob<PolishResult>(
            () => api.polishSegmentAsync(pid, text, tendency),
            { kind: `polish-segment-${pid}` });
      if (r) {
        setResult(r);
        setPolishedDraft(r.polished);
      }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  // 手动编辑原文后保存:写回定稿 → 重抽取 + 重建下游摘要(同写作面板的 saveEdit 流程)
  async function saveOriginalEdit() {
    if (chapterNum === null) return;
    const num = chapterNum;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setBusy("保存正文…"); setErr(""); setMsg("");
    try {
      const updated = await api.editChapterContent(pid, num, editText);
      setOriginal(updated.final_content || updated.draft_content);
      setEditingOriginal(false);
      setResult(null); setPolishedDraft("");
      await reload();
      // 手改后同步一致性引擎
      const { job_id } = await api.reExtractAsync(pid, num);
      await pollJob(job_id, {
        signal: ctrl.signal,
        onStage: (stage) => setBusy(`保存后同步一致性引擎(${stage})…`),
      });
      if (!ctrl.signal.aborted) setMsg(`第 ${num} 章正文已保存,并同步一致性引擎。`);
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const m = e instanceof Error ? e.message : String(e);
        // 轮询中断(超时/网络抖动):任务可能仍在后台运行,刷新列表让用户看到真实进度
        if (m.startsWith("任务超时") || m.startsWith("多次查询")) {
          setErr(`进度查询中断:${m}`);
          await reload().catch(() => undefined);
        } else {
          setErr(m);
        }
      }
    } finally { if (!ctrl.signal.aborted) setBusy(""); }
  }

  async function apply() {
    if (!result || chapterNum === null) return;
    setBusy("写回定稿…");
    try {
      // 应用用户微调后的润色稿
      await api.applyPolish(pid, chapterNum, polishedDraft);
      setOriginal(polishedDraft);
      setResult(null); setPolishedDraft("");
      setMsg(`第 ${chapterNum} 章已更新为润色稿。`);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  return (
    <>
      <div className="card">
        <h2>润色工作台</h2>
        <div className="card-desc">
          只改文笔不改剧情:润色前自动抽取情节事实锁定,润色后逐条校验;默认开启「去AI味」。
        </div>
        <div className="chips mb-3">
          <span className={"chip" + (mode === "chapter" ? " on" : "")} onClick={() => { setMode("chapter"); setResult(null); }}>
            润色整章
          </span>
          <span className={"chip" + (mode === "segment" ? " on" : "")} onClick={() => { setMode("segment"); setResult(null); }}>
            润色一段文本
          </span>
        </div>

        {mode === "chapter" ? (
          chapters.length ? (
            <>
              <select className="narrow" value={chapterNum ?? ""} onChange={(e) => setChapterNum(Number(e.target.value))}>
                {chapters.map((c) => (
                  <option key={c.chapter_number} value={c.chapter_number}>
                    第{c.chapter_number}章({c.word_count}字{c.is_stale ? " · 大纲已变" : ""})
                  </option>
                ))}
              </select>
              <div className="card-head mb-2 mt-3">
                <span className="pane-title grow">原文({original.length}字)</span>
                {!editingOriginal ? (
                  <button className="btn-sm" disabled={!!busy}
                    onClick={() => { setEditText(original); setEditingOriginal(true); }}>
                    手动编辑
                  </button>
                ) : (
                  <>
                    <button className="primary btn-sm" disabled={!!busy} onClick={saveOriginalEdit}>
                      {busy.startsWith("保存") && <span className="spin" />}保存修改
                    </button>
                    <button className="btn-sm" disabled={!!busy} onClick={() => setEditingOriginal(false)}>取消</button>
                  </>
                )}
              </div>
              {editingOriginal ? (
                <textarea
                  className="editor-area"
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                />
              ) : (
                <div className="pane pane-prose prose">{original}</div>
              )}
            </>
          ) : <div className="muted">还没有已生成的章节,先去「写作」生成正文。</div>
        ) : (
          <textarea rows={6} value={segment} onChange={(e) => setSegment(e.target.value)}
            placeholder="把要润色的段落贴进来(最长 12000 字)…" />
        )}

        <label className="fl">润色风格(可多选 + 我要输入)</label>
        <TendencySelector node="polish" value={tendency} onChange={setTendency} compact />
        <div className="actions mt-3">
          <button className="primary" disabled={!!busy} onClick={run}>
            {busy && !busy.startsWith("保存") && <span className="spin" />}生成润色预览
          </button>
          {busy && <span className="muted">{busy}</span>}
          {msg && <span className="msg-ok">{msg}</span>}
        </div>
        {err && <div className="msg-err mt-2">{err}</div>}
      </div>

      {result && (
        <div className="card">
          <div className="mb-3">
            <span className="badge"
              title={`润色前:${flavorTitle(result.flavor_before)}\n润色后:${flavorTitle(result.flavor_after)}`}>
              AI味 {result.flavor_before.score} → {result.flavor_after.score} /千字
            </span>
            <span className="badge ok">锁定事实 {result.locked_facts.length} 条</span>
            {result.violations.length
              ? <span className="badge err">⚠ 事实违规 {result.violations.length} 处</span>
              : <span className="badge ok">情节零改动 ✓</span>}
          </div>
          {result.violations.map((v, i) => (
            <div key={i} className="msg-err fact-line">「{v.fact}」— {v.problem}</div>
          ))}
          <div className="split mt-3">
            <div>
              <div className="pane-title">原文({(mode === "chapter" ? original : segment).length}字)</div>
              <div className="pane pane-prose prose">
                {mode === "chapter" ? original : segment}
              </div>
            </div>
            <div>
              <div className="pane-title">润色稿({polishedDraft.length}字 · 应用前可手动微调)</div>
              <textarea
                className="editor-area"
                value={polishedDraft}
                onChange={(e) => setPolishedDraft(e.target.value)}
              />
            </div>
          </div>
          <div className="actions mt-3">
            {mode === "chapter" && (
              <button className="primary" disabled={!!busy || !!result.violations.length || !polishedDraft.trim()} onClick={apply}>
                应用(写回第{chapterNum}章定稿)
              </button>
            )}
            <button onClick={() => { setResult(null); setPolishedDraft(""); }}>放弃这版</button>
            {mode === "chapter" && !!result.violations.length && (
              <span className="msg-err">有事实违规,不允许直接应用,请重新润色</span>
            )}
          </div>
        </div>
      )}
    </>
  );
}
