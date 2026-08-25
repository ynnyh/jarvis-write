// 出片工作台:选定本子后,按切段搭一张「执行盘」——每段记参考图(上传/外链)、
// 出片完成与否、成品链接与备注,一条片从提示词复制到成片在哪,在这里闭环。
// 段号与手卡 clip.chunks 对齐:手卡改完重算切段后,前端按 index 归并
// (消失的段忽略、新段无状态),后端只认整卡回传,不猜 merge 策略。
import { useCallback, useEffect, useRef, useState } from "react";
import { ClipCard, ClipRefImage, ClipShootUnit, clipsApi } from "../../clipsApi";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { CopyBtn } from "../../ui/copy";
import { confirmDialog } from "../../ui/ConfirmDialog";
import EmptyState from "../../ui/EmptyState";
import { chunkPromptText } from "./shared";

/** 一张段参考图缩略图:上传的走鉴权端点转 blob,外链直接用 src。 */
function RefThumb({ clipId, index, imgIndex, image, canEdit, onDelete }: {
  clipId: number; index: number; imgIndex: number; image: ClipRefImage;
  canEdit: boolean; onDelete: () => void;
}) {
  const [blob, setBlob] = useState<string | null>(null);
  const [bad, setBad] = useState(false);
  useEffect(() => {
    if (image.kind !== "upload") { setBlob(null); setBad(false); return; }
    let alive = true;
    void clipsApi.refBlobUrl(clipId, index, imgIndex)
      .then((u) => { if (alive) setBlob(u); })
      .catch(() => { if (alive) setBad(true); });
    return () => { alive = false; };
  }, [clipId, index, imgIndex, image.kind, image.src]);
  return (
    <div className="ref-thumb">
      {image.kind === "upload"
        ? (blob ? <img src={blob} alt="参考图" /> : <div className="ref-thumb-bad">{bad ? "读取失败" : "加载…"}</div>)
        : <img src={image.src} alt="参考图" />}
      {canEdit && (
        <div className="ref-thumb-foot">
          <button className="btn-sm" onClick={onDelete}>✕ 删</button>
        </div>
      )}
    </div>
  );
}

export default function ShootWorkbench({ clipId, card, onProgress }: {
  clipId: number; card: ClipCard;
  /** 把「已出片段数/总段数」回传给顶层工作台,供步骤条的③出片步判断 done */
  onProgress?: (done: number, total: number) => void;
}) {
  const [units, setUnits] = useState<ClipShootUnit[] | null>(null);
  const [saving, setSaving] = useState(false);
  // 每段「贴外链参考图」的临时输入(回车确认);空串=收着不显示
  const [linkIdx, setLinkIdx] = useState(-1);
  const [linkVal, setLinkVal] = useState("");
  const fileRefs = useRef<Record<number, HTMLInputElement | null>>({});
  const persistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 防抖持久化要从「最新一次 render 的 units」读,闭包直接捕获会拿到首帧的空盘;
  // 用 ref 顶住最新的 persist,setTimeout 里调它,改完 500ms 后再落盘。
  const persistRef = useRef<() => Promise<void>>(async () => {});

  // 载入:后端懒建盘后按当前手卡切段归并,保证段号与展示的切段始终对得上
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const { shoot } = await clipsApi.getShoot(clipId);
        if (!alive) return;
        const byIdx = new Map(shoot.map((u) => [u.index, u]));
        const merged = (card.chunks ?? []).map((c) => ({
          index: c.index, start_s: c.start_s, end_s: c.end_s, duration_s: c.duration_s,
          over_limit: !!c.over_limit, subtitle: c.subtitle ?? "", shot_seqs: c.shot_seqs ?? [],
          scenes: c.scenes ?? [],
          ref_images: byIdx.get(c.index)?.ref_images ?? [],
          done: byIdx.get(c.index)?.done ?? false,
          result_link: byIdx.get(c.index)?.result_link ?? "",
          note: byIdx.get(c.index)?.note ?? "",
        } as ClipShootUnit));
        setUnits(merged);
      } catch (e) { toast.err("出片工作台加载失败", errMsg(e)); }
    })();
    return () => { alive = false; };
  }, [clipId, card]);

  // 出片进度有变就回传给顶层(步骤条第③步要靠它判「全出完」)
  useEffect(() => {
    if (!onProgress) return;
    const total = card.chunks?.length ?? 0;
    const done = (units ?? []).filter((u) => u.done).length;
    onProgress(done, total);
  }, [units, card, onProgress]);

  const patch = useCallback((index: number, p: Partial<ClipShootUnit>) => {
    setUnits((us) => (us ?? []).map((u) => (u.index === index ? { ...u, ...p } : u)));
  }, []);

  async function persist() {
    // 整卡回送最新盘;并发时后写覆盖先写,但都带的是当时最新快照,结果只增不丢
    setSaving(true);
    try {
      setUnits((await clipsApi.updateShoot(clipId, units ?? [])).shoot);
    } catch (e) { toast.err("进度保存失败", errMsg(e)); } finally { setSaving(false); }
  }
  // 渲染后把最新 persist 顶进 ref:setTimeout 触发时已拿到本帧闭包(首帧空盘会整体被覆盖)
  useEffect(() => { persistRef.current = persist; });

  // 进度类改动防抖持久化:done/结果链接/备注改一下存一下,别每键一次请求
  const schedulePersist = useCallback(() => {
    if (persistTimer.current) clearTimeout(persistTimer.current);
    persistTimer.current = setTimeout(() => { void persistRef.current(); }, 500);
  }, []);

  async function afterMutate(p: Promise<{ shoot: ClipShootUnit[] }>) {
    try { setUnits((await p).shoot); }
    catch (e) { toast.err("操作失败", errMsg(e)); }
  }

  function onRefUpload(index: number, file: File) {
    void afterMutate(clipsApi.uploadRef(clipId, index, file));
  }

  async function onRefDelete(index: number, imgIndex: number) {
    const ok = await confirmDialog({
      title: "删掉这张参考图?",
      body: "上传的会连文件一起删掉,不可恢复。",
      confirmText: "删除", danger: true,
    });
    if (!ok) return;
    void afterMutate(clipsApi.deleteRef(clipId, index, imgIndex));
  }

  async function submitLink(index: number) {
    const url = linkVal.trim();
    if (!url) return;
    void afterMutate(clipsApi.linkRef(clipId, index, url));
    setLinkIdx(-1); setLinkVal("");
  }

  const total = card.chunks?.length ?? 0;
  const doneCount = (units ?? []).filter((u) => u.done).length;
  const pct = total ? Math.round((doneCount / total) * 100) : 0;

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">出片工作台<span className="muted">逐段生成 · 传参考图 · 记成片在哪</span></h3>
        {saving && <span className="muted">保存中…</span>}
      </div>
      <p className="card-desc">
        按生成切段一段一段出片:把「角色定妆图 + 本段提示词」搬进图文生视频工具(如 minimax H3),
        生成的成片贴回「成品链接」存个档。段号与手卡切段一致,手卡改过会按新切段自动归并。
      </p>

      {units === null ? (
        <p className="muted">加载出片盘…</p>
      ) : units.length === 0 ? (
        <EmptyState>还没有可出片的切段——先「三选一」选定本子并让手卡就绪。</EmptyState>
      ) : (
        <>
          <div className="gen-progress" style={{ marginBottom: 12 }}>
            <div className="gen-progress-label">{doneCount}/{total} 段已出片 · {pct}%</div>
            <div className="gen-progress-bar">
              <div className="gen-progress-fill" style={{ width: `${pct}%` }} />
            </div>
          </div>

          {units.map((u) => {
            const segText = chunkPromptText(card.shots, u.shot_seqs ?? []);
            return (
              <div key={u.index} className="sub-summary">
                <div className="card-head mb-2">
                  <b>段 {u.index}</b>
                  <span className="muted">({u.start_s}-{u.end_s}s · 镜头 {u.shot_seqs.join("、")})</span>
                  {u.over_limit && <span className="warn-tip"> ⚠超限</span>}
                  <span className="grow" />
                  <label className="shot-ticks" style={{ margin: 0 }}>
                    <input type="checkbox" checked={u.done} title="这段出片完成"
                      onChange={(e) => { patch(u.index, { done: e.target.checked }); schedulePersist(); }} />
                    <span className="muted">出好了</span>
                  </label>
                </div>
                {u.subtitle && <div className="hint"><b>段字幕:</b>{u.subtitle.replace(/\n/g, " / ")}</div>}

                {segText && (
                  <div className="media-field">
                    <div className="card-head mb-2">
                      <span className="muted">本段提示词(先传参考图再粘)</span>
                      <span className="grow" />
                      <CopyBtn text={segText} label="复制段提示词" />
                    </div>
                    <textarea rows={3} readOnly value={segText} />
                  </div>
                )}

                {u.ref_images.length > 0 && (
                  <div className="ref-thumbs">
                    {u.ref_images.map((r, j) => (
                      <RefThumb key={j} clipId={clipId} index={u.index} imgIndex={j} image={r}
                        canEdit onDelete={() => void onRefDelete(u.index, j)} />
                    ))}
                  </div>
                )}

                <div className="media-field">
                  <div className="card-head mb-2"><span className="muted">参考图</span></div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                    <input ref={(el) => { fileRefs.current[u.index] = el; }}
                      type="file" accept="image/png,image/jpeg,image/webp" hidden
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) onRefUpload(u.index, f);
                        e.target.value = "";
                      }} />
                    <button className="btn-sm" onClick={() => fileRefs.current[u.index]?.click()}>＋ 上传参考图</button>
                    {linkIdx === u.index ? (
                      <>
                        <input value={linkVal} maxLength={500} placeholder="https://… (生图站图片地址)"
                          autoFocus
                          onChange={(e) => setLinkVal(e.target.value)}
                          onBlur={() => { setLinkIdx(-1); setLinkVal(""); }}
                          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void submitLink(u.index); } }} />
                        <button className="btn-sm primary" onClick={() => void submitLink(u.index)}>确认</button>
                      </>
                    ) : (
                      <button className="btn-sm" onClick={() => setLinkIdx(u.index)}>＋ 贴链接</button>
                    )}
                  </div>
                  <p className="hint">外链(生图站地址)可能带时效签名会失效;要长期留存建议下载后点「上传参考图」。</p>
                </div>

                <div className="media-field">
                  <div className="card-head mb-2"><span className="muted">成品与备注</span></div>
                  <label className="field">
                    <span className="fl">成品链接</span>
                    <input value={u.result_link} maxLength={500}
                      placeholder="https://… 生成的成片存档"
                      onChange={(e) => { patch(u.index, { result_link: e.target.value }); schedulePersist(); }} />
                  </label>
                  <label className="field">
                    <span className="fl">备注</span>
                    <input value={u.note} maxLength={500}
                      placeholder="如「用定妆卡·模特A 出的,脸部偏年轻,已调」"
                      onChange={(e) => { patch(u.index, { note: e.target.value }); schedulePersist(); }} />
                  </label>
                </div>
              </div>
            );
          })}
        </>
      )}
    </section>
  );
}