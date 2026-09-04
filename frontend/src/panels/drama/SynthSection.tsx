import { useEffect, useState } from "react";
import type { DramaShot } from "../../dramaApi";
import type { RenderTaskOut } from "../../renderApi";
import { renderApi } from "../../renderApi";
import { useJob } from "../../ui/useJob";
import { downloadFile } from "../../api";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { confirmDialog } from "../../ui/ConfirmDialog";

/** 一键合成(完整档):整集拼接 + 静帧占位 + BGM 垫底 + 字幕烧录 → 整集 mp4。
 *
 *  拼接规则与后端 synthesis.collect_plan 同口径:clip_ref 是本站草片 → 可拼;
 *  没草片有静帧 → 静帧定格占位;都没有 → 跳过并如实上报。BGM 每集一段,
 *  用户自己传(主题曲/垫乐),合成时低音量混入。
 */
export function SynthSection({ pid, eid, shots, onDone }: {
  pid: number; eid: number; shots: DramaShot[]; onDone: () => void;
}) {
  const { run } = useJob();
  const [burn, setBurn] = useState(true);
  const [busy, setBusy] = useState(false);
  const [bgmBusy, setBgmBusy] = useState(false);
  const [bgmUrl, setBgmUrl] = useState("");
  const [synth, setSynth] = useState<RenderTaskOut | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");

  // 初载:接续最新一次合成的状态与成片;BGM 试听
  useEffect(() => {
    let alive = true;
    let revoke = "";
    void (async () => {
      try {
        const s = (await renderApi.episodeSynthStatus(pid, eid)).synth;
        if (!alive) return;
        setSynth(s);
        if (s?.status === "success" && s.result_path) {
          const u = await renderApi.taskBlobUrl(s.id);
          if (alive) { revoke = u; setPreviewUrl(u); }
        }
      } catch { /* 静默:合成状态拿不到不影响页面 */ }
    })();
    void renderApi.episodeBgmBlobUrl(pid, eid)
      .then((u) => { if (alive) setBgmUrl(u); else URL.revokeObjectURL(u); })
      .catch(() => {});
    return () => { alive = false; if (revoke) URL.revokeObjectURL(revoke); };
  }, [pid, eid]);

  // 拼接口径(与后端 collect_plan 同规则):clip_ref 指向本站草片 → 可拼
  const RE_CLIP = /^render\/r\d+\.mp4$/;
  const clipCount = shots.filter((s) => RE_CLIP.test((s.clip_ref || "").trim())).length;
  const stillCount = shots.filter(
    (s) => !RE_CLIP.test((s.clip_ref || "").trim()) && (s.assets?.length ?? 0) > 0,
  ).length;
  const skipCount = shots.length - clipCount - stillCount;

  async function pickBgm(file: File | undefined) {
    if (!file) return;
    setBgmBusy(true);
    try {
      await renderApi.uploadEpisodeBgm(pid, eid, file);
      setBgmUrl(await renderApi.episodeBgmBlobUrl(pid, eid));
      toast.ok("BGM 已上传", "合成时自动低音量垫底,不抢配音");
    } catch (e) { toast.err("BGM 上传失败", errMsg(e)); } finally { setBgmBusy(false); }
  }

  async function removeBgm() {
    if (!await confirmDialog({ title: "删掉这一集的 BGM?", confirmText: "删除", danger: true })) return;
    setBgmBusy(true);
    try {
      await renderApi.deleteEpisodeBgm(pid, eid);
      setBgmUrl("");
    } catch (e) { toast.err("删除失败", errMsg(e)); } finally { setBgmBusy(false); }
  }

  async function synthNow() {
    setBusy(true);
    try {
      const r = await renderApi.submitEpisodeSynth(pid, eid, burn);
      await run(() => Promise.resolve({ job_id: r.job_id }),
        { kind: `render:synth:e${eid}` });
      const s = (await renderApi.episodeSynthStatus(pid, eid)).synth;
      setSynth(s);
      if (s?.status === "success") {
        setPreviewUrl(await renderApi.taskBlobUrl(s.id));
        toast.ok("整集成片已合成", "下方直接预览;不满意逐格重 roll 再合成一次");
      }
      onDone();
    } catch (e) { toast.err("合成失败", errMsg(e)); } finally { setBusy(false); }
  }

  return (
    <div className="card">
      <div className="card-head mb-2">
        <b>⑤ 合成成片(一键整集)</b>
        <span className="badge">完整档</span>
        <span className="muted">
          可拼 {clipCount} 格 · 静帧占位 {stillCount} 格{skipCount > 0 ? ` · 跳过 ${skipCount} 格` : ""}
        </span>
        <span className="grow" />
        <button className="primary" disabled={busy || clipCount + stillCount === 0}
          title={clipCount + stillCount === 0 ? "先给分镜格出片或挂静帧" : "整集一次编码,几分钟属正常"}
          onClick={() => void synthNow()}>
          {busy ? "合成中…" : "合成整集"}
        </button>
      </div>
      <p className="hint">
        按镜号顺序拼接已出片的格(对白格的配音已内嵌);没出片的格用静帧定格占位,
        连静帧都没有的格跳过并在结果里说明。BGM 低音量垫底,人声与垫乐不打架。
      </p>

      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">这一集的 BGM{bgmUrl ? "(已上传,试听↓)" : "(可选,合成时低音量垫底)"}</span>
          <span className="grow" />
          <label className="btn-sm" style={{ cursor: "pointer" }}>
            {bgmUrl ? "换一首" : "上传 BGM"}
            <input type="file" accept="audio/mpeg,audio/wav,.mp3,.wav" hidden disabled={bgmBusy}
              onChange={(e) => { void pickBgm(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          {bgmUrl && (
            <button className="btn-sm" disabled={bgmBusy} onClick={() => void removeBgm()}>删除</button>
          )}
        </div>
        {bgmUrl && <audio controls src={bgmUrl} preload="none" style={{ width: "100%", maxWidth: 320 }} />}
        <p className="hint">MP3/WAV ≤15MB,一首主题曲或一段垫乐;每集一段,重传即换。</p>
      </div>

      <label className="guard-toggle">
        <input type="checkbox" checked={burn} onChange={(e) => setBurn(e.target.checked)} />
        <span>
          烧录台词字幕
          <b className="hint">按每格台词与实际时长压字幕;想拿去剪映自己压就关掉</b>
        </span>
      </label>

      {synth?.status === "failed" && synth.error && (
        <div className="msg-err mt-2">合成失败:{synth.error}</div>
      )}
      {synth?.status === "success" && synth.params.note && (
        <div className="notice notice-warn mt-2">{synth.params.note}</div>
      )}
      {previewUrl && (
        <div className="mt-2">
          <video className="render-preview" src={previewUrl} controls preload="metadata" />
          <div className="mt-1">
            <button className="btn-sm"
              onClick={() => void downloadFile(`/api/render/tasks/${synth?.id}/file`,
                `第${eid}集合成片.mp4`)}>
              下载成片
            </button>
            <span className="muted"> 也可以就地预览;逐格不满意就重 roll 后再合成一次</span>
          </div>
        </div>
      )}
    </div>
  );
}
