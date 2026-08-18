// 伏笔四态面板:未回收/到期/已回收统计 + 手动干预(弃用/标记已收/改预期回收章)(拆自 BoardPanel.tsx)。
import { useEffect, useState } from "react";
import { api, ForeshadowOut } from "../../api";
import { FS_CN, Props } from "./shared";
import { errMsg } from "../../pollJob";

export default function ForeshadowBoard({ pid, outlines }: Props) {
  const maxCh = outlines.length ? Math.max(...outlines.map((o) => o.chapter_number)) : 1;
  const [foreshadows, setForeshadows] = useState<ForeshadowOut[]>([]);
  const [err, setErr] = useState("");
  // 改预期回收章的编辑态
  const [editExpect, setEditExpect] = useState<number | null>(null);
  const [expectVal, setExpectVal] = useState("");

  const reload = async () => {
    setErr("");
    try { setForeshadows(await api.foreshadowings(pid, maxCh)); } catch (e) { setErr(errMsg(e)); }
  };
  useEffect(() => { reload(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [pid, maxCh]);

  async function patchFs(fid: number, patch: Parameters<typeof api.patchForeshadow>[2]) {
    setErr("");
    try {
      await api.patchForeshadow(pid, fid, patch);
      setEditExpect(null);
      await reload();
    } catch (e) { setErr(errMsg(e)); }
  }

  const open = foreshadows.filter((f) => f.status === "planted" || f.status === "reinforced");
  const due = open.filter((f) => f.is_due);
  const paid = foreshadows.filter((f) => f.status === "paid_off");

  return (
    <div className="card">
      <h2>伏笔面板
        <span className="badge">{open.length} 未回收</span>
        {due.length > 0 && <span className="badge warn">{due.length} 条到期</span>}
        <span className="badge ok">{paid.length} 已回收</span>
      </h2>
      <div className="hint mt-1">AI 判定不准时可手动干预:弃用、标记已回收、改预期回收章。</div>
      {err && <div className="msg-err mt-2">{err}</div>}
      <div className="ov-scroll">
      <div className="tbl-wrap">
      <table className="tbl">
        <thead>
          <tr><th>状态</th><th>伏笔</th><th>埋设</th><th>预期回收</th><th>实际回收</th><th>强化于</th><th>操作</th></tr>
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
              <td>
                {editExpect === f.id ? (
                  <span className="input-row">
                    <input type="number" value={expectVal} style={{ width: 72 }}
                      min={f.chapter_planted} autoFocus
                      onChange={(e) => setExpectVal(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && Number(expectVal) >= f.chapter_planted)
                          patchFs(f.id, { expected_payoff_chapter: Number(expectVal) });
                        if (e.key === "Escape") setEditExpect(null);
                      }} />
                    <button className="btn-sm" onClick={() => patchFs(f.id, { expected_payoff_chapter: Number(expectVal) })}
                      disabled={!Number(expectVal) || Number(expectVal) < f.chapter_planted}>✓</button>
                  </span>
                ) : (
                  <button type="button" className="linkish" title="点击修改预期回收章"
                    onClick={() => { setEditExpect(f.id); setExpectVal(String(f.expected_payoff_chapter ?? f.chapter_planted + 3)); }}>
                    {f.expected_payoff_chapter ? `第${f.expected_payoff_chapter}章` : "设定"}
                  </button>
                )}
              </td>
              <td>{f.payoff_chapter ? `第${f.payoff_chapter}章` : "—"}</td>
              <td>{f.reinforcement_chapters.length ? f.reinforcement_chapters.map((c) => `第${c}章`).join("、") : "—"}</td>
              <td className="fs-ops">
                {(f.status === "planted" || f.status === "reinforced") && (
                  <>
                    <button className="btn-sm" title="剧情其实已经回收了,AI 没识别出来"
                      onClick={() => patchFs(f.id, { status: "paid_off" })}>标记已收</button>
                    <button className="btn-sm" title="这条伏笔不打算要了,不再提醒"
                      onClick={() => patchFs(f.id, { status: "abandoned" })}>弃用</button>
                  </>
                )}
                {f.status === "abandoned" && (
                  <button className="btn-sm" onClick={() => patchFs(f.id, { status: "planted" })}>恢复</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      </div>
      {!foreshadows.length && <div className="muted">暂无登记伏笔。</div>}
    </div>
  );
}
