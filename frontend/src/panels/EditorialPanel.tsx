// 编辑部:主编评分 / 校对 / 审核报告 / 润色工作台(四个角色一站式)
import { useEffect, useState } from "react";
import { api, AuditReport, ChapterBrief, ChapterReview, ProofIssue } from "../api";
import PolishPanel from "./PolishPanel";
import { useJob } from "../ui/useJob";
import { toast } from "../ui/Toaster";

interface Props { pid: number; }

type Tab = "review" | "proofread" | "audit" | "polish";
const TABS: { key: Tab; label: string; who: string }[] = [
  { key: "review", label: "主编评分", who: "情节/文笔/节奏/人物四维打分 + 修改建议" },
  { key: "proofread", label: "校对", who: "错别字/语病/标点/重复用词,一键修复" },
  { key: "audit", label: "审核报告", who: "失配章/伏笔悬空/断章,一致性引擎聚合" },
  { key: "polish", label: "润色工作台", who: "整章/选段风格化润色" },
];

const SCORE_LABEL: Record<string, string> = {
  plot: "情节", prose: "文笔", pacing: "节奏", character: "人物",
};
const ISSUE_TYPE: Record<string, string> = {
  typo: "错字", grammar: "语病", punct: "标点", dup: "重复",
};

export default function EditorialPanel({ pid }: Props) {
  const { run: runJob } = useJob();
  const [tab, setTab] = useState<Tab>("review");
  const [chapters, setChapters] = useState<ChapterBrief[]>([]);
  const [chapterNum, setChapterNum] = useState<number | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  // 主编
  const [review, setReview] = useState<ChapterReview | null>(null);
  // 校对
  const [issues, setIssues] = useState<ProofIssue[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  // 审核
  const [audit, setAudit] = useState<AuditReport | null>(null);

  useEffect(() => {
    api.listChapters(pid).then((list) => {
      const withText = list.filter((c) => c.status !== "empty");
      setChapters(withText);
      if (withText.length && chapterNum === null) setChapterNum(withText[0].chapter_number);
    }).catch((e) => setErr(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  useEffect(() => {
    if (tab === "audit" && !audit) {
      api.auditReport(pid).then(setAudit).catch((e) => setErr(String(e)));
    }
  }, [tab, audit, pid]);

  async function runReview() {
    if (chapterNum === null) return;
    setBusy("主编审读中(约 1 分钟)…"); setErr(""); setReview(null);
    try {
      const r = await runJob<ChapterReview>(
        () => api.reviewChapterAsync(pid, chapterNum),
        { kind: `review-${pid}-${chapterNum}` },
      );
      if (r) setReview(r);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function runProofread() {
    if (chapterNum === null) return;
    setBusy("校对逐句检查中(约 1-2 分钟)…"); setErr(""); setIssues(null); setPicked(new Set());
    try {
      const r = await runJob<{ issues: ProofIssue[] }>(
        () => api.proofreadAsync(pid, chapterNum),
        { kind: `proofread-${pid}-${chapterNum}` },
      );
      if (r) {
        setIssues(r.issues);
        setPicked(new Set(r.issues.map((_, i) => i)));
      }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  async function applyFixes() {
    if (chapterNum === null || !issues) return;
    const fixes = issues.filter((_, i) => picked.has(i))
      .map((it) => ({ original: it.original, suggestion: it.suggestion }));
    if (!fixes.length) return;
    setBusy("应用修复…"); setErr("");
    try {
      const r = await api.proofreadApply(pid, chapterNum, fixes);
      toast.ok(`已修复 ${r.applied.length} 处`,
        r.failed.length ? `${r.failed.length} 处未找到原文(可能已改动)` : undefined);
      setIssues(null); setPicked(new Set());
      // 修完同步一致性引擎(后台跑,不阻塞)
      api.reExtractAsync(pid, chapterNum).catch(() => undefined);
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  const chapterPicker = (
    <select value={chapterNum ?? ""} onChange={(e) => {
      setChapterNum(Number(e.target.value)); setReview(null); setIssues(null);
    }}>
      {chapters.map((c) => (
        <option key={c.chapter_number} value={c.chapter_number}>
          第 {c.chapter_number} 章({c.word_count} 字)
        </option>
      ))}
    </select>
  );

  return (
    <>
      <div className="ed-tabs">
        {TABS.map((t) => (
          <div key={t.key} className={"ed-tab" + (tab === t.key ? " on" : "")}
            onClick={() => setTab(t.key)}>
            <b>{t.label}</b>
            <span>{t.who}</span>
          </div>
        ))}
      </div>

      {tab === "polish" && <PolishPanel pid={pid} />}

      {tab === "review" && (
        <div className="card">
          <div className="card-head">
            <h3 className="grow">主编评分</h3>
            {chapters.length > 0 && chapterPicker}
            <button className="primary" disabled={!!busy || chapterNum === null} onClick={runReview}>
              {busy && <span className="spin" />}请主编审读
            </button>
          </div>
          {!chapters.length && <div className="muted mt-2">还没有已生成的章节。</div>}
          {busy && <div className="muted mt-2">{busy}(可切到别处,进度看右上角任务)</div>}
          {review && (
            <div className="mt-3">
              <div className="score-row">
                {Object.entries(review.scores).map(([k, v]) => (
                  <div key={k} className={"score-item" + (v > 0 && v < 6 ? " low" : "")}>
                    <b>{v || "—"}</b>
                    <span>{SCORE_LABEL[k] ?? k}</span>
                  </div>
                ))}
              </div>
              {review.comment && <div className="notice notice-info mt-3">{review.comment}</div>}
              {review.suggestions.length > 0 && (
                <div className="mt-3">
                  <label className="fl">最该改的三件事(可复制到写作页的重写意见里)</label>
                  {review.suggestions.map((s, i) => (
                    <div key={i} className="fact-line">{i + 1}. {s}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {tab === "proofread" && (
        <div className="card">
          <div className="card-head">
            <h3 className="grow">校对</h3>
            {chapters.length > 0 && chapterPicker}
            <button className="primary" disabled={!!busy || chapterNum === null} onClick={runProofread}>
              {busy && <span className="spin" />}开始校对
            </button>
          </div>
          {!chapters.length && <div className="muted mt-2">还没有已生成的章节。</div>}
          {busy && <div className="muted mt-2">{busy}</div>}
          {issues !== null && (
            issues.length === 0 ? (
              <div className="msg-ok mt-3">没发现硬伤,这章很干净。</div>
            ) : (
              <div className="mt-3">
                <div className="hint mb-2">勾选要修复的问题({picked.size}/{issues.length}):</div>
                {issues.map((it, i) => (
                  <div key={i} className="proof-item">
                    <input type="checkbox" checked={picked.has(i)}
                      onChange={(e) => {
                        const next = new Set(picked);
                        if (e.target.checked) next.add(i); else next.delete(i);
                        setPicked(next);
                      }} />
                    <span className="badge">{ISSUE_TYPE[it.type] ?? it.type}</span>
                    <div className="proof-body">
                      <div className="diff-old">{it.original}</div>
                      <div className="diff-new">{it.suggestion}</div>
                      {it.reason && <div className="hint">{it.reason}</div>}
                    </div>
                  </div>
                ))}
                <div className="actions mt-3">
                  <button className="primary" disabled={!!busy || !picked.size} onClick={applyFixes}>
                    {busy && <span className="spin" />}修复选中的 {picked.size} 处
                  </button>
                  <button disabled={!!busy} onClick={() => setIssues(null)}>放弃</button>
                </div>
              </div>
            )
          )}
        </div>
      )}

      {tab === "audit" && (
        <div className="card">
          <div className="card-head">
            <h3 className="grow">审核报告(一致性引擎聚合,随写作实时更新)</h3>
            <button className="btn-sm" onClick={() => { setAudit(null); }}>刷新</button>
          </div>
          {!audit ? <div className="muted mt-2"><span className="spin" />加载中…</div> : (
            <div className="mt-2">
              <div className="stat-strip">
                <div className="stat">进度<b>{audit.written_chapters}/{audit.target_chapters} 章</b></div>
                <div className="stat">伏笔<b>{audit.foreshadow.resolved} 收 / {audit.foreshadow.open} 悬</b></div>
                {audit.stale_chapters.length > 0 && (
                  <div className="stat">失配<b className="stat-alert">{audit.stale_chapters.length} 章</b></div>
                )}
              </div>
              {audit.stale_chapters.length > 0 && (
                <div className="notice notice-err mt-3">
                  第 {audit.stale_chapters.join("、")} 章正文与新大纲失配——大纲改过之后这些章没重写,建议去「写作」处理。
                </div>
              )}
              {audit.holes.length > 0 && (
                <div className="notice notice-warn mt-3">
                  第 {audit.holes.join("、")} 章被跳过没写(后面的章已生成)——摘要链会缺一环,建议补上。
                </div>
              )}
              {audit.foreshadow.overdue.length > 0 && (
                <div className="mt-3">
                  <label className="fl">逾期未收的伏笔({audit.foreshadow.overdue.length})</label>
                  {audit.foreshadow.overdue.map((f, i) => (
                    <div key={i} className="fact-line">
                      「{f.description}」— 第 {f.planted} 章埋下,预期第 {f.expected} 章回收,至今未收
                    </div>
                  ))}
                </div>
              )}
              {audit.stale_chapters.length === 0 && audit.holes.length === 0
                && audit.foreshadow.overdue.length === 0 && (
                <div className="msg-ok mt-3">没有失配、断章或逾期伏笔,状态健康。</div>
              )}
            </div>
          )}
        </div>
      )}

      {err && <div className="msg-err mt-2">{err}</div>}
    </>
  );
}
