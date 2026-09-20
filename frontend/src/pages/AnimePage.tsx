// 动画短剧入口(/anime):系列列表 + 新建系列;系列工作台拆在 panels/anime/。
// 产品心智与角色系列同源——卡司是资产,剧情是耗材:定好 1 主角 + 2-3 配角,
// 每集只管出梗;类型用户自选(节奏库后端下发),提示词逐段复制贴外部模型出片。
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AnimeMeta, AnimeSeries, animeApi } from "../animeApi";
import { toast } from "../ui/Toaster";
import { errMsg } from "../pollJob";
import EmptyState from "../ui/EmptyState";
import { confirmDialog } from "../ui/ConfirmDialog";
import SeriesWorkspace from "../panels/anime/SeriesWorkspace";

export default function AnimePage() {
  const { id } = useParams();
  return id ? <SeriesWorkspace sid={Number(id)} /> : <SeriesList />;
}

// ================= 系列列表 + 新建 =================
function SeriesList() {
  const nav = useNavigate();
  const [meta, setMeta] = useState<AnimeMeta | null>(null);
  const [rows, setRows] = useState<AnimeSeries[] | null>(null);
  const [title, setTitle] = useState("");
  const [premise, setPremise] = useState("");
  const [genre, setGenre] = useState("comedy");
  const [direction, setDirection] = useState("chibi");
  const [episodeS, setEpisodeS] = useState(60);
  const [creating, setCreating] = useState(false);
  // 没灵感:AI 出的系列设定点子(点一条回填设定框)
  const [ideas, setIdeas] = useState<string[]>([]);
  const [ideaBusy, setIdeaBusy] = useState(false);

  const reload = useCallback(async () => {
    try { setRows((await animeApi.list()).series); }
    catch (e) { toast.err("加载失败", errMsg(e)); }
  }, []);
  useEffect(() => {
    void reload();
    animeApi.meta().then(setMeta).catch(() => setMeta(null));
  }, [reload]);

  async function askIdeas() {
    setIdeaBusy(true);
    try {
      setIdeas((await animeApi.suggestPremise(genre)).premises);
      toast.ok("出了三个点子", "点一条填进设定框;不满意换个类型再出");
    } catch (e) { toast.err("出点子失败", errMsg(e)); } finally { setIdeaBusy(false); }
  }

  async function create() {
    if (!premise.trim()) {
      toast.err("先写一句系列设定", "如「饭团精灵阿丸的厨房日常,认真撞上不靠谱」");
      return;
    }
    setCreating(true);
    try {
      const r = await animeApi.create({
        title: title.trim() || "未命名系列",
        premise: premise.trim(), genre, direction, episode_s: episodeS,
      });
      toast.ok("系列已建", "下一步:让 AI 设计固定卡司(1 主角 + 2-3 配角)");
      nav(`/anime/${r.series.id}`);
    } catch (e) { toast.err("创建失败", errMsg(e)); } finally { setCreating(false); }
  }

  async function remove(s: AnimeSeries) {
    const ok = await confirmDialog({
      title: `删除系列「${s.title}」?`,
      body: "全部剧集、卡司与提示词都会一起删掉,不可恢复。",
      confirmText: "确认删除", danger: true,
    });
    if (!ok) return;
    try { await animeApi.remove(s.id); await reload(); }
    catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  const dirInfo = (meta?.directions ?? []).find((d) => d.key === direction);

  return (
    <>
      <div className="page-head">
        <h1>动画短剧</h1>
      </div>

      <section className="card">
        <div className="card-head">
          <h3 className="grow">
            新建一个系列
            <span className="muted">卡司是资产:1 主角 + 2-3 配角定一次,每集只管出新梗</span>
          </h3>
        </div>
        <p className="card-desc">
          像爆笑虫子那样的短集数系列动画:固定卡司、每集一个独立小故事。选好类型与画风,
          AI 按你的一句话设定设计全班人马;之后每集给个情境命题,三选一梗纲 → 分镜 →
          整集分段提示词,逐段复制贴进视频模型(即梦/可灵/Sora)就能出片。
        </p>
        <div className="form-grid">
          <div className="field">
            <label className="fl" htmlFor="an-title">系列名<span className="hint">可后改</span></label>
            <input id="an-title" value={title} maxLength={120}
              onChange={(e) => setTitle(e.target.value)} placeholder="如「饭团小厨房」" />
          </div>
          <div className="field">
            <label className="fl" htmlFor="an-dur">每集时长</label>
            <select id="an-dur" value={episodeS} onChange={(e) => setEpisodeS(Number(e.target.value))}>
              {(meta?.episode_s ?? [60, 90]).map((s) => (
                <option key={s} value={s}>{s} 秒</option>
              ))}
            </select>
          </div>
          <div className="field field-full">
            <label className="fl" htmlFor="an-premise">
              一句话设定<span className="hint">卡司与每集出梗都从它长出来</span>
            </label>
            <input id="an-premise" value={premise} maxLength={500}
              placeholder="如「饭团精灵阿丸的厨房日常,认真撞上不靠谱」"
              onChange={(e) => setPremise(e.target.value)} />
            <div className="form-actions" style={{ margin: 0, marginTop: 6 }}>
              <button className="btn-sm" disabled={ideaBusy} onClick={() => void askIdeas()}>
                {ideaBusy ? "AI 出点子中…" : "🎲 没灵感?AI 出三个点子"}
              </button>
              {ideas.length > 0 && <span className="form-actions-tip">点一条直接填进上面</span>}
            </div>
            {ideas.length > 0 && (
              <div className="chips ideas" style={{ marginTop: 6 }}>
                {ideas.map((p, i) => (
                  <button key={i} type="button" className="chip"
                    title={p} onClick={() => setPremise(p)}>{p}</button>
                ))}
              </div>
            )}
          </div>
          <div className="field field-full">
            <span className="fl">类型<span className="hint">决定每集的节奏套路,出梗时按库展开</span></span>
            <div className="chips">
              {(meta?.genres ?? []).map((g) => (
                <button key={g.key} type="button"
                  className={"chip" + (genre === g.key ? " on" : "")}
                  aria-pressed={genre === g.key}
                  onClick={() => setGenre(g.key)}>{g.label}</button>
              ))}
            </div>
          </div>
          <div className="field field-full">
            <span className="fl">画风<span className="hint">全系列固定;搞笑日常类推荐 Q版沙雕</span></span>
            <div className="chips">
              {(meta?.directions ?? []).map((d) => (
                <button key={d.key} type="button"
                  className={"chip" + (direction === d.key ? " on" : "")}
                  aria-pressed={direction === d.key}
                  onClick={() => setDirection(d.key)}>{d.label}</button>
              ))}
            </div>
            {dirInfo?.tip && <div className="warn-tip">⚠ {dirInfo.tip}</div>}
          </div>
        </div>
        <div className="form-actions">
          <button className="primary" disabled={creating} onClick={create}>
            {creating ? "创建中…" : "建系列"}
          </button>
          <span className="form-actions-tip">建好先别急着写:先让 AI 把卡司定下来,形象才能全季不漂。</span>
        </div>      </section>

      {rows === null ? <p className="muted">加载中…</p> : rows.length === 0 ? (
        <EmptyState>还没有系列。上面建第一个,定好卡司就能出第一集。</EmptyState>
      ) : (
        <section className="card">
          <div className="card-head">
            <h3 className="grow">我的系列<span className="muted">点进去出梗、出分镜、出提示词</span></h3>
          </div>
          {rows.map((s) => (
            <div key={s.id} className="sub-summary ep-row" onClick={() => nav(`/anime/${s.id}`)}>
              <div className="card-head mb-2">
                <b>{s.title}</b>
                <span className="badge mute">{s.genre_label}</span>
                <span className="badge mute">每集 {s.episode_s}s</span>
                <span className="badge">{s.cast.length > 0 ? `卡司 ${s.cast.length} 人` : "待定卡司"}</span>
                <span className="grow" />
                <button className="btn-sm" onClick={(e) => {
                  e.stopPropagation();
                  void remove(s);
                }}>删除</button>
              </div>
              <div className="muted">{s.premise.slice(0, 90)}{s.premise.length > 90 ? "…" : ""}</div>
            </div>
          ))}
        </section>
      )}
    </>
  );
}
