// ④ 解说词(主区):按简报的段落契约推进,事实只用素材点。
import { PromoPlan } from "../../promoApi";
import Banner from "../../ui/Banner";
import { CopyBtn } from "../../ui/copy";
import { PromoJobs } from "./usePromoJobs";

export default function ScriptSection({ plan, hasBrief, jobs, onWrite }: {
  plan: PromoPlan;
  hasBrief: boolean;
  jobs: PromoJobs;
  onWrite: () => void;
}) {
  const lines = plan.script?.lines ?? [];
  // 整段解说:配音/TTS 要的是一整段,不是一行行复制
  const fullText = lines.map((l) => l.text).join("\n");

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">④ 解说词 <span className="muted">素材点是唯一事实来源</span></h3>
        {lines.length > 0 && <CopyBtn text={fullText} label="复制全文" />}
        <button className="primary" disabled={!!jobs.busy || !hasBrief} onClick={onWrite}>
          {lines.length ? "重写解说词" : "写解说词"}
        </button>
      </div>
      {jobs.busy === "script" && <Banner stage={jobs.stage} text="AI 正在写解说词…" />}
      {lines.length > 0 ? (
        <div className="sub-summary">
          {plan.script.synopsis && <p className="hint">{plan.script.synopsis}</p>}
          {lines.map((l, i) => (
            <div key={i} className="script-line">
              <b>{l.speaker}</b>:{l.text}
              {l.action && <span className="muted">(画面:{l.action})</span>}
            </div>
          ))}
        </div>
      ) : (
        <p className="hint">
          {hasBrief
            ? "点「写解说词」:按简报段落推进,每段落到具体画面上;事实只用①的素材点。"
            : "要等②简报就绪——解说词是按简报的契约执行的。"}
        </p>
      )}
    </section>
  );
}
