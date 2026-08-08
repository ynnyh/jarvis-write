// 编辑部:主编评分 / 校对 / 审核报告 / 润色工作台(四个角色一站式)
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, AuditReport, ChapterBrief, ChapterReview, ProofIssue, ProofreadSnapshot, ReviewSuggestion } from "../api";
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
  plot: "情节", prose: "文笔", pacing: "节奏", character: "人物", continuity: "连续性",
};
const ISSUE_TYPE: Record<string, string> = {
  typo: "错字", grammar: "语病", punct: "标点", dup: "重复",
};

export default function EditorialPanel({ pid }: Props) {
  const { run: runJob } = useJob();
  const nav = useNavigate();
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
  // 校对回显:生成时自动修复的清单(只读展示)
  const [proofEcho, setProofEcho] = useState<ProofreadSnapshot | null>(null);
  // 审核
  const [audit, setAudit] = useState<AuditReport | null>(null);
  // 全书体检 / 批量补契约结果(docs/08 §7 P2)
  const [diagResult, setDiagResult] = useState<{
    scanned: number; with_issues: number[]; total_issues: number; total_blockers: number;
  } | null>(null);
  const [backfillResult, setBackfillResult] = useState<{
    extracted: number[]; skipped: number[]; failed: number[];
  } | null>(null);
  // 规则扫描结果(与全书体检同构,问题以「规则」来源落各章审核报告)
  const [scanResult, setScanResult] = useState<{
    scanned: number; with_issues: number[]; total_issues: number; total_blockers: number;
  } | null>(null);
  // 世界观硬规则钉板:每行一条,注入后续所有生成;null=尚未加载
  const [worldRules, setWorldRules] = useState<string | null>(null);
  const [rulesSaving, setRulesSaving] = useState(false);

  useEffect(() => {
    api.listChapters(pid).then((list) => {
      const withText = list.filter((c) => c.status !== "empty");
      setChapters(withText);
      if (withText.length && chapterNum === null) setChapterNum(withText[0].chapter_number);
    }).catch((e) => setErr(String(e)));
    // 世界观硬规则编辑区初始值(未设置 = 空串)
    api.getProject(pid).then((p) => setWorldRules(p.world_rules ?? "")).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  useEffect(() => {
    if (tab === "audit" && !audit) {
      api.auditReport(pid).then(setAudit).catch((e) => setErr(String(e)));
    }
  }, [tab, audit, pid]);

  // 回显:生成时/手动审校的结果都存在章节上,打开本页直接读最近一次,
  // 正文改动后指纹失配后端返回 null,不会展示过期评分
  useEffect(() => {
    if (tab !== "review" || chapterNum === null) return;
    let cancelled = false;
    api.getReview(pid, chapterNum).then((r) => {
      if (!cancelled && r.review) setReview(r.review);
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [tab, chapterNum, pid]);

  // 校对回显:手动校对→填进 issues 走原有勾选修复 UI;生成时校对→只读清单
  useEffect(() => {
    if (tab !== "proofread" || chapterNum === null) return;
    let cancelled = false;
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
  }, [tab, chapterNum, pid]);

  // 把主编建议打包成重写指令,交接给写作页(localStorage 中转,写作面板挂载时消费)
  function toRewrite(sugs: ReviewSuggestion[]) {
    if (!review) return;
    const text = sugs
      .map((s) => (s.evidence ? `"${s.evidence}"这里` : "") + s.issue + (s.fix ? `,改法:${s.fix}` : ""))
      .join(";")
      .slice(0, 500);
    localStorage.setItem(`revise-draft-${pid}`, JSON.stringify({
      num: review.chapter_number, text,
    }));
    toast.ok("意见已带到写作页", `第 ${review.chapter_number} 章的重写框已填好,确认后开跑`);
    nav(`/project/${pid}/write`);
  }

  // 全书体检:LLM 逐章扫跨章矛盾,问题落各章审核报告;完成后刷新聚合报告
  async function runDiag() {
    setErr(""); setBusy("全书体检中(逐章扫描,较长)…"); setDiagResult(null);
    try {
      const r = await runJob<{
        scanned: number; with_issues: number[]; total_issues: number; total_blockers: number;
      }>(() => api.diagAsync(pid), { kind: `diag-${pid}` });
      if (r) { setDiagResult(r); setAudit(null); }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  // 规则扫描:LLM 逐章对照世界观硬规则体检正文,问题以「规则」落各章审核报告
  async function runRuleScan() {
    setErr(""); setBusy("规则扫描中(逐章对照硬规则,较长)…"); setScanResult(null);
    try {
      const r = await runJob<{
        scanned: number; with_issues: number[]; total_issues: number; total_blockers: number;
      }>(() => api.ruleScanAsync(pid), { kind: `rule-scan-${pid}` });
      if (r) { setScanResult(r); setAudit(null); }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  // 老书批量补契约:缺有效契约的章逐章重提;完成后刷新聚合报告(缺契约数变)
  async function runBackfill() {
    setErr(""); setBusy("批量补提契约中…"); setBackfillResult(null);
    try {
      const r = await runJob<{ extracted: number[]; skipped: number[]; failed: number[] }>(
        () => api.contractsBackfillAsync(pid), { kind: `contract-backfill-${pid}` },
      );
      if (r) { setBackfillResult(r); setAudit(null); }
    } catch (e) { setErr(String(e)); } finally { setBusy(""); }
  }

  // 保存世界观硬规则(整段覆盖,空串清空)
  async function saveWorldRules() {
    if (worldRules === null) return;
    setErr(""); setRulesSaving(true);
    try {
      await api.patchProject(pid, { world_rules: worldRules });
      toast.ok("世界观硬规则已保存", "将注入后续所有生成,可用于规则扫描体检正文");
    } catch (e) { setErr(String(e)); } finally { setRulesSaving(false); }
  }

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
      setChapterNum(Number(e.target.value)); setReview(null); setIssues(null); setProofEcho(null);
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
          <button key={t.key} type="button" className={"ed-tab" + (tab === t.key ? " on" : "")}
            onClick={() => setTab(t.key)}>
            <b>{t.label}</b>
            <span>{t.who}</span>
          </button>
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
              <div className="review-meta">
                <span className="badge">{review.source === "generation" ? "生成时审校" : "手动审校"}</span>
                {review.reviewed_at && (
                  <span className="hint">{new Date(review.reviewed_at).toLocaleString("zh-CN", {
                    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
                  })}</span>
                )}
                {!!review.revision_rounds && <span className="hint">回炉 {review.revision_rounds} 轮</span>}
                {!!review.proofread_fixed && <span className="hint">已自动修复 {review.proofread_fixed} 处硬伤</span>}
              </div>
              <div className="score-row">
                {Object.entries(review.scores).map(([k, v]) => (
                  <div key={k} className={"score-item" + (v > 0 && v < 6 ? " low" : "")}>
                    <b>{v || "—"}</b>
                    <span>{SCORE_LABEL[k] ?? k}</span>
                  </div>
                ))}
              </div>
              {review.passed !== undefined && (
                <div className="mt-2">
                  <span className={"badge " + (review.passed ? "ok" : "err")}>
                    {review.passed ? "已达达标线" : "未达达标线"}
                    {review.threshold ? `（四维均需 ≥${review.threshold}）` : ""}
                  </span>
                </div>
              )}
              {review.comment && <div className="notice notice-info mt-3">{review.comment}</div>}
              {review.suggestions.length > 0 && (
                <div className="mt-3">
                  <label className="fl">最该改的三件事(可一键转成本章重写指令)</label>
                  {review.suggestions.map((s, i) => (
                    <div key={i} className="review-sug">
                      <div className="rs-head">
                        <b>{i + 1}. {s.issue}</b>
                        <button className="btn-sm" title="带着这条意见去写作页重写本章"
                          onClick={() => toRewrite([s])}>→ 按此重写</button>
                      </div>
                      {s.evidence && <blockquote className="rs-quote">"{s.evidence}"</blockquote>}
                      {s.fix && <div className="rs-fix">改法:{s.fix}</div>}
                    </div>
                  ))}
                  <div className="actions mt-2">
                    <button className="primary btn-sm" onClick={() => toRewrite(review.suggestions)}>
                      全部意见转为重写指令,去写作页 →
                    </button>
                  </div>
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
      )}

      {tab === "audit" && (
        <>
        <div className="card mb-3">
          <div className="card-head">
            <h3 className="grow">全书体检与契约</h3>
          </div>
          <div className="card-desc mt-1">
            体检:LLM 逐章对照圣经/上章契约/上章结尾,把跨章矛盾一次扫出来,按「诊断」来源落进
            各章的审核报告(写作页 → 打开章节 → 审核报告,可逐条修订/忽略)。
            契约是门禁和下章衔接的对照基准,老书缺契约时先批量补提,体检结果才完整。
            规则扫描:逐章对照下方世界观硬规则体检正文,违反项按「规则」来源落进审核报告。
          </div>
          <div className="actions mt-2">
            <button className="primary btn-sm" disabled={!!busy} onClick={runDiag}>
              {busy.startsWith("全书体检") && <span className="spin spin-sm" />}全书体检
            </button>
            <button className="btn-sm" disabled={!!busy || !worldRules?.trim()}
              title={!worldRules?.trim() ? "先在下方填写并保存世界观硬规则" : undefined}
              onClick={runRuleScan}>
              {busy.startsWith("规则扫描") && <span className="spin spin-sm" />}规则扫描
            </button>
            <button className="btn-sm" disabled={!!busy} onClick={runBackfill}>
              {busy.startsWith("批量补提") && <span className="spin spin-sm" />}
              批量补提契约
              {audit?.contracts_missing?.length
                ? `(${audit.contracts_missing.length} 章缺)` : ""}
            </button>
          </div>
          {diagResult && (
            diagResult.total_issues ? (
              <div className="notice notice-warn mt-2">
                体检完成:扫 {diagResult.scanned} 章,第 {diagResult.with_issues.join("、")} 章
                共 {diagResult.total_issues} 个问题({diagResult.total_blockers} 个致命),
                去各章「审核报告」逐条处理。
              </div>
            ) : (
              <div className="msg-ok mt-2">
                体检完成:扫 {diagResult.scanned} 章,未发现跨章矛盾。
              </div>
            )
          )}
          {scanResult && (
            scanResult.total_issues ? (
              <div className="notice notice-warn mt-2">
                规则扫描完成:扫 {scanResult.scanned} 章,第 {scanResult.with_issues.join("、")} 章
                共 {scanResult.total_issues} 处违反硬规则({scanResult.total_blockers} 处致命),
                去各章「审核报告」逐条处理(来源:规则)。
              </div>
            ) : (
              <div className="msg-ok mt-2">
                规则扫描完成:扫 {scanResult.scanned} 章,未发现违反硬规则的内容。
              </div>
            )
          )}
          {backfillResult && (
            <div className="msg-ok mt-2">
              补提完成:成功 {backfillResult.extracted.length} 章,
              跳过 {backfillResult.skipped.length} 章(已有有效契约)
              {backfillResult.failed.length > 0 &&
                `,失败 ${backfillResult.failed.length} 章(第 ${backfillResult.failed.join("、")} 章,可重试)`}
            </div>
          )}
          <div className="mt-3">
            <label className="fl">世界观硬规则(每行一条)</label>
            <div className="hint mb-1">
              钉死本书不可违背的设定/常识(如:2024 新高考,理科不考政治;高考只考 6.7-6.8 两天)。
              保存后注入后续所有生成,并可发起规则扫描全书体检正文。
            </div>
            <textarea rows={4} value={worldRules ?? ""}
              placeholder={"2024 新高考,理科不考政治\n高考只考 6.7-6.8 两天"}
              onChange={(e) => setWorldRules(e.target.value)} />
            <div className="actions mt-2">
              <button className="primary btn-sm" disabled={rulesSaving || worldRules === null}
                onClick={saveWorldRules}>
                {rulesSaving && <span className="spin spin-sm" />}保存规则
              </button>
            </div>
          </div>
        </div>
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
        </>
      )}

      {err && <div className="msg-err mt-2">{err}</div>}
    </>
  );
}
