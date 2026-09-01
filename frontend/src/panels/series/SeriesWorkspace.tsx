// 角色系列短片工作台(/series/:cid):顶部主角 tab 切换 + 档案(定妆锚) + 剧集列表。
// 这条线的产品心智与三本子工坊相反——「主角是资产,剧情是耗材」:
// - 档案卡里的定妆描述(look)是全系列一致性的锚,改它会连带影响之后每一集;
// - 每集只要写剧情,生成一条成片提示词(篇幅自由:短到一两百字、长到上千字
//   都收,细节密度按剧情需要——用户明确不设字数强限制)。
// 生成走异步 job(useJob 进任务中心,切走页面也可见);有集在生成中时 3s 轮询刷新。
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { SeriesCharacter, SeriesEpisode, SeriesRefImage, seriesApi } from "../../seriesApi";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { CopyBtn } from "../../ui/copy";
import { confirmDialog } from "../../ui/ConfirmDialog";
import EmptyState from "../../ui/EmptyState";
import { useJob } from "../../ui/useJob";

/** 一张定妆参考图缩略图:上传的走鉴权端点转 blob,外链直接用 src(同 Birthday 的做法)。 */
function RefThumb({ cid, imgIndex, image, onDelete }: {
  cid: number; imgIndex: number; image: SeriesRefImage; onDelete: () => void;
}) {
  const [blob, setBlob] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  useEffect(() => {
    if (image.kind !== "upload") { setBlob(null); setBad(false); return; }
    let alive = true;
    void seriesApi.refBlobUrl(cid, imgIndex)
      .then((u) => { if (alive) setBlob(u); })
      .catch(() => { if (alive) setBad(true); });
    return () => { alive = false; if (blob) URL.revokeObjectURL(blob); };
  }, [cid, imgIndex, image.kind, image.src]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="ref-thumb">
      {image.kind === "upload"
        ? (blob ? <img src={blob} alt="定妆参考图" /> : <div className="ref-thumb-bad">{bad ? "读取失败" : "加载…"}</div>)
        : <img src={image.src} alt="定妆参考图" referrerPolicy="no-referrer" />}
      <div className="ref-thumb-foot">
        {image.note && <span className="muted" style={{ fontSize: "var(--fs-xs)" }}>{image.note}</span>}
        <button className="btn-sm" onClick={onDelete}>✕ 删</button>
      </div>
    </div>
  );
}

export default function SeriesWorkspace({ cid }: { cid: number }) {
  const nav = useNavigate();
  const job = useJob();
  const [chars, setChars] = useState<SeriesCharacter[] | null>(null);
  const [character, setCharacter] = useState<SeriesCharacter | null>(null);
  const [episodes, setEpisodes] = useState<SeriesEpisode[]>([]);
  const [loaded, setLoaded] = useState(false);
  // ---- 档案编辑态(null = 不在编辑;进入编辑时从当前 character 拷一份) ----
  const [draft, setDraft] = useState<{ look: string; direction: string; default_duration_s: number; style_hints: string } | null>(null);
  const [saving, setSaving] = useState(false);
  // ---- 参考图:上传/贴外链 ----
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [linkVal, setLinkVal] = useState("");
  // ---- 新建一集 ----
  const [plot, setPlot] = useState("");
  const [duration, setDuration] = useState(10);
  const [creating, setCreating] = useState(false);
  // ---- 编辑已生成输出(eid | null) ----
  const [editEid, setEditEid] = useState<number | null>(null);
  const [editOut, setEditOut] = useState({ title: "", prompt_cn: "", negative: "" });

  const reload = useCallback(async () => {
    try {
      const r = await seriesApi.getCharacter(cid);
      setCharacter(r.character_row);
      // 展示按集序(创建顺序)正排——「系列」的心智是第 1 集、第 2 集…
      setEpisodes([...r.episodes].sort((a, b) => a.id - b.id));
      setDuration(r.character_row.default_duration_s);
    } catch (e) { toast.err("加载失败", errMsg(e)); }
  }, [cid]);

  useEffect(() => {
    setLoaded(false); setDraft(null); setEditEid(null);
    void reload().finally(() => setLoaded(true));
    // tab 列表独立拉(切主角 tab 不用等详情)
    seriesApi.listCharacters()
      .then((r) => setChars(r.characters))
      .catch(() => setChars([]));
  }, [cid, reload]);

  // 有集在生成中:3s 轮询刷新,直到没有 generating 为止(与其它工坊同一节奏)
  const generating = episodes.some((e) => e.status === "generating");
  useEffect(() => {
    if (!generating) return;
    const t = setInterval(() => { void reload(); }, 3000);
    return () => clearInterval(t);
  }, [generating, reload]);

  // ================= 档案 =================
  async function saveProfile() {
    if (!character || !draft) return;
    if (!draft.look.trim()) { toast.err("定妆描述不能为空", "它是全系列一致性的锚"); return; }
    setSaving(true);
    try {
      const r = await seriesApi.patchCharacter(cid, {
        look: draft.look.trim(), direction: draft.direction,
        default_duration_s: draft.default_duration_s, style_hints: draft.style_hints,
      });
      setCharacter(r.character_row);
      setDraft(null);
      toast.ok("档案已保存", "之后每一集都会按这份定妆生成");
    } catch (e) { toast.err("保存失败", errMsg(e)); } finally { setSaving(false); }
  }

  // ================= 参考图 =================
  async function uploadRef(file: File) {
    if (!character) return;
    try {
      const r = await seriesApi.uploadRef(cid, file);
      setCharacter(r.character_row);
      toast.ok("参考图已上传", "出片时丢给图生视频当人物锚");
    } catch (e) { toast.err("上传失败", errMsg(e)); }
  }

  async function linkRef() {
    if (!character) return;
    const url = linkVal.trim();
    if (!url) { toast.err("先贴图片地址", "生图站出的定妆照链接"); return; }
    try {
      const r = await seriesApi.linkRef(cid, url);
      setCharacter(r.character_row);
      setLinkVal("");
      toast.ok("外链已添加", "带时效签名的链接会过期,建议下载后上传");
    } catch (e) { toast.err("添加失败", errMsg(e)); }
  }

  async function deleteRef(imgIndex: number) {
    if (!character) return;
    const ok = await confirmDialog({
      title: "删掉这张定妆参考图?",
      body: "上传的会连文件一起删掉,不可恢复。",
      confirmText: "确认删除", danger: true,
    });
    if (!ok) return;
    try {
      const r = await seriesApi.deleteRef(cid, imgIndex);
      setCharacter(r.character_row);
    } catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  // ================= 剧集 =================
  async function createEpisode() {
    if (!plot.trim()) { toast.err("先写这一集的剧情", "一句话到一段话都行"); return; }
    setCreating(true);
    try {
      await seriesApi.createEpisode(cid, plot.trim(), duration);
      setPlot("");
      await reload();
      toast.ok("已建一集", "点「生成提示词」出成片提示词");
    } catch (e) { toast.err("建集失败", errMsg(e)); } finally { setCreating(false); }
  }

  async function generate(eid: number) {
    try {
      await job.run(() => seriesApi.generateEpisode(eid), { kind: "series-gen" });
      await reload();
      toast.ok("提示词已生成", "可直接复制投喂图生视频");
    } catch (e) { toast.err("生成失败", errMsg(e)); }
  }

  async function removeEpisode(eid: number) {
    const ok = await confirmDialog({
      title: "删掉这一集?",
      body: "剧情与已生成的提示词都会一起删掉,不可恢复。",
      confirmText: "确认删除", danger: true,
    });
    if (!ok) return;
    try {
      await seriesApi.removeEpisode(eid);
      await reload();
    } catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  async function removeCharacter() {
    if (!character) return;
    const ok = await confirmDialog({
      title: `删除主角「${character.name}」?`,
      body: "全部剧集与定妆参考图都会一起删掉,不可恢复。",
      confirmText: "确认删除", danger: true,
    });
    if (!ok) return;
    try {
      await seriesApi.removeCharacter(cid);
      nav("/series");
    } catch (e) { toast.err("删除失败", errMsg(e)); }
  }

  function startEditOutput(ep: SeriesEpisode) {
    const o = (ep.output ?? {}) as { title?: string; prompt_cn?: string; negative?: string };
    setEditEid(ep.id);
    setEditOut({ title: o.title ?? "", prompt_cn: o.prompt_cn ?? "", negative: o.negative ?? "" });
  }

  async function saveOutput(eid: number) {
    try {
      await seriesApi.patchEpisode(eid, { output: editOut });
      setEditEid(null);
      await reload();
      toast.ok("输出已更新");
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  // 一键复制全部:按集序正排、空行分隔、仅中文提示词(全站公约)
  const doneEps = episodes.filter((e) => e.status === "done" && (e.output as Record<string, string>)?.prompt_cn);
  const allPrompts = doneEps
    .map((e) => (e.output as Record<string, string>).prompt_cn)
    .join("\n\n");

  const epNo = (ep: SeriesEpisode) => episodes.findIndex((x) => x.id === ep.id) + 1;

  if (!loaded) return <p className="muted">加载中…</p>;
  if (!character) return <EmptyState>这个主角不存在(或已被删除)。</EmptyState>;

  return (
    <>
      <div className="page-head">
        <h1>系列短片 · {character.name}</h1>
        <div className="grow" />
        <CopyBtn text={allPrompts} label="复制全部提示词" title="按集序复制全部已生成的中文提示词(空行分隔)" />
        <button className="btn" onClick={() => nav("/series")}>← 全部主角</button>
      </div>

      {/* 主角 tab:资产制的核心入口——新想到一个主角就加一个,下次直接点进来写剧情 */}
      <div className="tabs">
        {(chars ?? []).map((c) => (
          <button key={c.id}
            className={"tab" + (c.id === cid ? " on" : "")}
            onClick={() => { if (c.id !== cid) nav(`/series/${c.id}`); }}>
            {c.name}
          </button>
        ))}
        <button className="tab" onClick={() => nav("/series")}>＋ 新主角</button>
      </div>

      {/* ============ 档案(定妆锚) ============ */}
      <section className="card">
        <div className="card-head">
          <h3 className="grow">主角档案<span className="muted">定妆描述是全系列一致性的锚,每一集逐字复用</span></h3>
          {draft ? (
            <>
              <button className="btn-sm" onClick={() => setDraft(null)}>取消</button>
              <button className="btn-sm primary" disabled={saving} onClick={saveProfile}>
                {saving ? "保存中…" : "保存档案"}
              </button>
            </>
          ) : (
            <>
              <button className="btn-sm" onClick={() => setDraft({
                look: character.look, direction: character.direction,
                default_duration_s: character.default_duration_s, style_hints: character.style_hints,
              })}>编辑</button>
              <button className="btn-sm" onClick={removeCharacter}>删除主角</button>
            </>
          )}
        </div>
        {draft ? (
          <div className="form-grid">
            <div className="field field-full">
              <label className="fl" htmlFor="sw-look">
                定妆描述<span className="hint">长短不拘,写全关键项即可;改动会影响之后每一集</span>
              </label>
              <textarea id="sw-look" rows={8} maxLength={2000} value={draft.look}
                onChange={(e) => setDraft({ ...draft, look: e.target.value })} />
            </div>
            <div className="field field-full">
              <label className="fl" htmlFor="sw-hints">氛围关键词<span className="hint">可选;会融进每集的画面与光线</span></label>
              <input id="sw-hints" maxLength={80} value={draft.style_hints}
                onChange={(e) => setDraft({ ...draft, style_hints: e.target.value })} />
            </div>
            <div className="field">
              <label className="fl" htmlFor="sw-dur">默认时长(秒)</label>
              <input id="sw-dur" type="number" min={5} max={15} value={draft.default_duration_s}
                onChange={(e) => setDraft({ ...draft, default_duration_s: Number(e.target.value) })} />
            </div>
          </div>
        ) : (
          <>
            <p className="card-desc" style={{ whiteSpace: "pre-wrap" }}>{character.look}</p>
            <div className="chips" style={{ marginBottom: 8 }}>
              <span className="badge mute">{character.direction_label}</span>
              <span className="badge mute">默认 {character.default_duration_s}s</span>
              {character.style_hints && <span className="badge mute">{character.style_hints}</span>}
            </div>
          </>
        )}

        {/* 定妆参考图:文生图出的定妆照,出片时丢给图生视频当人物锚 */}
        <div className="field">
          <span className="fl">定妆参考图<span className="hint">文生图出的定妆照;出片时上传给图生视频锁人物形象</span></span>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {(character.ref_images ?? []).map((img, i) => (
              <RefThumb key={`${img.kind}-${img.src}`} cid={cid} imgIndex={i} image={img}
                onDelete={() => void deleteRef(i)} />
            ))}
          </div>
          <div className="clip-edit-line" style={{ marginTop: 8 }}>
            <input placeholder="贴定妆照外链(建议下载后上传,外链会过期)" value={linkVal}
              onChange={(e) => setLinkVal(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void linkRef(); }} />
            <button className="btn-sm" onClick={() => void linkRef()}>贴外链</button>
            <button className="btn-sm" onClick={() => fileInput.current?.click()}>上传图片</button>
            <input ref={fileInput} type="file" accept="image/*" hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void uploadRef(f);
                e.target.value = "";
              }} />
          </div>
        </div>
      </section>

      {/* ============ 新建一集 ============ */}
      <section className="card">
        <div className="card-head">
          <h3 className="grow">写一集新剧情<span className="muted">主角形象自动复用定妆,只管讲故事</span></h3>
        </div>
        <div className="form-grid">
          <div className="field field-full">
            <label className="fl" htmlFor="sw-plot">
              剧情<span className="hint">一句话到一段话都行;AI 会按定妆展开成成片提示词(长短按剧情需要)</span>
            </label>
            <textarea id="sw-plot" rows={3} maxLength={1000} value={plot}
              placeholder="如「小浣熊盯上了货架最上层的蜂蜜罐,踮脚、晃罐、最后一屁股坐在地上稳稳接住」"
              onChange={(e) => setPlot(e.target.value)} />
          </div>
          <div className="field">
            <label className="fl" htmlFor="sw-ep-dur">时长(秒)</label>
            <input id="sw-ep-dur" type="number" min={5} max={15} value={duration}
              onChange={(e) => setDuration(Number(e.target.value))} />
          </div>
        </div>
        <div className="form-actions">
          <button className="primary" disabled={creating} onClick={createEpisode}>
            {creating ? "建集中…" : "建一集"}
          </button>
          <span className="form-actions-tip">建完点「生成提示词」;不合适可改剧情重新生成。</span>
        </div>
      </section>

      {/* ============ 剧集列表 ============ */}
      {episodes.length === 0 ? (
        <EmptyState>还没有剧集。上面写一段剧情,建第一集。</EmptyState>
      ) : episodes.map((ep) => {
        const out = (ep.output ?? {}) as { title?: string; prompt_cn?: string; negative?: string };
        const editing = editEid === ep.id;
        return (
          <section key={ep.id} className="card">
            <div className="card-head">
              <h3 className="grow">
                第 {epNo(ep)} 集{out.title ? `《${out.title}》` : ""}
                <span className="badge mute">{ep.duration_s}s</span>
                <span className={"badge" + (ep.status === "done" ? " ok" : ep.status === "generating" ? " run" : "")}>
                  {ep.status_cn}
                </span>
              </h3>
              {ep.status === "done" && !editing && (
                <button className="btn-sm" onClick={() => startEditOutput(ep)}>编辑输出</button>
              )}
              {editing && (
                <>
                  <button className="btn-sm" onClick={() => setEditEid(null)}>取消</button>
                  <button className="btn-sm primary" onClick={() => void saveOutput(ep.id)}>保存输出</button>
                </>
              )}
              <button className="btn-sm primary" disabled={ep.status === "generating"}
                onClick={() => void generate(ep.id)}>
                {ep.status === "generating" ? "生成中…" : ep.status === "done" ? "重新生成" : "生成提示词"}
              </button>
              <button className="btn-sm" disabled={ep.status === "generating"}
                onClick={() => void removeEpisode(ep.id)}>删除</button>
            </div>

            <p className="card-desc">{ep.plot}</p>

            {editing ? (
              <div className="form-grid">
                <div className="field">
                  <label className="fl" htmlFor={`eo-title-${ep.id}`}>标题</label>
                  <input id={`eo-title-${ep.id}`} maxLength={24} value={editOut.title}
                    onChange={(e) => setEditOut({ ...editOut, title: e.target.value })} />
                </div>
                <div className="field">
                  <label className="fl" htmlFor={`eo-neg-${ep.id}`}>负面词(画面)</label>
                  <input id={`eo-neg-${ep.id}`} maxLength={200} value={editOut.negative}
                    onChange={(e) => setEditOut({ ...editOut, negative: e.target.value })} />
                </div>
                <div className="field field-full">
                  <label className="fl" htmlFor={`eo-prompt-${ep.id}`}>成片提示词</label>
                  <textarea id={`eo-prompt-${ep.id}`} rows={10} value={editOut.prompt_cn}
                    onChange={(e) => setEditOut({ ...editOut, prompt_cn: e.target.value })} />
                </div>
              </div>
            ) : ep.status === "done" && out.prompt_cn ? (
              <>
                <div className="sub-summary" style={{ whiteSpace: "pre-wrap" }}>{out.prompt_cn}</div>
                {out.negative && (
                  <div className="muted" style={{ marginTop: 6 }}>
                    负面词:{out.negative}
                    <CopyBtn text={out.negative} label="复制负面词" />
                  </div>
                )}
                <div className="form-actions">
                  <CopyBtn text={out.prompt_cn} label="复制提示词" />
                  <span className="form-actions-tip">
                    连同定妆参考图一起投喂图生视频(minimax 等),人物形象即可锁住。
                  </span>
                </div>
              </>
            ) : null}
          </section>
        );
      })}
    </>
  );
}
