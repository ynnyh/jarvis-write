// 出片工作台:选定本子后,按切段搭一张「执行盘」——每段记参考图(上传/外链)、
// 出片完成与否、成品链接与备注。生日片的参考图更多是寿星真实照片:回忆杀段
// 传照片走图生视频(能力分类参考开源 ai-video-generation 类 skill 的玩法指引,
// 已由后端写进导出手卡;盘内按段给到同一份提示)。
// 段号与手卡 clip.chunks 对齐:手卡改完重算切段后,前端按 index 归并
// (消失的段忽略、新段无状态),后端只认整卡回传,不猜 merge 策略。
import { useCallback, useEffect, useRef, useState } from "react";
import { WishCard, WishShootUnit, birthdayApi } from "../../birthdayApi";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { CopyBtn } from "../../ui/copy";
import { confirmDialog } from "../../ui/ConfirmDialog";
import EmptyState from "../../ui/EmptyState";
import { chunkPromptText } from "./shared";
import { RefThumb } from "../../ui/RefThumb";

export default function ShootWorkbench({ wishId, card, onProgress }: {
  wishId: number; card: WishCard;
  /** 把「已出片段数/总段数」回传给顶层工作台,供步骤条的③出片步判断 done */
  onProgress?: (done: number, total: number) => void;
}) {
  const [units, setUnits] = useState<WishShootUnit[] | null>(null);
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
        const { shoot } = await birthdayApi.getShoot(wishId);
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
        } as WishShootUnit));
        setUnits(merged);
      } catch (e) { toast.err("出片工作台加载失败", errMsg(e)); }
    })();
    return () => { alive = false; };
  }, [wishId, card]);

  // 出片进度有变就回传给顶层(步骤条第③步要靠它判「全出完」)
  useEffect(() => {
    if (!onProgress) return;
    const total = card.chunks?.length ?? 0;
    const done = (units ?? []).filter((u) => u.done).length;
    onProgress(done, total);
  }, [units, card, onProgress]);

  const patch = useCallback((index: number, p: Partial<WishShootUnit>) => {
    setUnits((us) => (us ?? []).map((u) => (u.index === index ? { ...u, ...p } : u)));
  }, []);

  async function persist() {
    // 整卡回送最新盘;并发时后写覆盖先写,但都带的是当时最新快照,结果只增不丢
    setSaving(true);
    try {
      setUnits((await birthdayApi.updateShoot(wishId, units ?? [])).shoot);
    } catch (e) { toast.err("进度保存失败", errMsg(e)); } finally { setSaving(false); }
  }
  // 渲染后把最新 persist 顶进 ref:setTimeout 触发时已拿到本帧闭包(首帧空盘会整体被覆盖)
  useEffect(() => { persistRef.current = persist; });

  // 进度类改动防抖持久化:done/结果链接/备注改一下存一下,别每键一次请求
  const schedulePersist = useCallback(() => {
    if (persistTimer.current) clearTimeout(persistTimer.current);
    persistTimer.current = setTimeout(() => { void persistRef.current(); }, 500);
  }, []);

  async function afterMutate(p: Promise<{ shoot: WishShootUnit[] }>) {
    try { setUnits((await p).shoot); }
    catch (e) { toast.err("操作失败", errMsg(e)); }
  }

  function onRefUpload(index: number, file: File) {
    void afterMutate(birthdayApi.uploadRef(wishId, index, file));
  }

  async function onRefDelete(index: number, imgIndex: number) {
    const ok = await confirmDialog({
      title: "删掉这张参考图?",
      body: "上传的会连文件一起删掉,不可恢复。",
      confirmText: "删除", danger: true,
    });
    if (!ok) return;
    void afterMutate(birthdayApi.deleteRef(wishId, index, imgIndex));
  }

  async function submitLink(index: number) {
    const url = linkVal.trim();
    if (!url) return;
    void afterMutate(birthdayApi.linkRef(wishId, index, url));
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
        按生成切段一段一段出片:普通段粘提示词直接生成;回忆杀段传<b>寿星真实照片</b>当参考图走图生视频,
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
            const hasPhoto = u.ref_images.length > 0;
            return (
              <div key={u.index} className="sub-summary">
                <div className="card-head mb-2">
                  <b>段 {u.index}</b>
                  <span className="muted">({u.start_s}-{u.end_s}s · 镜头 {u.shot_seqs.join("、")})</span>
                  {u.over_limit && <span className="warn-tip"> ⚠超限</span>}
                  {/* 能力指引(参考开源视频 skill 的 t2v/i2v 分类):有参考图的段走图生视频 */}
                  <span className="badge mute">{hasPhoto ? "图生视频 · 照片锚" : "文生视频"}</span>
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
                      <span className="muted">本段提示词(回忆杀段先传寿星照片再粘)</span>
                      <span className="grow" />
                      <CopyBtn text={segText} label="复制段提示词" />
                    </div>
                    <textarea rows={3} readOnly value={segText} />
                  </div>
                )}

                {u.ref_images.length > 0 && (
                  <div className="ref-thumbs">
                    {u.ref_images.map((r, j) => (
                      <RefThumb key={j} image={r}
                        loadBlob={() => birthdayApi.refBlobUrl(wishId, u.index, j)}
                        onDelete={() => void onRefDelete(u.index, j)} />
                    ))}
                  </div>
                )}

                <div className="media-field">
                  <div className="card-head mb-2"><span className="muted">参考图(寿星真实照片优先)</span></div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                    <input ref={(el) => { fileRefs.current[u.index] = el; }}
                      type="file" accept="image/png,image/jpeg,image/webp" hidden
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) onRefUpload(u.index, f);
                        e.target.value = "";
                      }} />
                    <button className="btn-sm" onClick={() => fileRefs.current[u.index]?.click()}>＋ 上传寿星照片/参考图</button>
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
                  <p className="hint">回忆杀段传寿星照片走图生视频(提示词已含「保持参考照片面部特征与体型」);想让照片开口说祝福,把台词拿去对口型工具单独做,成片剪辑时插入。外链可能带时效签名会失效,要长期留存建议下载后上传。</p>
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
                      placeholder="如「用寿星大学照片出的,脸部还原度高」"
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
