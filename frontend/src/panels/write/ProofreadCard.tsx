// 校对卡(write 区 act=proofread):从原 EditorialPanel proofread 页签拆出。
// 手动校对→勾选修复;生成时自动校对的结果以只读清单回显。修复后后台同步一致性引擎。
import { useEffect, useState } from "react";
import { api, ProofIssue, ProofreadSnapshot } from "../../api";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";

const ISSUE_TYPE: Record<string, string> = {
  typo: "错字", grammar: "语病", punct: "标点", dup: "重复",
};

interface Props { pid: number; chapterNum: number; }

export default function ProofreadCard({ pid, chapterNum }: Props) {
  const { run: runJob } = useJob();
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  // 手动校对结果(可勾选修复)
  const [issues, setIssues] = useState<ProofIssue[] | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  // 校对回显:生成时自动修复的清单(只读展示)
  const [proofEcho, setProofEcho] = useState<ProofreadSnapshot | null>(null);

  // 回显:手动校对→填进 issues 走勾选修复 UI;生成时校对→只读清单
  useEffect(() => {
    let cancelled = false;
    setIssues(null); setPicked(new Set()); setProofEcho(null);
    api.getProofread(pid, chapterNum).then((r) => {
      if (cancelled || !r.proofread) return;
      if (r.proofread.source === "manual") {
        setIssues(r.proofread.issues);
        setPicked(new Set(r.proofread.issues.map((_, i) => i)));
      } else {
        setProofEcho(r.proofread);
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [pid, chapterNum]);

  async function runProofread() {
    setBusy("校对逐句检查中(约 1-2 分钟)…"); setErr(""); setIssues(null); setPicked(new Set()); setProofEcho(null);
    try {
      const r = await runJob<{ issues: ProofIssue[] }>(
        () => api.proofreadAsync(pid, chapterNum),
        { kind: `proofread-${pid}-${chapterNum}` },
      );
      if (r) {
        setIssues(r.issues);
        setPicked(new Set(r.issues.map((_, i) => i)));
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  // 「修复所选」:只应用勾选的条目;修完后台同步一致性引擎(不阻塞)
  async function applyFixes() {
    if (!issues) return;
    const fixes = issues.filter((_, i) => picked.has(i))
      .map((it) => ({ original: it.original, suggestion: it.suggestion }));
    if (!fixes.length) return;
    setBusy("应用修复…"); setErr("");
    try {
      const r = await api.proofreadApply(pid, chapterNum, fixes);
      toast.ok(`已修复 ${r.applied.length} 处`,
        r.failed.length ? `${r.failed.length} 处未找到原文(可能已改动)` : undefined);
      setIssues(null); setPicked(new Set());
      api.reExtractAsync(pid, chapterNum).catch(() => undefined);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">校对 · 第{chapterNum}章</h3>
        <button className="primary" disabled={!!busy} onClick={runProofread}>
          {busy && <span className="spin" />}开始校对
        </button>
      </div>
      <div className="card-desc mt-1">错别字/语病/标点/重复用词,逐条勾选后一键修复。</div>
      {busy && <div className="muted mt-2">{busy}</div>}
      {err && <div className="msg-err mt-2">{err}</div>}
      {proofEcho?.source === "generation" && (
        <div className="mt-3">
          <div className="review-meta">
            <span className="badge">生成时校对</span>
            {proofEcho.proofread_at && (
              <span className="hint">{new Date(proofEcho.proofread_at).toLocaleString("zh-CN", {
                month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
              })}</span>
            )}
            <span className="hint">已自动修复 {proofEcho.fixed} 处硬伤</span>
          </div>
          {proofEcho.issues.length === 0 ? (
            <div className="msg-ok">生成时未发现硬伤。</div>
          ) : (
            proofEcho.issues.map((it, i) => (
              <div key={i} className="proof-item">
                <span className="badge">{ISSUE_TYPE[it.type] ?? it.type}</span>
                <div className="proof-body">
                  <div className="diff-old">{it.original}</div>
                  <div className="diff-new">{it.suggestion}</div>
                  {it.reason && <div className="hint">{it.reason}</div>}
                </div>
              </div>
            ))
          )}
        </div>
      )}
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
  );
}
