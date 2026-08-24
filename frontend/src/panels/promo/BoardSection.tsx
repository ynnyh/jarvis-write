// ⑤ 分镜与三轨提示词(主区):把解说词摊成一格格画面,再给每格出中英静帧提示词。
// 提示词要吃③的画风锚与地标锚(后端注入),所以「出提示词」按钮要求风格卡已就绪。
import { PromoPlan, PromoShot } from "../../promoApi";
import Banner from "../../ui/Banner";
import { CopyBtn } from "../../ui/copy";
import { PromoJobs } from "./usePromoJobs";

export default function BoardSection({ plan, shots, jobs, onBoard, onPrompts }: {
  plan: PromoPlan;
  shots: PromoShot[];
  jobs: PromoJobs;
  onBoard: () => void;
  onPrompts: () => void;
}) {
  const hasScript = (plan.script?.lines?.length ?? 0) > 0;
  const withPrompts = shots.filter((s) => s.prompt_cn || s.prompt_en);
  const totalS = shots.reduce((s, x) => s + x.duration_s, 0);
  const running = jobs.busy === "board" || jobs.busy === "prompts";

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">
          ⑤ 分镜与三轨提示词
          <span className="muted">
            {shots.length ? `${shots.length} 格 · 约 ${totalS} 秒(目标 ${plan.duration_s}s)` : "尚未拆分镜"}
          </span>
        </h3>
        <button className="primary" disabled={!!jobs.busy || !hasScript} onClick={onBoard}>
          {shots.length ? "重新拆分镜" : "拆分镜"}
        </button>
        <button className="primary" disabled={!!jobs.busy || shots.length === 0 || !plan.style_cn}
          onClick={onPrompts}>出提示词</button>
      </div>
      {running && <Banner stage={jobs.stage}
        text={jobs.busy === "board" ? "AI 正在拆分镜…" : "AI 正在出三轨提示词…"} />}

      {shots.length === 0 ? (
        <p className="hint">
          {hasScript
            ? "点「拆分镜」:解说词摊成一格格画面(场景/景别/运镜/秒数/解说对位)。重拆会覆盖旧分镜。"
            : "要等④解说词——分镜是按解说词一句句摊开的。"}
        </p>
      ) : (
        <>
          <div className="tbl-wrap">
            <table className="tbl">
              <thead>
                <tr><th>#</th><th>场景</th><th>景别</th><th>运镜</th><th>秒</th><th>画面</th><th>解说词</th></tr>
              </thead>
              <tbody>
                {shots.map((s) => (
                  <tr key={s.id}>
                    <td>{s.seq}</td><td>{s.scene_name}</td><td>{s.shot_type}</td>
                    <td>{s.camera}</td><td>{s.duration_s}</td>
                    <td>{s.action_desc}</td><td>{s.dialogue}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {withPrompts.length === 0 && (
            <p className="hint">
              {plan.style_cn
                ? "分镜有了,点「出提示词」——每格出中文(即梦/可灵)+ 英文(MJ)静帧提示词,画风锚与地标锚逐字注入。"
                : "出提示词前先去③生成视觉风格:提示词要逐字嵌画风锚,没锚每格一个画风。"}
            </p>
          )}
        </>
      )}

      {withPrompts.map((s) => (
        <div key={s.id} className="sub-summary">
          <div className="card-head mb-2">
            <b>镜头 {s.seq}({s.shot_type}/{s.camera}/{s.duration_s}s)</b>
          </div>
          <div className="media-field">
            <div className="card-head mb-2">
              <span className="muted">静帧中文提示词(即梦/可灵)</span>
              <span className="grow" />
              <CopyBtn text={s.prompt_cn} />
            </div>
            <textarea rows={3} readOnly value={s.prompt_cn} />
          </div>
          <div className="media-field">
            <div className="card-head mb-2">
              <span className="muted">静帧英文提示词(MJ)</span>
              <span className="grow" />
              <CopyBtn text={s.prompt_en} />
            </div>
            <textarea rows={2} readOnly value={s.prompt_en} />
          </div>
          {s.negative && <div className="hint">负面:{s.negative}</div>}
        </div>
      ))}
    </section>
  );
}
