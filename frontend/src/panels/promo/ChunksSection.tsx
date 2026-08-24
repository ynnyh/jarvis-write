// ⑥-1 生成切段(主区):按镜头边界并段,每段 ≤N 秒 = 视频站一次生成的量,出完拖上画布拼。
// 段边界只落在镜头边界上、单镜头超上限独立成段并标 ⚠ —— 这两条口径由后端
// media.segments 保证(漫剧/情绪短片同一份),前端只负责把「⚠ 超限该怎么办」说清楚。
import { useState } from "react";
import { PromoPlan } from "../../promoApi";
import Banner from "../../ui/Banner";
import { CopyBtn } from "../../ui/copy";
import { PromoJobs } from "./usePromoJobs";

export default function ChunksSection({ plan, shotCount, jobs, onBuild }: {
  plan: PromoPlan;
  shotCount: number;
  jobs: PromoJobs;
  onBuild: (chunkS: number) => void;
}) {
  const [chunkS, setChunkS] = useState(plan.chunks?.chunk_s ?? 15);
  const items = plan.chunks?.items ?? [];
  const hasShots = shotCount > 0;

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">⑥-1 生成切段 <span className="muted">一段一次生成,画布拼接</span></h3>
      </div>
      <p className="card-desc">
        视频站单次生成有时长上限(常见 5/10/15 秒)。这里按镜头边界把分镜并成不超上限的段——
        绝不在一个镜头中间断开;单镜头本身就超限的段会标 ⚠(降速或分两次生成再接尾帧)。
        时间码与 SRT、剪辑清单同一根轴,直接对齐画布。
      </p>
      <div className="form-grid">
        <div className="field">
          <label className="fl" htmlFor="pc-chunk">
            每段上限<span className="hint">按你用的视频站档位选</span>
          </label>
          <select id="pc-chunk" value={chunkS} onChange={(e) => setChunkS(Number(e.target.value))}>
            <option value={5}>≤5 秒</option>
            <option value={10}>≤10 秒</option>
            <option value={15}>≤15 秒</option>
          </select>
        </div>
      </div>
      <div className="form-actions">
        <button className="primary" disabled={!!jobs.busy || !hasShots} onClick={() => onBuild(chunkS)}>
          {items.length ? "重新切段" : "切段出视频提示词"}
        </button>
        <span className="form-actions-tip">
          {hasShots ? "换档位要重新切段:段边界跟着上限走。" : "先在⑤拆好分镜再来切段。"}
        </span>
      </div>
      {jobs.busy === "chunks" && <Banner stage={jobs.stage} text="AI 正在写分段视频提示词…" />}

      {items.length > 0 && (
        <>
          <div className="card-head mb-2">
            <b>{items.length} 段 · 每段 ≤{plan.chunks.chunk_s}s</b>
            <span className="muted">按段号顺序首尾相接</span>
          </div>
          {items.map((c) => (
            <div key={c.index} className="sub-summary">
              <div className="card-head mb-2">
                <b>段 {c.index}({c.start_s}-{c.end_s}s · {c.duration_s}s)</b>
                <span className="badge">镜头 {c.shot_seqs.join("、")}</span>
                {c.over_limit && <span className="badge warn-tip">⚠ 超限</span>}
              </div>
              <div className="media-field">
                <div className="card-head mb-2">
                  <span className="muted">视频提示词(整段一次生成)</span>
                  <span className="grow" />
                  <CopyBtn text={c.motion_prompt_cn} />
                </div>
                <textarea rows={3} readOnly value={c.motion_prompt_cn} />
              </div>
              <div className="media-field">
                <div className="card-head mb-2">
                  <span className="muted">英文视频提示词</span>
                  <span className="grow" />
                  <CopyBtn text={c.motion_prompt_en} />
                </div>
                <textarea rows={2} readOnly value={c.motion_prompt_en} />
              </div>
              <div className="hint"><b>首帧:</b>{c.first_frame_hint}</div>
              {c.link_note && <div className="hint"><b>拼接:</b>{c.link_note}</div>}
              {c.subtitle && <div className="hint muted">字幕:{c.subtitle.replace(/\n/g, " / ")}</div>}
            </div>
          ))}
        </>
      )}
    </section>
  );
}
