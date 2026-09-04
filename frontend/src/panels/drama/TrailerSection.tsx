import { useEffect, useState } from "react";
import type { DramaEpisode, DramaTrailer } from "../../dramaApi";
import { dramaApi } from "../../dramaApi";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import Banner from "../../ui/Banner";
import { CopyBtn, selectAll } from "../../ui/copy";

// ================= 预告片 =================
export function TrailerSection({ pid, episodes, trailer, onGenerated }: {
  pid: number;
  episodes: DramaEpisode[];
  trailer: DramaTrailer | null;
  onGenerated: (t: DramaTrailer) => void;
}) {
  const { run } = useJob();
  const [fromEp, setFromEp] = useState(1);
  const [toEp, setToEp] = useState(9999);
  const [targetS, setTargetS] = useState(45);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    if (episodes.length) {
      setFromEp(1);
      setToEp(episodes[episodes.length - 1].ep_index);
    }
  }, [episodes]);

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<DramaTrailer>(
        () => dramaApi.generateTrailer(pid, { from_ep: fromEp, to_ep: toEp, target_s: targetS }),
        { kind: `drama-trailer-${pid}`, onStage: setStage },
      );
      if (r) { onGenerated(r); toast.ok("预告片已生成", "炸点开场 + 冲突连切 + 悬念定格"); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  async function exp(fmt: "md" | "srt") {
    try { await dramaApi.exportTrailer(pid, fmt); }
    catch (e) { toast.err("导出失败", errMsg(e)); }
  }

  const epNums = episodes.map((e) => e.ep_index);

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">⑤ 预告片 <span className="muted">高能混剪</span></h3>
        {trailer && (
          <>
            <button className="btn-sm" onClick={() => exp("md")}>导出手册</button>
            <button className="btn-sm" onClick={() => exp("srt")}>字幕SRT</button>
          </>
        )}
      </div>
      <p className="card-desc">
        从各集的钩子/卡点/高能分镜里混剪一条 {targetS} 秒宣传片:炸点开场 → 人设速览 →
        冲突升级连切 → 悬念定格。镜头提示词同样注入画风/角色锚,人物不换脸。
      </p>
      {episodes.length === 0 ? (
        <p className="hint">先「切集」,有了集才能混剪预告片。</p>
      ) : (
        <>
          <div className="form-grid">
            <div className="field">
              <label className="fl" htmlFor="tr-from">从第几集</label>
              <select id="tr-from" value={fromEp}
                onChange={(e) => { const v = Number(e.target.value); setFromEp(v); if (toEp < v) setToEp(v); }}>
                {epNums.map((n) => <option key={n} value={n}>第 {n} 集</option>)}
              </select>
            </div>
            <div className="field">
              <label className="fl" htmlFor="tr-to">到第几集</label>
              <select id="tr-to" value={toEp}
                onChange={(e) => { const v = Number(e.target.value); setToEp(v); if (fromEp > v) setFromEp(v); }}>
                {epNums.map((n) => <option key={n} value={n}>第 {n} 集</option>)}
              </select>
            </div>
            <div className="field">
              <label className="fl" htmlFor="tr-dur">预告片时长</label>
              <select id="tr-dur" value={targetS} onChange={(e) => setTargetS(Number(e.target.value))}>
                <option value={30}>30 秒</option>
                <option value={45}>45 秒</option>
                <option value={60}>60 秒</option>
              </select>
            </div>
          </div>
          <div className="form-actions">
            <button className="primary" disabled={busy} onClick={generate}>
              {trailer ? "重新混剪" : "AI 混剪预告片"}
            </button>
            <span className="form-actions-tip">从这几集里挑高能素材,重新混剪会覆盖上一条。</span>
          </div>
        </>
      )}
      {busy && <Banner stage={stage} text="AI 正在混剪预告片…" />}
      {err && <div className="msg-err">{err}</div>}

      {trailer && !busy && (
        <>
          <div className="sub-summary">
            <div className="card-head mb-2">
              <b>《{trailer.title || "预告片"}》</b>
              <span className="muted">
                {trailer.totals.shots} 格 · 分镜 {trailer.totals.duration_s}s(目标 {trailer.target_s}s)
              </span>
            </div>
            {trailer.lines.length > 0 && (
              <div className="mb-2">
                <div className="card-head mb-2"><b>文案骨架(旁白 + 金句)</b>
                  <CopyBtn text={trailer.lines.map((l) => l.text).join("\n")} label="复制全文" /></div>
                {trailer.lines.map((l, i) => (
                  <div key={i} className="script-line"><b>{l.speaker}</b>:{l.text}</div>
                ))}
              </div>
            )}
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr><th>#</th><th>取材</th><th>场景</th><th>角色</th><th>景别</th><th>运镜</th><th>秒</th><th>画面</th><th>台词</th></tr>
                </thead>
                <tbody>
                  {trailer.shots.map((s) => (
                    <tr key={s.seq}>
                      <td>{s.seq}</td>
                      <td>{s.source_ep ? `第${s.source_ep}集` : "新创"}</td>
                      <td>{s.scene_name}</td>
                      <td>{s.characters.join("、")}</td>
                      <td>{s.shot_type}</td>
                      <td>{s.camera}</td>
                      <td>{s.duration_s}</td>
                      <td>{s.action_desc}</td>
                      <td>{s.dialogue}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {trailer.shots.filter((s) => s.prompt_cn || s.prompt_en).map((s) => (
            <div key={s.seq} className="sub-summary">
              <div className="card-head mb-2">
                <b>镜头 {s.seq}{s.source_ep ? `(取材第${s.source_ep}集)` : "(新创)"}</b>
              </div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">中文提示词(即梦/可灵)</span><CopyBtn text={s.prompt_cn} /></div>
                <textarea rows={3} readOnly value={s.prompt_cn} onFocus={selectAll} />
              </div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">英文提示词(Midjourney)</span><CopyBtn text={s.prompt_en} /></div>
                <textarea rows={2} readOnly value={s.prompt_en} onFocus={selectAll} />
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
