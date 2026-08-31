// 生成结果卡片(章节审核面板):字数/AI味、门禁状态、五维审校分、
// 写前提醒(preflight)与可操作问题清单(按建议修订/人工解决/忽略)。
// 门禁拦截时(仅刚生成完的非历史卡)顶部出统一处理卡 GateResolve(说人话三选一:让 AI 按这条
// 重写 / 去改设定 / 就这样忽略继续 + 重新检查);历史模式由章首 ChapterStatusCard 的 GateResolve
// 承接,这里只做完整报告明细,不重复出卡。
import { useCallback, useEffect, useState } from "react";
import {
  api, ChapterIssue, flavorTitle, GenerateChapterResponse, SpotRepairResult,
} from "../../api";
import { errMsg } from "../../pollJob";
import { dispatchAction } from "../../ui/actions";
import GateRepairDetails from "../../ui/GateRepairDetails";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import GateResolve from "./GateResolve";
import { useInvalidateProject } from "../../hooks/queries";

// 审校五维分中文标签;旧快照无 continuity 键,Object.entries 遍历天然不渲染该行
const SCORE_LABEL: Record<string, string> = {
  plot: "情节", prose: "文笔", pacing: "节奏", character: "人物", continuity: "连续性",
};
// 问题严重度:徽标配色 + 中文
const SEV_BADGE: Record<string, string> = { blocker: "err", major: "warn", minor: "" };
const SEV_CN: Record<string, string> = { blocker: "致命", major: "重要", minor: "次要" };
// 问题来源中文
const SOURCE_CN: Record<string, string> = {
  gate: "门禁", preflight: "预审", diag: "诊断", review: "审校", rules: "规则",
  canon: "宪法建议", clock: "时间线", devices: "常驻装置",
};
const ISSUE_STATUS_CN: Record<string, string> = { resolved: "已人工解决", ignored: "已忽略" };
// AI 味偏高分界线(/千字,加权命中+统计罚分;经验值,可调):超过即提示一键去味
const FLAVOR_HIGH = 6;
// AI 味说人话分档(P0 术语减负):轻/中/重 + 一句人话,具体分值收进 tooltip
function flavorLevel(score: number): { label: string; cn: string } {
  if (score < 3) return { label: "轻", cn: "读起来基本像人写的,可以不管" };
  if (score < FLAVOR_HIGH) return { label: "中", cn: "有点 AI 腔(套话/翻译腔),在意文风的话去过一遍去味" };
  return { label: "重", cn: "AI 腔明显,建议过一遍去味再看" };
}

interface Props {
  pid: number;
  result: GenerateChapterResponse;
  // 本次生成实耗秒数(P1 成本透明;重连/历史模式无当次数据不传)
  durationSec?: number | null;
  // 章节数据有变(修订完成/放行后状态变化):刷新章节列表与打开的正文
  onChanged: () => void;
  // 「重写」引导:展开本章的行内重写框
  onRewrite: () => void;
  // 关闭按钮(右上角 ×):仅刚生成完的结果卡传入(WritePanel setGenResult(null));
  // 历史模式(审核报告)由抽屉/sheet 容器负责关闭,不传
  onClose?: () => void;
  // 任务锁(坏味道 #9 统一):有生成/重写任务在跑时禁用「按建议修订」与门禁横幅
  // 「放行」(gate-release 后端对进行中的章节任务返回 409),title 给原因
  genBlocked?: boolean;
  genHint?: string;
  // 历史模式:从章节列表「审核报告」打开(非刚生成完)。标题换为「审核报告」,
  // 无当次一致性检查数据时不显示"检查通过"徽标;拦截状态按章节 status 推导
  historical?: boolean;
}

export default function GenResultCard({ pid, result, durationSec, onChanged, onRewrite, onClose, genBlocked, genHint, historical }: Props) {
  const { run } = useJob();
  const invalidateProject = useInvalidateProject(pid);
  const n = result.chapter_number;
  // 问题清单:挂载后按章拉取(与生成响应里的 consistency_issues 互补,这份可操作)
  const [issues, setIssues] = useState<ChapterIssue[] | null>(null);
  const [issuesErr, setIssuesErr] = useState("");
  // 单条问题操作进行中(禁用该条按钮)
  const [busyIssue, setBusyIssue] = useState<number | null>(null);
  // 「放弃去味」回退进行中(去味自愈采纳过才有按钮)
  const [deaiBusy, setDeaiBusy] = useState(false);

  const reloadIssues = useCallback(async () => {
    try {
      setIssues(await api.listChapterIssues(pid, n));
      setIssuesErr("");
    } catch (e) {
      setIssuesErr(errMsg(e));
    }
  }, [pid, n]);

  useEffect(() => {
    setIssues(null); setIssuesErr("");
    let cancelled = false;
    api.listChapterIssues(pid, n)
      .then((list) => { if (!cancelled) setIssues(list); })
      .catch((e) => { if (!cancelled) setIssuesErr(errMsg(e)); });
    return () => { cancelled = true; };
  }, [pid, n]);

  // open → resolved(已人工改完)/ ignored(确认忽略)
  async function markIssue(issue: ChapterIssue, status: "resolved" | "ignored") {
    setBusyIssue(issue.id);
    try {
      await api.patchChapterIssue(pid, n, issue.id, status);
      toast.ok(status === "resolved" ? "已标记为人工解决" : "已忽略该问题");
      await reloadIssues();
    } catch (e) {
      toast.err("问题状态更新失败", errMsg(e));
    } finally {
      setBusyIssue(null);
    }
  }

  // 按建议修订:异步重写 job(受理即标 resolved,门禁重跑后未消除的问题会重新 open);
  // 409 = 本章/队列已有任务在跑
  async function applyRevision(issue: ChapterIssue) {
    setBusyIssue(issue.id);
    try {
      const res = await run<GenerateChapterResponse & { applied_issue_id?: number }>(
        () => api.applyIssueRevision(pid, n, issue.id),
        { kind: `chapter-${pid}-${n}` },
      );
      if (res) {
        toast.ok(`第 ${n} 章已按建议修订`, "正文已更新,一致性门禁已重跑");
        await reloadIssues();
        onChanged();
      }
    } catch (e) {
      toast.err("修订失败", errMsg(e));
      await reloadIssues();
    } finally {
      setBusyIssue(null);
    }
  }

  // 定点修复:AI 原位改句消掉这条矛盾(不重写),门禁复查干净才落库;
  // ok=false 时正文未动,issue 保持 open,提示改走「按建议修订」
  async function spotRepair(issue: ChapterIssue) {
    setBusyIssue(issue.id);
    try {
      const res = await run<SpotRepairResult>(
        () => api.spotRepairIssue(pid, n, issue.id),
        { kind: `chapter-${pid}-${n}` },
      );
      if (res?.ok) {
        toast.ok(
          "已定点修复",
          res.status === "quarantined"
            ? "矛盾已清零;本章仍在隔离中,可点「放行」补走抽取/摘要/契约"
            : "正文已更新,门禁复查通过",
        );
        await reloadIssues();
        onChanged();
      } else {
        toast.err("定点修复未生效", res?.reason || "正文未改动;建议改走「按建议修订」");
        await reloadIssues();
      }
    } catch (e) {
      toast.err("定点修复失败", errMsg(e));
      await reloadIssues();
    } finally {
      setBusyIssue(null);
    }
  }

  // 采纳一条宪法建议(source=canon)进 project.canon:标 issue resolved,并刷新宪法编辑器(project 查询)
  async function adoptCanon(issue: ChapterIssue) {
    setBusyIssue(issue.id);
    try {
      const res = await api.adoptCanonSuggestion(pid, n, issue.id);
      toast.ok(res.changed ? "已采纳进故事宪法" : "该建议此前已在宪法里,已标记为已采纳");
      await reloadIssues();
      await invalidateProject();
      onChanged();
    } catch (e) {
      toast.err("采纳失败", errMsg(e));
      await reloadIssues();
    } finally {
      setBusyIssue(null);
    }
  }

  const gate = result.gate;
  // 历史模式无当次 gate 数据:按章节 status 推导拦截态(blocker 明细在下方问题清单里)
  const quarantined = gate?.status === "quarantined" || result.status === "quarantined";
  const warnings = result.preflight?.warnings ?? [];
  const openIssues = (issues ?? []).filter((i) => i.status === "open");
  const doneIssues = (issues ?? []).filter((i) => i.status !== "open");

  // 放弃去味:回退到去味前的正文快照(source=deai,版本列表最新在前)。
  // 回退本身可再回滚(restore 前会把当前版留痕),不需二次确认。
  async function revertDeai() {
    setDeaiBusy(true);
    try {
      const versions = await api.listChapterVersions(pid, n);
      const snap = versions.find((v) => v.source === "deai");
      if (!snap) {
        toast.err("找不到去味前的版本", "可能已被后续操作顶替,可在版本历史里人工对比");
        return;
      }
      await api.restoreChapterVersion(pid, n, snap.id);
      toast.ok("已放弃去味,恢复去味前正文", "回退前的版本仍留在版本历史里");
      await reloadIssues();
      onChanged();
    } catch (e) {
      toast.err("放弃去味失败", errMsg(e));
    } finally {
      setDeaiBusy(false);
    }
  }

  return (
    <div className={"card " + (quarantined ? "card-warn" : "card-ok")}>
      <div className="card-head">
        <b className="grow">{historical ? "审核报告" : "生成完成"}</b>
        {/* 关闭入口(引导断裂修复):结果卡此前只能切章才消失,给用户明确的退出路径 */}
        {onClose && (
          <button className="btn-sm" title="关闭结果卡(正文已保存,可随时从章首状态卡「查看审核报告」复查)"
            onClick={onClose}>×</button>
        )}
      </div>
      {result.word_count} 字
      {durationSec != null && durationSec >= 60 && (
        <span className="badge"
          title="本章从发起到落地的实际用时(含草稿/审校/自愈等全部环节)">用时 {Math.round(durationSec / 60)} 分钟</span>
      )}
      {result.ai_flavor && (() => {
        const lv = flavorLevel(result.ai_flavor.score);
        return (
          <span className={"badge" + (result.ai_flavor.score >= FLAVOR_HIGH ? " warn" : "")}
            title={`${lv.cn}\n${flavorTitle(result.ai_flavor)}`}>
            AI味·{lv.label}
          </span>
        );
      })()}
      {result.ai_flavor && result.ai_flavor.score >= FLAVOR_HIGH && (
        <>
          <span className="muted"> 腔调偏重,建议过一遍去味</span>
          <button className="btn-sm" title="打开 AI 栏梳理整章优化意见(默认去AI味方向)"
            onClick={() => dispatchAction("polish")}>
            一键去味
          </button>
        </>
      )}
      {result.review?.deai && result.review.deai.before > result.review.deai.after && (
        <>
          <span className="badge ok"
            title="定稿 AI 味超标,已自动定向去味重写(带篇幅/统计判据安全阀);去味前的正文存在版本历史里,不满意可放弃">
            去味自愈 {result.review.deai.before.toFixed(1)}→{result.review.deai.after.toFixed(1)}
          </span>
          <button className="btn-sm" disabled={deaiBusy} onClick={revertDeai}
            title="回退到去味前的正文快照;回退前的当前版也会留在版本历史,随时可再切回">
            {deaiBusy && <span className="spin spin-sm" />}放弃去味
          </button>
        </>
      )}

      {/* 快捷徽标行(P2 分层):一眼看结论——一致性/审校过没过;细节在下方折叠区 */}
      <div className="mt-1">
        {!historical && (result.consistency_issues.length
          ? <span className="badge err"
              title="AI 复核发现人物状态、时间线或设定有前后矛盾,明细见下方「检查明细」">
              一致性问题 {result.consistency_issues.length}
            </span>
          : <span className="badge ok"
              title="AI 自动核对了人物状态、时间线、设定等有没有前后矛盾,没发现冲突">一致性✓</span>
        )}
        {result.review && (
          <span className={"badge " + (result.review.passed ? "ok" : "err")}
            title="AI 主审按情节/文笔/节奏/人物/连续性五个维度打分,达到达标线即通过;分数见下方「检查明细」">
            {result.review.passed ? "审校✓" : "审校未达标"}
          </span>
        )}
        {issues !== null && openIssues.length > 0 && (
          <span className="badge warn"
            title="需要你过目的问题(下面可直接按建议修订/人工解决/忽略)">待处理 {openIssues.length}</span>
        )}
      </div>

      {/* 门禁拦截(仅刚生成完的非历史卡):顶部出统一处理卡,说人话三选一。
          历史模式由章首 ChapterStatusCard 的 GateResolve 承接,这里不重复出卡。 */}
      {quarantined && !historical && (
        <div className="gate-banner mt-2">
          <GateResolve
            pid={pid}
            n={n}
            genBlocked={genBlocked}
            genHint={genHint}
            onChanged={() => { reloadIssues(); onChanged(); }}
            onRewriteFallback={onRewrite}
          />
        </div>
      )}

      {/* 可操作区(P2 分层):有需要拍板的问题才占屏;干净章节这里什么都不出 */}
      {issuesErr && (
        <div className="mt-2">
          <span className="msg-err"> 问题清单加载失败:{issuesErr}
            <button className="btn-sm" onClick={reloadIssues}>重试</button>
          </span>
        </div>
      )}
      {openIssues.length > 0 && (
        <div className="mt-2">
          <b>问题清单</b>
          <span className="muted"> 逐条处理,或全部留到最后一起看</span>
          {openIssues.map((issue) => (
            <IssueRow key={issue.id} issue={issue} busy={busyIssue === issue.id}
              genBlocked={genBlocked} genHint={genHint}
              onApply={() => applyRevision(issue)}
              onSpotRepair={() => spotRepair(issue)}
              onResolve={() => markIssue(issue, "resolved")}
              onIgnore={() => markIssue(issue, "ignored")}
              onAdopt={() => adoptCanon(issue)} />
          ))}
        </div>
      )}

      {/* 拆章改了书的结构,必须常驻可见;压缩只是措辞层面的,收进明细 */}
      {result.word_guard_action === "split" && result.split_info && (
        <div className="mt-2">
          <span className="badge err"
            title="这章写得太长(超出上限较多),AI 按情节断点拆成了两章,后续章节号自动顺延">字数守卫:已自动拆章</span>
          <div className="fact-line">
            原第{result.split_info.original_chapter}章 →
            第{result.split_info.original_chapter}章({result.split_info.part_a_words}字)
            + 第{result.split_info.new_chapter}章《{result.split_info.new_title}》({result.split_info.part_b_words}字)
          </div>
          {result.split_info.reason && (
            <div className="muted">断点:{result.split_info.reason}</div>
          )}
        </div>
      )}

      {/* 检查明细(P2 分层):写前提醒/一致性明细/五维分/守卫/已处理问题——
          想深究再展开;历史模式(用户主动点「审核报告」来的)默认展开 */}
      <details className="gen-detail-box mt-2" open={historical}>
        <summary className="muted">检查明细(写前提醒 · 五维评分 · 处理记录)</summary>
        <div className="mt-1">

        {warnings.length > 0 && (
          <div>
            <span className="badge warn">写前提醒 {warnings.length}</span>
            <span className="muted"> 写前审核发现的疑似矛盾,只提醒不阻断</span>
            {warnings.map((w, k) => (
              <div key={k} className="fact-line">
                <b>[{w.type === "timeline" ? "时间线" : "状态"}]</b> {w.description}
                {w.evidence && <div className="muted">证据: {w.evidence}</div>}
                {w.conflicting_fact && <div className="muted">冲突设定: {w.conflicting_fact}</div>}
                {w.suggestion && <div className="muted">建议: {w.suggestion}</div>}
              </div>
            ))}
          </div>
        )}

        {result.consistency_issues.length > 0 && (
          <div className="mt-2">
            <span className="badge err">一致性问题 {result.consistency_issues.length}</span>
            {result.consistency_issues.map((i, k) => (
              <div key={k} className="fact-line">
                <b>[{i.severity}]</b> {i.description}
                <div className="muted">建议: {i.suggestion}</div>
              </div>
            ))}
          </div>
        )}

        {result.review && (
          <div className="mt-2">
            <span className={"badge " + (result.review.passed ? "ok" : "err")}
              title="AI 主审按情节/文笔/节奏/人物/连续性五个维度打分,达到达标线即通过;未达标会有具体扣分原因">
              {result.review.passed ? "审校达标" : "审校未达标"}
            </span>
            <span className="muted"
              title="AI 主审的五维打分(满分10):情节·文笔·节奏·人物·连续性">
              {" "}{Object.entries(result.review.scores)
                .map(([k, v]) => `${SCORE_LABEL[k] ?? k}${v}`)
                .join("·")}
              （达标线{result.review.threshold}）
            </span>
            {result.review.revision_rounds > 0 && (
              <span className="badge"
                title="审校没过,AI 按扣分意见自动重写改进了几轮才交出这版"> 自动回炉 {result.review.revision_rounds} 轮</span>
            )}
            {(result.review.repair_rounds ?? 0) > 0 && (
              <span className="badge ok" title="一致性矛盾由 AI 定点改句消除,未整章重写">
                定点修复 {result.review.repairs?.applied.length ?? 0} 处
              </span>
            )}
            <GateRepairDetails repairs={result.review.repairs} />
            {result.review.gate_note && (
              <div className="notice notice-warn mt-2">{result.review.gate_note}</div>
            )}
            {result.review.stall_note && (
              <div className="notice notice-info mt-2">{result.review.stall_note}</div>
            )}
            {result.review.hints?.map((h, i) => (
              <div key={i} className="notice notice-warn mt-1">{h}</div>
            ))}
            {result.review.comment && (
              <div className="muted">主审:{result.review.comment}</div>
            )}
          </div>
        )}

        {result.word_guard_action === "compressed" && (
          <div className="mt-2">
            <span className="badge"
              title="生成字数超出每章目标,AI 已自动压缩回目标范围(可在项目设置里关掉)">字数守卫:已压缩至目标范围</span>
          </div>
        )}

        {/* 空态收进明细:干净章节的"暂无未处理问题"不是需要占屏的信息 */}
        {issues !== null && !issues.length && (
          <div className="mt-2">
            <span className="badge ok"> 暂无未处理问题</span>
          </div>
        )}
        {issues === null && !issuesErr && (
          <span className="muted"> 问题清单加载中…</span>
        )}
        {doneIssues.length > 0 && (
          <details className="issue-done-box mt-1">
            <summary className="muted">已处理 {doneIssues.length} 条(已解决/已忽略)</summary>
            {doneIssues.map((issue) => (
              <IssueRow key={issue.id} issue={issue} busy={false} done />
            ))}
          </details>
        )}

        </div>
      </details>
    </div>
  );
}

// 单条问题:severity 徽标 + 来源 + 描述 + 可折叠证据 + 建议;open 状态给三个操作
function IssueRow({ issue, busy, done, genBlocked, genHint, onApply, onSpotRepair, onResolve, onIgnore, onAdopt }: {
  issue: ChapterIssue;
  busy: boolean;
  done?: boolean;
  // 任务锁:生成/重写任务在跑时禁用「按建议修订」(它也是一条重写链路,409 会被后端拦)
  genBlocked?: boolean;
  genHint?: string;
  onApply?: () => void;
  // 定点修复:仅带逐字证据的 open 问题显示(没有锚无法定位),失败正文不变
  onSpotRepair?: () => void;
  onResolve?: () => void;
  onIgnore?: () => void;
  // 宪法建议(source=canon)专用:采纳进 project.canon
  onAdopt?: () => void;
}) {
  // source=canon 是「宪法建议」:操作换成「采纳进宪法/忽略」,done 后徽标显示「已采纳进宪法」
  const isCanon = issue.source === "canon";
  const doneCn = isCanon && issue.status === "resolved"
    ? "已采纳进宪法"
    : (ISSUE_STATUS_CN[issue.status] ?? issue.status);
  return (
    <div className={"fact-line issue-item" + (done ? " issue-done" : "")}>
      <div className="issue-head">
        <span className={"badge " + (SEV_BADGE[issue.severity] ?? "")}>
          {SEV_CN[issue.severity] ?? issue.severity}
        </span>
        <span className="badge">{SOURCE_CN[issue.source] ?? issue.source}</span>
        {done && <span className="badge">{doneCn}</span>}
        <span>{issue.description}</span>
      </div>
      {issue.evidence && (
        <details className="issue-ev">
          <summary>证据(原文引用)</summary>
          <blockquote>{issue.evidence}</blockquote>
        </details>
      )}
      {issue.suggestion && <div className="muted">建议: {issue.suggestion}</div>}
      {!done && (
        <div className="issue-actions">
          {isCanon ? (
            <button className="primary btn-sm" disabled={busy}
              title="把这条书级设定写进故事宪法,全程注入生成并参与一致性门禁(可稍后在项目设定里编辑)"
              onClick={onAdopt}>
              {busy && <span className="spin spin-sm" />}采纳进宪法
            </button>
          ) : (
            <button className="primary btn-sm" disabled={busy || genBlocked}
              title={genBlocked ? genHint : "把该问题的修正建议交给 AI 走重写链路(受理即标记解决,门禁会重跑验证)"}
              onClick={onApply}>
              {busy && <span className="spin spin-sm" />}按建议修订
            </button>
          )}
          {!isCanon && issue.evidence && (
            <button className="btn-sm" disabled={busy || genBlocked}
              title={genBlocked ? genHint : "AI 原位改句消掉这条矛盾(不整章重写),门禁复查干净才生效;失败正文不变"}
              onClick={onSpotRepair}>
              定点修复
            </button>
          )}
          {!isCanon && (
            <button className="btn-sm" disabled={busy} onClick={onResolve}>已人工解决</button>
          )}
          <button className="btn-sm" disabled={busy} onClick={onIgnore}>忽略</button>
        </div>
      )}
    </div>
  );
}
