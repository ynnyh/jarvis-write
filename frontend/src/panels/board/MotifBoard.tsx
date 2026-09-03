// book 区「桥段」页签:跨章重复描写的治理台——雷区清单(作者明令禁止,一次标注
// 全书生效)+ 桥段台账(各章自动抽取的描写母题,按标签聚合出现章号与次数)。
// 台账只记录标签不沉淀原句;写新章前「已写滥」的母题会注入 prompt 禁止复用。
// 存量书用「扫描全书」一次性回填台账;抽错的/有意的主母题可清除,惯犯可一键升格雷区。
import { useEffect, useState } from "react";
import { api, BannedMotif, LedgerMotif, MotifsOut } from "../../api";
import { errMsg } from "../../pollJob";
import { useJob } from "../../ui/useJob";
import EmptyState from "../../ui/EmptyState";

export default function MotifBoard({ pid }: { pid: number }) {
  const { run: runJob } = useJob();
  const [data, setData] = useState<MotifsOut | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState("");
  // 新增雷区的表单态
  const [label, setLabel] = useState("");
  const [detail, setDetail] = useState("");
  // 扫描结果回显
  const [scanResult, setScanResult] = useState<{ chapters_scanned: number; motifs_added: number } | null>(null);

  const reload = async () => {
    try { setData(await api.motifs(pid)); } catch (e) { setErr(errMsg(e)); }
  };
  useEffect(() => { void reload(); /* eslint-disable-line react-hooks/exhaustive-deps */ }, [pid]);

  async function addBanned() {
    if (label.trim().length < 2 || busy) return;
    setErr(""); setBusy("登记雷区");
    try {
      await api.addBannedMotif(pid, label.trim(), detail.trim());
      setLabel(""); setDetail("");
      await reload();
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  async function removeBanned(m: BannedMotif) {
    setErr("");
    try { await api.removeBannedMotif(pid, m.id); await reload(); } catch (e) { setErr(errMsg(e)); }
  }

  async function promote(m: LedgerMotif) {
    setErr("");
    try { await api.promoteMotif(pid, m.label); await reload(); } catch (e) { setErr(errMsg(e)); }
  }

  async function clearLabel(m: LedgerMotif) {
    setErr("");
    try { await api.clearLedgerMotif(pid, m.label); await reload(); } catch (e) { setErr(errMsg(e)); }
  }

  // 全书扫描:存量章节批量回填台账(逐批 LLM,较长);完成后刷新
  async function runScan() {
    setErr(""); setBusy("扫描全书母题中(逐章抽取,较长)…"); setScanResult(null);
    try {
      const r = await runJob<{ chapters_scanned: number; motifs_added: number }>(
        () => api.scanMotifsAsync(pid), { kind: `motifscan-${pid}` },
      );
      if (r) { setScanResult(r); await reload(); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); }
  }

  const banned = data?.banned ?? [];
  const ledger = data?.ledger ?? [];

  return (
    <>
      <div className="card">
        <h2>桥段台账与雷区
          <span className="badge">{ledger.length} 个母题</span>
          {banned.length > 0 && <span className="badge err">{banned.length} 条雷区</span>}
        </h2>
        <div className="hint mt-1">
          AI 逐章抽取有辨识度的描写母题(意象/标志性动作/场景收束套路),跨章聚合出现次数;
          写新章时已复现的母题会注入 prompt 禁止复用——治「连续几章写同一描写」。
          被写烦了的桥段直接设为雷区,以后每章都不再写。
        </div>
        {err && <div className="msg-err mt-2">{err}</div>}

        <div className="card-head mt-3">
          <h3 className="grow">雷区清单(明令禁止,后续每章都规避)</h3>
        </div>
        {banned.length > 0 ? (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>标签</th><th>说明</th><th>操作</th></tr>
              </thead>
              <tbody>
                {banned.map((m) => (
                  <tr key={m.id}>
                    <td><span className="badge err">{m.label}</span></td>
                    <td>{m.detail || "—"}</td>
                    <td>
                      <button className="btn-sm" title="撤销后 AI 可能再写这个桥段"
                        onClick={() => removeBanned(m)}>撤销</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="muted mt-1">暂无雷区。被反复写烦的桥段,在下面登记一条即可全书禁用。</div>
        )}
        <div className="form-grid mt-3">
          <div className="field">
            <label className="fl">桥段标签(2 字以上,如:铁锈玫瑰 / 扎胸膛 / 躺下等天亮)</label>
            <input type="text" value={label} maxLength={100} autoFocus
              placeholder="给这个桥段起个短名字"
              onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void addBanned(); }} />
          </div>
          <div className="field">
            <label className="fl">说明(可选)</label>
            <input type="text" value={detail} maxLength={500}
              placeholder="一句话说明它长什么样,如:自残式把铁片扎进胸口的动作"
              onChange={(e) => setDetail(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void addBanned(); }} />
          </div>
        </div>
        <div className="form-actions">
          <button className="primary btn-sm" disabled={label.trim().length < 2 || !!busy}
            onClick={addBanned}>
            {busy === "登记雷区" && <span className="spin spin-sm" />}登记雷区
          </button>
        </div>
      </div>

      <div className="card mt-3">
        <div className="card-head">
          <h3 className="grow">复现台账(自动聚合,≥2 次才会提醒复用)</h3>
          <button className="btn-sm" disabled={!!busy} title="逐章抽取历史章节的母题回填台账(存量书用,较长)"
            onClick={runScan}>
            {busy.startsWith("扫描") && <span className="spin spin-sm" />}扫描全书
          </button>
        </div>
        <div className="hint mt-1">
          台账由每章写完后的自动抽取维护;老书(或想立即覆盖已有章节)点「扫描全书」回填。
          惯犯可直接「设为雷区」;抽错了或这是有意的主母题,「清除」即可不再提醒。
        </div>
        {scanResult && (
          <div className="msg-ok mt-2">
            扫描完成:覆盖 {scanResult.chapters_scanned} 章,台账新增 {scanResult.motifs_added} 条母题记录。
          </div>
        )}
        {ledger.length > 0 ? (
          <div className="tbl-wrap mt-2">
            <table className="tbl">
              <thead>
                <tr><th>母题</th><th>说明</th><th>出现章节</th><th>次数</th><th>操作</th></tr>
              </thead>
              <tbody>
                {ledger.map((m) => (
                  <tr key={m.label}>
                    <td>
                      <span className={"badge" + (m.count >= 3 ? " err" : m.count === 2 ? " warn" : "")}>
                        {m.label}
                      </span>
                    </td>
                    <td>{m.detail || "—"}</td>
                    <td>{m.chapters.map((c) => `第${c}章`).join("、")}</td>
                    <td>{m.count}</td>
                    <td>
                      <button className="btn-sm" title="以后每章都不再写这个母题"
                        onClick={() => promote(m)}>设为雷区</button>
                      <button className="btn-sm" title="抽错了,或这是有意的主母题:清掉历史,不再提醒"
                        onClick={() => clearLabel(m)}>清除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="mt-2">
            <EmptyState>还没有台账数据:写完新章会自动抽取;存量章节点右上「扫描全书」回填。</EmptyState>
          </div>
        )}
      </div>
    </>
  );
}
