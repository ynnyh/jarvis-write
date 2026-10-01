// 默认只展示理解摘要；材料与证据折叠，避免创作入口变成参数表。
import { useEffect, useState } from "react";
import { CREATIVE_DIMS, CreativeDimension, CreativeGoal, CreativeReference, CreativeScope, creativeApi } from "../creativeApi";
import { errMsg } from "../pollJob";
import Banner from "./Banner";
import { useJob } from "./useJob";

const blankReference = (): CreativeReference => ({ name: "", description: "", excerpt: "", locator: "", url: "" });
function emptyGoal(form: CreativeGoal["form"]): CreativeGoal {
  return { intent: "", form, references: [], selected: Object.keys(CREATIVE_DIMS) as CreativeDimension[], observations: [], unknowns: [], must: "", avoid: "", enabled: true, expected_version: 0 };
}

export default function CreativeReferenceCard({ scope, targetId, form, disabled = false, onSaved }: {
  scope: CreativeScope; targetId: number; form: CreativeGoal["form"]; disabled?: boolean; onSaved: () => void | Promise<void>;
}) {
  const [goal, setGoal] = useState(() => emptyGoal(form));
  const [saved, setSaved] = useState<Partial<CreativeGoal>>({});
  const [ready, setReady] = useState(false);
  const [analyzed, setAnalyzed] = useState(false);
  const [busy, setBusy] = useState("");
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const { run } = useJob();
  useEffect(() => {
    let active = true;
    setReady(false);
    setError("");
    setSaved({});
    setGoal(emptyGoal(form));
    setAnalyzed(false);
    creativeApi.get(scope, targetId).then(({ goal: g }) => {
      if (!active) return;
      setSaved(g);
      setGoal({ ...emptyGoal(form), ...g, expected_version: g.version ?? 0 });
      setAnalyzed(!!g.version);
      setReady(true);
    }).catch((e) => { if (active) setError(errMsg(e)); });
    return () => { active = false; };
  }, [scope, targetId, form]);
  const locked = disabled || !!busy || !ready;
  function change(patch: Partial<CreativeGoal>) { setGoal((g) => ({ ...g, ...patch })); setAnalyzed(false); }
  function reference(index: number, patch: Partial<CreativeReference>) {
    change({ references: goal.references.map((r, i) => i === index ? { ...r, ...patch } : r) });
  }
  async function analyze() {
    setBusy("正在理解参考…"); setError(""); setStage("");
    try {
      const result = await run<{ goal: CreativeGoal }>(() => creativeApi.analyze(scope, targetId, goal), { kind: `creative-${scope}-${targetId}`, onStage: setStage });
      if (result) { setGoal(result.goal); setAnalyzed(true); }
    } catch (e) { setError(errMsg(e)); } finally { setBusy(""); }
  }
  async function adopt(enabled = goal.enabled) {
    setBusy("正在保存方向…"); setError("");
    try {
      const { goal: g } = await creativeApi.save(scope, targetId, { ...goal, enabled, expected_version: saved.version ?? 0 });
      setSaved(g); setGoal({ ...g, expected_version: g.version }); setAnalyzed(true);
      await onSaved();
    } catch (e) { setError(errMsg(e)); } finally { setBusy(""); }
  }
  return <section className="card creative-reference">
    <h3>想写出什么感觉？</h3>
    {saved.version && <p className="hint">已采用方向 {saved.version}{saved.enabled ? "" : " · 已停用"}：{saved.intent || "按所选参考要求创作"}</p>}
    <details>
      <summary>给参考、调整理解</summary>
      <fieldset disabled={locked} className="form-grid">
        <label className="field field-full"><span className="fl">想要的体验</span>
          <textarea rows={3} maxLength={2000} value={goal.intent} onChange={(e) => change({ intent: e.target.value })}
            placeholder="例如：一本正经的日常对白喜剧，误导后抖包袱；或者现实小说，人物有私心，读起来顺。" /></label>
        <label className="field"><span className="fl">故事形式</span><select value={goal.form} onChange={(e) => change({ form: e.target.value as CreativeGoal["form"] })}>
          {scope === "project" ? <option value={form}>{form === "short" ? "短故事，一次讲完" : form === "continuous" ? "连续剧情漫剧" : "连续小说"}</option>
            : <><option value="sketch">独立情景短剧</option><option value="anthology">多段子合集</option></>}
        </select></label>
        <label className="field"><span className="fl">必须保留</span><input maxLength={1000} value={goal.must} onChange={(e) => change({ must: e.target.value })} /></label>
        <label className="field field-full"><span className="fl">不想要</span><input maxLength={1000} value={goal.avoid} onChange={(e) => change({ avoid: e.target.value })} placeholder="如：不要总亮身份打脸，不要一句动作拆三句" /></label>
      </fieldset>
      {goal.references.map((r, i) => <fieldset key={i} disabled={locked} className="form-grid sub-summary">
        <legend>参考 {i + 1}</legend>
        <label className="field"><span className="fl">作品/材料名</span><input value={r.name} maxLength={150} onChange={(e) => reference(i, { name: e.target.value })} /></label>
        <label className="field"><span className="fl">来源与片段位置</span><input value={r.locator} maxLength={300} onChange={(e) => reference(i, { locator: e.target.value })} placeholder="如：第2集，饭店买单片段" /></label>
        <label className="field field-full"><span className="fl">喜欢什么</span><textarea value={r.description} rows={2} maxLength={2000} onChange={(e) => reference(i, { description: e.target.value })} /></label>
        <label className="field field-full"><span className="fl">正文/剧本/字幕摘录</span><textarea value={r.excerpt} rows={4} maxLength={12000} onChange={(e) => reference(i, { excerpt: e.target.value })} /></label>
        <label className="field field-full"><span className="fl">公开文本链接（也可直接粘贴正文/字幕）</span><input value={r.url} maxLength={1000} onChange={(e) => reference(i, { url: e.target.value, read_scope: "" })} /></label>
        {r.read_scope && <p className="field-note">{r.read_scope}</p>}
        <div className="form-actions"><button onClick={() => change({ references: goal.references.filter((_, j) => j !== i) })}>移除参考</button></div>
      </fieldset>)}
      {goal.references.some((r) => r.url && !r.excerpt) && <label className="field">
        <span><input type="checkbox" disabled={locked} checked={!!goal.fetch_links} onChange={(e) => change({ fetch_links: e.target.checked })} /> 分析时读取公开文本链接</span>
        <span className="field-note">登录页面、音视频或仅有脚本的页面无法读取时，会明确提示并继续使用已给材料。</span>
      </label>}
      <div className="form-actions">
        <button disabled={locked || goal.references.length >= 5} onClick={() => change({ references: [...goal.references, blankReference()] })}>添加参考</button>
        <button className="primary" disabled={locked || (!goal.intent.trim() && !goal.references.length)} onClick={() => void analyze()}>理解参考与想法</button>
      </div>
      {analyzed && <>
        <p>选取要借鉴的部分，再读样稿校准：</p>
        <div className="form-grid">
          {(Object.entries(CREATIVE_DIMS) as [CreativeDimension, string][]).map(([key, label]) => <label key={key} className="field">
            <span><input type="checkbox" disabled={locked} checked={goal.selected.includes(key)} onChange={(e) => setGoal((g) => ({ ...g, selected: e.target.checked ? [...g.selected, key] : g.selected.filter((k) => k !== key) }))} /> {label}</span>
          </label>)}
        </div>
        {goal.observations.filter((o) => goal.selected.includes(o.dimension)).map((o, i) => <div key={i} className="sub-summary">
          <b>{CREATIVE_DIMS[o.dimension]}{o.basis === "inferred" ? " · 待样稿确认" : ""}</b>
          <p>{o.instruction}</p>
          {o.evidence && <details><summary>查看依据</summary><p>参考 {o.source_index + 1} · {goal.references[o.source_index]?.locator || "所给材料"}</p><blockquote>{o.evidence}</blockquote></details>}
        </div>)}
        {goal.unknowns.length > 0 && <details><summary>材料不足与待确认项</summary>{goal.unknowns.map((u, i) => <p key={i}>{u}</p>)}</details>}
        <p className="hint">采用新方向会让旧方案或剧集标为待更新；已有正文保留。</p>
        <div className="form-actions"><button className="primary" disabled={locked} onClick={() => void adopt(true)}>采用这个理解</button>
          {saved.enabled && <button disabled={locked} onClick={() => void adopt(false)}>停用参考方向</button>}</div>
      </>}
      {!!saved.history?.length && <details><summary>历史方向</summary>{saved.history.filter((g) => g.version).map((g) => <p key={g.version}>
        {g.version} · {g.intent} <button disabled={locked} onClick={() => { setGoal({ ...g, expected_version: saved.version }); setAnalyzed(true); }}>载入此方向</button>
      </p>)}</details>}
    </details>
    {busy && <Banner stage={stage} text={busy} />}
    {error && <p role="alert" className="hint">{error}</p>}
  </section>;
}
