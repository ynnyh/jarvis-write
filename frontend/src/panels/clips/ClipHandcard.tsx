// 手卡:选定本子的完整物料(分镜/三轨/金句/切段),可编辑保存。
import { useState } from "react";
import { ClipCard, ClipShot } from "../../clipsApi";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { CopyBtn } from "../../ui/copy";
import { chunkPromptText } from "./shared";

export default function ClipHandcard({ card, onExport, onSave }: {
  card: ClipCard;
  onExport: (fmt: "md" | "srt" | "json") => void;
  onSave?: (card: ClipCard) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ClipCard | null>(null);
  const [busy, setBusy] = useState(false);

  function startEdit() {
    // 深拷贝:编辑期间原卡不动,取消可无损还原
    setDraft(JSON.parse(JSON.stringify(card)) as ClipCard);
    setEditing(true);
  }

  async function save() {
    if (!draft) return;
    if (!onSave) return;
    setBusy(true);
    try {
      await onSave(draft);
      setEditing(false); setDraft(null);
      toast.ok("手卡已保存", "切段与总时长已按新分镜重算");
    } catch (e) { toast.err("保存失败", errMsg(e)); } finally { setBusy(false); }
  }

  const view = editing && draft ? draft : card;
  // 整片提示词:全部切段拼成一版,想整片存一份或一次性搬走时用
  const allPromptsText = (card.chunks ?? []).map((c) =>
    `【段 ${c.index} · ${c.start_s}-${c.end_s}s】\n${chunkPromptText(card.shots, c.shot_seqs ?? [])}`
  ).join("\n\n");
  const upd = (patch: Partial<ClipCard>) => setDraft({ ...(draft as ClipCard), ...patch });
  const updShot = (seq: number, patch: Partial<ClipShot>) => setDraft({
    ...(draft as ClipCard),
    shots: (draft as ClipCard).shots.map((s) => (s.seq === seq ? { ...s, ...patch } : s)),
  });

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">手卡 · 《{view.take}》{editing ? "(编辑中)" : ""}</h3>
        {!editing && <CopyBtn text={card.punchline} label="复制金句" />}
        {!editing && <button className="btn-sm" onClick={() => onExport("md")}>导出手卡</button>}
        {!editing && <button className="btn-sm" onClick={() => onExport("srt")}>字幕SRT</button>}
        {!editing && <button className="btn-sm" onClick={() => onExport("json")}>JSON</button>}
        {onSave && !editing && <button className="btn-sm" onClick={startEdit}>✍ 编辑</button>}
        {editing && <button className="primary btn-sm" disabled={busy} onClick={() => void save()}>保存</button>}
        {editing && <button className="btn-sm" disabled={busy} onClick={() => { setEditing(false); setDraft(null); }}>取消</button>}
      </div>
      {editing ? (
        <div className="form-grid">
          <div className="field">
            <label className="fl">金句(结尾字幕卡)</label>
            <input value={view.punchline} maxLength={60} onChange={(e) => upd({ punchline: e.target.value })} />
          </div>
          <div className="field">
            <label className="fl">钩子文案</label>
            <input value={view.hook_text} maxLength={60} onChange={(e) => upd({ hook_text: e.target.value })} />
          </div>
          <div className="field field-full">
            <label className="fl">台词(每句一行;留空即无台词)</label>
            {(view.lines ?? []).map((l, i) => (
              <div key={i} className="clip-edit-line">
                <input className="clip-edit-who" value={l.speaker} maxLength={40}
                  onChange={(e) => upd({
                    lines: view.lines.map((x, j) => (j === i ? { ...x, speaker: e.target.value } : x)),
                  })} />
                <input value={l.text} maxLength={120}
                  onChange={(e) => upd({
                    lines: view.lines.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)),
                  })} />
                <button className="btn-sm" onClick={() => upd({ lines: view.lines.filter((_, j) => j !== i) })}>✕</button>
              </div>
            ))}
            <button className="btn-sm" onClick={() => upd({ lines: [...(view.lines ?? []), { speaker: "旁白", text: "" }] })}>+ 加一句</button>
          </div>
          {(view.shots ?? []).map((s) => (
            <div key={s.seq} className="field-full sub-summary">
              <div className="card-head mb-2"><b>镜头 {s.seq}</b></div>
              <div className="clip-edit-grid">
                <input value={s.scene_name} maxLength={40} placeholder="场景"
                  onChange={(e) => updShot(s.seq, { scene_name: e.target.value })} />
                <input value={s.shot_type} maxLength={20} placeholder="景别"
                  onChange={(e) => updShot(s.seq, { shot_type: e.target.value })} />
                <input value={s.camera} maxLength={20} placeholder="运镜"
                  onChange={(e) => updShot(s.seq, { camera: e.target.value })} />
                <input type="number" min={1} max={8} value={s.duration_s} placeholder="秒"
                  onChange={(e) => updShot(s.seq, { duration_s: Math.max(1, Math.min(8, Number(e.target.value) || 1)) })} />
              </div>
              <textarea rows={2} value={s.action_desc} placeholder="画面(40 字内,必须可画)"
                onChange={(e) => updShot(s.seq, { action_desc: e.target.value })} />
              <input value={s.dialogue} maxLength={200} placeholder="该镜头台词(可空)"
                onChange={(e) => updShot(s.seq, { dialogue: e.target.value })} />
              <textarea rows={3} value={s.prompt_cn} placeholder="中文提示词(含画风锚)"
                onChange={(e) => updShot(s.seq, { prompt_cn: e.target.value })} />
              <textarea rows={2} value={s.prompt_en} placeholder="英文提示词"
                onChange={(e) => updShot(s.seq, { prompt_en: e.target.value })} />
            </div>
          ))}
        </div>
      ) : (
        <>
          {card.hook_text && <p className="hint"><b>投流钩子:</b>{card.hook_text}</p>}
          {card.logline && <p className="hint">{card.logline}</p>}
          {card.emotion_curve && <p className="hint"><b>情绪曲线:</b>{card.emotion_curve}</p>}
          {card.quote_source && <p className="hint"><b>金句原句(正文):</b>{card.quote_source}</p>}
          {card.cautions?.length > 0 && <div className="msg-err">⚠ {card.cautions.join(";")}</div>}
          {(() => {
            const films = (card.character_cards ?? []).filter((c) => (c.desc || "").trim());
            if (!films.length) return null;
            return (
              <div className="sub-summary">
                <div className="card-head mb-2">
                  <b>角色定妆卡(参考图用)</b>
                  <span className="muted">每个角色复制一张,先出定妆图再传参考</span>
                </div>
                {films.map((c, i) => (
                  <div key={i} className="media-field">
                    <div className="card-head mb-2">
                      <span className="muted">{c.name}</span>
                      <span className="grow" />
                      <CopyBtn text={c.desc} label="复制定妆描述" />
                    </div>
                    <textarea rows={3} readOnly value={c.desc} />
                  </div>
                ))}
                <p className="hint">操作:复制上方描述 → 贴到文生图工具出定妆图(正面全身+清晰五官最稳) → 上传到图文生视频工具(如 minimax H3)当参考图 → 再复制对应段的提示词去生成。人物样貌就不会漂。</p>
              </div>
            );
          })()}
          {card.lines?.length > 0 && (
            <div className="sub-summary">
              {card.lines.map((l, i) => (
                <div key={i} className="script-line"><b>{l.speaker}</b>:{l.text}
                  {l.action && <span className="muted">(画面:{l.action})</span>}
                </div>
              ))}
            </div>
          )}
          <div className="card-head mb-2">
            <b>分镜({card.shots.length} 格 · {card.shots.reduce((s, x) => s + x.duration_s, 0)}s)</b>
            <span className="muted">画风锚已注入每格提示词</span>
          </div>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>#</th><th>场景</th><th>景别</th><th>运镜</th><th>秒</th><th>画面</th><th>台词</th></tr></thead>
              <tbody>
                {card.shots.map((s) => (
                  <tr key={s.seq}>
                    <td>{s.seq}</td><td>{s.scene_name}</td><td>{s.shot_type}</td>
                    <td>{s.camera}</td><td>{s.duration_s}</td>
                    <td>{s.action_desc}</td><td>{s.dialogue}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {card.shots.filter((s) => s.prompt_cn || s.prompt_en).map((s) => (
            <div key={s.seq} className="sub-summary">
              <div className="card-head mb-2"><b>镜头 {s.seq}({s.shot_type}/{s.camera}/{s.duration_s}s)</b></div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">中文提示词</span><CopyBtn text={s.prompt_cn} /></div>
                <textarea rows={3} readOnly value={s.prompt_cn} />
              </div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">英文提示词</span><CopyBtn text={s.prompt_en} /></div>
                <textarea rows={2} readOnly value={s.prompt_en} />
              </div>
            </div>
          ))}
          {card.chunks?.length > 0 && (
            <>
              <div className="card-head mb-2">
                <b>生成切段(一段一生成,先传角色参考图再粘提示词)</b>
                <span className="grow" />
                {allPromptsText && <CopyBtn text={allPromptsText} label="复制全部段提示词" />}
              </div>
              {card.chunks.map((c) => {
                const segText = chunkPromptText(card.shots, c.shot_seqs ?? []);
                return (
                  <div key={c.index} className="sub-summary">
                    <div className="card-head mb-2">
                      <b>段 {c.index}</b>
                      <span className="muted">({c.start_s}-{c.end_s}s · 镜头 {c.shot_seqs.join("、")})</span>
                      {c.over_limit && <span className="warn-tip"> ⚠超限</span>}
                      <span className="grow" />
                      {segText && <CopyBtn text={segText} label="复制段提示词" />}
                    </div>
                    {c.subtitle && (
                      <div className="hint"><b>段字幕:</b>{c.subtitle.replace(/\n/g, " / ")}</div>
                    )}
                    {segText ? (
                      <>
                        <textarea rows={3} readOnly value={segText} />
                        <p className="hint">粘到图文生视频工具(如 minimax H3):先把首格角色画面传成参考图,这段人物才稳。段内镜头连续,人物一致靠参考图兜底。</p>
                      </>
                    ) : (
                      <p className="muted">(该段镜头暂无提示词)</p>
                    )}
                  </div>
                );
              })}
            </>
          )}
          <p className="hint">出片:按段生成 → 画布拼接 → 压 SRT → 末格加金句字幕卡「{card.punchline}」。</p>
          {/* 15s 短片常常一段就出完,这时模型自带音频直接可用;要拼接才需要自己配人声。
              恰好一段才算(===1):还没切段(0)时按分轨口径说,别把「没切」当「一段出完」 */}
          <p className="hint">
            {(card.chunks?.length ?? 0) === 1
              ? "音频:整片一段出完,不存在段间错位——直接用模型自带的音频最省事;只有金句要一字不差时才自己配一条人声。"
              : "音频:环境音留给模型出,人声与 BGM 整片后期铺(分段各自带人声与音乐,拼接处必然断)。"}
          </p>
        </>
      )}
    </section>
  );
}