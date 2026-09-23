// 动画短剧系列工作台:卡司区常驻在上(资产),剧集列表在下;单集面板串行推进
// 「点子聊天确认简介 → 分镜 → 整集分段提示词」。对话式确认流是产品核心:
// 用户的点子先和 AI 聊,AI 补充完善出简介,用户拍板后才展开分镜;没点子的走
// 「三选一梗纲」捷径(选定即确认)。换简介/换梗纲会作废下游产物,面板上明示。
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AnimeCastMember, AnimeEpisode, AnimeMeta, AnimeSeries, AnimeShot, animeApi,
} from "../../animeApi";
import { toast } from "../../ui/Toaster";
import { ConfirmGate } from "../../ui/confirmKit";
import { errMsg } from "../../pollJob";
import { CopyBtn } from "../../ui/copy";
import { FilmPromptCard } from "../../ui/FilmPromptCard";
import EmptyState from "../../ui/EmptyState";
import { useJob } from "../../ui/useJob";
import { confirmDialog } from "../../ui/ConfirmDialog";

const EP_STATUS_CN: Record<string, string> = {
  premise: "待聊简介", takes_ready: "梗纲已出", synopsis_ready: "简介已确认",
  shots_ready: "分镜已出", prompted: "提示词已出",
};

export default function SeriesWorkspace({ sid }: { sid: number }) {
  const nav = useNavigate();
  const [meta, setMeta] = useState<AnimeMeta | null>(null);
  const [series, setSeries] = useState<AnimeSeries | null>(null);
  const [episodes, setEpisodes] = useState<AnimeEpisode[]>([]);
  const [selId, setSelId] = useState<number | null>(null);

  const reload = useCallback(async () => {
    try {
      const r = await animeApi.get(sid);
      setSeries(r.series);
      setEpisodes([...r.episodes].sort((a, b) => a.seq - b.seq));
    } catch (e) { toast.err("加载失败", errMsg(e)); }
  }, [sid]);
  useEffect(() => {
    void reload();
    animeApi.meta().then(setMeta).catch(() => setMeta(null));
  }, [reload]);

  // 把(工作台内的)一集更新合并进列表态
  const mergeEpisode = useCallback((ep: AnimeEpisode) => {
    setEpisodes((list) => list.map((e) => (e.id === ep.id ? ep : e)));
  }, []);

  async function removeSeries() {
    const ok = await confirmDialog({
      title: `删除系列「${series?.title}」?`,
      body: "全部剧集、卡司与提示词都会一起删掉,不可恢复。",
      confirmText: "确认删除", danger: true,
    });
    if (!ok) return;
    try {
      await animeApi.remove(sid);
      toast.ok("系列已删除");
      nav("/anime");
    } catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  if (series === null) return <p className="muted">加载中…</p>;

  return (
    <>
      <div className="page-head">
        <h1>{series.title}</h1>
        <span className="badge mute">{series.genre_label}</span>
        <span className="badge mute">每集 {series.episode_s}s</span>
        <span className="grow" />
        <button className="btn-sm" onClick={() => void removeSeries()}>删除系列</button>
      </div>

      <CastSection series={series} meta={meta} onSaved={setSeries} />

      <section className="card">
        <div className="card-head">
          <h3 className="grow">剧集<span className="muted">每集一个独立小故事,互不接续</span></h3>
        </div>
        <EpisodeCreator sid={sid} hasCast={series.cast.length > 0} onCreated={(ep) => {
          setEpisodes((list) => [...list, ep]);
          setSelId(ep.id);
        }} />
        {episodes.length === 0 ? (
          <EmptyState>还没有一集。给个情境命题开新的一集。</EmptyState>
        ) : (
          episodes.map((ep) => (
            <div key={ep.id} className="sub-summary ep-row" onClick={() => setSelId(selId === ep.id ? null : ep.id)}>
              <div className="card-head mb-2">
                <b>第 {ep.seq} 集</b>
                {ep.title && <b>{ep.title}</b>}
                <span className="badge">{EP_STATUS_CN[ep.status] ?? ep.status}</span>
                <span className="grow" />
                <button className="btn-sm" onClick={(e) => {
                  e.stopPropagation();
                  void (async () => {
                    const ok = await confirmDialog({
                      title: `删掉第 ${ep.seq} 集?`, body: "这一集的内容与提示词一起删掉。",
                      confirmText: "删除", danger: true,
                    });
                    if (!ok) return;
                    try {
                      await animeApi.removeEpisode(ep.id);
                      setEpisodes((list) => list.filter((x) => x.id !== ep.id));
                      if (selId === ep.id) setSelId(null);
                    } catch (e2) { toast.err("删除失败", errMsg(e2)); }
                  })();
                }}>删除</button>
              </div>
              <div className="muted">{ep.premise || ep.synopsis.slice(0, 60) || "(还没聊简介)"}</div>
            </div>
          ))
        )}
      </section>

      {selId !== null && (() => {
        const ep = episodes.find((e) => e.id === selId);
        return ep ? (
          <EpisodePanel key={ep.id} series={series} episode={ep} meta={meta}
            onEpisode={mergeEpisode} />
        ) : null;
      })()}
    </>
  );
}

// ================= 卡司(系列级资产) =================
function CastSection({ series, meta, onSaved }: {
  series: AnimeSeries;
  meta: AnimeMeta | null;
  onSaved: (s: AnimeSeries) => void;
}) {
  const { run } = useJob();
  const [cast, setCast] = useState<AnimeCastMember[]>(series.cast);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setCast(series.cast); }, [series.cast]);

  const dirLabel = (meta?.directions ?? []).find((d) => d.key === series.direction)?.label
    ?? series.direction;

  function edit(i: number, p: Partial<AnimeCastMember>) {
    setCast((list) => list.map((c, j) => (j === i ? { ...c, ...p } : c)));
  }

  async function save() {
    try { onSaved((await animeApi.saveCast(series.id, cast)).series); }
    catch (e) { toast.err("卡司保存失败", errMsg(e)); }
  }

  async function generate() {
    setBusy(true);
    try {
      await run(() => animeApi.buildCast(series.id), { kind: `anime-cast-${series.id}` });
      onSaved((await animeApi.get(series.id)).series);
      toast.ok("卡司已出", "锁定不想被覆盖的角色,再点「重出」可以只换其余的");
    } catch (e) { toast.err("卡司生成失败", errMsg(e)); } finally { setBusy(false); }
  }

  return (
    <section className="card">
      <div className="card-head mb-2">
        <h3 className="grow">固定卡司<span className="muted">主角全季固定;锁定的角色重出时原样保留</span></h3>
        <span className="badge mute">{dirLabel}</span>
        {cast.length > 0 && <button className="btn-sm" disabled={busy} onClick={() => void save()}>保存修改</button>}
        <button className={cast.length === 0 ? "primary" : "btn-sm"} disabled={busy}
          title={cast.length === 0 ? "按一句话设定设计 1 主角 + 2-3 配角" : "重出卡司:锁定的角色不动,其余换新"}
          onClick={() => void generate()}>
          {busy ? "AI 设计中…" : cast.length === 0 ? "AI 设计卡司" : "AI 重出卡司"}
        </button>
      </div>
      <p className="card-desc">
        定妆描述是全系列一致性的锚——出提示词时逐字注入,配合同名角色的定妆参考图走
        图生视频,形象就锁住了。每个角色可直接改文字;点「锁定」后重出不覆盖;
        「复制定妆词」贴进文生图工具就能出这个角色的定妆照。
      </p>
      {cast.length === 0 ? (
        <EmptyState>还没有卡司:点「AI 设计卡司」,或先把一句话设定改得更具体些。</EmptyState>
      ) : (
        cast.map((c, i) => {
          // 定妆照提示词:画风锚 + 定妆 + 服装成套,贴进文生图即可出定妆照
          const lookPrompt = [
            series.style_cn || dirLabel,
            `${c.name}(${c.role})定妆:${c.appearance}`,
            c.wardrobe ? `常驻服装与配饰:${c.wardrobe}` : "",
          ].filter(Boolean).join("\n");
          return (
            <div key={i} className="sub-summary">
              <div className="card-head mb-2">
                <b>{c.name}</b>
                <span className={"badge" + (c.role === "主角" ? "" : " mute")}>{c.role}</span>
                <span className="grow" />
                <CopyBtn text={lookPrompt} label="复制定妆词"
                  title="画风+定妆+服装成套复制,贴进文生图出定妆照" />
                <button type="button"
                  className={"chip" + (c.locked ? " on" : "")}
                  aria-pressed={!!c.locked}
                  title="锁定后「AI 重出卡司」不会覆盖这个角色"
                  onClick={() => edit(i, { locked: !c.locked })}>
                  🔒 {c.locked ? "已锁定" : "锁定"}
                </button>
              </div>
              <div className="form-grid">
                <div className="field field-full">
                  <label className="fl">
                    定妆描述<span className="hint">外貌写到能认脸;提示词逐字用它</span>
                  </label>
                  <textarea rows={6} maxLength={800} value={c.appearance}
                    onChange={(e) => edit(i, { appearance: e.target.value })} />
                </div>
                <div className="field">
                  <label className="fl">常驻服装</label>
                  <input maxLength={300} value={c.wardrobe}
                    onChange={(e) => edit(i, { wardrobe: e.target.value })} />
                </div>
                <div className="field">
                  <label className="fl">口头禅</label>
                  <input maxLength={60} value={c.catchphrase}
                    onChange={(e) => edit(i, { catchphrase: e.target.value })} />
                </div>
                <div className="field field-full">
                  <label className="fl">性格神态</label>
                  <input maxLength={300} value={c.personality}
                    onChange={(e) => edit(i, { personality: e.target.value })} />
                </div>
              </div>
            </div>
          );
        })
      )}
    </section>
  );
}

// ================= 新开一集 =================
function EpisodeCreator({ sid, hasCast, onCreated }: {
  sid: number; hasCast: boolean; onCreated: (ep: AnimeEpisode) => void;
}) {
  const [premise, setPremise] = useState("");
  const [busy, setBusy] = useState(false);
  // 没灵感:AI 出的下一集命题(点一条回填命题框)
  const [ideas, setIdeas] = useState<string[]>([]);
  const [ideaBusy, setIdeaBusy] = useState(false);

  async function askIdeas() {
    setIdeaBusy(true);
    try {
      setIdeas((await animeApi.suggestEpisode(sid)).premises);
      toast.ok("出了三个点子", "点一条填进命题框,也可以直接照它聊简介");
    } catch (e) { toast.err("出点子失败", errMsg(e)); } finally { setIdeaBusy(false); }
  }

  async function create() {
    if (!hasCast) { toast.err("先定卡司再开集", "卡司是每集出梗的班底;先点「AI 设计卡司」"); return; }
    setBusy(true);
    try {
      const r = await animeApi.createEpisode(sid, premise.trim());
      toast.ok("新的一集已开", "把你的点子告诉 AI,聊出简介再往下走");
      onCreated(r.episode);
      setPremise("");
      setIdeas([]);
    } catch (e) { toast.err("开集失败", errMsg(e)); } finally { setBusy(false); }
  }

  return (
    <div className="media-field" style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", gap: 8 }}>
        <input value={premise} maxLength={500} style={{ flex: 1 }}
          placeholder="本集情境命题,如「阿丸第一次掌勺年夜饭」(留空 = AI 自拟)"
          onChange={(e) => setPremise(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void create(); } }} />
        <button disabled={ideaBusy} title="没灵感?让 AI 按卡司与类型出三个下一集点子"
          onClick={() => void askIdeas()}>
          {ideaBusy ? "AI 出点子中…" : "🎲 AI 出点子"}
        </button>
        <button className="primary" disabled={busy} onClick={() => void create()}>
          {busy ? "开集中…" : "＋ 新开一集"}
        </button>
      </div>
      {ideas.length > 0 && (
        <div className="chips ideas" style={{ marginTop: 6 }}>
          {ideas.map((p, i) => (
            <button key={i} type="button" className="chip" title={p}
              onClick={() => setPremise(p)}>{p}</button>
          ))}
        </div>
      )}
    </div>
  );
}

// ================= 单集面板:聊简介 → 分镜 → 整集提示词 =================
function EpisodePanel({ series, episode, meta, onEpisode }: {
  series: AnimeSeries;
  episode: AnimeEpisode;
  meta: AnimeMeta | null;
  onEpisode: (ep: AnimeEpisode) => void;
}) {
  const { run } = useJob();
  const [premise, setPremise] = useState(episode.premise);
  const [msg, setMsg] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [shots, setShots] = useState<AnimeShot[]>(episode.shots);
  const [shotsDirty, setShotsDirty] = useState(false);
  const [segS, setSegS] = useState<15 | 30>(15);
  const [busy, setBusy] = useState("");
  useEffect(() => { setShots(episode.shots); setShotsDirty(false); }, [episode.shots]);

  function editShot(i: number, p: Partial<AnimeShot>) {
    setShots((list) => list.map((s, j) => (j === i ? { ...s, ...p } : s)));
    setShotsDirty(true);
  }

  async function savePremise() {
    try {
      onEpisode((await animeApi.patchEpisode(episode.id, { premise })).episode);
      toast.ok("命题已更新", "聊简介时 AI 会参考它");
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  async function send(text: string) {
    if (chatBusy) return;
    setChatBusy(true);
    try {
      const r = await animeApi.chat(episode.id, text);
      onEpisode(r.episode);
      setMsg("");
      toast.ok("AI 出了一版简介", "不满意就接着改;满意点「确认简介」往下走");
    } catch (e) { toast.err("这轮没接住", errMsg(e)); } finally { setChatBusy(false); }
  }

  async function confirmNow() {
    try {
      onEpisode((await animeApi.confirmSynopsis(episode.id)).episode);
      toast.ok("简介已确认", "分镜解锁了;再聊天或改简介会重新上锁");
    } catch (e) { toast.err("确认失败", errMsg(e)); }
  }

  async function genTakes() {
    setBusy("takes");
    try {
      await run(() => animeApi.buildTakes(episode.id), { kind: `anime-takes-${episode.id}` });
      onEpisode((await animeApi.get(series.id)).episodes.find((e) => e.id === episode.id)!);
      toast.ok("三个梗纲已出", "挑最对味的一版,选定即确认简介");
    } catch (e) { toast.err("出梗纲失败", errMsg(e)); } finally { setBusy(""); }
  }

  async function pick(index: number) {
    try { onEpisode((await animeApi.pick(episode.id, index)).episode); }
    catch (e) { toast.err("选定失败", errMsg(e)); }
  }

  async function genShots() {
    setBusy("shots");
    try {
      await run(() => animeApi.buildShots(episode.id), { kind: `anime-shots-${episode.id}` });
      onEpisode((await animeApi.get(series.id)).episodes.find((e) => e.id === episode.id)!);
      toast.ok("分镜已出", "每镜 2-5 秒,台词动作全开;可直接改字后保存");
    } catch (e) { toast.err("出分镜失败", errMsg(e)); } finally { setBusy(""); }
  }

  async function saveShots() {
    try {
      onEpisode((await animeApi.saveShots(episode.id, shots)).episode);
      setShotsDirty(false);
      toast.ok("分镜已保存", "整集提示词要按新分镜重新生成");
    } catch (e) { toast.err("分镜保存失败", errMsg(e)); }
  }

  const chosen = episode.chosen >= 0 ? episode.takes[episode.chosen] : null;
  const hasShots = shots.length > 0;
  const confirmed = episode.synopsis_ok;

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">
          第 {episode.seq} 集{episode.title ? ` · ${episode.title}` : ""}
          <span className="muted">{EP_STATUS_CN[episode.status] ?? episode.status}</span>
        </h3>
      </div>

      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">情境命题</span>
          <span className="grow" />
          {premise !== episode.premise && (
            <button className="btn-sm primary" onClick={() => void savePremise()}>保存命题</button>
          )}
        </div>
        <input value={premise} maxLength={500}
          placeholder="如「阿丸第一次掌勺年夜饭」(留空 = AI 自拟)"
          onChange={(e) => setPremise(e.target.value)} />
      </div>

      {/* ---- ① 点子聊天:确认简介后才往下走 ---- */}
      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">① 简介(和 AI 聊出来)</span>
          <ConfirmGate confirmed={confirmed}
            confirmText="✓ 简介就按这个来" confirmedText="已确认 ✓"
            confirmTitle="拍板这版简介,解锁分镜"
            onConfirm={() => { void confirmNow(); }} />
          <span className="grow" />
          <button className="btn-sm" disabled={busy !== ""}
            title="没点子?让 AI 按类型节奏库出三个梗纲,选定即确认"
            onClick={() => void genTakes()}>
            {busy === "takes" ? "出梗纲中…" : "没点子?出三个梗纲"}
          </button>
        </div>
        <p className="hint">
          把你的点子告诉 AI(哪怕只有一句),它补充完善成一版完整简介;不满意接着改,
          满意点「确认简介」——<b>确认之前不会生成任何分镜和提示词</b>。
        </p>

        {episode.chat.length > 0 && (
          <div className="sub-summary" style={{ marginBottom: 8 }}>
            {episode.chat.map((m, i) => (
              <div key={i} style={{ margin: "6px 0" }}>
                <b className="muted">{m.role === "user" ? "我" : "AI"}:</b>
                <span style={{ whiteSpace: "pre-wrap" }}>{String(m.content)}</span>
              </div>
            ))}
          </div>
        )}

        {episode.synopsis && (
          <div className="sub-summary">
            <div className="card-head mb-2">
              <b>当前简介{confirmed ? "" : "(草稿,还没拍板)"}</b>
              <span className="grow" />
              {!confirmed && (
                <button className="btn-sm primary" onClick={() => void confirmNow()}>
                  ✓ 简介就按这个来
                </button>
              )}
            </div>
            <div style={{ whiteSpace: "pre-wrap" }}>{episode.synopsis}</div>
          </div>
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <input value={msg} maxLength={500} style={{ flex: 1 }}
            placeholder={episode.chat.length === 0
              ? "说说这一集想讲什么,如「豆包偷吃年夜饭主菜,阿丸用一颗蛋救场」"
              : "改哪里?如「结尾改成锅盖背锅」"}
            onChange={(e) => setMsg(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void send(msg); } }} />
          {episode.chat.length === 0 && !episode.synopsis ? (
            <button className="primary" disabled={chatBusy} onClick={() => void send(msg)}>
              {chatBusy ? "AI 打磨中…" : "让 AI 出一版简介"}
            </button>
          ) : (
            <button className="primary" disabled={chatBusy || !msg.trim()} onClick={() => void send(msg)}>
              {chatBusy ? "AI 接手中…" : "发送"}
            </button>
          )}
        </div>

        {episode.takes.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div className="hint mb-2">梗纲({chosen ? "已选定其一" : "未选定"}):</div>
            {episode.takes.map((t, i) => (
              <div key={i} className="sub-summary"
                style={episode.chosen === i ? { outline: "2px solid var(--brand, #4a7dff)" } : undefined}>
                <div className="card-head mb-2">
                  <b>梗纲 {i + 1}</b>
                  {episode.chosen === i && <span className="badge">已选定</span>}
                  <span className="grow" />
                  {episode.chosen !== i && (
                    <button className="btn-sm primary" onClick={() => void pick(i)}
                      title="选定 = 确认这条简介,分镜解锁">选定这版</button>
                  )}
                </div>
                <div>{t.logline}</div>
                {t.highlight && <div className="hint">差异点:{t.highlight}</div>}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ---- ② 分镜(简介确认后解锁) ---- */}
      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">② 分镜({shots.length || "0"} 镜)</span>
          <span className="grow" />
          {shotsDirty && <button className="btn-sm primary" onClick={() => void saveShots()}>保存修改</button>}
          <button className="btn-sm" disabled={busy !== "" || !confirmed}
            title={confirmed ? "重出会覆盖当前分镜,提示词也要重出" : "先确认简介,这里才解锁"}
            onClick={() => void genShots()}>
            {busy === "shots" ? "出分镜中…" : hasShots ? "重出分镜" : "展开分镜"}
          </button>
        </div>
        {!hasShots ? (
          <p className="hint">{confirmed
            ? "简介已确认:点「展开分镜」,每镜 2-5 秒,台词动作全开。"
            : "锁着——先在上面把简介确认下来(AI 的每版新简介都会重新上锁)。"}</p>
        ) : (
          <>
            <p className="hint">可以直接改字(画面动作/台词/秒数);改完点「保存修改」,再重出提示词。</p>
            {shots.map((s, i) => (
              <div key={i} className="sub-summary">
                <div className="card-head mb-2">
                  <b>镜 {s.seq}</b>
                  <span className="badge mute">{s.shot_type}</span>
                  <span className="badge mute">{s.camera}</span>
                  <span className="badge mute">{s.duration_s}s</span>
                  {s.characters.length > 0 && (
                    <span className="badge mute">{s.characters.join("、")}</span>
                  )}
                </div>
                <div className="form-grid">
                  <div className="field field-full">
                    <label className="fl">画面动作</label>
                    <textarea rows={2} maxLength={300} value={s.action_desc}
                      onChange={(e) => editShot(i, { action_desc: e.target.value })} />
                  </div>
                  <div className="field">
                    <label className="fl">台词</label>
                    <input maxLength={200} value={s.dialogue} placeholder="没有就留空"
                      onChange={(e) => editShot(i, { dialogue: e.target.value })} />
                  </div>
                  <div className="field">
                    <label className="fl">说话人</label>
                    <input maxLength={30} value={s.speaker} placeholder="与卡司名一致"
                      onChange={(e) => editShot(i, { speaker: e.target.value })} />
                  </div>
                  <div className="field">
                    <label className="fl">时长(秒)</label>
                    <input type="number" min={1} max={9} value={s.duration_s}
                      onChange={(e) => editShot(i, { duration_s: Number(e.target.value) || 3 })} />
                  </div>
                  <div className="field">
                    <label className="fl">音效点</label>
                    <input maxLength={80} value={s.sfx} placeholder="如「锅铲刮底声」"
                      onChange={(e) => editShot(i, { sfx: e.target.value })} />
                  </div>
                </div>
              </div>
            ))}
          </>
        )}
      </div>

      {/* ---- ③ 整集分段提示词 ---- */}
      <FilmPromptCard
        load={() => animeApi.getFilmPrompt(episode.id).then((r) => r.film_prompt)}
        save={(t) => animeApi.saveFilmPrompt(episode.id, t).then((r) => r.film_prompt)}
        generate={() => animeApi.buildFilmPrompt(episode.id, segS)}
        jobKind={`anime-fp-${episode.id}`}
        ready={hasShots}
        readyHint="先确认简介并展开分镜,才有原料组装整集提示词"
        generateDetail="文档已按段切好:逐段复制贴进视频模型,生成完按段号拼接"
        headerExtra={(
          <select value={segS} title="单段时长上限:外部模型单次生成的上限"
            onChange={(e) => setSegS(Number(e.target.value) as 15 | 30)}
            style={{ padding: "2px 6px" }}>
            <option value={15}>单段 ≤15s</option>
            <option value={30}>单段 ≤30s</option>
          </select>
        )}
      />
      <p className="hint">
        提示词由分镜+卡司定妆+画风锚组装,细节全部写死:{meta?.max_shots ?? 40} 镜以内,
        每段开头复述画风锚,出镜角色的定妆逐字注入——跨段形象不漂移。
      </p>
    </section>
  );
}
