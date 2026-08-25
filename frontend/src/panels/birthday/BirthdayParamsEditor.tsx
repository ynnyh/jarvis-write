// 寿星资料/参数编辑(建后可调):改动保存后对下一次「换一批/重拍」生效。
// 与 clips 的参数编辑器不同:这里改的是「拍谁」(称呼/关系/里程碑/回忆点)——
// 资料变了等于换了定制对象,保存后必须重跑生成才生效,文案里要说清。
import { useState } from "react";
import { BirthdayWish, birthdayApi } from "../../birthdayApi";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { useBirthdayMeta } from "./shared";

export default function BirthdayParamsEditor({ row, onSaved }: {
  row: BirthdayWish; onSaved: (r: BirthdayWish) => void;
}) {
  const meta = useBirthdayMeta();
  const maxMem = meta?.max_memories ?? 5;
  const maxChars = meta?.memory_max_chars ?? 120;
  const [honoree, setHonoree] = useState(row.honoree_name);
  const [milestone, setMilestone] = useState(row.milestone);
  const [sender, setSender] = useState(row.sender_desc);
  const [memories, setMemories] = useState<string[]>(row.memories?.length ? row.memories : [""]);
  const [duration, setDuration] = useState(row.duration_s);
  const [pack, setPack] = useState(row.pack || "");
  const [direction, setDirection] = useState(row.direction);
  const [styleHints, setStyleHints] = useState(row.style_hints || "");
  const [busy, setBusy] = useState(false);
  const cleanMems = () => memories.map((m) => m.trim()).filter(Boolean);
  const dirty = honoree !== row.honoree_name || milestone !== row.milestone
    || sender !== row.sender_desc
    || JSON.stringify(cleanMems()) !== JSON.stringify(row.memories ?? [])
    || duration !== row.duration_s || pack !== (row.pack || "")
    || direction !== row.direction
    || styleHints !== (row.style_hints || "");

  async function save() {
    const mems = cleanMems();
    if (!honoree.trim()) { toast.err("称呼不能为空", "祝福台词要靠它点名"); return; }
    if (!mems.length) { toast.err("至少留 1 条回忆点", "没有回忆点就没有定制感"); return; }
    setBusy(true);
    try {
      const r = await birthdayApi.patch(row.id, {
        honoree_name: honoree.trim(), milestone: milestone.trim(), sender_desc: sender.trim(),
        memories: mems, duration_s: duration, pack, direction,
        style_hints: styleHints.trim(),
      });
      onSaved(r.wish_row);
      toast.ok("资料已保存", "对下一次「换一批/重拍」生效——改完记得重跑");
    } catch (e) { toast.err("保存失败", errMsg(e)); } finally { setBusy(false); }
  }

  const dirInfo = (meta?.directions ?? []).find((d) => d.key === direction);
  const updMem = (i: number, v: string) =>
    setMemories(memories.map((m, j) => (j === i ? v.slice(0, maxChars) : m)));

  return (
    <div className="form-grid">
      <div className="field">
        <label className="fl" htmlFor="bp-name">寿星称呼<span className="hint">祝福台词靠它点名</span></label>
        <input id="bp-name" value={honoree} maxLength={60}
          onChange={(e) => setHonoree(e.target.value)} placeholder="如「老王 / 糖糖 / 妈」" />
      </div>
      <div className="field">
        <label className="fl" htmlFor="bp-milestone">里程碑</label>
        <input id="bp-milestone" value={milestone} maxLength={80}
          onChange={(e) => setMilestone(e.target.value)} placeholder="如「30 而立 / 60 大寿」,可空" />
      </div>
      <div className="field">
        <label className="fl" htmlFor="bp-sender">送出人</label>
        <input id="bp-sender" value={sender} maxLength={80}
          onChange={(e) => setSender(e.target.value)} placeholder="如「全家 / 闺蜜团 / 部门」" />
      </div>
      <div className="field field-full">
        <label className="fl" htmlFor="bp-mem0">
          回忆点<span className="hint">分镜只从这些点选材,改动后重跑</span>
        </label>
        {memories.map((m, i) => (
          <div key={i} className="clip-edit-line">
            <input value={m} maxLength={maxChars}
              placeholder={i === 0 ? "如「大学时一起在天台看流星雨」" : "再一条具体场景 / 梗 / 口头禅"}
              onChange={(e) => updMem(i, e.target.value)} />
            <button className="btn-sm" disabled={memories.length <= 1}
              onClick={() => setMemories(memories.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        {memories.length < maxMem && (
          <button className="btn-sm" onClick={() => setMemories([...memories, ""])}>+ 加一条回忆点</button>
        )}
      </div>
      <div className="field">
        <label className="fl" htmlFor="bp-duration">时长</label>
        <select id="bp-duration" value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
          <option value={30}>30 秒</option>
          <option value={60}>60 秒</option>
        </select>
      </div>
      <div className="field">
        <label className="fl" htmlFor="bp-pack">风格包<span className="hint">选后画风以包为准</span></label>
        <select id="bp-pack" value={pack} onChange={(e) => setPack(e.target.value)}>
          <option value="">不用包(通用画风)</option>
          {(meta?.packs ?? []).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </select>
      </div>
      {!pack && (
        <div className="field">
          <label className="fl" htmlFor="bp-direction">画风</label>
          <select id="bp-direction" value={direction} onChange={(e) => setDirection(e.target.value)}>
            {(meta?.directions ?? []).map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
          </select>
          {dirInfo?.tip && <div className="warn-tip">⚠ {dirInfo.tip}</div>}
        </div>
      )}
      <div className="field field-full">
        <label className="fl" htmlFor="bp-hints">氛围关键词</label>
        <input id="bp-hints" value={styleHints} maxLength={80}
          onChange={(e) => setStyleHints(e.target.value)} placeholder="如「烛光、老照片质感」,可空" />
      </div>
      <div className="form-actions">
        <button className="primary btn-sm" disabled={busy || !dirty} onClick={() => void save()}>保存资料</button>
        {!dirty && <span className="form-actions-tip">与当前一致</span>}
      </div>
    </div>
  );
}
