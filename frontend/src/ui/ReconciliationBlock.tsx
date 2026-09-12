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
  const nothing = d.confirmed && !d.ledger && d.foreshadow_changes.length === 0;
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
