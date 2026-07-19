// 一致性看板:故事圣经(时序快照) + 伏笔四态面板
import { useCallback, useEffect, useState } from "react";
import { api, BibleSnapshot, ForeshadowOut, Outline } from "../api";

interface Props { pid: number; outlines: Outline[]; }

const FS_CN: Record<string, string> = {
  planted: "已埋设", reinforced: "已强化", paid_off: "已回收", abandoned: "已弃用",
};
const IMP_BADGE: Record<string, string> = { critical: "err", major: "warn", minor: "" };

export default function BoardPanel({ pid, outlines }: Props) {
  const maxCh = outlines.length ? Math.max(...outlines.map((o) => o.chapter_number)) : 1;
  const [atChapter, setAtChapter] = useState(maxCh);
  const [bible, setBible] = useState<BibleSnapshot | null>(null);
  const [foreshadows, setForeshadows] = useState<ForeshadowOut[]>([]);
  const [err, setErr] = useState("");

  const reload = useCallback(async (ch: number) => {
    setErr("");
    try {
      const [b, f] = await Promise.all([api.bible(pid, ch), api.foreshadowings(pid, ch)]);
      setBible(b); setForeshadows(f);
    } catch (e) { setErr(String(e)); }
  }, [pid]);

  useEffect(() => { reload(atChapter); }, [reload, atChapter]);

  const byEntity = new Map<string, typeof bible extends null ? never : NonNullable<typeof bible>["facts"]>();
  bible?.facts.forEach((f) => {
    if (!byEntity.has(f.entity)) byEntity.set(f.entity, [] as never);
    (byEntity.get(f.entity) as unknown as typeof bible.facts).push(f);
  });

  const open = foreshadows.filter((f) => f.status === "planted" || f.status === "reinforced");
  const due = open.filter((f) => f.is_due);
  const paid = foreshadows.filter((f) => f.status === "paid_off");

  return (
    <>
      <div className="card">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <h2 style={{ margin: 0 }}>故事圣经 · 时间机</h2>
          <span className="muted">查看任意章节时刻的世界状态</span>
          <div style={{ flex: 1 }} />
          <span className="muted">第</span>
          <input type="number" min={1} max={maxCh} value={atChapter}
            style={{ width: 80 }}
            onChange={(e) => setAtChapter(Math.max(1, Math.min(maxCh, Number(e.target.value) || 1)))} />
          <span className="muted">章时刻 · {bible?.entities_count ?? 0} 实体 / {bible?.facts.length ?? 0} 条有效事实</span>
        </div>
        {err && <div className="msg-err" style={{ marginTop: 8 }}>{err}</div>}
        <div style={{ marginTop: 12 }}>
          {[...byEntity.entries()].map(([entity, facts]) => (
            <div key={entity} style={{ marginBottom: 10 }}>
              <b>{entity}</b>
              {(facts as BibleSnapshot["facts"]).map((f, i) => (
                <div key={i} className="fact-line">
                  <span className={"badge " + (IMP_BADGE[f.importance] ?? "")}>{f.importance}</span>
                  {" "}{f.content}
                  <span className="muted">(第{f.valid_from}{f.valid_until ? `-${f.valid_until}` : " 章起"}章有效)</span>
                </div>
              ))}
            </div>
          ))}
          {!bible?.facts.length && <div className="muted">该时刻暂无已登记事实(生成章节后自动抽取)。</div>}
        </div>
      </div>

      <div className="card">
        <h2>伏笔面板
          <span className="badge">{open.length} 未回收</span>
          {due.length > 0 && <span className="badge warn">{due.length} 条到期</span>}
          <span className="badge ok">{paid.length} 已回收</span>
        </h2>
        <table className="tbl">
          <thead>
            <tr><th>状态</th><th>伏笔</th><th>埋设</th><th>预期回收</th><th>实际回收</th><th>强化于</th></tr>
          </thead>
          <tbody>
            {foreshadows.map((f) => (
              <tr key={f.id}>
                <td>
                  <span className={"badge " + (f.status === "paid_off" ? "ok" : f.is_due ? "warn" : "")}>
                    {FS_CN[f.status] ?? f.status}{f.is_due ? " · 到期" : ""}
                  </span>
                </td>
                <td>{f.description}</td>
                <td>第{f.chapter_planted}章</td>
                <td>{f.expected_payoff_chapter ? `第${f.expected_payoff_chapter}章` : "—"}</td>
                <td>{f.payoff_chapter ? `第${f.payoff_chapter}章` : "—"}</td>
                <td>{f.reinforcement_chapters.length ? f.reinforcement_chapters.map((c) => `第${c}章`).join("、") : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!foreshadows.length && <div className="muted">暂无登记伏笔。</div>}
      </div>
    </>
  );
}
