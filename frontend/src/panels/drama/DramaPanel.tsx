// 漫剧工坊面板:把已定稿小说改编成漫剧拍摄手册。
// 四步流水线(每步独立可重跑):美术风格卡/资产卡 → 集规划 → 单集剧本 → 分镜 → 三轨提示词,
// 产物拿去即梦/可灵/Midjourney/剪映出片——沿用「只产提示词」哲学,不接生成模型。
// 一致性靠锚段注入:画风锚(风格卡)+ 人物锚(角色卡)+ 场景锚(场景卡)逐字嵌入每格分镜。
import { useCallback, useEffect, useState } from "react";
import {
  DRAMA_STATUS_CN,
  DramaCharacterCard,
  DramaEpisode,
  DramaSceneCard,
  DramaShot,
  DramaStyleCard,
  dramaApi,
} from "../../dramaApi";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import EmptyState from "../../ui/EmptyState";

interface Props { pid: number }

// 复制按钮(与 SubmissionPanel 同款;面板间不共享组件,各自内聚)
function CopyBtn({ text, label = "复制" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  async function go() {
    if (!text.trim()) { toast.err("内容为空", "没有可复制的内容"); return; }
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1200);
    } catch {
      toast.err("复制失败", "请手动选中文本复制");
    }
  }
  return <button className="btn-sm" onClick={go}>{done ? "✓ 已复制" : label}</button>;
}

function Banner({ stage, text }: { stage: string; text: string }) {
  return (
    <div className="gen-banner"><span className="spin" /><span className="gen-banner-text">{stage || text}</span></div>
  );
}

export default function DramaPanel({ pid }: Props) {
  const [meta, setMeta] = useState<{ approved_chapters: number[] } | null>(null);
  const [style, setStyle] = useState<DramaStyleCard | null>(null);
  const [cards, setCards] = useState<DramaCharacterCard[]>([]);
  const [scenes, setScenes] = useState<DramaSceneCard[]>([]);
  const [episodes, setEpisodes] = useState<DramaEpisode[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loadErr, setLoadErr] = useState("");

  const reloadBase = useCallback(async () => {
    try {
      const [m, s, c, e] = await Promise.all([
        dramaApi.meta(pid),
        dramaApi.getStyle(pid),
        dramaApi.getCharacters(pid),
        dramaApi.getEpisodes(pid),
      ]);
      setMeta(m);
      setStyle(s.style);
      setCards(c.cards);
      setScenes(c.scenes);
      setEpisodes(e.episodes);
    } catch (e) {
      setLoadErr(errMsg(e));
    }
  }, [pid]);

  useEffect(() => { void reloadBase(); }, [reloadBase]);

  if (loadErr) return <div className="msg-err">{loadErr}</div>;
  if (meta === null) return <p className="muted">加载中…</p>;

  if (meta.approved_chapters.length === 0) {
    return (
      <EmptyState>
        还没有已定稿章节——漫剧工坊改编的是小说成稿。先去写作区定稿几章,再回来把故事变成漫剧。
      </EmptyState>
    );
  }

  return (
    <div className="stack">
      <StyleSection pid={pid} style={style} onSaved={setStyle} />
      <AssetsSection pid={pid} cards={cards} scenes={scenes}
        onChanged={(c, s) => { setCards(c); setScenes(s); }} />
      <PlanSection pid={pid} approved={meta.approved_chapters}
        episodes={episodes}
        onChanged={setEpisodes}
        selectedId={selectedId}
        onSelect={setSelectedId} />
      {selectedId !== null && (
        <EpisodeDetail pid={pid} eid={selectedId}
          onEpisodesChanged={setEpisodes}
          onDeselect={() => setSelectedId(null)} />
      )}
    </div>
  );
}

// ================= 美术风格卡 =================
function StyleSection({ pid, style, onSaved }: {
  pid: number; style: DramaStyleCard | null; onSaved: (s: DramaStyleCard) => void;
}) {
  const { run } = useJob();
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  const [draft, setDraft] = useState<DramaStyleCard | null>(style);

  useEffect(() => { setDraft(style); }, [style]);

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<DramaStyleCard>(
        () => dramaApi.generateStyle(pid),
        { kind: `drama-style-${pid}`, onStage: setStage },
      );
      if (r) { onSaved(r); toast.ok("美术风格已定", "这段画风锁定会注入每一格分镜"); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  async function save() {
    if (!draft) return;
    try {
      const r = await dramaApi.saveStyle(pid, draft);
      onSaved(r.style);
      toast.ok("风格卡已保存");
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  function field(label: string, key: keyof DramaStyleCard, rows: number) {
    if (!draft) return null;
    return (
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">{label}</span></div>
        <textarea rows={rows} value={draft[key] as string}
          onChange={(e) => setDraft({ ...draft, [key]: e.target.value })} />
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">① 美术风格卡 <span className="muted">全片画风统一</span></h3>
        {style && <button className="btn-sm" onClick={save}>保存修改</button>}
        <button className="primary" disabled={busy} onClick={generate}>
          {style ? "重新生成" : "AI 定美术风格"}
        </button>
      </div>
      <p className="card-desc">
        按本书类型定一段「画风锁定」(媒介/笔触/色彩/光影),之后每一条分镜提示词都会逐字嵌入这段锚,
        上百格画面画风不漂移。生成后可直接改字微调。
      </p>
      {busy && <Banner stage={stage} text="AI 正在定全片美术风格…" />}
      {err && <div className="msg-err">{err}</div>}
      {draft && (
        <>
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">风格名</span></div>
            <input value={draft.style_name}
              onChange={(e) => setDraft({ ...draft, style_name: e.target.value })} />
          </div>
          {field("画风锁定段(中文,即梦等)", "style_cn", 3)}
          {field("画风锁定段(英文,Midjourney)", "style_en", 2)}
          {field("负面词基座", "negative", 2)}
          <div className="media-field">
            <div className="card-head mb-2"><span className="muted">画幅</span></div>
            <input value={draft.ratio}
              onChange={(e) => setDraft({ ...draft, ratio: e.target.value })} />
          </div>
        </>
      )}
    </div>
  );
}

// ================= 角色卡 / 场景卡 =================
function AssetsSection({ pid, cards, scenes, onChanged }: {
  pid: number;
  cards: DramaCharacterCard[];
  scenes: DramaSceneCard[];
  onChanged: (cards: DramaCharacterCard[], scenes: DramaSceneCard[]) => void;
}) {
  const { run } = useJob();
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<{ cards: DramaCharacterCard[]; skipped_locked: number; scenes: DramaSceneCard[] }>(
        () => dramaApi.generateCharacters(pid),
        { kind: `drama-chars-${pid}`, onStage: setStage },
      );
      if (r) {
        const fresh = await dramaApi.getCharacters(pid);
        onChanged(fresh.cards, fresh.scenes);
        const lockedNote = r.skipped_locked ? `,${r.skipped_locked} 张锁定卡未动` : "";
        toast.ok("资产卡已生成", `角色 ${r.cards.length} 张${lockedNote}`);
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">② 角色卡与场景卡 <span className="muted">人物一致性</span></h3>
        <button className="primary" disabled={busy} onClick={generate}>
          {cards.length ? "重新生成(锁定的不动)" : "AI 生成资产卡"}
        </button>
      </div>
      <p className="card-desc">
        从故事圣经批量出「锁定外貌段」:角色出场的那格分镜会逐字嵌入这段描述,
        同一个人不换脸;声线描述留给配音阶段。手动调过的卡点「锁定」,重跑不覆盖。
      </p>
      {busy && <Banner stage={stage} text="AI 正在设计角色视觉卡…" />}
      {err && <div className="msg-err">{err}</div>}
      {cards.map((c) => <CharCardRow key={c.id} pid={pid} card={c} onSaved={(nc) => {
        onChanged(cards.map((x) => (x.id === nc.id ? nc : x)), scenes);
      }} />)}
      {scenes.length > 0 && (
        <div className="sub-summary">
          <div className="card-head mb-2"><b>场景卡({scenes.length})</b></div>
          {scenes.map((s) => (
            <div key={s.id} className="mb-2">
              <b>{s.name}</b>
              <div className="muted">{s.appearance_cn}</div>
            </div>
          ))}
        </div>
      )}
      {cards.length === 0 && !busy && (
        <p className="hint">还没生成。角色来自故事圣经——写了几章、圣经里有角色后即可生成。</p>
      )}
    </div>
  );
}

function CharCardRow({ pid, card, onSaved }: {
  pid: number; card: DramaCharacterCard; onSaved: (c: DramaCharacterCard) => void;
}) {
  const [draft, setDraft] = useState(card);
  const [dirty, setDirty] = useState(false);

  useEffect(() => { setDraft(card); setDirty(false); }, [card]);

  async function save(extra?: Partial<DramaCharacterCard>) {
    try {
      const r = await dramaApi.patchCharacter(pid, card.id, { ...draft, ...extra });
      onSaved(r.card);
      setDirty(false);
      if (extra) toast.ok(extra.locked ? "已锁定" : "已解锁");
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  return (
    <div className="sub-summary">
      <div className="card-head mb-2">
        <b>{draft.name}</b>
        <span className="grow" />
        <button className={"btn-sm" + (card.locked ? " primary" : "")}
          onClick={() => save({ locked: !card.locked })}>
          {card.locked ? "🔒 已锁定" : "锁定"}
        </button>
        {dirty && <button className="btn-sm primary" onClick={() => save()}>保存</button>}
      </div>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">锁定外貌段(注入每格分镜)</span><CopyBtn text={draft.appearance_cn} /></div>
        <textarea rows={3} value={draft.appearance_cn}
          onChange={(e) => { setDraft({ ...draft, appearance_cn: e.target.value }); setDirty(true); }} />
      </div>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">声线(配音用)</span></div>
        <input value={draft.voice_desc}
          onChange={(e) => { setDraft({ ...draft, voice_desc: e.target.value }); setDirty(true); }} />
      </div>
    </div>
  );
}

// ================= 集规划 + 集列表 =================
function PlanSection({ pid, approved, episodes, onChanged, selectedId, onSelect }: {
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
    if (!confirm("删除这一集(连分镜)?")) return;
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
      </p>
      <div className="card-head mb-2 plan-form">
        <label>从第
          <select value={from} onChange={(e) => { const v = Number(e.target.value); setFrom(v); if (to < v) setTo(v); }}>
            {approved.map((n) => <option key={n} value={n}>{n}</option>)}
          </select> 章
        </label>
        <label>到第
          <select value={to} onChange={(e) => { const v = Number(e.target.value); setTo(v); if (from > v) setFrom(v); }}>
            {approved.map((n) => <option key={n} value={n}>{n}</option>)}
          </select> 章
        </label>
        <label>模式
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="dialogue">对白演绎</option>
            <option value="narration">口播解说</option>
          </select>
        </label>
        <label>单集约
          <input type="number" min={30} max={180} value={duration}
            onChange={(e) => setDuration(Number(e.target.value) || 90)} /> 秒
        </label>
        <button className="primary" disabled={busy} onClick={plan}>
          {episodes.length ? "重新规划" : "切集"}
        </button>
      </div>
      {busy && <Banner stage={stage} text="AI 正在切集(钩子/卡点)…" />}
      {err && <div className="msg-err">{err}</div>}
      {episodes.map((ep) => (
        <div key={ep.id}
          className={"sub-summary ep-row" + (selectedId === ep.id ? " ep-on" : "")}
          onClick={() => onSelect(ep.id === selectedId ? null : ep.id)}>
          <div className="card-head mb-2">
            <b>第 {ep.ep_index} 集《{ep.title}》</b>
            <span className="badge">源:第{ep.source_chapter}章</span>
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

// ================= 单集详情:剧本 → 分镜 → 提示词 → 导出 =================
function EpisodeDetail({ pid, eid, onEpisodesChanged, onDeselect }: {
  pid: number; eid: number;
  onEpisodesChanged: (eps: DramaEpisode[]) => void;
  onDeselect: () => void;
}) {
  const { run } = useJob();
  const [episode, setEpisode] = useState<DramaEpisode | null>(null);
  const [shots, setShots] = useState<DramaShot[]>([]);
  const [busy, setBusy] = useState(""); // script | board | prompts | ""
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  const reload = useCallback(async () => {
    try {
      const r = await dramaApi.getEpisode(pid, eid);
      setEpisode(r.episode);
      setShots(r.shots);
    } catch (e) { setErr(errMsg(e)); }
  }, [pid, eid]);

  useEffect(() => { void reload(); }, [reload]);

  async function refreshList() {
    try { onEpisodesChanged((await dramaApi.getEpisodes(pid)).episodes); } catch { /* 列表刷新失败不阻塞 */ }
  }

  async function act(kind: "script" | "board" | "prompts", start: () => Promise<{ job_id: string }>, okTitle: string) {
    setBusy(kind); setErr(""); setStage("");
    try {
      await run(start, { kind: `drama-${kind}-${eid}`, onStage: setStage });
      await reload();
      await refreshList();
      toast.ok(okTitle);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); setStage(""); }
  }

  async function exp(fmt: "md" | "csv" | "json") {
    try { await dramaApi.exportEpisode(pid, eid, fmt); }
    catch (e) { toast.err("导出失败", errMsg(e)); }
  }

  if (episode === null && !err) return <p className="muted">加载中…</p>;

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">
          ④ 第 {episode?.ep_index} 集《{episode?.title}》
          <span className="badge">{episode ? (DRAMA_STATUS_CN[episode.status] ?? episode.status) : ""}</span>
        </h3>
        <button className="btn-sm" onClick={onDeselect}>收起</button>
      </div>
      {err && <div className="msg-err">{err}</div>}

      <div className="card-head mb-2 ep-actions">
        <button className="primary" disabled={!!busy}
          onClick={() => act("script", () => dramaApi.writeScript(pid, eid), "剧本已生成")}>
          {episode?.script?.lines?.length ? "重写剧本" : "④-1 写剧本"}
        </button>
        <button className="primary" disabled={!!busy || !episode?.script?.lines?.length}
          onClick={() => act("board", () => dramaApi.storyboard(pid, eid), "分镜已生成(旧分镜已覆盖)")}>
          {shots.length ? "重新拆分镜" : "④-2 拆分镜"}
        </button>
        <button className="primary" disabled={!!busy || shots.length === 0}
          onClick={() => act("prompts", () => dramaApi.prompts(pid, eid), "三轨提示词已生成")}>
          ④-3 出提示词
        </button>
        <span className="grow" />
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("md")}>导出手册</button>
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("csv")}>CSV</button>
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("json")}>JSON</button>
      </div>
      {busy && <Banner stage={stage} text="AI 正在处理…" />}

      {/* 剧本 */}
      {episode?.script?.lines?.length ? (
        <div className="sub-summary">
          <div className="card-head mb-2"><b>剧本({episode.script.lines.length} 条)</b>
            <span className="muted">{episode.script.synopsis}</span></div>
          {episode.script.lines.map((l, i) => (
            <div key={i} className="script-line">
              <b>{l.speaker}</b>:{l.text}
              {l.action && <span className="muted">(画面:{l.action})</span>}
            </div>
          ))}
        </div>
      ) : (
        <p className="hint">先「写剧本」:按开场钩子开场、结尾卡点收束,台词口语化、每句可拍。</p>
      )}

      {/* 分镜表 */}
      {shots.length > 0 && (
        <>
          <div className="card-head mb-2"><b>分镜表({shots.length} 格 · 约 {shots.reduce((s, x) => s + x.duration_s, 0)} 秒)</b></div>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>#</th><th>场景</th><th>角色</th><th>景别</th><th>运镜</th><th>秒</th><th>画面</th><th>台词</th></tr>
              </thead>
              <tbody>
                {shots.map((s) => (
                  <tr key={s.id}>
                    <td>{s.seq}</td>
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
        </>
      )}

      {/* 三轨提示词 */}
      {shots.some((s) => s.prompt_cn || s.prompt_en) && (
        <>
          <div className="card-head mb-2"><b>三轨提示词(即拿即用)</b>
            <span className="muted">画风锚/角色锚已注入,可在框里继续微调</span></div>
          {shots.filter((s) => s.prompt_cn || s.prompt_en).map((s) => (
            <PromptRow key={s.id} pid={pid} shot={s} onSaved={(ns) => {
              setShots(shots.map((x) => (x.id === ns.id ? ns : x)));
            }} />
          ))}
        </>
      )}
    </div>
  );
}

function PromptRow({ pid, shot, onSaved }: {
  pid: number; shot: DramaShot; onSaved: (s: DramaShot) => void;
}) {
  const [draft, setDraft] = useState(shot);
  const [dirty, setDirty] = useState(false);

  useEffect(() => { setDraft(shot); setDirty(false); }, [shot]);

  async function save() {
    try {
      const r = await dramaApi.patchShot(pid, shot.id, draft);
      onSaved(r.shot);
      setDirty(false);
      toast.ok(`镜头 ${shot.seq} 已保存`);
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  return (
    <div className="sub-summary">
      <div className="card-head mb-2">
        <b>镜头 {draft.seq}({draft.shot_type}/{draft.camera}/{draft.duration_s}s)</b>
        <span className="grow" />
        {dirty && <button className="btn-sm primary" onClick={save}>保存</button>}
      </div>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">中文提示词(即梦/可灵)</span><CopyBtn text={draft.prompt_cn} /></div>
        <textarea rows={4} value={draft.prompt_cn}
          onChange={(e) => { setDraft({ ...draft, prompt_cn: e.target.value }); setDirty(true); }} />
      </div>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">英文提示词(Midjourney)</span><CopyBtn text={draft.prompt_en} /></div>
        <textarea rows={3} value={draft.prompt_en}
          onChange={(e) => { setDraft({ ...draft, prompt_en: e.target.value }); setDirty(true); }} />
      </div>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">负面提示词</span><CopyBtn text={draft.negative} /></div>
        <textarea rows={2} value={draft.negative}
          onChange={(e) => { setDraft({ ...draft, negative: e.target.value }); setDirty(true); }} />
      </div>
    </div>
  );
}
