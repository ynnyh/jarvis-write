// 剧本工坊(/scripts):独立写剧本 + 小说改编,共用一套集管线。
// 列表(建卡/打开/删除) + 工作台(设定 → AI 分集大纲 → 逐集生成/改稿)。
// 与系列短片页同一套组织:id 在路由上,/scripts 列表、/scripts/:id 工作台。
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Script, ScriptEpisode, scriptsApi } from "../scriptsApi";
import { api, Project } from "../api";
import { toast } from "../ui/Toaster";
import { errMsg } from "../pollJob";
import EmptyState from "../ui/EmptyState";
import { confirmDialog } from "../ui/ConfirmDialog";

export default function ScriptsPage() {
  const { id } = useParams();
  return id ? <ScriptWorkbench sid={Number(id)} /> : <ScriptsList />;
}

const STATUS_CN: Record<string, string> = {
  empty: "未开工", outlined: "已有大纲", drafted: "出稿中",
};
const EP_STATUS_CN: Record<string, string> = {
  empty: "待生成", outlined: "只有大纲", drafted: "已出稿",
};

// 剧本片长估算:中文剧本(含舞台指示)经验值约 350 字/分钟,只做参考标注
function minutesOf(words: number) {
  const m = Math.round(words / 350);
  return m > 0 ? `约 ${m} 分钟` : "";
}

// ================= 列表 =================
function ScriptsList() {
  const nav = useNavigate();
  const [rows, setRows] = useState<Script[] | null>(null);
  const [title, setTitle] = useState("");
  const [genre, setGenre] = useState("");
  const [logline, setLogline] = useState("");
  const [episodes, setEpisodes] = useState(12);
  const [creating, setCreating] = useState(false);
  // ---- 小说改编:选定稿小说 → AI 压缩成改编蓝本 → 分集大纲 → 建剧本 ----
  const [projects, setProjects] = useState<Project[]>([]);
  const [adaptPid, setAdaptPid] = useState(0);
  const [adaptEps, setAdaptEps] = useState(12);
  const [adapting, setAdapting] = useState(false);

  useEffect(() => {
    api.listProjects()
      .then((ps) => { setProjects(ps); if (ps.length > 0) setAdaptPid((v) => v || ps[0].id); })
      .catch(() => setProjects([]));
  }, []);

  async function adapt() {
    if (!adaptPid) return;
    setAdapting(true);
    try {
      const r = await scriptsApi.adapt(adaptPid, { target_episodes: adaptEps });
      toast.ok("分集大纲已按小说生成", "进了剧本工作台,逐集生成正文");
      nav(`/scripts/${r.script_id}`);
    } catch (e) { toast.err("改编失败", errMsg(e)); } finally { setAdapting(false); }
  }

  const reload = useCallback(async () => {
    try { setRows(await scriptsApi.list()); }
    catch (e) { toast.err("加载失败", errMsg(e)); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  async function create() {
    setCreating(true);
    try {
      const s = await scriptsApi.create({
        title: title.trim(), genre: genre.trim(),
        logline: logline.trim(), target_episodes: episodes,
      });
      toast.ok("剧本已建", "先 AI 生成分集大纲,再逐集写正文");
      nav(`/scripts/${s.id}`);
    } catch (e) { toast.err("创建失败", errMsg(e)); } finally { setCreating(false); }
  }

  async function remove(s: Script) {
    const ok = await confirmDialog({
      title: `删除剧本「${s.title}」?`,
      body: "全部分集大纲与剧本正文会一起删掉,不可恢复。",
      confirmText: "确认删除", danger: true,
    });
    if (!ok) return;
    try { await scriptsApi.remove(s.id); await reload(); }
    catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  return (
    <>
      <div className="page-head">
        <h1>剧本工坊</h1>
      </div>
      <p className="muted" style={{ marginTop: -6 }}>
        从一句话故事到分集大纲再到逐集剧本;也可以把写完的小说直接改编成剧(下方入口)。
      </p>

      {projects.length > 0 && (
        <section className="card">
          <div className="card-head">
            <h3 className="grow">从小说改编{" "}<span className="muted">取定稿章压缩成改编蓝本,一次生成分集大纲</span></h3>
          </div>
          <div className="form-grid">
            <div className="field">
              <label className="fl" htmlFor="sc-adapt-book">选小说</label>
              <select id="sc-adapt-book" value={adaptPid}
                onChange={(e) => setAdaptPid(Number(e.target.value))}>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
              </select>
            </div>
            <div className="field">
              <label className="fl" htmlFor="sc-adapt-eps">改成几集<span className="hint">2-60</span></label>
              <input id="sc-adapt-eps" type="number" min={2} max={60} value={adaptEps}
                onChange={(e) => setAdaptEps(Number(e.target.value))} />
            </div>
          </div>
          <div className="actions">
            <button className="primary" disabled={adapting || !adaptPid} onClick={adapt}>
              {adapting ? "改编中,先出大纲,约 1-3 分钟…" : "改编成剧本"}
            </button>
            <span className="muted">没有定稿章的小说改不了;改编后可在工作台重建大纲或逐集生成。</span>
          </div>
        </section>
      )}

      <section className="card">
        <div className="card-head">
          <h3 className="grow">新建一个剧本{" "}<span className="muted">先立设定,大纲和正文交给 AI 分步来</span></h3>
        </div>
        <div className="form-grid">
          <div className="field">
            <label className="fl" htmlFor="sc-title">剧名<span className="hint">之后随时可改</span></label>
            <input id="sc-title" value={title} maxLength={60}
              onChange={(e) => setTitle(e.target.value)} placeholder="如《长夜灯》" />
          </div>
          <div className="field">
            <label className="fl" htmlFor="sc-genre">类型<span className="hint">悬疑 / 年代 / 都市…</span></label>
            <input id="sc-genre" value={genre} maxLength={30}
              onChange={(e) => setGenre(e.target.value)} placeholder="决定对白与节奏的底色" />
          </div>
          <div className="field field-full">
            <label className="fl" htmlFor="sc-log">一句话故事<span className="hint">给 AI 的种子,越具体越好</span></label>
            <input id="sc-log" value={logline} maxLength={200}
              onChange={(e) => setLogline(e.target.value)}
              placeholder="谁,在什么处境下,撞上了什么麻烦" />
          </div>
          <div className="field">
            <label className="fl" htmlFor="sc-eps">目标集数<span className="hint">2-100</span></label>
            <input id="sc-eps" type="number" min={2} max={100} value={episodes}
              onChange={(e) => setEpisodes(Number(e.target.value))} />
          </div>
        </div>
        <div className="actions">
          <button className="primary" disabled={creating} onClick={create}>
            {creating ? "创建中…" : "创建剧本"}
          </button>
        </div>
      </section>

      {rows !== null && rows.length === 0 && (
        <EmptyState>
          还没有剧本。上面立一个设定,或把写完的小说改编成剧。
        </EmptyState>
      )}

      {rows && rows.length > 0 && (
        <section className="card">
          <div className="card-head"><h3 className="grow">我的剧本</h3></div>
          <div className="script-list">
            {rows.map((s) => (
              <div key={s.id} className="script-row">
                <div className="script-row-main">
                  <Link to={`/scripts/${s.id}`} className="script-title">{s.title}</Link>
                  <div className="script-meta">
                    {s.genre && <span className="badge mute">{s.genre}</span>}
                    <span className="badge mute">{STATUS_CN[s.status] ?? s.status}</span>
                    <span className="muted">目标 {s.target_episodes} 集</span>
                    {s.source_project_id && <span className="badge mute">改编自小说</span>}
                  </div>
                  {s.logline && <p className="script-logline">{s.logline}</p>}
                </div>
                <div className="actions">
                  <Link className="btn-sm" to={`/scripts/${s.id}`}>打开</Link>
                  <button className="btn-sm danger" onClick={() => remove(s)}>删除</button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

// ================= 工作台 =================
function ScriptWorkbench({ sid }: { sid: number }) {
  const nav = useNavigate();
  const [script, setScript] = useState<Script | null>(null);
  const [eps, setEps] = useState<ScriptEpisode[] | null>(null);
  // 设定表单(加载后初始化,保存后回写)
  const [genre, setGenre] = useState("");
  const [logline, setLogline] = useState("");
  const [memo, setMemo] = useState("");
  const [targetEps, setTargetEps] = useState(12);
  const [savingMeta, setSavingMeta] = useState(false);
  const [outlining, setOutlining] = useState(false);
  const [genning, setGenning] = useState(0); // 正在生成的集号,0=空闲
  const [openEp, setOpenEp] = useState(0);   // 展开正文的集号
  const [extra, setExtra] = useState("");     // 生成时的补充方向

  const reloadScript = useCallback(async () => {
    try {
      const s = await scriptsApi.get(sid);
      setScript(s);
      setGenre(s.genre); setLogline(s.logline); setMemo(s.style_memo);
      setTargetEps(s.target_episodes);
    } catch (e) {
      toast.err("加载失败", errMsg(e));
    }
  }, [sid]);
  const reloadEps = useCallback(async () => {
    try { setEps(await scriptsApi.episodes(sid)); }
    catch (e) { toast.err("加载失败", errMsg(e)); }
  }, [sid]);
  useEffect(() => { void reloadScript(); void reloadEps(); }, [reloadScript, reloadEps]);

  async function saveMeta() {
    setSavingMeta(true);
    try {
      const s = await scriptsApi.update(sid, {
        genre: genre.trim(), logline: logline.trim(),
        style_memo: memo.trim(), target_episodes: targetEps,
      });
      setScript(s);
      toast.ok("设定已保存", "改了集数的话,重新生成分集大纲时生效");
    } catch (e) { toast.err("保存失败", errMsg(e)); } finally { setSavingMeta(false); }
  }

  async function outline() {
    const has = (eps?.length ?? 0) > 0;
    if (has) {
      const ok = await confirmDialog({
        title: "重新生成分集大纲?",
        body: `会清掉现有 ${eps!.length} 集的大纲与正文,全部重建,不可恢复。`,
        confirmText: "重建大纲", danger: true,
      });
      if (!ok) return;
    }
    setOutlining(true);
    try {
      setEps((await scriptsApi.outline(sid)).episodes);
      await reloadScript();
      toast.ok("分集大纲已生成", "逐集检查,满意一集生成一集");
    } catch (e) { toast.err("大纲生成失败", errMsg(e)); } finally { setOutlining(false); }
  }

  async function genEp(n: number) {
    setGenning(n);
    try {
      const ep = await scriptsApi.genEpisode(sid, n, extra.trim());
      setEps((old) => (old ?? []).map((e) => (e.episode_number === n ? ep : e)));
      setOpenEp(n);
      setExtra("");
      toast.ok(`第 ${n} 集剧本已生成`, "直接在下面改稿,别让 AI 定稿");
    } catch (e) { toast.err(`第 ${n} 集生成失败`, errMsg(e)); } finally { setGenning(0); }
  }

  async function saveEp(n: number, content: string) {
    try {
      const ep = await scriptsApi.saveEpisode(sid, n, { content, status: "drafted" });
      setEps((old) => (old ?? []).map((e) => (e.episode_number === n ? ep : e)));
      toast.ok("已保存", "手改的内容不会被动生成覆盖");
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  async function removeScript() {
    if (!script) return;
    const ok = await confirmDialog({
      title: `删除剧本「${script.title}」?`,
      body: "全部分集与正文一起删除,不可恢复。",
      confirmText: "确认删除", danger: true,
    });
    if (!ok) return;
    try { await scriptsApi.remove(sid); nav("/scripts"); }
    catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  if (!script) return <p className="muted">加载中…</p>;
  const drafted = (eps ?? []).filter((e) => e.status === "drafted").length;

  return (
    <>
      <div className="page-head">
        <h1>{script.title}</h1>
        <Link className="btn ghost" to="/scripts">← 全部剧本</Link>
      </div>
      <p className="muted" style={{ marginTop: -6 }}>
        {script.genre || "未设类型"}
        {" · "}目标 {script.target_episodes} 集
        {(eps?.length ?? 0) > 0 && ` · 已出稿 ${drafted}/${eps!.length} 集`}
        {script.source_project_id && " · 改编自小说"}
        {" · "}
        <button className="btn-sm danger" onClick={removeScript}>删除剧本</button>
      </p>

      <section className="card">
        <div className="card-head">
          <h3 className="grow">剧本设定{" "}<span className="muted">类型与一句话故事会进每一步生成的提示词</span></h3>
        </div>
        <div className="form-grid">
          <div className="field">
            <label className="fl" htmlFor="wb-genre">类型</label>
            <input id="wb-genre" value={genre} maxLength={30}
              onChange={(e) => setGenre(e.target.value)} placeholder="悬疑 / 年代 / 都市…" />
          </div>
          <div className="field">
            <label className="fl" htmlFor="wb-eps">目标集数<span className="hint">重新生成大纲时生效</span></label>
            <input id="wb-eps" type="number" min={2} max={100} value={targetEps}
              onChange={(e) => setTargetEps(Number(e.target.value))} />
          </div>
          <div className="field field-full">
            <label className="fl" htmlFor="wb-log">一句话故事</label>
            <input id="wb-log" value={logline} maxLength={200}
              onChange={(e) => setLogline(e.target.value)} placeholder="谁,在什么处境下,撞上了什么麻烦" />
          </div>
          <div className="field field-full">
            <label className="fl" htmlFor="wb-memo">风格备忘<span className="hint">如「台词短,少形容词;每集结尾留钩子」</span></label>
            <textarea id="wb-memo" rows={3} value={memo} maxLength={500}
              onChange={(e) => setMemo(e.target.value)} />
          </div>
        </div>
        <div className="actions">
          <button className="primary" disabled={savingMeta} onClick={saveMeta}>
            {savingMeta ? "保存中…" : "保存设定"}
          </button>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h3 className="grow">
            分集大纲{" "}
            <span className="muted">{(eps?.length ?? 0) > 0 ? `${eps!.length} 集 · 可整表重建` : "还没生成"}</span>
          </h3>
          <button className="primary" disabled={outlining || genning > 0} onClick={outline}>
            {outlining ? "生成分集大纲中,约 1-3 分钟…" : (eps?.length ?? 0) > 0 ? "重建大纲" : "AI 生成分集大纲"}
          </button>
        </div>
      </section>

      {eps && eps.length > 0 && (
        <section className="card">
          <div className="card-head"><h3 className="grow">分集{" "}<span className="muted">一集一集来:大纲满意再生成正文</span></h3></div>
          <div className="ep-list">
            {eps.map((e) => (
              <EpisodeCard key={e.episode_number} ep={e} sid={sid}
                genning={genning === e.episode_number}
                busy={genning > 0 || outlining}
                open={openEp === e.episode_number}
                onToggle={() => setOpenEp(openEp === e.episode_number ? 0 : e.episode_number)}
                extra={extra} setExtra={setExtra}
                onGen={() => genEp(e.episode_number)}
                onSave={(c) => saveEp(e.episode_number, c)} />
            ))}
          </div>
        </section>
      )}
    </>
  );
}

// 单集卡:大纲信息 + 生成/改稿。正文展开时场景标题行(内景/外景…)高亮,便于扫读结构。
function EpisodeCard({ ep, sid, genning, busy, open, onToggle, extra, setExtra, onGen, onSave }: {
  ep: ScriptEpisode;
  sid: number;
  genning: boolean;
  busy: boolean;
  open: boolean;
  onToggle: () => void;
  extra: string;
  setExtra: (v: string) => void;
  onGen: () => void;
  onSave: (content: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(ep.content);
  useEffect(() => { setDraft(ep.content); }, [ep.content]);

  return (
    <div className="ep-card">
      <div className="ep-head" onClick={onToggle} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter") onToggle(); }}>
        <span className="ep-no">第 {ep.episode_number} 集</span>
        <span className="ep-title">{ep.title}</span>
        <span className={"badge " + (ep.status === "drafted" ? "" : "mute")}>
          {EP_STATUS_CN[ep.status] ?? ep.status}
        </span>
        {ep.word_count > 0 && (
          <span className="muted ep-dur">{ep.word_count} 字{minutesOf(ep.word_count) && ` · ${minutesOf(ep.word_count)}`}</span>
        )}
        <span className="grow" />
        <span className="muted">{open ? "收起 ▴" : "展开 ▾"}</span>
      </div>
      <div className="ep-outline">
        <p>{ep.synopsis}</p>
        <p className="muted ep-hooks">
          {ep.opening_hook && <>开场:{ep.opening_hook}{" · "}</>}
          {ep.ending_hook && <>结尾:{ep.ending_hook}</>}
        </p>
      </div>
      {open && (
        <div className="ep-body">
          {ep.content ? (
            editing ? (
              <>
                <textarea className="ep-editor" rows={18} value={draft}
                  onChange={(e) => setDraft(e.target.value)} />
                <div className="actions">
                  <button className="primary" onClick={() => { onSave(draft); setEditing(false); }}>保存修改</button>
                  <button className="btn ghost" onClick={() => { setDraft(ep.content); setEditing(false); }}>取消</button>
                </div>
              </>
            ) : (
              <>
                <ScriptText content={ep.content} />
                <div className="actions">
                  <button className="btn-sm" onClick={() => setEditing(true)}>改稿</button>
                  <button className="btn ghost" disabled={busy}
                    onClick={async () => {
                      const ok = await confirmDialog({
                        title: `重新生成第 ${ep.episode_number} 集?`,
                        body: "现有正文会被覆盖,不可恢复。手改过的话先复制留底。",
                        confirmText: "重新生成", danger: true,
                      });
                      if (ok) onGen();
                    }}>
                    {genning ? "生成中…" : "重新生成"}
                  </button>
                </div>
              </>
            )
          ) : (
            <div className="ep-gen">
              <label className="fl" htmlFor={`ep-x-${sid}-${ep.episode_number}`}>
                补充方向<span className="hint">可选,如「本集多写市井声」</span></label>
              <input id={`ep-x-${sid}-${ep.episode_number}`} value={extra} maxLength={200}
                onChange={(e) => setExtra(e.target.value)} />
              <div className="actions">
                <button className="primary" disabled={genning || busy} onClick={onGen}>
                  {genning ? "写本集中,约 1-3 分钟…" : "生成本集剧本"}
                </button>
                <button className="btn-sm" onClick={onToggle}>收起</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// 剧本正文渲染:场景标题行(内景/外景/INT./EXT. 开头)高亮成场景块头,其余按原样保留换行。
function ScriptText({ content }: { content: string }) {
  return (
    <div className="script-text">
      {content.split("\n").map((line, i) => {
        const isScene = /^(内景|外景|INT\.|EXT\.|内|外)/i.test(line.trim());
        return isScene && line.trim()
          ? <div key={i} className="scene-line">{line}</div>
          : <div key={i} className={line.trim() ? "" : "blank"}>{line || "\u00A0"}</div>;
      })}
    </div>
  );
}
