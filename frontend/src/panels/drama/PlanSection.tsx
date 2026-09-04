import { useEffect, useState } from "react";
import type { DramaEpisode } from "../../dramaApi";
import { DRAMA_STATUS_CN, dramaApi } from "../../dramaApi";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import Banner from "../../ui/Banner";
import { confirmDialog } from "../../ui/ConfirmDialog";

// ================= 集规划 + 集列表 =================
export function PlanSection({ pid, approved, episodes, onChanged, selectedId, onSelect }: {
  pid: number;
  approved: number[];
  episodes: DramaEpisode[];
  onChanged: (eps: DramaEpisode[]) => void;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}) {
  const { run } = useJob();
  const [from, setFrom] = useState(approved[0] ?? 1);
  const [to, setTo] = useState(approved[approved.length - 1] ?? from);
  const [mode, setMode] = useState("dialogue");
  const [duration, setDuration] = useState(90);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    setFrom(approved[0] ?? 1);
    setTo(approved[approved.length - 1] ?? 1);
  }, [approved]);

  async function plan() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<DramaEpisode[]>(
        () => dramaApi.plan(pid, { from_chapter: from, to_chapter: to, mode, duration_s: duration }),
        { kind: `drama-plan-${pid}`, onStage: setStage },
      );
      if (r) {
        onChanged(r);
        if (r[0]) onSelect(r[0].id);
        toast.ok(`已切出 ${r.length} 集`, "每集都带开场钩子与结尾卡点");
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  async function remove(eid: number) {
    if (!await confirmDialog({
      title: "删除这一集?",
      body: "这一集的分镜、提示词与已挂的静帧都会一起删掉,不可恢复。",
      confirmText: "确认删除",
      danger: true,
    })) return;
    try {
      await dramaApi.deleteEpisode(pid, eid);
      const fresh = await dramaApi.getEpisodes(pid);
      onChanged(fresh.episodes);
      if (selectedId === eid) onSelect(null);
    } catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">③ 集数规划 <span className="muted">{episodes.length ? `${episodes.length} 集` : "尚未规划"}</span></h3>
      </div>
      <p className="card-desc">
        选已定稿的章节范围,按短剧节奏切成一集集(默认一集约 90 秒):每集独立小冲突 + 开场钩子 +
        结尾卡点。重新规划会替换所选范围内的旧集,范围外不动。
        素材来源:章节蓝图(概要/节拍/悬念) + 本书基因 + 作者雷区(设计钩子卡点时回避)。
      </p>
      <div className="form-grid">
        <div className="field">
          <label className="fl" htmlFor="dp-from">从第几章</label>
          <select id="dp-from" value={from}
            onChange={(e) => { const v = Number(e.target.value); setFrom(v); if (to < v) setTo(v); }}>
            {approved.map((n) => <option key={n} value={n}>第 {n} 章</option>)}
          </select>
        </div>
        <div className="field">
          <label className="fl" htmlFor="dp-to">到第几章</label>
          <select id="dp-to" value={to}
            onChange={(e) => { const v = Number(e.target.value); setTo(v); if (from > v) setFrom(v); }}>
            {approved.map((n) => <option key={n} value={n}>第 {n} 章</option>)}
          </select>
        </div>
        <div className="field">
          <label className="fl" htmlFor="dp-mode">演绎方式</label>
          <select id="dp-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="dialogue">对白演绎</option>
            <option value="narration">口播解说</option>
          </select>
        </div>
        <div className="field">
          <label className="fl" htmlFor="dp-dur">单集时长<span className="hint">秒</span></label>
          <input id="dp-dur" type="number" min={30} max={180} value={duration}
            onChange={(e) => setDuration(Number(e.target.value) || 90)} />
        </div>
      </div>
      <div className="form-actions">
        <button className="primary" disabled={busy} onClick={plan}>
          {episodes.length ? "重新规划" : "切集"}
        </button>
        <span className="form-actions-tip">重新规划只替换所选范围内的旧集,范围外的不动。</span>
      </div>
      {busy && <Banner stage={stage} text="AI 正在切集(钩子/卡点)…" />}
      {err && <div className="msg-err">{err}</div>}
      {episodes.length > 0 && (
        <p className="hint">
          点一行展开那一集的流水线(剧本/分镜/提示词/成片包),再点一下收起。
          {selectedId === null && " ↓ 现在选一集吧。"}
        </p>
      )}
      {episodes.map((ep) => (
        <div key={ep.id}
          className={"sub-summary ep-row" + (selectedId === ep.id ? " ep-on" : "")}
          onClick={() => onSelect(ep.id === selectedId ? null : ep.id)}>
          <div className="card-head mb-2">
            <b>第 {ep.ep_index} 集《{ep.title}》</b>
            <span className="badge">源:{ep.source_label || `第${ep.source_chapter}章`}</span>
            <span className="badge">{ep.mode === "narration" ? "口播" : "对白"}</span>
            <span className="badge">{DRAMA_STATUS_CN[ep.status] ?? ep.status}</span>
            <span className="grow" />
            <button className="btn-sm" onClick={(e) => { e.stopPropagation(); void remove(ep.id); }}>删除</button>
          </div>
          {ep.hook && <div><span className="muted">钩子:</span>{ep.hook}</div>}
          {ep.cliffhanger && <div><span className="muted">卡点:</span>{ep.cliffhanger}</div>}
        </div>
      ))}
    </div>
  );
}
