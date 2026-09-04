import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { DramaShot } from "../../dramaApi";
import { dramaApi } from "../../dramaApi";
import type { PrevFrameInfo, RenderConfigOut, RenderTaskOut } from "../../renderApi";
import { checkImageAspect, RENDER_STATUS_CN, renderApi } from "../../renderApi";
import { useJob } from "../../ui/useJob";
import { useProject } from "../../hooks/queries";
import { RefThumb } from "../../ui/RefThumb";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import EmptyState from "../../ui/EmptyState";
import { CopyBtn } from "../../ui/copy";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { PasteBox } from "./PasteBox";
import { EMOTION_OPTIONS, VIDEO_PLATFORM_KEY } from "./dramaShared";

/** 末帧接力候选缩略图:上一镜末帧走鉴权端点,blob 转本地 URL(同图片缩略图)。 */
/** 图片缩略图(角色定妆照 / 分镜静帧):读取端点要带 Authorization,<img src> 带不了头,
 *  所以取 blob 转本地 URL。owner 决定读哪条端点——两种资产的挂法/删法一模一样,
 *  只是挂在角色卡上还是挂在分镜格上,不值得复制一份组件。 */
function PrevFrameThumb({ taskId }: { taskId: number }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let revoke = "";
    let alive = true;
    renderApi.lastFrameBlobUrl(taskId)
      .then((u) => { if (alive) { revoke = u; setUrl(u); } else URL.revokeObjectURL(u); })
      .catch(() => { if (alive) setUrl(""); });
    return () => { alive = false; if (revoke) URL.revokeObjectURL(revoke); };
  }, [taskId]);
  return (
    <div className="ref-thumb">
      {url
        ? <img src={url} alt="上一镜末帧" />
        : <div className="ref-thumb-bad">加载…</div>}
      <div className="ref-thumb-foot"><span className="muted">上一镜末帧</span></div>
    </div>
  );
}
export function PromptRow({ pid, shot, ratio, prevFrame, onSaved, onRegenerated }: {
  pid: number; shot: DramaShot; ratio: string;
  /** 上一镜末帧候选(整集清单里查本格 seq;有出片才有) */
  prevFrame?: PrevFrameInfo;
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
    // 画幅软校验:横竖搞反的图出视频会被大幅裁切,先提醒一句(不拦,用户自己拍板)
    const aspectWarn = await checkImageAspect(file, ratio);
    if (aspectWarn) toast.info("画幅可能不匹配", aspectWarn);
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

  // ===== 本站出片(轻量档):点按钮出视频草片,不出站 =====
  const [renderCfg, setRenderCfg] = useState<RenderConfigOut | null>(null);
  const [renderTasks, setRenderTasks] = useState<RenderTaskOut[]>([]);
  const [renderBusy, setRenderBusy] = useState(false);
  // 正在预览的版本 id;null = 跟随「当前成片指针」自动选
  const [previewId, setPreviewId] = useState<number | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  // 出片模式(项目级;react-query 缓存,几十格共享一份请求)
  const { data: proj } = useProject(pid);
  const renderMode = proj?.render_mode === "full" ? "full" : "lite";

  useEffect(() => {
    let alive = true;
    void renderApi.getConfig().then((c) => { if (alive) setRenderCfg(c); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const reloadRenderTasks = useCallback(async () => {
    try { setRenderTasks((await renderApi.dramaShotTasks(pid, shot.id)).tasks); }
    catch { setRenderTasks([]); }
  }, [pid, shot.id]);
  useEffect(() => { void reloadRenderTasks(); setPreviewId(null); }, [reloadRenderTasks]);

  // 预览跟随:明确点过某版就播那版,否则播「当前成片指针」指向的那版(对不上就播最新成功的)
  const shownTask =
    renderTasks.find((t) => t.id === previewId)
    ?? renderTasks.find((t) => t.status === "success" && t.result_path === (draft.clip_ref || ""))
    ?? renderTasks.find((t) => t.status === "success")
    ?? null;
  useEffect(() => {
    let revoke = "";
    let alive = true;
    if (!shownTask) { setPreviewUrl(""); return; }
    void renderApi.taskBlobUrl(shownTask.id)
      .then((u) => { if (alive) { revoke = u; setPreviewUrl(u); } else URL.revokeObjectURL(u); })
      .catch(() => { if (alive) setPreviewUrl(""); });
    return () => { alive = false; if (revoke) URL.revokeObjectURL(revoke); };
  }, [shownTask]);

  async function renderNow() {
    // 运动轨是出片提示词的原料,手改没保存等于让引擎拿旧词出片
    if (!await flush()) return;
    setRenderBusy(true);
    try {
      const r = await renderApi.submitDramaShot(pid, shot.id);
      await run(() => Promise.resolve({ job_id: r.job_id }),
        { kind: `render:drama:shot:${shot.id}` });
      await reloadRenderTasks();
      setPreviewId(null); // 回到「跟随指针」:新版本就是指针
      onRegenerated(); // 集级刷新:本格新末帧会让下一格亮出「接力」候选
      toast.ok(`镜头 ${shot.seq} 的草片已出`, "不满意直接再点一次「重出一版」;版本都在下方列表里");
    } catch (e) { toast.err("出片失败", errMsg(e)); } finally { setRenderBusy(false); }
  }

  async function adoptRender(taskId: number) {
    try {
      const r = await renderApi.adoptTask(taskId);
      setDraft((d) => ({ ...d, clip_ref: r.clip_ref || d.clip_ref }));
      setDirty(false);
      toast.ok("已设为这一格的成片", "「视频出好了」的勾仍由你自己打");
    } catch (e) { toast.err("设为成片失败", errMsg(e)); }
  }

  /** 末帧接力:上一镜末帧一键挂为本格静帧(服务端复制,不走本地上传)。 */
  async function adoptPrev() {
    setAssetBusy(true);
    try {
      if (!await flush()) return;
      const r = await renderApi.adoptPrevFrame(pid, shot.id);
      onSaved(r.shot);
      onRegenerated();
      toast.ok(`已用第 ${r.from_seq} 格的末帧当本格首帧`, "本格出片即以它开头,镜头自然衔接");
    } catch (e) { toast.err("采纳上一镜末帧失败", errMsg(e)); } finally { setAssetBusy(false); }
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
              <RefThumb key={`${img.src}-${i}`} image={img} alt={`镜头 ${draft.seq} 的静帧`}
                loadBlob={() => dramaApi.shotAssetBlobUrl(pid, shot.id, i)}
                footLeft={<span className="muted">{img.kind === "url" ? "外链" : "已上传"}</span>}
                onDelete={() => void removeStill(i)} />
            ))}
          </div>
        ) : prevFrame ? (
          /* 末帧接力候选:上一格刚出完片,末帧就是现成的本格首帧——一键采纳 */
          <div className="card-head mb-2 relay-offer">
            <PrevFrameThumb taskId={prevFrame.task_id} />
            <span className="muted">
              上一镜(第 {prevFrame.from_seq} 格)出完的末帧——拿它当本格首帧,镜头天然衔接
            </span>
            <span className="grow" />
            <button className="btn-sm primary" disabled={assetBusy}
              onClick={() => void adoptPrev()}>
              {assetBusy ? "采纳中…" : "用上一镜末帧当首帧"}
            </button>
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
        {/* 本站出片(轻量档):不想出站折腾的,点一个按钮直接出视频草片——
            提示词引擎按「只写运动」的口径拼好,有静帧走首尾帧,没静帧走文生视频。 */}
        <div className="media-field">
          <div className="card-head mb-2">
            <b>本站直接出片</b>
            {draft.dialogue?.trim() ? (
              <span className="badge">
                {renderMode === "full" ? "对白出片 · 配音对口型" : "普通出片 · 完整档可配音"}
              </span>
            ) : (
              <span className="badge">
                {(draft.assets?.length ?? 0) > 0 ? "首尾帧 · 静帧当首帧" : "文生视频 · 未挂静帧"}
              </span>
            )}
            <span className="grow" />
            {!!draft.dialogue?.trim() && (
              <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                配音情绪
                <select value={draft.emotion || ""} disabled={assetBusy}
                  title="对白出片时喂给配音的情感;点一下就存"
                  onChange={(e) => void tick({ emotion: e.target.value })}>
                  {EMOTION_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                </select>
              </label>
            )}
            <button className="btn-sm primary" disabled={renderBusy}
              onClick={() => void renderNow()}>
              {renderBusy ? "出片中…(约 1-3 分钟)" : renderTasks.some((t) => t.status === "success") ? "重出一版" : "出片"}
            </button>
          </div>
          {renderCfg && !renderCfg.configured ? (
            <EmptyState>
              还没配置出片引擎:先到 <Link to="/settings">设置 → 出片引擎</Link> 填 autodl.art
              的令牌(费用约 ¥0.02/秒),回来这个按钮就能点了。不想用也行——上面「一键粘贴」
              把提示词搬到即梦/可灵,路还是通的。
            </EmptyState>
          ) : (
            <>
              {previewUrl
                ? <video className="render-preview" src={previewUrl} controls preload="metadata" />
                : <p className="hint">还没出过片:点上面的「出片」,出好的草片会显示在这里。</p>}
              {renderTasks.length > 0 && (
                <div className="card-head mb-2">
                  <span className="muted">版本({renderTasks.length})</span>
                  <select value={String(shownTask?.id ?? "")}
                    onChange={(e) => setPreviewId(Number(e.target.value))}>
                    {renderTasks.map((t) => (
                      <option key={t.id} value={t.id}>
                        #{t.id} · {RENDER_STATUS_CN[t.status] ?? t.status}
                        {t.status === "success" ? ` · ${t.kind === "talk" ? "配音对口型" : ""}${t.params.duration_s ?? "?"}s` : ""}
                        {t.result_path && t.result_path === (draft.clip_ref || "") ? " · 当前成片" : ""}
                      </option>
                    ))}
                  </select>
                  {shownTask?.status === "success" && shownTask.result_path !== (draft.clip_ref || "") && (
                    <button className="btn-sm" onClick={() => void adoptRender(shownTask.id)}>
                      设为成片
                    </button>
                  )}
                </div>
              )}
              <p className="hint">
                有台词的格在<b>完整档</b>下自动「配音+对口型」(先给说话角色传音色参考,见角色卡);
                没音色/轻量档走普通出片。有静帧走首尾帧(长相稳),没静帧走文生视频。
                重 roll 几分钱一次,不满意就连出几版挑;「视频出好了」的勾照旧由你打。
              </p>
            </>
          )}
        </div>
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
