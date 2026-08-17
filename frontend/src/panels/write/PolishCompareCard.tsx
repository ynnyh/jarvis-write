// 整章润色对照卡(「正文即界面」P2/P3,docs/10 §6):从 PolishPanel 抽出的结果卡——
// AI 窄栏 ③档「整章优化」跑完后在正文区顶部展示:AI味/锁定事实/事实违规 badge + 违规清单
// + 两种看法切换:①看改动(diffParagraphs 逐段字符级 diff,红删绿增,默认)②编辑微调(左原文只读、
// 右润色稿可改)→ [应用写回定稿](有违规禁用)/[放弃]。
// 与原 PolishPanel 一致:应用只写回定稿,不自动同步一致性引擎(润色锁情节、不动情节)。
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, flavorTitle, PolishResult } from "../../api";
import { qk, useInvalidateProject } from "../../hooks/queries";
import { errMsg } from "../../pollJob";
import { diffParagraphs } from "./charDiff";
import DiffText from "./DiffText";

interface Props {
  pid: number;
  chapterNum: number;
  // 发起润色时的原文快照(对照左栏;润色期间正文若被改动,应用仍以润色稿整体覆盖)
  original: string;
  result: PolishResult;
  // 应用成功(缓存已失效重拉):父级收起此卡
  onApplied: () => void;
  onDiscard: () => void;
}

export default function PolishCompareCard({
  pid, chapterNum, original, result, onApplied, onDiscard,
}: Props) {
  const qc = useQueryClient();
  const invalidateProject = useInvalidateProject(pid);
  // 润色稿可编辑副本:应用时以此为准(用户可在 AI 结果上手动微调)
  const [polishedDraft, setPolishedDraft] = useState(result.polished);
  // 看法:true=逐段字符级 diff(默认,验收即 diff)/ false=左右对照可编辑微调
  const [showDiff, setShowDiff] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function apply() {
    setBusy(true); setErr("");
    try {
      await api.applyPolish(pid, chapterNum, polishedDraft);
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.chapter(pid, chapterNum) }),
        invalidateProject(),
      ]);
      onApplied();
    } catch (e) {
      setErr(errMsg(e));
    } finally { setBusy(false); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">整章优化对照 · 第{chapterNum}章</h3>
        <button className="btn-sm" onClick={() => setShowDiff((v) => !v)}>
          {showDiff ? "编辑微调" : "看改动"}
        </button>
      </div>
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
      {showDiff ? (
        <div className="polish-diff mt-3">
          <div className="rp-label">
            逐段改动(红=删,绿=增;{original.length}→{polishedDraft.length}字)
          </div>
          {diffParagraphs(original, polishedDraft).map((d, i) => (
            <p key={i} className={"pd-para pd-" + d.status}>
              {d.status === "same" && d.newText}
              {d.status === "changed" && d.ops && <DiffText ops={d.ops} />}
              {d.status === "added" && <span className="diff-new">{d.newText}</span>}
              {d.status === "removed" && <span className="diff-old">{d.oldText}</span>}
            </p>
          ))}
        </div>
      ) : (
        <div className="split mt-3">
          <div>
            <div className="fl">原文({original.length}字)</div>
            <div className="pane pane-prose prose">{original}</div>
          </div>
          <div>
            <div className="fl">润色稿({polishedDraft.length}字 · 应用前可手动微调)</div>
            <textarea
              className="editor-area"
              value={polishedDraft}
              onChange={(e) => setPolishedDraft(e.target.value)}
            />
          </div>
        </div>
      )}
      <div className="actions mt-3">
        <button className="primary"
          disabled={busy || !!result.violations.length || !polishedDraft.trim()}
          onClick={apply}>
          {busy && <span className="spin" />}应用(写回第{chapterNum}章定稿)
        </button>
        <button disabled={busy} onClick={onDiscard}>放弃这版</button>
        {!!result.violations.length && (
          <span className="msg-err">有事实违规,不允许直接应用,请重新润色</span>
        )}
      </div>
      {err && <div className="msg-err mt-2">{err}</div>}
    </div>
  );
}
