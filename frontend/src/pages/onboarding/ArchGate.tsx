// ArchGate — 架构工作墙(确认链 L2):把雪花四步从「串行黑盒」拆成四张层卡闸门。
// 每层独立生成(吃已拍板上游)→ 展开看/直接改 → 带话重出 → 拍板 → 解锁下一层。
// 种子层默认必停(全书基因,人介入价值最大);角色/世界观/情节默认「连跑」——
// 上游拍板后自动生成并自动拍板直通骨架墙,可逐层关掉变必停。信任模式(一枪整本)
// 由父级走旧链路,此处只提供入口。
// 作废语义:撤回/手改/重出第 N 层 → 该层与下游回未拍板(StaleBadge 可见)。
import { useEffect, useRef, useState } from "react";
import {
  api, ARCH_LAYER_DESC, ARCH_LAYER_KEYS, ARCH_LAYER_LABEL, ArchLayerKey,
  Architecture, resolvedLayers, Tendency,
} from "../../api";
import { errMsg, pollJob } from "../../pollJob";
import { ConfirmGate, DirectiveBar, StaleBadge } from "../../ui/confirmKit";
import { ThinkingText } from "../../ui/ThinkingText";

const AUTO_RUN_KEYS = ["character_dynamics", "world_building", "plot_architecture"] as const;
type AutoRunKey = (typeof AUTO_RUN_KEYS)[number];

const RUNNABLE = [...AUTO_RUN_KEYS] as ArchLayerKey[];

export default function ArchGate({ pid, tendency, onTrust, onAllConfirmed }: {
  pid: number;
  tendency: Tendency;
  /** 跳过闸门:走旧的一枪整本链路(信任模式) */
  onTrust: () => void;
  /** 四层全部拍板 → 父级亮骨架墙 */
  onAllConfirmed: () => void;
}) {
  const [arch, setArch] = useState<Architecture | null>(null);
  const [state, setState] = useState<Record<ArchLayerKey, boolean> | null>(null);
  const [runKey, setRunKey] = useState<ArchLayerKey | null>(null);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  const [edits, setEdits] = useState<Partial<Record<ArchLayerKey, string>>>({});
  // 连跑开关(逐层可关):关掉 = 这层变必停,拍板上游后停在这等人工拍板
  const [autoRun, setAutoRun] = useState<Record<AutoRunKey, boolean>>({
    character_dynamics: true, world_building: true, plot_architecture: true,
  });
  const bootRef = useRef(false);
  const busy = runKey !== null;

  async function refresh(): Promise<{ arch: Architecture | null; st: Record<ArchLayerKey, boolean> }> {
    let a: Architecture | null = null;
    try {
      a = await api.getArchitecture(pid);
    } catch { a = null; }
    const st = resolvedLayers(a);
    setArch(a);
    setState(st);
    return { arch: a, st };
  }

  function upstreamConfirmed(st: Record<ArchLayerKey, boolean>, k: ArchLayerKey): boolean {
    const idx = ARCH_LAYER_KEYS.indexOf(k);
    return ARCH_LAYER_KEYS.slice(0, idx).every((u) => st[u]);
  }

  function allConfirmed(st: Record<ArchLayerKey, boolean>, a: Architecture | null): boolean {
    return !!a && ARCH_LAYER_KEYS.every((k) => st[k]);
  }

  // 生成指定一层(directive = 带话重出);返回是否成功
  async function generateLayer(k: ArchLayerKey, directive = ""): Promise<boolean> {
    setErr("");
    setRunKey(k);
    setStage(directive ? "按你的要求重出" : "生成中");
    try {
      const { job_id } = await api.architectureLayerAsync(pid, k, tendency, directive);
      await pollJob(job_id, { onStage: setStage });
      const { st } = await refresh();
      // 带话重出/重出后该层回未拍板(后端语义),刷新编辑态
      setEdits((e) => { const n = { ...e }; delete n[k]; return n; });
      void st;
      return true;
    } catch (e) {
      setErr(errMsg(e));
      return false;
    } finally {
      setRunKey(null);
      setStage("");
    }
  }

  // 拍板/撤回一层;返回是否成功
  async function confirmLayer(k: ArchLayerKey, confirmed: boolean): Promise<boolean> {
    setErr("");
    try {
      await api.confirmArchitectureLayer(pid, k, confirmed);
      await refresh();
      return true;
    } catch (e) {
      setErr(errMsg(e));
      return false;
    }
  }

  // 链式驱动:从最靠前的未拍板层开始,能自动跑的连跑,遇到必停层就停。
  // 只在「拍板确认」与「首次点火」后调用——撤回/手改不自动重跑(不作惊吓)。
  async function drive(): Promise<void> {
    const { arch: a, st } = await refresh();
    if (allConfirmed(st, a)) { onAllConfirmed(); return; }
    for (const k of ARCH_LAYER_KEYS) {
      if (st[k]) continue;
      if (!upstreamConfirmed(st, k)) return; // 卡在更早的必停层
      const auto = (AUTO_RUN_KEYS as readonly string[]).includes(k) && autoRun[k as AutoRunKey];
      if (!auto) return; // 这层必停:等人
      if (!(await generateLayer(k))) return;
      if (!(await confirmLayer(k, true))) return;
    }
    const { arch: a2, st: st2 } = await refresh();
    if (allConfirmed(st2, a2)) onAllConfirmed();
  }

  // 首次进入:没架构自动点火生成种子层(与旧自动点火同一心智,仅一次);
  // 有架构且全拍板 → 直接亮骨架墙
  useEffect(() => {
    if (bootRef.current) return;
    bootRef.current = true;
    (async () => {
      const { arch: a, st } = await refresh();
      if (allConfirmed(st, a)) { onAllConfirmed(); return; }
      if (!a) await generateLayer("core_seed");
      // 半途恢复:只刷新视图,是否连跑交给人(避免刷新后突然自动烧 token)
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  // 手改一层:PATCH 落库(后端把该层与下游回未拍板)
  async function saveEdit(k: ArchLayerKey) {
    const text = edits[k];
    if (text === undefined) return;
    setErr("");
    try {
      await api.patchArchitecture(pid, { [k]: text });
      setEdits((e) => { const n = { ...e }; delete n[k]; return n; });
      await refresh();
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  const st = state ?? resolvedLayers(arch);
  const staleOf = (k: ArchLayerKey) => (arch && (arch[k] ?? "").trim()) && !upstreamConfirmed(st, k);
  const conceptStale = !!arch?.concept_stale;

  return (
    <div className="card mt-3 arch-gate" data-testid="arch-gate">
      <div className="card-head">
        <h3>架构工作墙 · 逐层拍板</h3>
        <span className="hint">从上往下:每层生成→看/改→拍板→解锁下一层;种子里你的介入价值最大。</span>
      </div>

      {conceptStale && (
        <div className="mt-2">
          <StaleBadge stale reason="概念已改,这份架构基于旧概念" actionText="一枪重出整本" actionTitle="走信任模式整本重出(不逐层停)"
            onAction={onTrust} />
        </div>
      )}

      <div className="arch-layers">
        {ARCH_LAYER_KEYS.map((k) => {
          const confirmed = st[k];
          const text = edits[k] ?? (arch?.[k] ?? "");
          const hasText = !!(arch?.[k] ?? "").trim() || edits[k] !== undefined;
          const upOk = upstreamConfirmed(st, k);
          const stale = staleOf(k);
          const running = runKey === k;
          const draftDirty = edits[k] !== undefined && edits[k] !== (arch?.[k] ?? "");
          return (
            <div key={k} className={"arch-layer" + (confirmed ? " confirmed" : "") + (!upOk ? " locked" : "")}
              data-testid={`arch-layer-${k}`}>
              <div className="arch-layer-head">
                <span className={"arch-dot " + (running ? "run" : confirmed ? "ok" : stale ? "stale" : hasText ? "draft" : "wait")} />
                <b>{ARCH_LAYER_LABEL[k]}</b>
                {running && <span className="muted arch-stage"><ThinkingText phrases={[stage || "生成中"]} interval={4000} />…</span>}
                {confirmed && !running && <span className="ck-state">✓ 已拍板</span>}
                <span className="grow" />
                {RUNNABLE.includes(k) && !confirmed && upOk && (
                  <label className="arch-auto hint" title="勾上:上游拍板后自动生成并拍板这一层,直通骨架墙">
                    <input type="checkbox" checked={autoRun[k as AutoRunKey]} disabled={running}
                      onChange={(e) => setAutoRun((m) => ({ ...m, [k as AutoRunKey]: e.target.checked }))} />
                    连跑
                  </label>
                )}
                {!confirmed && upOk && !running && (
                  <button className="btn-sm" disabled={busy}
                    title={hasText ? "不要这版,重新生成一层" : `用 AI 生成${ARCH_LAYER_LABEL[k]}`}
                    onClick={() => { void generateLayer(k).then(() => void refresh()); }}>
                    {hasText ? "重出" : "生成"}
                  </button>
                )}
              </div>
              <div className="hint arch-desc">{ARCH_LAYER_DESC[k]}</div>
              {stale && (
                <div className="mt-1">
                  <StaleBadge stale reason="上游已变,这版基于旧上游" actionText="重出这层"
                    onAction={() => { void generateLayer(k).then(() => void refresh()); }} />
                </div>
              )}
              {(hasText || running) && (
                <>
                  <textarea className="arch-text mt-1" rows={k === "core_seed" ? 3 : 5}
                    disabled={running || !upOk}
                    placeholder={running ? "生成中…" : upOk ? "可直接手写/改;改完点「存」" : "上游拍板后解锁"}
                    value={running ? (stage || "生成中") : text}
                    onChange={(e) => setEdits((m) => ({ ...m, [k]: e.target.value }))} />
                  <div className="arch-actions">
                    {draftDirty && <button className="btn-sm" onClick={() => { void saveEdit(k); }}>存</button>}
                    {!confirmed && upOk && !running && (
                      <ConfirmGate confirmed={false} disabled={busy}
                        confirmTitle={`这一版${ARCH_LAYER_LABEL[k]}我认了,解锁下一层`}
                        onConfirm={() => { void confirmLayer(k, true).then(() => void drive()); }} />
                    )}
                    {confirmed && (
                      <ConfirmGate confirmed disabled={busy}
                        unconfirmTitle="撤回拍板(下游一并回未拍板)"
                        onUnconfirm={() => { void confirmLayer(k, false); }} />
                    )}
                  </div>
                  {!confirmed && upOk && !running && (
                    <div className="mt-1">
                      <DirectiveBar disabled={busy} busy={false}
                        placeholder={`带句话重出${ARCH_LAYER_LABEL[k]},如「反派的动机再立体一点」`}
                        sendText="带话重出" onSend={(t) => { void generateLayer(k, t).then(() => void refresh()); }} />
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>

      {err && <div className="msg-err mt-2">{err}</div>}

      <div className="actions mt-2">
        <span className="hint">
          等不了?{onTrust ? <button className="btn-sm" onClick={onTrust}>跳过闸门,一枪生成整本架构(信任模式)</button> : null}
        </span>
      </div>
    </div>
  );
}
