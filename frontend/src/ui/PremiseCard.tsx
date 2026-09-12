// PremiseCard — 核心梗卡(docs/19):高概念/兑现机制/节拍表/边界禁忌/钩子计划。
// 一本书的「纲」:蓝图逐章标「梗兑现」、作战图顶行、交稿对账都以它为轴。
// 三种用法:只读展示(作战图顶行)、设置页编辑(ProjectSettingsPanel)、
// 开书向导确认步的预填卡(autoSuggest:无梗卡时 AI 提炼一次,作者改完保存)。
import { useEffect, useState } from "react";
import { api, Premise, PremiseInput } from "../api";
import { errMsg } from "../pollJob";
import { toast } from "./Toaster";

interface Props {
  pid: number;
  /** 初始梗卡;null = 未建。autoSuggest 时组件自己拉 AI 草稿 */
  initial: Premise | null;
  /** 无梗卡时自动请 AI 提炼一次(开书向导确认步用) */
  autoSuggest?: boolean;
  /** 保存成功后的回调(向导里用于刷新预览) */
  onSaved?: (p: Premise) => void;
  /** 只读紧凑态(作战图顶行):只出一行,不带编辑 */
  compact?: boolean;
}

const EMPTY: Premise = {
  high_concept: "", payoff: "", beats: [], boundaries: [],
  hook_plan: {}, source: "ai",
};

export default function PremiseCard({ pid, initial, autoSuggest = false, onSaved, compact = false }: Props) {
  const [premise, setPremise] = useState<Premise | null>(initial);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

  // initial 变化(异步查询加载完成)→ 同步内部态;编辑中不打断
  useEffect(() => {
    if (editing) return;
    setPremise(initial);
  }, [initial]);

  // 无梗卡且允许自动建议:拉一次 AI 草稿(失败静默,编辑框仍可手填)
  useEffect(() => {
    if (!autoSuggest || initial || premise) return;
    let alive = true;
    api.suggestPremise(pid)
      .then((p) => { if (alive && p) setPremise({ ...p }); })
      .catch(() => { /* 提炼失败:留空白卡手填,不打断开书 */ });
    return () => { alive = false; };
  }, [autoSuggest, initial, pid, premise]);

  async function save(p: PremiseInput) {
    if (!p.high_concept.trim()) { toast.err("高概念不能为空", "一句话说清这本书的梗,50 字内"); return; }
    setBusy(true);
    try {
      const saved = await api.savePremise(pid, p);
      setPremise(saved);
      setEditing(false);
      toast.ok("核心梗卡已保存", "蓝图与正文生成会以它为纲");
      onSaved?.(saved);
    } catch (e) { toast.err("梗卡保存失败", errMsg(e)); }
    finally { setBusy(false); }
  }

  if (compact) {
    if (!premise) return null;
    return (
      <span className="premise-compact" title={`${premise.high_concept} · ${premise.payoff}`}>
        🎯 {premise.high_concept}
      </span>
    );
  }

  if (!premise && !editing) {
    return (
      <div className="premise-card premise-empty">
        <b>🎯 还没有核心梗卡</b>
        <span className="card-desc">梗是纲:蓝图、正文、体检都以它为轴。让 AI 从概念提炼一份,或自己手填。</span>
        <div className="mt-2">
          <button className="btn-sm primary" disabled={busy}
            onClick={async () => {
              setBusy(true);
              try { setPremise({ ...EMPTY, ...(await api.suggestPremise(pid)) }); setEditing(true); }
              catch { setPremise({ ...EMPTY }); setEditing(true); }
              finally { setBusy(false); }
            }}>
            {busy ? "提炼中…" : "✨ AI 提炼 / 手填"}
          </button>
        </div>
      </div>
    );
  }

  if (editing && premise) {
    return <PremiseEditor draft={premise} busy={busy} onSave={save} onCancel={() => { setEditing(false); if (!initial) setPremise(initial); }} />;
  }

  if (!premise) return null;
  return (
    <div className="premise-card">
      <div className="premise-row"><b>高概念</b><span>{premise.high_concept || "—"}</span></div>
      {premise.payoff && <div className="premise-row"><b>兑现机制</b><span>{premise.payoff}</span></div>}
      {!!premise.beats.length && (
        <div className="premise-row"><b>节拍表</b>
          <span className="premise-beats">
            {premise.beats.map((b, i) => <span key={i} className="chip">第{i + 1}拍·{b}</span>)}
          </span>
        </div>
      )}
      {!!premise.boundaries.length && (
        <div className="premise-row"><b>边界禁忌</b>
          <span className="premise-beats">
            {premise.boundaries.map((b, i) => <span key={i} className="chip chip-warn">✗{b}</span>)}
          </span>
        </div>
      )}
      <div className="premise-row"><b>钩子计划</b>
        <span>
          开局:{premise.hook_plan?.opening || "—"} · 中反转:{premise.hook_plan?.mid || "—"} · 大高潮:{premise.hook_plan?.climax || "—"}
        </span>
      </div>
      {!compact && (
        <div className="mt-2">
          <button className="btn-sm" onClick={() => setEditing(true)}>改梗卡</button>
        </div>
      )}
    </div>
  );
}

/** 编辑表单:字段全部可改;节拍/禁忌走可增删行 */
function PremiseEditor({ draft, busy, onSave, onCancel }: {
  draft: Premise; busy: boolean;
  onSave: (p: PremiseInput) => void; onCancel: () => void;
}) {
  const [high, setHigh] = useState(draft.high_concept);
  const [payoff, setPayoff] = useState(draft.payoff);
  const [beats, setBeats] = useState<string[]>(draft.beats.length ? draft.beats : ["", "", ""]);
  const [bounds, setBounds] = useState<string[]>(draft.boundaries);
  const [opening, setOpening] = useState(draft.hook_plan?.opening ?? "");
  const [mid, setMid] = useState(draft.hook_plan?.mid ?? "");
  const [climax, setClimax] = useState(draft.hook_plan?.climax ?? "");

  const cleaned = (arr: string[]) => arr.map((s) => s.trim()).filter(Boolean);
  const val = (s: string, set: (v: string) => void) => (
    <input value={s} onChange={(e) => set(e.target.value)} className="premise-input" />
  );

  return (
    <div className="premise-edit" data-testid="premise-editor">
      <div className="field"><div className="fl">高概念(50 字内,一句话说清这本书的梗)</div>
        {val(high, setHigh)}
      </div>
      <div className="field"><div className="fl">兑现机制(这个梗为什么能反复产生冲突与满足)</div>
        <textarea value={payoff} onChange={(e) => setPayoff(e.target.value)} rows={2} />
      </div>
      <div className="field"><div className="fl">兑现节拍(3-6 拍,蓝图逐章对拍;留空的行保存时忽略)</div>
        {beats.map((b, i) => (
          <div className="premise-line" key={i}>
            <span className="muted">第{i + 1}拍</span>
            <input value={b} onChange={(e) => setBeats((v) => v.map((x, j) => (j === i ? e.target.value : x)))} />
            <button className="btn-sm" title="删掉这一拍" onClick={() => setBeats((v) => v.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        {beats.length < 6 && <button className="btn-sm" onClick={() => setBeats((v) => [...v, ""])}>+ 加一拍</button>}
      </div>
      <div className="field"><div className="fl">边界禁忌(写什么会把梗写崩)</div>
        {bounds.map((b, i) => (
          <div className="premise-line" key={i}>
            <input value={b} onChange={(e) => setBounds((v) => v.map((x, j) => (j === i ? e.target.value : x)))} />
            <button className="btn-sm" onClick={() => setBounds((v) => v.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <button className="btn-sm" onClick={() => setBounds((v) => [...v, ""])}>+ 加一条</button>
      </div>
      <div className="field"><div className="fl">钩子计划</div>
        <div className="premise-line"><span className="muted">开局钩</span>{val(opening, setOpening)}</div>
        <div className="premise-line"><span className="muted">中反转</span>{val(mid, setMid)}</div>
        <div className="premise-line"><span className="muted">大高潮</span>{val(climax, setClimax)}</div>
      </div>
      <div className="form-actions">
        <button className="primary" disabled={busy}
          onClick={() => onSave({
            high_concept: high, payoff, beats: cleaned(beats), boundaries: cleaned(bounds),
            hook_plan: { opening, mid, climax },
          })}>
          {busy ? "保存中…" : "保存梗卡"}
        </button>
        <button onClick={onCancel}>取消</button>
      </div>
    </div>
  );
}
