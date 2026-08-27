// 漫剧工坊面板:把已定稿小说改编成漫剧拍摄手册。
// 四步流水线(每步独立可重跑):美术风格卡/资产卡 → 集规划 → 单集剧本 → 分镜 → 三轨提示词,
// 产物拿去即梦/可灵/Midjourney/剪映出片——沿用「只产提示词」哲学,不接生成模型。
// 一致性靠锚段注入:画风锚(风格卡)+ 人物锚(角色卡)+ 场景锚(场景卡)逐字嵌入每格分镜。
// 阶段 2 补齐出片最后一块:声线选型 + 成片包(配音稿/剪辑清单/SRT 字幕)。
import { useCallback, useEffect, useState } from "react";
import {
  ClipPlan,
  DRAMA_STATUS_CN,
  DramaBoardResult,
  DramaCharacterCard,
  DramaDirection,
  DramaDirectionRec,
  DramaEpisode,
  DramaMeta,
  DramaProductionPack,
  DramaRefImage,
  DramaSceneCard,
  DramaShot,
  DramaStyleCard,
  DramaTrailer,
  PasteSet,
  dramaApi,
} from "../../dramaApi";
import { useJob } from "../../ui/useJob";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import EmptyState from "../../ui/EmptyState";
import Banner from "../../ui/Banner";
import StepBar, { Step } from "../../ui/StepBar";
import { CopyBtn, selectAll } from "../../ui/copy";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { DramaGuide, DramaProductionGuide } from "./DramaGuide";

interface Props { pid: number }

// 记住用户上次选的生图站:换一格/刷新页面不用重选(全项目共用一个偏好)
const PASTE_PLATFORM_KEY = "jarvis_drama_paste_platform";
// 视频站的偏好单独存:生图与生视频的 key 空间不同(oneframe/dualbox/mj vs i2v/i2v_en/t2v/r2v),
// 共用一个键会互相把对方的选择顶掉。
const VIDEO_PLATFORM_KEY = "jarvis_drama_video_platform";
// 单次生成时长上限:各视频站不一样(5/10/15 秒),记住用户那家站的档位
const CLIP_LIMIT_KEY = "jarvis_drama_clip_limit";

/** 一键粘贴框:按用户的生图站给「整段能直接粘」的版本。
 *
 *  为什么不让用户自己拼:我们出的是三轨(中文/英文/负面),而生图站长相不一——
 *  只有一个描述框的站(GPT-image / 豆包 / 通义)没处放负面词,照原样复制等于把
 *  负面词丢了。拼装规则在后端(paste.py),导出手册用的是同一份,不会两边跑偏。
 *
 *  生视频那套粘贴版结构完全相同(video.py),所以这个组件同时伺候两边,
 *  只是换个平台偏好键与标题。
 */
function PasteBox({ paste, stale, rows = 5, storeKey = PASTE_PLATFORM_KEY, title = "一键粘贴 · 你用的生图站" }: {
  paste?: PasteSet | null; stale?: boolean; rows?: number; storeKey?: string; title?: string;
}) {
  const [plat, setPlat] = useState(
    () => localStorage.getItem(storeKey) || "oneframe",
  );
  if (!paste) return null;
  const keys = Object.keys(paste);
  if (!keys.length) return null;
  const key = paste[plat] ? plat : keys[0];
  const v = paste[key];
  if (!v.main.trim()) return null;
  return (
    <div className="media-field paste-box">
      <div className="card-head mb-2">
        <span className="muted">{title}</span>
        <select value={key} onChange={(e) => {
          setPlat(e.target.value);
          localStorage.setItem(storeKey, e.target.value);
        }}>
          {keys.map((k) => <option key={k} value={k}>{paste[k].label}</option>)}
        </select>
        <span className="grow" />
        <CopyBtn text={v.main} label="复制整段" />
        {v.negative.trim() && <CopyBtn text={v.negative} label="复制负面词" />}
      </div>
      <textarea rows={rows} readOnly value={v.main} onFocus={selectAll} />
      {v.negative.trim() && (
        <>
          <div className="card-head mb-2 mt-2"><span className="muted">负面词(粘到负面词框)</span></div>
          <textarea rows={2} readOnly value={v.negative} onFocus={selectAll} />
        </>
      )}
      <p className="hint">{v.hint}</p>
      {stale && <p className="hint">提示词改过还没保存——这里是已保存版本,点「保存」后同步。</p>}
    </div>
  );
}

/** 图片缩略图(角色定妆照 / 分镜静帧):读取端点要带 Authorization,<img src> 带不了头,
 *  所以取 blob 转本地 URL。owner 决定读哪条端点——两种资产的挂法/删法一模一样,
 *  只是挂在角色卡上还是挂在分镜格上,不值得复制一份组件。 */
function RefThumb({ pid, owner, id, index, img, alt = "定妆照", onDelete }: {
  pid: number; owner: "card" | "shot"; id: number; index: number;
  img: DramaRefImage; alt?: string; onDelete: () => void;
}) {
  const [url, setUrl] = useState(img.kind === "url" ? img.src : "");
  const [bad, setBad] = useState(false);

  useEffect(() => {
    if (img.kind === "url") { setUrl(img.src); return; }
    let revoke = "";
    let alive = true;
    const read = owner === "card" ? dramaApi.refBlobUrl : dramaApi.shotAssetBlobUrl;
    read(pid, id, index)
      .then((u) => { if (alive) { revoke = u; setUrl(u); } else URL.revokeObjectURL(u); })
      .catch(() => setBad(true));
    return () => { alive = false; if (revoke) URL.revokeObjectURL(revoke); };
  }, [pid, owner, id, index, img.kind, img.src]);

  return (
    <div className="ref-thumb">
      {url && !bad
        ? <img src={url} alt={img.note || alt} onError={() => setBad(true)} />
        : <div className="ref-thumb-bad">{bad ? "图片读不到" : "加载中…"}</div>}
      <div className="ref-thumb-foot">
        <span className="muted">{img.kind === "url" ? "外链" : "已上传"}</span>
        <button className="btn-sm" onClick={onDelete}>删除</button>
      </div>
    </div>
  );
}

/** 选中这一集时,「单集流水线」还差哪一步(状态 → 该点哪个按钮的人话)。 */
function nextEpisodeTodo(ep: DramaEpisode): string {
  const at = `第 ${ep.ep_index} 集`;
  if (ep.status === "planned") return `${at}还没剧本:点 ④-1 写剧本。`;
  if (ep.status === "scripted") return `${at}有剧本了,点 ④-2 拆分镜(把台词摊成一格格画面)。`;
  if (ep.status === "storyboarded") return `${at}有分镜了,点 ④-3 出提示词(每格的绘图提示词)。`;
  return `${at}提示词已就绪:点 ④-4 出成片包(配音稿 + 剪辑清单),再「导出手册」。`;
}

export default function DramaPanel({ pid }: Props) {
  const [meta, setMeta] = useState<DramaMeta | null>(null);
  const [style, setStyle] = useState<DramaStyleCard | null>(null);
  const [cards, setCards] = useState<DramaCharacterCard[]>([]);
  const [scenes, setScenes] = useState<DramaSceneCard[]>([]);
  const [episodes, setEpisodes] = useState<DramaEpisode[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [trailer, setTrailer] = useState<DramaTrailer | null>(null);
  const [loadErr, setLoadErr] = useState("");

  const reloadBase = useCallback(async () => {
    try {
      const [m, s, c, e, t] = await Promise.all([
        dramaApi.meta(pid),
        dramaApi.getStyle(pid),
        dramaApi.getCharacters(pid),
        dramaApi.getEpisodes(pid),
        dramaApi.getTrailer(pid),
      ]);
      setMeta(m);
      setStyle(s.style);
      setCards(c.cards);
      setScenes(c.scenes);
      setEpisodes(e.episodes);
      setTrailer(t.trailer);
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
        <b>漫剧工坊还开不了工:一章都还没定稿。</b>
        <div className="mt-2">
          这里改编的是小说<b>成稿</b>——先去<b>写作区</b>生成章节并点「定稿」,有一章就能开工。
          定稿后回来照着 ①→⑤ 走:定画风 → 出角色卡 → 切集 → 单集(剧本/分镜/提示词/成片包)→ 预告片,
          最后导出一份能直接照着出图、配音、剪辑的拍摄手册。
        </div>
      </EmptyState>
    );
  }

  // 管线步骤状态(步骤条 + 各区小标);第一个未完成的就是「现在做这步」
  const selected = episodes.find((e) => e.id === selectedId) ?? null;
  const steps: Step[] = [
    { key: "style", label: "风格卡", done: !!style?.style_cn,
      todo: "定全片画风:选个方向,点「AI 定美术风格」。" },
    { key: "assets", label: "角色/场景卡", done: cards.length > 0,
      todo: "出角色的锁定外貌段:点「AI 生成资产卡」,人物才不会换脸。" },
    { key: "plan", label: "切集", done: episodes.length > 0,
      todo: "选章节范围,点「切集」——把小说切成一集集短剧。" },
    { key: "episode", label: "单集流水线", done: episodes.some((e) => e.status === "ready"),
      todo: episodes.length === 0
        ? "先切集,再做单集。"
        : selected === null
          ? "在下面的集列表里点任意一集展开,再走 ④-1 → ④-4。"
          : nextEpisodeTodo(selected) },
    { key: "trailer", label: "预告片", done: !!trailer,
      todo: "可选:从各集高能素材混剪一条宣传片。" },
  ];

  return (
    <div className="wb-shell">
      <DramaGuide />

      <StepBar steps={steps} anchorPrefix="drama-step" allDone={<>
        五步都走完了 👏 导出拍摄手册,照下面的「出片指引」去出图/配音/剪辑;
        想做下一批章节就回 ③ 换个章节范围再切集。
      </>} />

      <div className="wb-cols">
        {/* 资产侧栏:桌面常驻左侧(sticky),移动端随流单列 */}
        <aside className="wb-rail">
          <div id="drama-step-style">
            <StyleSection pid={pid} style={style} directions={meta.directions} onSaved={setStyle} />
          </div>
          <div id="drama-step-assets">
            <AssetsSection pid={pid} cards={cards} scenes={scenes}
              onChanged={(c, s) => { setCards(c); setScenes(s); }} />
          </div>
        </aside>

        {/* 主区:切集 → 单集流水线 → 预告片 */}
        <div className="wb-main">
          <div id="drama-step-plan">
            <PlanSection pid={pid} approved={meta.approved_chapters}
              episodes={episodes}
              onChanged={setEpisodes}
              selectedId={selectedId}
              onSelect={setSelectedId} />
          </div>
          <div id="drama-step-episode">
            {selectedId !== null ? (
              <EpisodeDetail pid={pid} eid={selectedId}
                hasStyle={!!style?.style_cn}
                onEpisodesChanged={setEpisodes}
                onDeselect={() => setSelectedId(null)} />
            ) : episodes.length > 0 && (
              <div className="card drama-pick-hint">
                <h3>④ 单集流水线 <span className="muted">先选一集</span></h3>
                <p className="card-desc">
                  ↑ 在上面的集列表里<b>点任意一集</b>,这里就会展开那一集的流水线:
                  ④-1 写剧本 → ④-2 拆分镜 → ④-3 出提示词 → ④-4 出成片包 → 导出手册。
                  建议从第 1 集开始,一集走通了再批量做后面的。
                </p>
                <button className="primary" onClick={() => setSelectedId(episodes[0].id)}>
                  从第 {episodes[0].ep_index} 集开始
                </button>
              </div>
            )}
          </div>
          <div id="drama-step-trailer">
            <TrailerSection pid={pid} episodes={episodes}
              trailer={trailer} onGenerated={setTrailer} />
          </div>
          <DramaProductionGuide />
        </div>
      </div>
    </div>
  );
}

// ================= 预告片 =================
function TrailerSection({ pid, episodes, trailer, onGenerated }: {
  pid: number;
  episodes: DramaEpisode[];
  trailer: DramaTrailer | null;
  onGenerated: (t: DramaTrailer) => void;
}) {
  const { run } = useJob();
  const [fromEp, setFromEp] = useState(1);
  const [toEp, setToEp] = useState(9999);
  const [targetS, setTargetS] = useState(45);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    if (episodes.length) {
      setFromEp(1);
      setToEp(episodes[episodes.length - 1].ep_index);
    }
  }, [episodes]);

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<DramaTrailer>(
        () => dramaApi.generateTrailer(pid, { from_ep: fromEp, to_ep: toEp, target_s: targetS }),
        { kind: `drama-trailer-${pid}`, onStage: setStage },
      );
      if (r) { onGenerated(r); toast.ok("预告片已生成", "炸点开场 + 冲突连切 + 悬念定格"); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  async function exp(fmt: "md" | "srt") {
    try { await dramaApi.exportTrailer(pid, fmt); }
    catch (e) { toast.err("导出失败", errMsg(e)); }
  }

  const epNums = episodes.map((e) => e.ep_index);

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">⑤ 预告片 <span className="muted">高能混剪</span></h3>
        {trailer && (
          <>
            <button className="btn-sm" onClick={() => exp("md")}>导出手册</button>
            <button className="btn-sm" onClick={() => exp("srt")}>字幕SRT</button>
          </>
        )}
      </div>
      <p className="card-desc">
        从各集的钩子/卡点/高能分镜里混剪一条 {targetS} 秒宣传片:炸点开场 → 人设速览 →
        冲突升级连切 → 悬念定格。镜头提示词同样注入画风/角色锚,人物不换脸。
      </p>
      {episodes.length === 0 ? (
        <p className="hint">先「切集」,有了集才能混剪预告片。</p>
      ) : (
        <>
          <div className="form-grid">
            <div className="field">
              <label className="fl" htmlFor="tr-from">从第几集</label>
              <select id="tr-from" value={fromEp}
                onChange={(e) => { const v = Number(e.target.value); setFromEp(v); if (toEp < v) setToEp(v); }}>
                {epNums.map((n) => <option key={n} value={n}>第 {n} 集</option>)}
              </select>
            </div>
            <div className="field">
              <label className="fl" htmlFor="tr-to">到第几集</label>
              <select id="tr-to" value={toEp}
                onChange={(e) => { const v = Number(e.target.value); setToEp(v); if (fromEp > v) setFromEp(v); }}>
                {epNums.map((n) => <option key={n} value={n}>第 {n} 集</option>)}
              </select>
            </div>
            <div className="field">
              <label className="fl" htmlFor="tr-dur">预告片时长</label>
              <select id="tr-dur" value={targetS} onChange={(e) => setTargetS(Number(e.target.value))}>
                <option value={30}>30 秒</option>
                <option value={45}>45 秒</option>
                <option value={60}>60 秒</option>
              </select>
            </div>
          </div>
          <div className="form-actions">
            <button className="primary" disabled={busy} onClick={generate}>
              {trailer ? "重新混剪" : "AI 混剪预告片"}
            </button>
            <span className="form-actions-tip">从这几集里挑高能素材,重新混剪会覆盖上一条。</span>
          </div>
        </>
      )}
      {busy && <Banner stage={stage} text="AI 正在混剪预告片…" />}
      {err && <div className="msg-err">{err}</div>}

      {trailer && !busy && (
        <>
          <div className="sub-summary">
            <div className="card-head mb-2">
              <b>《{trailer.title || "预告片"}》</b>
              <span className="muted">
                {trailer.totals.shots} 格 · 分镜 {trailer.totals.duration_s}s(目标 {trailer.target_s}s)
              </span>
            </div>
            {trailer.lines.length > 0 && (
              <div className="mb-2">
                <div className="card-head mb-2"><b>文案骨架(旁白 + 金句)</b>
                  <CopyBtn text={trailer.lines.map((l) => l.text).join("\n")} label="复制全文" /></div>
                {trailer.lines.map((l, i) => (
                  <div key={i} className="script-line"><b>{l.speaker}</b>:{l.text}</div>
                ))}
              </div>
            )}
            <div className="tbl-wrap">
              <table className="tbl">
                <thead>
                  <tr><th>#</th><th>取材</th><th>场景</th><th>角色</th><th>景别</th><th>运镜</th><th>秒</th><th>画面</th><th>台词</th></tr>
                </thead>
                <tbody>
                  {trailer.shots.map((s) => (
                    <tr key={s.seq}>
                      <td>{s.seq}</td>
                      <td>{s.source_ep ? `第${s.source_ep}集` : "新创"}</td>
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
          </div>
          {trailer.shots.filter((s) => s.prompt_cn || s.prompt_en).map((s) => (
            <div key={s.seq} className="sub-summary">
              <div className="card-head mb-2">
                <b>镜头 {s.seq}{s.source_ep ? `(取材第${s.source_ep}集)` : "(新创)"}</b>
              </div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">中文提示词(即梦/可灵)</span><CopyBtn text={s.prompt_cn} /></div>
                <textarea rows={3} readOnly value={s.prompt_cn} onFocus={selectAll} />
              </div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">英文提示词(Midjourney)</span><CopyBtn text={s.prompt_en} /></div>
                <textarea rows={2} readOnly value={s.prompt_en} onFocus={selectAll} />
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

// ================= 美术风格卡 =================
function StyleSection({ pid, style, directions, onSaved }: {
  pid: number;
  style: DramaStyleCard | null;
  directions: DramaDirection[];
  onSaved: (s: DramaStyleCard) => void;
}) {
  const { run } = useJob();
  const [busy, setBusy] = useState(false);
  const [recBusy, setRecBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  const [draft, setDraft] = useState<DramaStyleCard | null>(style);
  const [direction, setDirection] = useState(style?.direction || "auto");
  const [recs, setRecs] = useState<DramaDirectionRec[]>([]);

  useEffect(() => { setDraft(style); }, [style]);

  const dirInfo = directions.find((d) => d.key === direction);

  async function generate() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<DramaStyleCard>(
        () => dramaApi.generateStyle(pid, direction),
        { kind: `drama-style-${pid}`, onStage: setStage },
      );
      if (r) { onSaved(r); toast.ok("美术风格已定", "这段画风锁定会注入每一格分镜"); }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  async function recommend() {
    setRecBusy(true); setErr("");
    try {
      const r = await run<{ recommendations: DramaDirectionRec[] }>(
        () => dramaApi.recommendDirections(pid),
        { kind: `drama-dirrec-${pid}` },
      );
      if (r) setRecs(r.recommendations);
    } catch (e) { setErr(errMsg(e)); } finally { setRecBusy(false); }
  }

  async function save() {
    if (!draft) return;
    try {
      const r = await dramaApi.saveStyle(pid, { ...draft, direction });
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
        <button className="btn-sm" disabled={recBusy} onClick={recommend}>
          {recBusy ? "推荐中…" : "AI 荐方向"}
        </button>
        {style && <button className="btn-sm" onClick={save}>保存修改</button>}
        <button className="primary" disabled={busy} onClick={generate}>
          {style ? "重新生成" : "AI 定美术风格"}
        </button>
      </div>
      <p className="card-desc">
        先拍板「画风方向」,再让 AI 在这个方向内定「画风锁定段」——之后每一条分镜提示词都会
        逐字嵌入这段锚,上百格画面画风不漂移。生成后可直接改字微调。
      </p>

      {/* 方向推荐:AI 荐、用户选,点一下即采用 */}
      {recs.length > 0 && (
        <div className="sub-summary">
          <div className="card-head mb-2"><b>按本书气质推荐</b>
            <span className="muted">点一条即选中该方向,也可以无视推荐自己挑</span></div>
          {recs.map((r) => (
            <button key={r.key} type="button"
              className={"chip dir-rec" + (direction === r.key ? " on" : "")}
              onClick={() => setDirection(r.key)}>
              <b>{r.priority === 1 ? "★ " : ""}{r.label}</b>
              <span className="muted">{r.reason}</span>
              {r.tip && <span className="warn-tip">⚠ {r.tip}</span>}
            </button>
          ))}
        </div>
      )}

      {/* 方向选择 */}
      <div className="chips board-tabs mb-2">
        {directions.map((d) => (
          <button key={d.key} type="button"
            className={"chip" + (direction === d.key ? " on" : "")}
            onClick={() => setDirection(d.key)}>
            {d.label}
          </button>
        ))}
      </div>
      {dirInfo?.tip && <p className="hint warn-tip">⚠ {dirInfo.tip}</p>}
      {!style && !busy && (
        <p className="hint wb-next">
          <b>第一步就在这儿:</b>拿不定方向就先点「AI 荐方向」看它怎么说,
          定好后点「AI 定美术风格」——没有这张卡,后面的「出提示词」会被拦下。
        </p>
      )}

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

  async function castVoices() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<{ cards: DramaCharacterCard[]; skipped_locked: number }>(
        () => dramaApi.generateVoiceCast(pid),
        { kind: `drama-voice-${pid}`, onStage: setStage },
      );
      if (r) {
        const fresh = await dramaApi.getCharacters(pid);
        onChanged(fresh.cards, scenes);
        toast.ok("声线选型已生成", "每个角色带 TTS 平台选型建议与朗读指示");
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  /** 批量出定妆照提示词:只补还没有的,不覆盖手改过的(要覆盖点单卡「重出提示词」)。 */
  async function refPrompts() {
    setBusy(true); setErr(""); setStage("");
    try {
      const r = await run<{ cards: DramaCharacterCard[]; generated: number; assembled?: number }>(
        () => dramaApi.genRefPrompts(pid),
        { kind: `drama-refsheet-${pid}-all`, onStage: setStage },
      );
      if (r) {
        onChanged(r.cards, scenes);
        // assembled = 模型没给、由引擎按「构图+外貌锚+画风锚」确定性拼的条数。
        // 如实说出来:它照样能用,但用户有权知道哪几条不是 AI 写的、想重出可以重出。
        const made = r.generated + (r.assembled || 0);
        toast.ok(
          made ? `${made} 张定妆照提示词已就绪` : "都已经有了",
          made
            ? (r.assembled
                ? `其中 ${r.assembled} 张由引擎按外貌锚+画风锚拼好(模型这次没给),照样能用;想换写法点那张卡的「重出提示词」`
                : "拿去生图站先出参考图,再上传回来")
            : "想重写某一张,点那张卡上的「重出提示词」",
        );
      }
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); setStage(""); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">② 角色卡与场景卡 <span className="muted">人物一致性</span></h3>
        <button disabled={busy || cards.length === 0} onClick={refPrompts}
          title="先出一张角色参考图,后面每格拿它当参考图,才真锁得住脸">出定妆照</button>
        <button disabled={busy || cards.length === 0} onClick={castVoices}>声线选型</button>
        <button className="primary" disabled={busy} onClick={generate}>
          {cards.length ? "重新生成" : "AI 生成资产卡"}
        </button>
      </div>
      <p className="card-desc">
        从故事圣经批量出「锁定外貌段」:角色出场的那格分镜会逐字嵌入这段描述,
        同一个人不换脸;「出定妆照」再给每个角色出一条参考图提示词——先出一张正面半身定妆照
        上传回来,之后每格「参考图 + 提示词」出图,人物一致性才从文字层落到像素层。
        「声线选型」给每个角色补 TTS 平台选型建议与朗读指示;手动调过的卡点「锁定」,重跑不覆盖。
        每张卡的<b>性别单列一栏</b>——女角色被 AI 写成男的,就在那儿点「女」拍板,
        再点那张卡的「重出这张卡」让 AI 按性别重写(定妆照提示词要一起换,再点「重出提示词」)。
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
      <p className="hint drama-compliance">
        配音合规:请用 TTS 平台的<b>正版音色库</b>;声音受法律保护,<b>切勿克隆他人声音</b>
        (包括「像某明星/某配音演员」的模仿);商用发布前先确认所选音色的<b>商用授权</b>。
      </p>
    </div>
  );
}

// 性别是硬约束:生成/重出时逐字下发给模型,定妆照与每格提示词都跟着它走。
// 单列一栏而不是埋在外貌文本里——埋着的话英文轨(appearance_en)那边根本改不到。
const GENDERS: { key: DramaCharacterCard["gender"]; label: string }[] = [
  { key: "female", label: "女" },
  { key: "male", label: "男" },
  { key: "other", label: "其他" },
  { key: "", label: "未定" },
];
const GENDER_LABEL: Record<string, string> = { female: "女", male: "男", other: "其他", "": "未定" };

function CharCardRow({ pid, card, onSaved }: {
  pid: number; card: DramaCharacterCard; onSaved: (c: DramaCharacterCard) => void;
}) {
  const { run } = useJob();
  const [draft, setDraft] = useState(card);
  const [dirty, setDirty] = useState(false);
  const [refBusy, setRefBusy] = useState(false);
  const [cardBusy, setCardBusy] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [link, setLink] = useState("");

  useEffect(() => { setDraft(card); setDirty(false); }, [card]);

  async function save(extra?: Partial<DramaCharacterCard>, note?: string) {
    try {
      const r = await dramaApi.patchCharacter(pid, card.id, { ...draft, ...extra });
      onSaved(r.card);
      setDirty(false);
      if (note) toast.ok(note);
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  /** 性别点一下就存:它是硬约束,不该跟别的编辑一起攒在「保存」里。 */
  async function pickGender(g: DramaCharacterCard["gender"]) {
    if (g === draft.gender) return;
    setDraft({ ...draft, gender: g });
    await save({ gender: g }, `性别已改为「${GENDER_LABEL[g]}」`);
  }

  /** 只重出这一张卡:按上面拍板的性别重写外貌/服饰/声线(锁定的卡也覆盖)。 */
  async function regenCard() {
    if (dirty && !await confirmDialog({
      title: "这张卡有还没保存的修改",
      body: "重出会用 AI 的新版本覆盖你改的内容。",
      confirmText: "覆盖重出",
      danger: true,
    })) return;
    setCardBusy(true);
    try {
      const r = await run<{ card: DramaCharacterCard }>(
        () => dramaApi.regenCharacter(pid, card.id),
        { kind: `drama-charcard-${pid}-${card.id}` },
      );
      if (r?.card) {
        onSaved(r.card);
        toast.ok(
          `${r.card.name} 的角色卡已重写`,
          (r.card.gender ? `按「${GENDER_LABEL[r.card.gender]}」重写了外貌/服饰/声线;` : "外貌/服饰/声线已重写;")
          + "定妆照提示词不会自动跟着变,要一起换就再点「重出提示词」",
        );
      }
    } catch (e) { toast.err("重出角色卡失败", errMsg(e)); } finally { setCardBusy(false); }
  }

  /** 只给这一张角色重出定妆照提示词(批量按钮只补缺,不覆盖手改过的)。 */
  async function regenRef() {
    setRefBusy(true);
    try {
      const r = await run<{ cards: DramaCharacterCard[]; generated: number; assembled?: number }>(
        () => dramaApi.genRefPrompts(pid, [card.name]),
        { kind: `drama-refsheet-${pid}-${card.name}` },
      );
      const fresh = r?.cards.find((c) => c.id === card.id);
      if (fresh) {
        onSaved(fresh);
        toast.ok(
          `${card.name} 的定妆照提示词已就绪`,
          r?.assembled ? "模型这次没给,已由引擎按外貌锚+画风锚拼好" : "",
        );
      }
    } catch (e) { toast.err("出定妆照提示词失败", errMsg(e)); } finally { setRefBusy(false); }
  }

  async function pickFile(file: File | undefined) {
    if (!file) return;
    setRefBusy(true);
    try {
      const r = await dramaApi.uploadRef(pid, card.id, file);
      onSaved(r.card);
      toast.ok("定妆照已上传", "这一格出图时把它当参考图传给生图站");
    } catch (e) { toast.err("上传失败", errMsg(e)); } finally { setRefBusy(false); }
  }

  async function addLink() {
    const url = link.trim();
    if (!url) { setLinkOpen(false); return; }
    setRefBusy(true);
    try {
      const r = await dramaApi.linkRef(pid, card.id, url);
      onSaved(r.card);
      setLink(""); setLinkOpen(false);
      toast.ok("已记下外链", "生图站链接常带时效,建议下载后改用上传");
    } catch (e) { toast.err("保存外链失败", errMsg(e)); } finally { setRefBusy(false); }
  }

  async function removeRef(index: number) {
    if (!await confirmDialog({ title: "删掉这张定妆照?", confirmText: "删除", danger: true })) return;
    try {
      const r = await dramaApi.deleteRef(pid, card.id, index);
      onSaved(r.card);
    } catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  return (
    <div className="sub-summary">
      <div className="card-head mb-2">
        <b>{draft.name}</b>
        <span className="muted">性别</span>
        <span className="gender-pick">
          {GENDERS.map((g) => (
            <button key={g.key || "none"}
              className={"btn-sm" + (draft.gender === g.key ? " primary" : "")}
              disabled={cardBusy}
              title="性别是硬约束:重出这张卡、出定妆照、出每格提示词都照它写"
              onClick={() => void pickGender(g.key)}>{g.label}</button>
          ))}
        </span>
        <span className="grow" />
        <button className="btn-sm" disabled={cardBusy} onClick={() => void regenCard()}
          title="按上面拍板的性别,让 AI 重写这张卡的外貌/服饰/声线(锁定的卡也会覆盖)">
          {cardBusy ? "重出中…" : "重出这张卡"}
        </button>
        <button className={"btn-sm" + (card.locked ? " primary" : "")}
          onClick={() => save({ locked: !card.locked }, card.locked ? "已解锁" : "已锁定")}>
          {card.locked ? "🔒 已锁定" : "锁定"}
        </button>
        {dirty && <button className="btn-sm primary" onClick={() => save(undefined, "已保存")}>保存</button>}
      </div>
      {/* 描述与拍板的性别打架(常见:女角色被写成「剑眉入鬓/青年男声」)。
          不自动改文字——女扮男装是正当写法,只能提示,由用户判断改哪边。 */}
      {card.gender_conflict && <div className="msg-err mb-2">⚠ {card.gender_conflict}</div>}
      <p className="hint">下面每一栏都能直接改,改完点右上「保存」;性别点一下就存。</p>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">锁定外貌段(注入每格分镜)</span><CopyBtn text={draft.appearance_cn} /></div>
        <textarea rows={3} value={draft.appearance_cn}
          onChange={(e) => { setDraft({ ...draft, appearance_cn: e.target.value }); setDirty(true); }} />
      </div>
      {/* 英文轨也得能改:生图站吃的是这条,只改中文那条等于没改 */}
      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">英文外貌关键词(生图站实际吃这条)</span>
          <CopyBtn text={draft.appearance_en} />
        </div>
        <textarea rows={2} value={draft.appearance_en}
          placeholder="young woman, oval face, pale blue robe…"
          onChange={(e) => { setDraft({ ...draft, appearance_en: e.target.value }); setDirty(true); }} />
      </div>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">标志服饰</span><CopyBtn text={draft.outfit_cn} /></div>
        <input value={draft.outfit_cn}
          onChange={(e) => { setDraft({ ...draft, outfit_cn: e.target.value }); setDirty(true); }} />
      </div>
      <div className="media-field">
        <div className="card-head mb-2"><span className="muted">声线(配音用)</span><CopyBtn text={draft.voice_desc} /></div>
        <input value={draft.voice_desc}
          onChange={(e) => { setDraft({ ...draft, voice_desc: e.target.value }); setDirty(true); }} />
      </div>
      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">定妆照(锁脸的关键一步)</span>
          <span className="grow" />
          <label className="btn-sm" style={{ cursor: "pointer" }}>
            上传参考图
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={refBusy}
              onChange={(e) => { void pickFile(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          <button className="btn-sm" disabled={refBusy} onClick={() => setLinkOpen(!linkOpen)}>贴外链</button>
          <button className="btn-sm" disabled={refBusy} onClick={() => void regenRef()}>
            {refBusy ? "处理中…" : card.ref_prompt_cn ? "重出提示词" : "出定妆照提示词"}
          </button>
        </div>
        <p className="hint">
          文字描述只能把「不像」压小,压不到零。正确做法:先用下面这段生成<b>一张</b>正面半身定妆照,
          上传到这里存好;之后每一格出图,都在生图站点「上传参考图」把它传上去 + 粘这一格的提示词,
          人物才真的前后一致。
        </p>
        {linkOpen && (
          <div className="card-head mb-2">
            <input value={link} placeholder="粘生图站的图片地址(http/https)"
              onChange={(e) => setLink(e.target.value)} />
            <button className="btn-sm primary" disabled={refBusy} onClick={() => void addLink()}>保存</button>
          </div>
        )}
        {draft.ref_images.length > 0 && (
          <div className="ref-thumbs">
            {draft.ref_images.map((img, i) => (
              <RefThumb key={`${img.src}-${i}`} pid={pid} owner="card" id={card.id} index={i}
                img={img} onDelete={() => void removeRef(i)} />
            ))}
          </div>
        )}
        {draft.ref_prompt_cn
          ? <PasteBox paste={draft.ref_paste} rows={4} />
          : <p className="hint">还没有定妆照提示词——点上面「出定妆照提示词」(需要先定好画风)。</p>}
      </div>
      {(draft.tts_hint || draft.reading_notes) && (
        <div className="media-field">
          <div className="card-head mb-2"><span className="muted">TTS 选型建议</span><CopyBtn text={draft.tts_hint} /></div>
          <div className="hint">{draft.tts_hint}</div>
          {draft.reading_notes && <div className="hint">朗读指示:{draft.reading_notes}</div>}
        </div>
      )}
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

// ================= 单集详情:剧本 → 分镜 → 提示词 → 导出 =================
function EpisodeDetail({ pid, eid, hasStyle, onEpisodesChanged, onDeselect }: {
  pid: number; eid: number; hasStyle: boolean;
  onEpisodesChanged: (eps: DramaEpisode[]) => void;
  onDeselect: () => void;
}) {
  const { run } = useJob();
  const [episode, setEpisode] = useState<DramaEpisode | null>(null);
  const [shots, setShots] = useState<DramaShot[]>([]);
  const [pack, setPack] = useState<DramaProductionPack | null>(null);
  const [busy, setBusy] = useState(""); // script | board | prompts | pack | ""
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");
  // 拆分镜的如实交代(被截断 / 总时长短于目标),留在页面上直到下次重拆
  const [boardNotice, setBoardNotice] = useState("");

  const reload = useCallback(async () => {
    try {
      const [r, pk] = await Promise.all([
        dramaApi.getEpisode(pid, eid),
        dramaApi.getPack(pid, eid),
      ]);
      setEpisode(r.episode);
      setShots(r.shots);
      setPack(pk.pack);
    } catch (e) { setErr(errMsg(e)); }
  }, [pid, eid]);

  useEffect(() => { void reload(); }, [reload]);

  async function refreshList() {
    try { onEpisodesChanged((await dramaApi.getEpisodes(pid)).episodes); } catch { /* 列表刷新失败不阻塞 */ }
  }

  async function act(kind: "script" | "board" | "prompts" | "pack", start: () => Promise<{ job_id: string }>, okTitle: string) {
    setBusy(kind); setErr(""); setStage("");
    try {
      const r = await run<unknown>(start, { kind: `drama-${kind}-${eid}`, onStage: setStage });
      if (kind === "board") setBoardNotice((r as DramaBoardResult | null)?.notice || "");
      await reload();
      await refreshList();
      toast.ok(okTitle);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); setStage(""); }
  }

  async function exp(fmt: "md" | "csv" | "json" | "pack" | "srt") {
    try { await dramaApi.exportEpisode(pid, eid, fmt); }
    catch (e) { toast.err("导出失败", errMsg(e)); }
  }

  if (episode === null && !err) return <p className="muted">加载中…</p>;

  const hasScript = !!episode?.script?.lines?.length;
  const sourceLabel = episode?.source_label || `第 ${episode?.source_chapter} 章`;
  const boardTotalS = shots.reduce((s, x) => s + x.duration_s, 0);
  // 施工进度就地算(不等接口回):挂静帧/打勾都是即时改本地 shots,重取一次反而慢半拍
  const stillsDone = shots.filter((s) => s.done_still).length;
  const videosDone = shots.filter((s) => s.done_video).length;
  // 段计划的重算信号:并段只看场景/角色/时长/台词这几栏,别的栏改了不必重取。
  // 静帧挂没挂上也要带——段表的「首帧图已就位」直接读它。
  const clipSig = shots
    .map((s) => `${s.seq}:${s.scene_name}:${s.characters.join(",")}:${s.duration_s}`
      + `:${s.dialogue ? 1 : 0}:${s.motion_cn || ""}:${s.done_still ? 1 : 0}${s.assets?.length ?? 0}`)
    .join("|");

  // 「复制全部中文」:把这一集每格的中文提示词一口气拼起来(镜头号开头,格与格空行隔开)。
  // 用户分工:中文提示词去即梦/可灵出图,几十格一格格复制太磨人;这份整集清单粘到
  // 文档/交给模型/批量出图都能用。其余轨道(英/负/运动)刻意不带,保持出图主链路干净。
  const allCnText = shots
    .filter((s) => (s.prompt_cn || "").trim())
    .map((s) => `镜头 ${s.seq}：${(s.prompt_cn || "").trim()}`)
    .join("\n\n");

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="grow">
          ④ 第 {episode?.ep_index} 集《{episode?.title}》
          <span className="badge">源:{sourceLabel}</span>
          <span className="badge">{episode ? (DRAMA_STATUS_CN[episode.status] ?? episode.status) : ""}</span>
        </h3>
        <button className="btn-sm" onClick={onDeselect}>收起</button>
      </div>
      {err && <div className="msg-err">{err}</div>}

      <div className="card-head mb-2 ep-actions">
        <button className="primary" disabled={!!busy}
          title={`按钩子/卡点把${sourceLabel}的正文写成台词稿`}
          onClick={() => act("script", () => dramaApi.writeScript(pid, eid), "剧本已生成")}>
          {hasScript ? "重写剧本" : "④-1 写剧本"}
        </button>
        <button className="primary" disabled={!!busy || !hasScript}
          title={hasScript ? "把台词摊成一格格可画的画面(会覆盖旧分镜)" : "先写剧本"}
          onClick={() => act("board", () => dramaApi.storyboard(pid, eid), "分镜已生成(旧分镜已覆盖)")}>
          {shots.length ? "重新拆分镜" : "④-2 拆分镜"}
        </button>
        <button className="primary" disabled={!!busy || shots.length === 0}
          title={shots.length ? "给每格出中文/英文/负面三轨绘图提示词" : "先拆分镜"}
          onClick={() => act("prompts", () => dramaApi.prompts(pid, eid), "三轨提示词已生成")}>
          ④-3 出提示词
        </button>
        <button className="primary" disabled={!!busy || shots.length === 0}
          title={shots.length ? "出配音稿 + 剪辑清单(剪映照着走)" : "先拆分镜"}
          onClick={() => act("pack", () => dramaApi.buildPack(pid, eid), "成片包已生成(配音稿 + 剪辑清单)")}>
          {pack ? "重建成片包" : "④-4 出成片包"}
        </button>
        <span className="grow" />
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("md")}>导出手册</button>
        <button className="btn-sm" disabled={!pack} onClick={() => exp("pack")}>成片包</button>
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("srt")}>字幕SRT</button>
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("csv")}>CSV</button>
        <button className="btn-sm" disabled={!shots.length} onClick={() => exp("json")}>JSON</button>
      </div>
      {/* 现在该点哪个 / 为什么点不动:按状态只说一句 */}
      {!busy && <p className="hint wb-next">{episode ? nextEpisodeTodo(episode) : ""}
        {!hasScript && <> 剧本取的是<b>{sourceLabel}</b>的正文,那几章没定稿就会报错。</>}
        {shots.length > 0 && !hasStyle && <> ⚠ 还没定美术风格卡,「出提示词」会被拦下——先回 ① 定画风。</>}
      </p>}
      {busy && <Banner stage={stage} text="AI 正在处理…" />}
      {boardNotice && !busy && <div className="notice notice-warn">分镜说明:{boardNotice}</div>}

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
          <div className="card-head mb-2">
            <b>分镜表({shots.length} 格 · 约 {boardTotalS} 秒)</b>
            <span className="muted">
              目标 {episode?.duration_target_s}s
              {episode && boardTotalS < episode.duration_target_s * 0.8
                ? " · 偏短,可重拆或手动加时长" : ""}
            </span>
          </div>
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
            {/* 逐格施工单的进度条:一集几十格,出到第几格得一眼看见 */}
            <span className="badge">静帧 {stillsDone}/{shots.length}</span>
            <span className="badge">视频 {videosDone}/{shots.length}</span>
            <span className="grow" />
            <CopyBtn
              text={allCnText}
              label="复制全部中文"
              title="把这集所有镜头的中文提示词按镜头号一口气复制,去即梦/可灵批量出图"
            />
            <span className="muted">
              画风锚/角色锚已注入。复制中文提示词去即梦/可灵出图;出好的静帧挂回那一格,
              做完打个勾——导出的施工单会带上这份进度
            </span></div>
          {shots.filter((s) => s.prompt_cn || s.prompt_en).map((s) => (
            <PromptRow key={s.id} pid={pid} shot={s} onSaved={(ns) => {
              // 函数式更新:几十格挂图/打勾是连着点的,拿闭包里的旧 shots 算会把上一格的结果吞掉
              setShots((prev) => prev.map((x) => (x.id === ns.id ? ns : x)));
            }} onRegenerated={() => { void reload(); void refreshList(); }} />
          ))}
        </>
      )}

      {/* 视频段计划:一次生成一段,再在画布里拼(治视频站的单次时长上限) */}
      {shots.length > 0 && <ClipPlanSection pid={pid} eid={eid} sig={clipSig} />}

      {/* 成片包(阶段 2):配音稿 + 剪辑清单 */}
      {pack && (
        <>
          <div className="card-head mb-2">
            <b>成片包</b>
            <span className="muted">
              镜头 {pack.totals.shots} 格 · 分镜 {pack.totals.storyboard_s}s(目标 {pack.totals.target_s}s)
              · 配音估时 {pack.totals.voice_s}s
            </span>
          </div>
          {pack.dubbing.length > 0 && (
            <>
              <div className="card-head mb-2"><b>配音稿({pack.dubbing.length} 条)</b></div>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr><th>#</th><th>说话人</th><th>声线</th><th>朗读文本</th><th>估时/画面</th><th>选型</th></tr>
                  </thead>
                  <tbody>
                    {pack.dubbing.map((d) => (
                      <tr key={d.seq}>
                        <td>{d.seq}</td>
                        <td>{d.speaker}</td>
                        <td>{d.voice}</td>
                        <td>{d.tts_text}</td>
                        <td>{d.est_s}s / {d.shot_duration_s}s</td>
                        <td className="muted">{d.tts_hint}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {pack.narration_full && (
            <div className="sub-summary">
              <div className="card-head mb-2"><b>整段口播(粘给 TTS 一把梭)</b><CopyBtn text={pack.narration_full} /></div>
              <div className="script-line pre-wrap">{pack.narration_full}</div>
            </div>
          )}
          {pack.checklist.length > 0 && (
            <>
              <div className="card-head mb-2"><b>剪辑清单(按镜头顺序)</b></div>
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead>
                    <tr><th>#</th><th>场景</th><th>秒</th><th>字幕</th><th>转场</th><th>配乐</th><th>备注</th></tr>
                  </thead>
                  <tbody>
                    {pack.checklist.map((c) => (
                      <tr key={c.seq}>
                        <td>{c.seq}</td>
                        <td>{c.scene}</td>
                        <td>{c.duration_s}</td>
                        <td>{c.subtitle}</td>
                        <td>{c.transition}</td>
                        <td>{c.bgm_tag}</td>
                        <td className="muted">{c.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="hint">出片顺序:分镜提示词出图(即梦/可灵) → 图生视频/加轻动 → 按配音稿合成语音 → 按剪辑清单拼接 → 压 SRT 字幕 → 铺 BGM。</p>
            </>
          )}
        </>
      )}
    </div>
  );
}

/** 视频段计划:治「视频站一次最多 5-15 秒,分镜格却是 2-8 秒」。
 *
 *  真实做法是「一段生成一次,再在画布/剪映里首尾相接」,所以这里把相邻的格
 *  按四条规则并成段(同场景 / 不引入新角色 / 不超上限 / 最多一条台词),
 *  每段给首帧是哪一格、怎么动、几秒、要压什么字幕。上限换档即时重算(纯确定性)。
 */
function ClipPlanSection({ pid, eid, sig }: { pid: number; eid: number; sig: string }) {
  const [limit, setLimit] = useState(() => Number(localStorage.getItem(CLIP_LIMIT_KEY)) || 10);
  const [plan, setPlan] = useState<ClipPlan | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      setPlan((await dramaApi.getClips(pid, eid, limit)).plan);
      setErr("");
    } catch (e) { setErr(errMsg(e)); }
  }, [pid, eid, limit]);

  // sig 变了 = 分镜/运动轨改过,段计划跟着重算(时长一改,并段结果就不一样了)
  useEffect(() => { void load(); }, [load, sig]);

  function pickLimit(n: number) {
    setLimit(n);
    localStorage.setItem(CLIP_LIMIT_KEY, String(n));
  }

  const options = plan?.options?.length ? plan.options : [5, 10, 15];
  const runs = plan ? plan.totals.segments + plan.totals.extra_runs : 0;

  return (
    <>
      <div className="card-head mb-2">
        <b>让它动起来:视频段计划</b>
        <span className="muted">你那家视频站单次最多能出几秒?</span>
        {options.map((n) => (
          <button key={n} className={"chip" + (limit === n ? " on" : "")}
            onClick={() => pickLimit(n)}>{n} 秒</button>
        ))}
      </div>
      {err && <div className="msg-err">{err}</div>}
      <p className="hint">
        视频站单次只能出 {limit} 秒,而分镜格是 2-8 秒——所以按「一段生成一次」并好段,
        你照段号顺序在画布/剪映里首尾相接就是成片。首帧图用该段第一格出好的静帧,
        人物长相全靠它锁住;提示词里<b>刻意不写外貌</b>(写了模型会重画脸)。
      </p>
      {plan && (
        <>
          <p className="hint wb-next">
            共 <b>{plan.totals.segments}</b> 段 · 合计 <b>{plan.totals.duration_s}</b> 秒 ·
            要生成 <b>{runs}</b> 次 · 首帧图已就位{" "}
            <b>{plan.totals.first_frames_ready}/{plan.totals.segments}</b> 段
            {plan.totals.over_limit > 0
              ? ` · ⚠ 其中 ${plan.totals.over_limit} 段单格就超过 ${limit} 秒,要靠尾帧续接`
              : " · 每段一次出得完"}
          </p>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>段</th><th>含分镜</th><th>场景</th><th>角色</th><th>秒</th><th>生成次数</th><th>首帧</th><th>首帧图</th><th>这一段怎么动</th></tr>
              </thead>
              <tbody>
                {plan.segments.map((seg) => (
                  <tr key={seg.index} className={seg.over_limit ? "row-warn" : ""}>
                    <td>{seg.index}</td>
                    <td>{seg.seqs.join("、")}</td>
                    <td>{seg.scene_name}</td>
                    <td>{seg.characters.join("、") || "(空镜)"}</td>
                    <td>{seg.duration_s}</td>
                    <td>{seg.runs}</td>
                    <td>{seg.first_frame}</td>
                    {/* 这一段能不能开工全看它:段首格的静帧挂上来了才有首帧图可传 */}
                    <td>{seg.first_frame_ready ? "✓ 已挂" : "待出图"}</td>
                    <td>{seg.motion}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {plan.segments.map((seg) => (
            <div key={seg.index} className="sub-summary">
              <div className="card-head mb-2">
                <b>视频段 {seg.index}(分镜 {seg.seqs.join("、")} · {seg.duration_s}s)</b>
                <span className="muted">首帧:{seg.first_frame}</span>
                <span className="badge">{seg.first_frame_ready ? "首帧图已就位" : "首帧图还没挂"}</span>
                {seg.over_limit && <span className="badge">要生成 {seg.runs} 次</span>}
              </div>
              {seg.split_hint && <div className="notice notice-warn">{seg.split_hint}</div>}
              {!seg.first_frame_ready && (
                <p className="hint">
                  先把{seg.first_frame}出好、在上面那一格「挂静帧」挂回来,再拿这段提示词去视频站——
                  没有首帧图就只能走文生视频,人物每段一张脸。
                </p>
              )}
              <PasteBox paste={seg.paste} rows={7}
                storeKey={VIDEO_PLATFORM_KEY} title="一键粘贴 · 你用的视频站" />
              {seg.dialogue && <p className="hint">这一段的字幕/配音:{seg.dialogue}</p>}
            </div>
          ))}
          <p className="hint">{plan.note}</p>
        </>
      )}
    </>
  );
}

function PromptRow({ pid, shot, onSaved, onRegenerated }: {
  pid: number; shot: DramaShot;
  onSaved: (s: DramaShot) => void;
  onRegenerated: () => void;
}) {
  const { run } = useJob();
  const [draft, setDraft] = useState(shot);
  const [dirty, setDirty] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  // 挂静帧那一条:上传/贴外链/删除都会把服务端的这一格整份换回来
  const [assetBusy, setAssetBusy] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [link, setLink] = useState("");

  useEffect(() => { setDraft(shot); setDirty(false); }, [shot]);

  async function save(quiet = false): Promise<boolean> {
    try {
      const r = await dramaApi.patchShot(pid, shot.id, draft);
      onSaved(r.shot);
      setDirty(false);
      if (!quiet) toast.ok(`镜头 ${shot.seq} 已保存`);
      return true;
    } catch (e) { toast.err("保存失败", errMsg(e)); return false; }
  }

  /** 挂素材/打勾之前先把手改的文字落库:这些操作都会用服务端返回的整份分镜刷掉 draft,
   *  不先存就等于把用户刚敲的提示词悄悄吞了。
   *
   *  存失败就返回 false 让调用方**整个操作中止**——照样挂图等于「一句保存失败 + 你的改动
   *  被服务端状态盖掉」,那还不如什么都没发生,让用户先把保存这一步弄好。 */
  async function flush(): Promise<boolean> {
    return dirty ? await save(true) : true;
  }

  async function pickStill(file: File | undefined) {
    if (!file) return;
    setAssetBusy(true);
    try {
      if (!await flush()) return;
      onSaved((await dramaApi.uploadShotAsset(pid, shot.id, file)).shot);
      toast.ok(`镜头 ${shot.seq} 的静帧已挂上`, "已自动勾上「静帧出好了」;这一段的首帧图就用它");
    } catch (e) { toast.err("挂静帧失败", errMsg(e)); } finally { setAssetBusy(false); }
  }

  async function addStillLink() {
    const url = link.trim();
    if (!url) { setLinkOpen(false); return; }
    setAssetBusy(true);
    try {
      if (!await flush()) return;
      onSaved((await dramaApi.linkShotAsset(pid, shot.id, url)).shot);
      setLink(""); setLinkOpen(false);
      toast.ok("已记下静帧外链", "生图站链接常带时效,建议下载后改成上传");
    } catch (e) { toast.err("保存外链失败", errMsg(e)); } finally { setAssetBusy(false); }
  }

  async function removeStill(index: number) {
    if (!await confirmDialog({ title: "删掉这张静帧?", confirmText: "删除", danger: true })) return;
    setAssetBusy(true);
    try {
      if (!await flush()) return;
      onSaved((await dramaApi.deleteShotAsset(pid, shot.id, index)).shot);
    } catch (e) { toast.err("删除失败", errMsg(e)); } finally { setAssetBusy(false); }
  }

  /** 两个打勾栏即点即存(进度条不该还要再点一次「保存」)。
   *  连点要挡住:两条 PATCH 并发回来的顺序不保证,后到的那份会把勾态改回去。 */
  async function tick(body: Partial<DramaShot>) {
    if (assetBusy) return;
    setAssetBusy(true);
    try {
      if (!await flush()) return;
      onSaved((await dramaApi.patchShot(pid, shot.id, body)).shot);
    } catch (e) { toast.err("记进度失败", errMsg(e)); } finally { setAssetBusy(false); }
  }

  /** 只重出这一格:整集重跑慢,还会盖掉别的格手改过的提示词。 */
  async function regen() {
    setBusy(true);
    try {
      const r = await run<{ shot: DramaShot }>(
        () => dramaApi.regenShotPrompt(pid, shot.id, note),
        { kind: `drama-shot-${shot.id}` },
      );
      if (r?.shot) {
        onSaved(r.shot);
        setNoteOpen(false);
        setNote("");
        onRegenerated();
        toast.ok(`镜头 ${shot.seq} 已重出`, note ? "已按你的额外要求重写" : "画风锚/角色锚照旧注入");
      }
    } catch (e) { toast.err("重出失败", errMsg(e)); } finally { setBusy(false); }
  }

  return (
    <div className="sub-summary">
      <div className="card-head mb-2">
        <b>镜头 {draft.seq}({draft.shot_type}/{draft.camera}/{draft.duration_s}s)</b>
        {/* 做到哪儿要能扫着看:几十格一格格翻,不标就得点开每一格数 */}
        {draft.done_still && <span className="badge">静帧✓</span>}
        {draft.done_video && <span className="badge">视频✓</span>}
        <span className="grow" />
        {dirty && <button className="btn-sm primary" onClick={() => void save()}>保存</button>}
        <button className="btn-sm" disabled={busy}
          title="只重生成这一格的提示词,别的格不动"
          onClick={() => (noteOpen ? void regen() : setNoteOpen(true))}>
          {busy ? "重出中…" : noteOpen ? "开始重出" : "重出这格"}
        </button>
        {noteOpen && !busy && (
          <button className="btn-sm" onClick={() => { setNoteOpen(false); setNote(""); }}>取消</button>
        )}
      </div>
      {noteOpen && (
        <div className="media-field">
          <div className="card-head mb-2">
            <span className="muted">这一格想怎么改?(可留空直接重出)</span>
          </div>
          <input value={note} disabled={busy} placeholder="例:改成仰角、雨天、刀锋上有血线"
            onChange={(e) => setNote(e.target.value)} />
        </div>
      )}
      <PasteBox paste={draft.paste} stale={dirty} />
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
      {/* 施工进度:出好的静帧挂回这一格 + 两个打勾栏。
          一集几十格、出图出视频都在本站之外一格格做,做到哪儿全靠脑子记 = 必然做丢或重做。 */}
      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">这一格出好的静帧(挂上来,段计划的首帧图就算就位)</span>
          <span className="grow" />
          <label className="btn-sm" style={{ cursor: "pointer" }}>
            挂静帧
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={assetBusy}
              onChange={(e) => { void pickStill(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
          <button className="btn-sm" disabled={assetBusy}
            onClick={() => setLinkOpen(!linkOpen)}>贴外链</button>
        </div>
        {linkOpen && (
          <div className="card-head mb-2">
            <input value={link} placeholder="粘生图站的图片地址(http/https)"
              onChange={(e) => setLink(e.target.value)} />
            <button className="btn-sm primary" disabled={assetBusy}
              onClick={() => void addStillLink()}>保存</button>
          </div>
        )}
        {(draft.assets?.length ?? 0) > 0 ? (
          <div className="ref-thumbs">
            {(draft.assets || []).map((img, i) => (
              <RefThumb key={`${img.src}-${i}`} pid={pid} owner="shot" id={shot.id} index={i}
                img={img} alt={`镜头 ${draft.seq} 的静帧`} onDelete={() => void removeStill(i)} />
            ))}
          </div>
        ) : (
          <p className="hint">
            还没挂静帧:拿上面的提示词去生图站出图(角色卡的定妆照当参考图),
            出好的那张挂回这里——挂上就自动勾「静帧出好了」,视频段计划里这一段也会亮「首帧图已就位」。
          </p>
        )}
        <div className="shot-ticks">
          <label>
            <input type="checkbox" checked={!!draft.done_still} disabled={assetBusy}
              onChange={(e) => void tick({ done_still: e.target.checked })} />
            静帧出好了
          </label>
          <label>
            <input type="checkbox" checked={!!draft.done_video} disabled={assetBusy}
              onChange={(e) => void tick({ done_video: e.target.checked })} />
            视频出好了
          </label>
          <span className="muted">点一下就存,不用再点「保存」</span>
        </div>
        <div className="card-head mb-2">
          <span className="muted">成片在哪(文件名/目录/外链,剪辑时按这个对号)</span>
        </div>
        <input value={draft.clip_ref || ""} placeholder="例:D:/漫剧/第1集/段3.mp4"
          onChange={(e) => { setDraft({ ...draft, clip_ref: e.target.value }); setDirty(true); }} />
        <p className="hint">
          视频文件刻意不收上传——动辄几十 MB,一集就能吃满整个项目的配额,而剪辑本来就在你本机做,
          站里记住「在哪」就够了(这一栏会一起写进导出的逐格施工单)。
        </p>
      </div>
      {/* 运动轨:出好静帧之后就靠这一条把它动起来 */}
      <div className="paste-box media-field">
        <div className="card-head mb-2">
          <b>让这一格动起来(图生视频)</b>
          <span className="muted">先用上面的提示词出静帧,再把静帧当首帧图传进视频站</span>
        </div>
        <PasteBox paste={draft.video_paste} stale={dirty} rows={6}
          storeKey={VIDEO_PLATFORM_KEY} title="一键粘贴 · 你用的视频站" />
        <div className="media-field">
          <div className="card-head mb-2">
            <span className="muted">怎么动(中文)</span>
            <CopyBtn text={draft.motion_cn || ""} />
          </div>
          <textarea rows={2} value={draft.motion_cn || ""}
            placeholder="例:她抬手抹去刀锋上的雪,镜头缓推,幅度小"
            onChange={(e) => { setDraft({ ...draft, motion_cn: e.target.value }); setDirty(true); }} />
          <p className="hint">
            只写「怎么动」——首帧图已经把长相钉死了,这里再写一遍外貌/服饰/画风,
            模型会照着文字把脸重画一遍,人物一致性当场报废。动得太大就把幅度改小。
          </p>
        </div>
        <div className="media-field">
          <div className="card-head mb-2">
            <span className="muted">怎么动(英文,Runway/Luma/Pika)</span>
            <CopyBtn text={draft.motion_en || ""} />
          </div>
          <textarea rows={2} value={draft.motion_en || ""}
            onChange={(e) => { setDraft({ ...draft, motion_en: e.target.value }); setDirty(true); }} />
        </div>
      </div>
    </div>
  );
}
