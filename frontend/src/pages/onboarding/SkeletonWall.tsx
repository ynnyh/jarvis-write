// SkeletonWall — 故事骨架墙(docs/20 两段式点火 §4.2-4.4)。
// 点火流水线的中间墙:架构生成完 → 出分段走向 → 作者逐段拍板 → 逐段铺章。
// 每段可改(段名/目标/冲突/起止状态)、可锁(重出骨架时保留)、确认后才铺章;
// 信任模式(跳过骨架一枪铺全书)走旧链路 runBp——确认是权利不是门槛。
// 本组件自管数据与任务轮询,不向导的 useOnboarding 加状态。
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, SkeletonSegment, Tendency } from "../../api";
import { pollJob, errMsg } from "../../pollJob";
import { ConfirmGate } from "../../ui/confirmKit";

type Phase = "loading" | "generating" | "wall" | "paving" | "error";

export default function SkeletonWall({ pid, tendency, onTrust, onPaved }: {
  pid: number;
  tendency: Tendency;
  /** 信任模式:跳过骨架,直接走旧的一枪铺全书链路 */
  onTrust: () => void;
  /** 任一段铺完(蓝图已有着落),向导亮「进入工作台」 */
  onPaved: () => void;
}) {
  const qc = useQueryClient();
  const [phase, setPhase] = useState<Phase>("loading");
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  const [segments, setSegments] = useState<SkeletonSegment[]>([]);
  const [edits, setEdits] = useState<Record<number, Partial<SkeletonSegment>>>({});
  const [pavedRanges, setPavedRanges] = useState<string>("");
  // 铺完一段后的前三张章卡速览(确认链 L4):歪了马上重铺,不等全书
  const [preview, setPreview] = useState<{ range: [number, number]; items: { num: number; title: string; summary: string }[] } | null>(null);

  async function refresh() {
    const out = await api.getSkeleton(pid);
    setSegments(out.segments);
    return out.segments;
  }

  // 首次进入:没骨架就自动出一次(与架构自动点火同一心智);有骨架直接上墙
  useEffect(() => {
    let stopped = false;
    (async () => {
      try {
        const segs = await refresh();
        if (stopped) return;
        if (segs.length) { setPhase("wall"); return; }
        await runGenerate();
      } catch (e) {
        if (!stopped) { setPhase("error"); setErr(errMsg(e)); }
      }
    })();
    return () => { stopped = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  async function runGenerate() {
    setPhase("generating");
    setErr("");
    try {
      const { job_id } = await api.generateSkeletonAsync(pid, tendency);
      await pollJob(job_id, { onStage: setStage });
      await refresh();
      setPhase("wall");
    } catch (e) {
      setPhase("error");
      setErr(errMsg(e));
    }
  }

  /** 铺一段;返回是否成功(paveAll 串行依赖这个,不能读 state 闭包) */
  async function pave(index: number): Promise<boolean> {
    setPhase("paving");
    setErr("");
    try {
      const { job_id } = await api.paveSegmentAsync(pid, index, tendency);
      const r = await pollJob<{ planned_range?: number[] }>(job_id, { onStage: setStage });
      const [s, e] = r?.planned_range ?? [];
      setPavedRanges((cur) => (cur ? `${cur}、第${s}-${e}章` : `第${s}-${e}章`));
      // 前三张速览:标题 + 一句话,人立刻能判断这段铺得对不对
      try {
        const outs = await api.listOutlines(pid);
        const inRange = outs.filter((o) => o.chapter_number >= s && o.chapter_number <= e);
        setPreview({
          range: [s, e],
          items: inRange.slice(0, 3).map((o) => ({ num: o.chapter_number, title: o.title, summary: o.summary })),
        });
      } catch { setPreview(null); }
      // 蓝图落了库,向导的大纲缓存作废;工作台按钮亮起
      qc.invalidateQueries({ queryKey: ["outlines", pid] });
      onPaved();
      setPhase("wall");
      return true;
    } catch (e) {
      setPhase("error");
      setErr(errMsg(e));
      return false;
    }
  }

  async function paveAll() {
    for (let i = 0; i < segments.length; i++) {
      if (!segments[i].confirmed) continue;
      // 逐段串行(铺章依赖前段衔接);已铺过的章由 save_blueprint 幂等
      // eslint-disable-next-line no-await-in-loop
      const ok: boolean = await pave(i);
      if (!ok) return;
    }
  }

  async function saveEdit(i: number) {
    const body = edits[i];
    if (!body || !Object.keys(body).length) return;
    await api.editSkeletonSegment(pid, i, body);
    setEdits((cur) => { const nxt = { ...cur }; delete nxt[i]; return nxt; });
    await refresh();
  }

  if (phase === "loading") {
    return <div className="muted mt-2"><span className="spin spin-sm" /> 读取故事骨架…</div>;
  }

  if (phase === "generating" || phase === "paving") {
    return (
      <div className="card mt-3 skeleton-pipe" data-testid="skeleton-wall">
        <div className="wiz-pipe">
          <div className="wiz-pipe-card run">
            <div className="wiz-pipe-icon"><span className="spin" /></div>
            <div className="grow">
              <div className="wiz-pipe-label">{phase === "generating" ? "生成故事骨架" : "按骨架铺章节蓝图"}</div>
              <div className="hint">
                {phase === "generating"
                  ? "把全书切成段,每段给走向:段名/目标/冲突/起止状态"
                  : "只铺确认过的段;段内已锁定的章不会被覆盖"}
              </div>
              <div className="muted mt-1"><ThinkingTextLite text={stage || "生成中"} />…</div>
            </div>
          </div>
        </div>
        <button className="btn-sm" onClick={onTrust}>等不了?跳过骨架,直接一枪铺全书(信任模式)</button>
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div className="card mt-3">
        <div className="msg-err">{err}</div>
        <div className="mt-2 actions">
          <button className="btn-sm" onClick={() => { void runGenerate(); }}>重出骨架</button>
          <button className="btn-sm" onClick={onTrust}>跳过骨架,直接铺全书(信任模式)</button>
        </div>
      </div>
    );
  }

  const allConfirmed = segments.every((s) => s.confirmed);

  return (
    <div className="card mt-3 skeleton-wall" data-testid="skeleton-wall">
      <div className="card-head">
        <h3>故事骨架 · 逐段拍板</h3>
        <span className="muted">改完点「存」;拍板(✓)后才能铺这一段;🔒 锁定的段重出骨架时保留。</span>
      </div>
      <div className="skeleton-list">
        {segments.map((seg, i) => {
          const view = { ...seg, ...(edits[i] ?? {}) } as SkeletonSegment;
          const dirty = !!edits[i] && Object.keys(edits[i]).length > 0;
          return (
            <div className={"skeleton-seg" + (seg.confirmed ? " confirmed" : "")} key={i}>
              <div className="skeleton-seg-head">
                <b>{i + 1}</b>
                <span className="muted">第 {view.start}-{view.end} 章</span>
                <input className="skeleton-title" value={view.title}
                  placeholder="段名"
                  onChange={(e) => setEdits((c) => ({ ...c, [i]: { ...c[i], title: e.target.value } }))} />
                <span className="grow" />
                {dirty && <button className="btn-sm" onClick={() => { void saveEdit(i); }}>存</button>}
                <ConfirmGate confirmed={seg.confirmed} locked={seg.locked}
                  lockTitleOn="锁定(重出骨架保留这一段)" lockTitleOff="解锁(重出骨架会重出这一段)"
                  onConfirm={() => { void api.confirmSkeletonSegment(pid, i, true).then(refresh); }}
                  onUnconfirm={() => { void api.confirmSkeletonSegment(pid, i, false).then(refresh); }}
                  onToggleLock={() => { void api.lockSkeletonSegment(pid, i, !seg.locked).then(refresh); }} />
                <button className="btn-sm primary" disabled={!seg.confirmed}
                  title={seg.confirmed ? `只铺第 ${view.start}-${view.end} 章的章节蓝图` : "先拍板再铺章"}
                  onClick={() => { void pave(i); }}>
                  铺这一段
                </button>
              </div>
              <textarea className="skeleton-goal" rows={2} value={view.goal}
                placeholder="段目标:开始什么处境 → 结束什么处境"
                onChange={(e) => setEdits((c) => ({ ...c, [i]: { ...c[i], goal: e.target.value } }))} />
              <div className="skeleton-meta">
                <input className="skeleton-in" value={view.conflict} placeholder="主线冲突"
                  onChange={(e) => setEdits((c) => ({ ...c, [i]: { ...c[i], conflict: e.target.value } }))} />
                <input className="skeleton-in" value={view.start_state} placeholder="进段状态"
                  onChange={(e) => setEdits((c) => ({ ...c, [i]: { ...c[i], start_state: e.target.value } }))} />
                <input className="skeleton-in" value={view.end_state} placeholder="出段状态"
                  onChange={(e) => setEdits((c) => ({ ...c, [i]: { ...c[i], end_state: e.target.value } }))} />
              </div>
            </div>
          );
        })}
      </div>
      {preview && (
        <div className="pave-preview mt-2" data-testid="pave-preview">
          <div className="card-head">
            <h3>第 {preview.range[0]}-{preview.range[1]} 章 · 前三张速览</h3>
            <span className="hint">方向不对就改完再「铺这一段」(会覆盖重铺);细看去工作台目录。</span>
          </div>
          <div className="pave-preview-cards">
            {preview.items.map((it) => (
              <div key={it.num} className="pave-preview-card">
                <b>第 {it.num} 章 · {it.title}</b>
                <p className="hint">{it.summary}</p>
              </div>
            ))}
          </div>
        </div>
      )}
      {err && <div className="msg-err mt-1">{err}</div>}
      <div className="actions mt-2">
        <button className="btn-sm" onClick={() => { void runGenerate(); }}
          title="未锁定段重出;已拍板的段走向会作为约束注入">
          重出骨架
        </button>
        <button className="primary" disabled={!allConfirmed}
          title="按顺序铺全部已拍板的段(段内已锁定章不覆盖)"
          onClick={() => { void paveAll(); }}>
          全部确认,开始铺章 →
        </button>
        <button className="btn-sm" onClick={onTrust}>跳过骨架,一枪铺全书(信任模式)</button>
        {pavedRanges && <span className="muted">已铺:{pavedRanges}</span>}
      </div>
    </div>
  );
}

/** 轻量思考文案(不引组件依赖,骨架墙自足) */
function ThinkingTextLite({ text }: { text: string }) {
  return <span>{text}</span>;
}
