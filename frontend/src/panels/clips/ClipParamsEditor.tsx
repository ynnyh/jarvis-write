// 生成参数编辑(建后可调):改动保存后对下一次「换一批/重拍」生效。
import { useState } from "react";
import { MoodClip, clipsApi } from "../../clipsApi";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { SteeringChips, useClipsMeta } from "./shared";

export default function ClipParamsEditor({ row, onSaved }: { row: MoodClip; onSaved: (r: MoodClip) => void }) {
  const meta = useClipsMeta();
  const [duration, setDuration] = useState(row.duration_s);
  const [direction, setDirection] = useState(row.direction);
  const [inspiration, setInspiration] = useState(row.inspiration);
  const [dialogueStyle, setDialogueStyle] = useState(row.dialogue_style || "auto");
  const [pacing, setPacing] = useState(row.pacing || "auto");
  const [intensity, setIntensity] = useState(row.intensity || "auto");
  const [styleHints, setStyleHints] = useState(row.style_hints || "");
  const [busy, setBusy] = useState(false);
  const dirty = duration !== row.duration_s || direction !== row.direction
    || inspiration !== row.inspiration || dialogueStyle !== (row.dialogue_style || "auto")
    || pacing !== (row.pacing || "auto") || intensity !== (row.intensity || "auto")
    || styleHints !== (row.style_hints || "");

  async function save() {
    setBusy(true);
    try {
      const r = await clipsApi.patch(row.id, {
        duration_s: duration, direction,
        inspiration: inspiration.trim(),
        dialogue_style: dialogueStyle, pacing, intensity,
        style_hints: styleHints.trim(),
      });
      onSaved(r.clip_row);
      toast.ok("参数已保存", "对下一次「换一批/重拍」生效");
    } catch (e) { toast.err("保存失败", errMsg(e)); } finally { setBusy(false); }
  }

  const dirInfo = (meta?.directions ?? []).find((d) => d.key === direction);

  return (
    <div className="form-grid">
      <div className="field">
        <label className="fl" htmlFor="cp-duration">时长</label>
        <select id="cp-duration" value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
          <option value={15}>15 秒</option>
          <option value={30}>30 秒</option>
        </select>
      </div>
      <div className="field">
        <label className="fl" htmlFor="cp-direction">画风</label>
        <select id="cp-direction" value={direction} onChange={(e) => setDirection(e.target.value)}>
          {(meta?.directions ?? []).map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
        </select>
        {dirInfo?.tip && <div className="warn-tip">⚠ {dirInfo.tip}</div>}
      </div>
      <SteeringChips label="台词风格" options={meta?.dialogue_styles ?? [{ key: "auto", label: "AI 定" }]}
        value={dialogueStyle} onChange={setDialogueStyle} />
      <SteeringChips label="节奏" options={meta?.pacings ?? [{ key: "auto", label: "AI 定" }]}
        value={pacing} onChange={setPacing} />
      <SteeringChips label="情绪浓度" options={meta?.intensities ?? [{ key: "auto", label: "AI 定" }]}
        value={intensity} onChange={setIntensity} />
      <div className="field">
        <label className="fl" htmlFor="cp-hints">氛围关键词</label>
        <input id="cp-hints" value={styleHints} maxLength={80}
          onChange={(e) => setStyleHints(e.target.value)} placeholder="如「雨夜便利店、暖光」" />
      </div>
      <div className="field field-full">
        <label className="fl" htmlFor="cp-inspire">一句话灵感</label>
        <input id="cp-inspire" value={inspiration} maxLength={60}
          onChange={(e) => setInspiration(e.target.value)} placeholder="故事种子,可空" />
      </div>
      <div className="form-actions">
        <button className="primary btn-sm" disabled={busy || !dirty} onClick={() => void save()}>保存参数</button>
        {!dirty && <span className="form-actions-tip">与当前一致</span>}
      </div>
    </div>
  );
}