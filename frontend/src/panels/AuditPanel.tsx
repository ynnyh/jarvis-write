// book 区「体检」页签:全书体检 / 规则扫描 / 批量补契约 + 审核报告聚合(一致性引擎)。
// 从原 EditorialPanel audit 页签拆出;世界观硬规则编辑已搬去 settings 区(ProjectSettingsPanel)。
import { useEffect, useState } from "react";
import { api, AuditReport, Project } from "../api";
import { errMsg } from "../pollJob";
import { useJob } from "../ui/useJob";

interface Props { pid: number; project: Project; }

export default function AuditPanel({ pid, project }: Props) {
  const { run: runJob } = useJob();
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  // 审核报告聚合
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

  // 世界观硬规则在 settings 区维护;这里只读判断有没有配(规则扫描的前置)
  const hasWorldRules = !!project.world_rules?.trim();

  useEffect(() => {
    if (!audit) {
      api.auditReport(pid).then(setAudit).catch((e) => setErr(errMsg(e)));
    }
  }, [audit, pid]);

  // 全书体检:LLM 逐章扫跨章矛盾,问题落各章审核报告;完成后刷新聚合报告
  async function runDiag() {
    setErr(""); setBusy("全书体检中(逐章扫描,较长)…"); setDiagResult(null);
    try {
      const r = await runJob<{
        scanned: number; with_issues: number[]; total_issues: number; total_blockers: number;
      }>(() => api.diagAsync(pid), { kind: `diag-${pid}` });
      if (r) { setDiagResult(r); setAudit(null); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  // 规则扫描:LLM 逐章对照世界观硬规则体检正文,问题以「规则」落各章审核报告
  async function runRuleScan() {
    setErr(""); setBusy("规则扫描中(逐章对照硬规则,较长)…"); setScanResult(null);
    try {
      const r = await runJob<{
        scanned: number; with_issues: number[]; total_issues: number; total_blockers: number;
      }>(() => api.ruleScanAsync(pid), { kind: `rule-scan-${pid}` });
      if (r) { setScanResult(r); setAudit(null); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  // 老书批量补契约:缺有效契约的章逐章重提;完成后刷新聚合报告(缺契约数变)
  async function runBackfill() {
    setErr(""); setBusy("批量补提契约中…"); setBackfillResult(null);
    try {
      const r = await runJob<{ extracted: number[]; skipped: number[]; failed: number[] }>(
        () => api.contractsBackfillAsync(pid), { kind: `contract-backfill-${pid}` },
      );
      if (r) { setBackfillResult(r); setAudit(null); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  return (
    <>
      <div className="card mb-3">
        <div className="card-head">
          <h3 className="grow">全书体检与契约</h3>
        </div>
        <div className="card-desc mt-1">
          体检:LLM 逐章对照圣经/上章契约/上章结尾,把跨章矛盾一次扫出来,按「诊断」来源落进
          各章的审核报告(写作区 → 打开章节 → 右侧「审核」,可逐条修订/忽略)。
          契约是门禁和下章衔接的对照基准,老书缺契约时先批量补提,体检结果才完整。
          规则扫描:逐章对照世界观硬规则(在「设置」维护)体检正文,违反项按「规则」来源落进审核报告。
        </div>
        <div className="actions mt-2">
          <button className="primary btn-sm" disabled={!!busy} onClick={runDiag}>
            {busy.startsWith("全书体检") && <span className="spin spin-sm" />}全书体检
          </button>
          <button className="btn-sm" disabled={!!busy || !hasWorldRules}
            title={!hasWorldRules ? "先去「设置」填写并保存世界观硬规则" : undefined}
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

      {err && <div className="msg-err mt-2">{err}</div>}
    </>
  );
}
