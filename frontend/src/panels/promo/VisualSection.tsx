// ③ 视觉锚(侧栏常驻):画风卡 + 地标卡。
// 为什么和分镜分开、还要常驻侧栏:这两张卡是「一致性锚」——每格提示词都要逐字嵌它们,
// 看分镜/提示词时随手要对照(锚变了,后面所有提示词都得重出)。
// 地标卡跟着简报的段落走(后端要求 brief.positioning 就绪),所以它排在②研讨之后。
import { PromoPlan } from "../../promoApi";
import Banner from "../../ui/Banner";
import { CopyBtn } from "../../ui/copy";
import { PromoJobs } from "./usePromoJobs";

export default function VisualSection({ plan, hasBrief, jobs, onStyle, onLandmarks }: {
  plan: PromoPlan;
  hasBrief: boolean;
  jobs: PromoJobs;
  onStyle: () => void;
  onLandmarks: () => void;
}) {
  const running = jobs.busy === "style" || jobs.busy === "landmarks";
  const landmarks = plan.landmarks ?? [];

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">③ 视觉锚 <span className="muted">画风卡 + 地标卡,一致性靠它们</span></h3>
        <button className="primary" disabled={!!jobs.busy || !plan.subject.trim()} onClick={onStyle}>
          {plan.style_cn ? "重新生成风格" : "生成视觉风格"}
        </button>
        <button disabled={!!jobs.busy || !hasBrief} onClick={onLandmarks}>
          {landmarks.length ? "重新生成地标" : "生成地标卡"}
        </button>
      </div>
      {running && <Banner stage={jobs.stage}
        text={jobs.busy === "style" ? "AI 正在定视觉风格…" : "AI 正在出地标卡…"} />}

      {plan.style_cn ? (
        <div className="sub-summary">
          <div className="card-head mb-2">
            <b>{plan.style_name || "风格卡"}</b>
            <span className="grow" />
            <CopyBtn text={plan.style_cn} />
          </div>
          <div className="hint">{plan.style_cn}</div>
          {plan.style_en && <div className="hint">EN: {plan.style_en}</div>}
          {plan.negative && <div className="hint">负面词: {plan.negative}</div>}
        </div>
      ) : (
        <p className="hint">
          还没定画风。先聊完②简报再生成会更贴基调(基调会喂给风格卡);想先看个大概也可以直接点。
        </p>
      )}

      {landmarks.length > 0 ? (
        <div className="sub-summary">
          <div className="card-head mb-2"><b>地标卡({landmarks.length})</b></div>
          {landmarks.map((l, i) => (
            <div key={i} className="mb-2">
              <b>{l.name}</b>
              <div className="muted">{l.appearance_cn}</div>
            </div>
          ))}
        </div>
      ) : (
        <p className="hint">
          {hasBrief
            ? "地标卡把要拍的地标外观锁死(季节/时段/材质),分镜提示词逐字引用,同一个地方不会两副长相。"
            : "地标卡要等②简报收敛——它跟着简报的段落挑地标。"}
        </p>
      )}
    </section>
  );
}
