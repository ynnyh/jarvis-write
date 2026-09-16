// ReconciliationBlock — 交稿对账(docs/19 M3):生成结果卡里的「本章对账」区。
// 系统在本章自动建联的东西(待确认关系边 / 梗兑现 / 伏笔变动)摆到作者面前:
// 关系逐条确认或否决(否决删边并收口证据事实);梗兑现账只读展示(设置页可改判)。
// 全部处理完(无 pending)只出一行摘要,不占地方。
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { errMsg } from "../pollJob";
import { toast } from "../ui/Toaster";

const OP_CN: Record<string, string> = { planted: "本章埋设", reinforced: "本章强化", payoff: "本章回收" };

export default function ReconciliationBlock({ pid, n }: { pid: number; n: number }) {
  const qc = useQueryClient();
  const recon = useQuery({
    queryKey: ["reconciliation", pid, n],
    queryFn: () => api.getReconciliation(pid, n),
    staleTime: 0,
  });
  const [busy, setBusy] = useState(false);

  if (recon.isLoading || recon.isError || !recon.data) return null;
  const d = recon.data;
  const oc = d.order_check;
  const orderHasFindings = !!oc && (
    oc.missed.length > 0 || oc.uninvited.length > 0 || oc.exit_missing.length > 0
    || oc.relations_missed.length > 0 || oc.beats.some((b) => !b.hit)
  );
  const nothing = d.confirmed && !d.ledger && d.foreshadow_changes.length === 0 && !orderHasFindings;
  if (nothing) return null;

  async function decide(confirmedIds: number[], rejectedIds: number[]) {
    if (!confirmedIds.length && !rejectedIds.length) return;
    setBusy(true);
    try {
      await api.confirmReconciliation(pid, n, {
        confirmed_relation_ids: confirmedIds,
        rejected_relation_ids: rejectedIds,
      });
      await qc.invalidateQueries({ queryKey: ["reconciliation", pid, n] });
      toast.ok("对账完成", `确认 ${confirmedIds.length} 条,否决 ${rejectedIds.length} 条`);
    } catch (e) {
      toast.err("对账操作失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="recon-block" data-testid="reconciliation">
      <div className="dossier-block-title">
        本章对账{d.pending_relations.length ? ` · ${d.pending_relations.length} 条待确认` : " · 无待确认项"}
        <span className="muted recon-hint">——系统读完这章自动登记的账:AI 替你记,你说了算</span>
      </div>

      {d.ledger && (
        <div className="recon-line">
          <span className={"chip" + (d.ledger.fulfilled ? "" : " chip-warn")}>
            {d.ledger.fulfilled ? "梗已兑现" : "梗未兑现"}
          </span>
          <span>
            {d.ledger.beat || "—"}
            {d.ledger.note ? ` · ${d.ledger.note}` : ""}
          </span>
        </div>
      )}

      {d.foreshadow_changes.map((f, i) => (
        <div className="recon-line" key={`f${i}`}>
          <span className="chip">{OP_CN[f.op] ?? f.op}</span>
          <span>{f.description}</span>
        </div>
      ))}

      {/* 订单对账(docs/20 §6.1):确认订单的六单 vs 成品;偏差逐条亮出来 */}
      {oc && (
        <div className="recon-order" data-testid="order-check">
          <div className="recon-line">
            <span className="chip">订单 v{oc.version}</span>
            <span className="muted">按单验收——偏差逐条裁决:接受就改订单,不对就打回重写</span>
          </div>
          {oc.missed.map((name, i) => (
            <div className="recon-line" key={`m${i}`}>
              <span className="chip chip-warn">该来没来</span><span>订单里要出场,正文没写到:{name}</span>
            </div>
          ))}
          {oc.uninvited.map((name, i) => (
            <div className="recon-line" key={`u${i}`}>
              <span className="chip chip-warn">不请自来</span><span>正文冒出的订单外人物:{name}(确认上方关系边或去圣经建档)</span>
            </div>
          ))}
          {oc.exit_missing.map((name, i) => (
            <div className="recon-line" key={`e${i}`}>
              <span className="chip chip-warn">没写退场</span><span>说好本章下场,正文连人都没出现:{name}</span>
            </div>
          ))}
          {oc.relations_missed.map((r, i) => (
            <div className="recon-line" key={`r${i}`}>
              <span className="chip chip-warn">该变没变</span><span>{r.from}—{r.to} 应变为「{r.after}」,本章没有对应的关系变化</span>
            </div>
          ))}
          {oc.beats_judged && oc.beats.map((b, i) => !b.hit && (
            <div className="recon-line" key={`b${i}`}>
              <span className="chip chip-warn">节拍未砸</span><span>{b.beat}{b.note ? ` · ${b.note}` : ""}</span>
            </div>
          ))}
          {oc.beats_judged && oc.beats.length > 0 && oc.beats.every((b) => b.hit) && (
            <div className="recon-line">
              <span className="chip">节拍全中</span>
              <span className="muted">{oc.beats.length} 拍全部兑现</span>
            </div>
          )}
          {!oc.beats_judged && (
            <div className="recon-line">
              <span className="chip chip-warn">节拍未判定</span>
              <span className="muted">章后判定没跑成(可重跑一致性同步补判),先看上面的人物账</span>
            </div>
          )}
          {!orderHasFindings && (
            <div className="recon-line">
              <span className="chip">订单全对</span><span className="muted">人物/关系与订单一致</span>
            </div>
          )}
        </div>
      )}

      {d.pending_relations.map((r) => (
        <div className="recon-line" key={r.id}>
          <span className="chip chip-warn">待确认</span>
          <span className="grow">
            {r.from_name} → {r.to_name}:{r.relation}
            {r.evidence_text && (
              <span className="muted"> · 证据(第{r.evidence_chapter ?? "?"}章):{r.evidence_text.slice(0, 40)}</span>
            )}
          </span>
          <span className="recon-actions">
            <button className="btn-sm primary" disabled={busy}
              title="确认后这条关系进入故事圣经,后续生成会遵守它"
              onClick={() => void decide([r.id], [])}>确认</button>
            <button className="btn-sm" disabled={busy}
              title="否决=这条关系不算数:删掉边,支撑它的本章事实一并作废,后续生成不再遵守"
              onClick={() => void decide([], [r.id])}>否决</button>
          </span>
        </div>
      ))}

      {d.pending_relations.length > 0 && (
        <div className="mt-1">
          <button className="btn-sm" disabled={busy}
            title="全部转入故事圣经;拿不准就逐条过,否决的不会进后续生成"
            onClick={() => void decide(
              d.pending_relations.map((r) => r.id), [],
            )}>
            全部确认
          </button>
        </div>
      )}
    </div>
  );
}
