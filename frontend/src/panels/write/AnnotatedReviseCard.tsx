// ②档「多处批注改」验收卡(「正文即界面」P3,docs/10 §4/§6):AI 窄栏「按批注改」跑完后
// 在正文区顶部展示——每条批注一张子卡,字符级 diff(charDiff/DiffText,红删绿增)逐条 [接受]/[拒绝];
// 也可「全部接受剩余」。接受走 paraEdit 共享写回链路(段号 + 原文快照守卫),逐条 PUT content 落库。
// 因逐条接受会改动正文,本卡以本地 chap 串起每次写回结果(而非依赖父级 re-render 时序),
// 保证第二条接受基于第一条之后的正文,不会用旧全文覆盖回退。段落数不变(一段→一段)故段号不错位。
import { useState } from "react";
import { ChapterDetail, RevisePair } from "../../api";
import { emitChapterSaved } from "../../desktop";
import { errMsg } from "../../pollJob";
import { applyParaReplacement } from "./paraEdit";
import { diffChars } from "./charDiff";
import DiffText from "./DiffText";

interface Props {
  pid: number;
  chapter: ChapterDetail;
  pairs: RevisePair[];
  // 单条接受写回成功:父级更新 qk.chapter 缓存并刷新章节列表(与 Prose/AiDock 同一回调)
  onSaved: (updated: ChapterDetail) => void;
  // 单条接受成功后的联动(可选):全书批修用它销账对应标记;②档单章流不用
  onPairAccepted?: (index: number) => void;
  // 卡头标题(可选):全书批修传「全书批修」,默认②档「按批注改」
  heading?: string;
  // 关闭本卡(全部处理完或用户主动收起):父级清空 reviseResult
  onClose: () => void;
};

type Resolution = "accepted" | "rejected";

export default function AnnotatedReviseCard({ pid, chapter, pairs, onSaved, onPairAccepted, heading, onClose }: Props) {
  // 本地章节:每次接受用它作为写回基准,并以返回结果更新(见文件头注释)
  const [chap, setChap] = useState(chapter);
  const [resolved, setResolved] = useState<Record<number, Resolution>>({});
  const [applyingIdx, setApplyingIdx] = useState<number | null>(null);
  const [applyingAll, setApplyingAll] = useState(false);
  const [err, setErr] = useState("");

  const busy = applyingIdx !== null || applyingAll;
  const resolvedCount = Object.keys(resolved).length;
  const pendingOk = pairs.filter((p, i) => p.ok && !resolved[i]).length;
  const allDone = resolvedCount === pairs.length;

  async function accept(i: number) {
    const p = pairs[i];
    if (busy || resolved[i] || !p.ok) return;
    setApplyingIdx(i); setErr("");
    try {
      const updated = await applyParaReplacement(pid, chap, p.para_idx, p.old, p.new);
      if (!updated) {
        setErr(`第 ${p.para_idx + 1} 段原文已对不上(正文可能被别处改动),这条无法接受`);
        return;
      }
      setChap(updated);
      onSaved(updated);
      onPairAccepted?.(i);
      void emitChapterSaved(pid, chap.chapter_number);
      setResolved((r) => ({ ...r, [i]: "accepted" }));
    } catch (e) {
      setErr(errMsg(e));
    } finally { setApplyingIdx(null); }
  }

  function reject(i: number) {
    if (busy) return;
    setResolved((r) => ({ ...r, [i]: "rejected" }));
  }

  // 全部接受剩余:串行写回(本地 chap 逐条前推),原文对不上的自动跳过并提示
  async function acceptAll() {
    if (busy || !pendingOk) return;
    setApplyingAll(true); setErr("");
    let cur = chap;
    const done: Record<number, Resolution> = {};
    let skipped = 0;
    try {
      for (let i = 0; i < pairs.length; i++) {
        const p = pairs[i];
        if (resolved[i] || !p.ok) continue;
        const updated = await applyParaReplacement(pid, cur, p.para_idx, p.old, p.new);
        if (!updated) { skipped++; continue; }
        cur = updated;
        done[i] = "accepted";
      }
      if (Object.keys(done).length) {
        setChap(cur);
        onSaved(cur);
        for (const i of Object.keys(done)) onPairAccepted?.(Number(i));
        void emitChapterSaved(pid, cur.chapter_number);
        setResolved((r) => ({ ...r, ...done }));
      }
      if (skipped) setErr(`有 ${skipped} 条原文已对不上(正文被改动过),已跳过,可单独处理`);
    } catch (e) {
      setErr(errMsg(e));
    } finally { setApplyingAll(false); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">{heading ?? "按批注改"} · 第{chap.chapter_number}章({resolvedCount}/{pairs.length} 已处理)</h3>
        {pendingOk > 0 && (
          <button className="btn-sm primary" disabled={busy} onClick={acceptAll}>
            {applyingAll && <span className="spin spin-sm" />}全部接受剩余({pendingOk})
          </button>
        )}
        <button className="btn-sm" disabled={busy} onClick={onClose}>
          {allDone ? "完成" : "关闭"}
        </button>
      </div>

      <div className="revise-pairs">
        {pairs.map((p, i) => {
          const res = resolved[i];
          if (res) {
            return (
              <div key={i} className="revise-pair resolved">
                <span className="rp-label grow">第 {p.para_idx + 1} 段</span>
                <span className={"badge" + (res === "accepted" ? " ok" : "")}>
                  {res === "accepted" ? "已接受 ✓" : "已拒绝"}
                </span>
              </div>
            );
          }
          if (!p.ok) {
            return (
              <div key={i} className="revise-pair">
                <div className="rp-label">第 {p.para_idx + 1} 段 · 这条没改成</div>
                <div className="msg-err">{p.notes || "改写失败"}</div>
                <div className="rp-actions">
                  <button className="btn-sm" disabled={busy} onClick={() => reject(i)}>知道了,移除</button>
                </div>
              </div>
            );
          }
          return (
            <div key={i} className="revise-pair">
              <div className="rp-label">第 {p.para_idx + 1} 段 · 改动(红=删,绿=增)</div>
              <div className="rp-text diff-card">
                <DiffText ops={diffChars(p.old, p.new)} />
              </div>
              {p.notes && <div className="hint revise-notes">改动说明:{p.notes}</div>}
              <div className="rp-actions">
                <button className="primary btn-sm" disabled={busy} onClick={() => accept(i)}>
                  {applyingIdx === i && <span className="spin spin-sm" />}接受
                </button>
                <button className="btn-sm" disabled={busy} onClick={() => reject(i)}>拒绝</button>
              </div>
            </div>
          );
        })}
      </div>

      {err && <div className="msg-err mt-2">{err}</div>}
    </div>
  );
}
