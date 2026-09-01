// 角色系列短片入口(/series):主角列表 + 新建主角表单;单主角工作台拆在 panels/series/。
// 与生日/短片工坊同一套组织:这里只留列表与建卡;建卡的核心是「一句话概念 →
// AI 代写定妆」——定妆描述是全系列一致性的锚,主角是资产,剧情是耗材。
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { SeriesCharacter, SeriesMeta, seriesApi } from "../seriesApi";
import { toast } from "../ui/Toaster";
import { errMsg } from "../pollJob";
import EmptyState from "../ui/EmptyState";
import { confirmDialog } from "../ui/ConfirmDialog";
import SeriesWorkspace from "../panels/series/SeriesWorkspace";

export default function SeriesPage() {
  const { id } = useParams();
  return id ? <SeriesWorkspace cid={Number(id)} /> : <SeriesList />;
}

// ================= 主角列表 + 新建 =================
function SeriesList() {
  const nav = useNavigate();
  const [meta, setMeta] = useState<SeriesMeta | null>(null);
  const [rows, setRows] = useState<SeriesCharacter[] | null>(null);
  // ---- 新建主角:概念 → AI 代写定妆 → 确认保存 ----
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  const [look, setLook] = useState("");
  const [direction, setDirection] = useState("render3d");
  const [duration, setDuration] = useState(10);
  const [styleHints, setStyleHints] = useState("");
  const [drafting, setDrafting] = useState(false); // AI 代写中(同步长调用)
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    try { setRows((await seriesApi.listCharacters()).characters); }
    catch (e) { toast.err("加载失败", errMsg(e)); }
  }, []);
  useEffect(() => {
    void reload();
    seriesApi.meta()
      .then((m) => {
        setMeta(m);
        if (!m.directions.some((d) => d.key === direction)) {
          setDirection(m.directions[0]?.key ?? "");
        }
      })
      .catch(() => setMeta(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload]);

  async function draft() {
    if (!brief.trim()) { toast.err("先写一句主角概念", "如「一只戴红围巾、爱囤零食的小浣熊」"); return; }
    setDrafting(true);
    try {
      const r = await seriesApi.draftLook(brief.trim(), direction, styleHints.trim());
      setLook(r.look);
      toast.ok("定妆草稿已生成", "不满意可手改,或改概念再点一次");
    } catch (e) { toast.err("代写失败", errMsg(e)); } finally { setDrafting(false); }
  }

  async function create() {
    if (!name.trim()) { toast.err("先给主角起个名字", "如「小浣熊」"); return; }
    if (!look.trim()) { toast.err("定妆描述不能为空", "写一句概念后点「AI 代写」,或直接手写"); return; }
    setCreating(true);
    try {
      const r = await seriesApi.createCharacter({
        name: name.trim(), look: look.trim(), direction,
        default_duration_s: duration, style_hints: styleHints.trim(),
      });
      toast.ok("主角已建", "进去写第一集剧情吧");
      nav(`/series/${r.character_row.id}`);
    } catch (e) { toast.err("创建失败", errMsg(e)); } finally { setCreating(false); }
  }

  async function remove(c: SeriesCharacter) {
    const ok = await confirmDialog({
      title: `删除主角「${c.name}」?`,
      body: "全部剧集与定妆参考图都会一起删掉,不可恢复。",
      confirmText: "确认删除", danger: true,
    });
    if (!ok) return;
    try { await seriesApi.removeCharacter(c.id); await reload(); }
    catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  const dirInfo = (meta?.directions ?? []).find((d) => d.key === direction);
  const maxDur = meta?.max_duration_s ?? 15;
  const minDur = meta?.min_duration_s ?? 5;

  return (
    <>
      <div className="page-head">
        <h1>系列短片</h1>
      </div>

      <section className="card">
        <div className="card-head">
          <h3 className="grow">
            新建一个固定主角
            <span className="muted">主角是资产:建一次,之后每集只写剧情</span>
          </h3>
        </div>
        <p className="card-desc">
          想到一个小浣熊、一只小老虎?把它建成主角档案——AI 按你的一句话概念写出
          细节够认脸的定妆描述(长短不拘,也可手写),之后每一集都逐字复用这份形象,
          出片时配上定妆参考图走图生视频,人物形象就锁住了。每集 5-15 秒,
          只写剧情,一键出成片提示词。
        </p>
        <div className="form-grid">
          <div className="field">
            <label className="fl" htmlFor="sr-name">主角名字<span className="hint">如「小浣熊 / 虎子」</span></label>
            <input id="sr-name" value={name} maxLength={60}
              onChange={(e) => setName(e.target.value)} placeholder="tab 上显示的就是它" />
          </div>
          <div className="field">
            <label className="fl" htmlFor="sr-dur">默认时长(秒)</label>
            <input id="sr-dur" type="number" min={minDur} max={maxDur} value={duration}
              onChange={(e) => setDuration(Number(e.target.value))} />
          </div>
          <div className="field field-full">
            <label className="fl" htmlFor="sr-brief">
              一句话概念<span className="hint">AI 按它代写定妆;不许加概念之外的设定</span>
            </label>
            <input id="sr-brief" value={brief} maxLength={500}
              placeholder="如「一只戴红围巾、爱囤零食的小浣熊,在杂货店里讨生活」"
              onChange={(e) => setBrief(e.target.value)} />
          </div>
          <div className="field field-full">
            <span className="fl">画风<span className="hint">全系列固定;定妆参考图也按它生成</span></span>
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
          <div className="field field-full">
            <label className="fl" htmlFor="sr-hints">氛围关键词<span className="hint">可选;如「市井烟火、暖光」</span></label>
            <input id="sr-hints" value={styleHints} maxLength={80}
              onChange={(e) => setStyleHints(e.target.value)}
              placeholder="不填则 AI 按画风自定氛围" />
          </div>
          <div className="field field-full">
            <label className="fl" htmlFor="sr-look">
              定妆描述<span className="hint">长短不拘,写全关键项即可;点「AI 代写」生成后可手改</span>
            </label>
            <textarea id="sr-look" rows={6} maxLength={2000} value={look}
              placeholder="写好上面的一句话概念后点「AI 代写」,或直接手写定妆描述"
              onChange={(e) => setLook(e.target.value)} />
          </div>
        </div>
        <div className="form-actions">
          <button disabled={drafting} onClick={() => void draft()}>
            {drafting ? "AI 代写中…" : "AI 代写定妆"}
          </button>
          <button className="primary" disabled={creating} onClick={create}>
            {creating ? "创建中…" : "建主角"}
          </button>
          <span className="form-actions-tip">定妆是全系列一致性的锚,建好之后还能在档案里改。</span>
        </div>
      </section>

      {rows === null ? <p className="muted">加载中…</p> : rows.length === 0 ? (
        <EmptyState>还没有主角。上面建第一个,写一段剧情就能出第一集。</EmptyState>
      ) : (
        <section className="card">
          <div className="card-head">
            <h3 className="grow">我的主角<span className="muted">点进去写剧情,每集只管讲故事</span></h3>
          </div>
          {rows.map((c) => (
            <div key={c.id} className="sub-summary ep-row" onClick={() => nav(`/series/${c.id}`)}>
              <div className="card-head mb-2">
                <b>{c.name}</b>
                <span className="badge mute">{c.direction_label}</span>
                <span className="badge mute">默认 {c.default_duration_s}s</span>
                <span className="grow" />
                <button className="btn-sm" onClick={(e) => {
                  e.stopPropagation();
                  void remove(c);
                }}>删除</button>
              </div>
              <div className="muted">{c.look.slice(0, 90)}{c.look.length > 90 ? "…" : ""}</div>
            </div>
          ))}
        </section>
      )}
    </>
  );
}
