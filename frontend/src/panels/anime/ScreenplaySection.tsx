// 直接读对白与动作，反馈只作用于本集剧本；定妆客串不改系列班底。
import { useEffect, useState } from "react";
import { AnimeCastMember, AnimeEpisode, AnimeScript, AnimeScriptLine, AnimeSeries, animeApi } from "../../animeApi";
import { errMsg } from "../../pollJob";
import Banner from "../../ui/Banner";
import { useJob } from "../../ui/useJob";

export default function ScreenplaySection({ series, episode, disabled, onBusy, onEpisode }: {
  series: AnimeSeries; episode: AnimeEpisode; disabled: boolean; onBusy: (busy: boolean) => void; onEpisode: (e: AnimeEpisode) => void;
}) {
  const [script, setScript] = useState<AnimeScript | null>(episode.script ?? null);
  const [guests, setGuests] = useState<AnimeCastMember[]>(episode.guests ?? []);
  const [feedback, setFeedback] = useState("");
  const [dirty, setDirty] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { run } = useJob();
  useEffect(() => { setScript(episode.script ?? null); setDirty(false); }, [episode.script]);
  useEffect(() => setGuests(episode.guests ?? []), [episode.guests]);
  const locked = disabled || busy;
  const stale = !!episode.creative_stale || !!script?.stale;
  function edit(si: number, li: number, patch: Partial<AnimeScriptLine>) {
    setScript((s) => s && ({ ...s, scenes: s.scenes.map((sc, i) => i === si ? { ...sc, lines: sc.lines.map((l, j) => j === li ? { ...l, ...patch } : l) } : sc) }));
    setDirty(true);
  }
  async function work(task: () => Promise<void>) {
    setBusy(true); onBusy(true); setError(""); setStage("");
    try { await task(); } catch (e) { setError(errMsg(e)); } finally { setBusy(false); onBusy(false); }
  }
  async function generate() {
    await work(async () => {
      const ep = await run<AnimeEpisode>(() => animeApi.buildScript(episode.id, feedback), { kind: `anime-script-${episode.id}`, onStage: setStage });
      if (ep) onEpisode(ep);
    });
  }
  return <div className="media-field anime-screenplay">
    <h4>完整短剧本 · 先读对白与落点</h4>
    {episode.creative_stale && <p className="hint">方向或设定已变。旧稿保留供参考，请先按当前方向重新打磨简介。</p>}
    <details><summary>本集客串定妆（最多3位）</summary>
      <p className="hint">系列班底可以演不同职业；新增角色先定妆，后续对白、分镜与提示词才会使用它。</p>
      {guests.map((g, i) => <fieldset key={i} disabled={locked} className="form-grid sub-summary">
        <label className="field"><span className="fl">名字</span><input value={g.name} maxLength={30} onChange={(e) => setGuests((list) => list.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} /></label>
        <label className="field"><span className="fl">服装</span><input value={g.wardrobe} maxLength={300} onChange={(e) => setGuests((list) => list.map((x, j) => j === i ? { ...x, wardrobe: e.target.value } : x))} /></label>
        <label className="field field-full"><span className="fl">定妆描述</span><textarea rows={2} value={g.appearance} maxLength={800} onChange={(e) => setGuests((list) => list.map((x, j) => j === i ? { ...x, appearance: e.target.value } : x))} /></label>
        <div className="form-actions"><button onClick={() => setGuests((list) => list.filter((_, j) => j !== i))}>移除</button></div>
      </fieldset>)}
      <div className="form-actions"><button disabled={locked || guests.length >= 3} onClick={() => setGuests((list) => [...list, { name: "", role: "客串", appearance: "", wardrobe: "", personality: "", catchphrase: "", locked: false }])}>添加客串</button>
        <button disabled={locked} onClick={() => void work(async () => onEpisode((await animeApi.saveGuests(episode.id, guests)).episode))}>保存本集客串</button></div>
    </details>
    {script && <>
      <p className="hint">{script.title} · 约{script.total_s}秒{stale ? " · 旧稿待更新" : ""}</p>
      {script.scenes.map((sc, si) => <div className="sub-summary" key={si}>
        <b>{sc.slug}</b><span className="muted"> · {sc.purpose}</span>
        {sc.lines.map((line, li) => <div key={li}>
          <p><b>{line.speaker || "动作"}</b>{line.text && `：${line.text}`}</p>
          <p className="muted">{line.action} · {line.duration_s}秒{line.pause_s > 0 && `（含停顿${line.pause_s}秒）`}</p>
          <details><summary>改这一拍</summary><fieldset className="form-grid" disabled={locked || stale}>
            <label className="field"><span className="fl">说话人</span><select value={line.speaker} onChange={(e) => edit(si, li, { speaker: e.target.value })}><option value="">动作</option><option value="旁白">旁白</option>{[...series.cast, ...(episode.guests ?? [])].map((c) => <option key={c.name}>{c.name}</option>)}</select></label>
            <label className="field"><span className="fl">对白</span><textarea value={line.text} maxLength={180} onChange={(e) => edit(si, li, { text: e.target.value })} /></label>
            <label className="field field-full"><span className="fl">动作</span><textarea value={line.action} maxLength={400} onChange={(e) => edit(si, li, { action: e.target.value })} /></label>
            <label className="field"><span className="fl">本拍秒数</span><input type="number" min={1} max={20} value={line.duration_s} onChange={(e) => edit(si, li, { duration_s: Number(e.target.value) })} /></label>
            <label className="field"><span className="fl">包含的停顿秒数</span><input type="number" min={0} max={20} step={0.5} value={line.pause_s} onChange={(e) => edit(si, li, { pause_s: Number(e.target.value) })} /></label>
          </fieldset></details>
        </div>)}
      </div>)}
      {dirty && <div className="form-actions"><button className="primary" disabled={locked || stale} onClick={() => void work(async () => onEpisode((await animeApi.saveScript(episode.id, script)).episode))}>保存剧本修改</button></div>}
      {!!script.history?.length && <details><summary>之前的剧本</summary>{script.history.map((s, i) => <details key={i}><summary>{s.title} · 方向{s.goal_version}</summary>{s.scenes.flatMap((sc) => sc.lines).map((l, j) => <p key={j}>{l.speaker}：{l.text} <span className="muted">{l.action}</span></p>)}</details>)}</details>}
    </>}
    <fieldset className="form-grid" disabled={locked}>
      <label className="field field-full"><span className="fl">样稿哪里不对味？</span><textarea value={feedback} maxLength={1000} rows={2} onChange={(e) => setFeedback(e.target.value)} placeholder="如：铺垫太长，结尾靠骂人而不是误导。保留买单情境，只改包袱。" /></label>
      <div className="form-actions"><button className="primary" disabled={!episode.synopsis_ok || !!episode.creative_stale} onClick={() => void generate()}>{script && !stale ? "按反馈改剧本" : "试写完整短剧本"}</button></div>
    </fieldset>
    {busy && <Banner stage={stage} text="正在处理剧本…" />}
    {error && <p role="alert" className="hint">{error}</p>}
  </div>;
}
