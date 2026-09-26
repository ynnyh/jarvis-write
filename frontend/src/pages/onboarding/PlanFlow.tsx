// PlanFlow — 方案屏(docs/22 P0,确认链 L0 新形态):三问定纲 → 整书方案×3 → 拍板。
// 替代简介逐格访谈:细节确认密度更高(三套方案每套 9 个格子全由 AI 写满),
// 用户的显式动作收敛为「点候选 → 选方案 → 拍板」。
//   ① 三问:每问 3-4 候选 + ★首推(带理由);「全部按推荐来」一键;跳过 = AI 定
//   ② 方案墙:三张卡点选;[改改再选] 一句话定向修订(只改这张卡);[再来三套] 可带话
//   ③ 拍板:选中方案渲染成开书订单(后端 brief_confirmed,概念深化的硬门)
// 已拍板回访:显示订单摘要 + 撤回拍板(回到方案墙,工作集还在)。
import { useEffect, useRef, useState } from "react";
import { BookPlan, ThreeQuestions } from "../../api";
import { ConfirmGate } from "../../ui/confirmKit";
import { errMsg } from "../../pollJob";
import { ThinkingText } from "../../ui/ThinkingText";
import { THINK_TITLE } from "./presets";

interface Props {
  pid: number;
  mode: string; // serial | short
  topic: string;
  briefText: string;
  briefConfirmed: boolean;
  questions: ThreeQuestions[] | null;
  qAnswers: Record<string, string>;
  plans: BookPlan[] | null;
  selectedPlan: number | null;
  planBusy: string;
  planFeedback: string;
  onQuestions: () => void;
  onAnswer: (key: string, text: string) => void;
  onAdoptAll: () => void;
  onGenPlans: () => void;
  onRevise: (index: number, directive: string) => void;
  onConfirmPlan: (index: number) => void;
  onUnconfirm: () => void;
  onFeedback: (text: string) => void;
  onSelect: (index: number | null) => void;
  onBack: () => void;
  onGotoConcept: () => void;
}

const PLAN_FIELDS: { key: keyof BookPlan; label: string }[] = [
  { key: "kernel", label: "内核" },
  { key: "protagonist", label: "主角" },
  { key: "world", label: "世界观" },
  { key: "arc", label: "首卷走向" },
  { key: "engine", label: "连载引擎" },
  { key: "ending", label: "结尾落在" },
];

export default function PlanFlow(p: Props) {
  // 每张卡的定向修订输入
  const [reviseOpen, setReviseOpen] = useState<number | null>(null);
  const [reviseText, setReviseText] = useState("");
  const [regenFeedbackOpen, setRegenFeedbackOpen] = useState(false);
  const askedRef = useRef(false);
  const isShort = p.mode === "short";

  // 进屏且无三问 → 自动拉一次(空手也能答:候选全靠 AI 出)
  useEffect(() => {
    if (!p.briefConfirmed && !p.questions && !p.planBusy && !askedRef.current) {
      askedRef.current = true;
      p.onQuestions();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.questions, p.briefConfirmed]);

  // ---------- 已拍板回访:订单摘要 + 撤回 ----------
  if (p.briefConfirmed) {
    return (
      <div className="card">
        <h2>方案已拍板</h2>
        <div className="card-desc">这就是全书的硬约束:AI 照单深化概念,不换故事。</div>
        <div className="sub-summary mt-2" style={{ whiteSpace: "pre-wrap" }}>{p.briefText}</div>
        <div className="actions mt-3">
          <button className="primary" onClick={p.onGotoConcept}>去深化概念 →</button>
          <span className="grow" />
          <ConfirmGate confirmed onUnconfirm={p.onUnconfirm}
            confirmedText="已拍板 ✓" unconfirmTitle="撤回拍板,回方案墙换一套或改完再拍" />
        </div>
      </div>
    );
  }

  // ---------- ① 三问定纲 ----------
  if (!p.plans) {
    const answered = Object.values(p.qAnswers).filter((v) => v.trim()).length;
    return (
      <div className="card">
        <h2>三个问题,把方向定住</h2>
        <div className="card-desc">
          每问端上候选你挑(★ 是 AI 首推,带理由)——不想答就跳过,AI 按惯例定;
          答{isShort ? "完" : "完"}就出 <b>三套填满细节的完整{isShort ? "短故事" : "整书"}方案</b>供你挑。
        </div>

        {p.planBusy ? (
          <div className="sub-summary mt-3"><ThinkingText phrases={THINK_TITLE} />{p.planBusy}</div>
        ) : !p.questions ? (
          <div className="sub-summary mt-3 muted">AI 正在准备三问…</div>
        ) : (
          <>
            {p.questions.map((q) => {
              const cur = p.qAnswers[q.key] ?? "";
              return (
                <div className="pref-row mt-3" key={q.key}>
                  <span className="pref-label">{q.title}<small>跳过 = AI 定</small></span>
                  <div className="title-chips">
                    {q.candidates.map((c) => (
                      <button key={c.text} type="button"
                        className={"title-chip" + (cur === c.text ? " on" : "")}
                        title={c.recommended ? `★ AI 首推:${c.reason}` : c.reason || undefined}
                        onClick={() => p.onAnswer(q.key, cur === c.text ? "" : c.text)}>
                        {c.recommended ? "★ " : ""}{c.text}
                      </button>
                    ))}
                  </div>
                  {(() => {
                    const rec = q.candidates.find((c) => c.recommended);
                    return rec?.reason ? <div className="fld-hint">★ 推荐理由:{rec.reason}</div> : null;
                  })()}
                  <div className="input-row mt-1">
                    <input type="text" value={cur.startsWith("自:") ? cur.slice(2) : ""}
                      placeholder="都不对味?自己写一句(可不填)"
                      onChange={(e) => p.onAnswer(q.key, e.target.value ? `自:${e.target.value}` : "")} />
                  </div>
                </div>
              );
            })}
            <div className="actions mt-3">
              <button className="btn-sm" onClick={p.onAdoptAll}>★ 全部按推荐来</button>
              <button className="btn-sm" onClick={p.onQuestions} disabled={!!p.planBusy}>🎲 换一批候选</button>
              <span className="grow" />
              <button className="primary" disabled={!!p.planBusy} onClick={p.onGenPlans}>
                出三套{isShort ? "短故事" : "整书"}方案 →（已答 {answered}/3,可跳过）
              </button>
            </div>
          </>
        )}
        <div className="actions mt-2 onboard-nav">
          <button onClick={p.onBack}>← 上一步</button>
        </div>
      </div>
    );
  }

  // ---------- ② 方案墙 ----------
  return (
    <div className="card">
      <h2>挑一套{isShort ? "短故事" : "整书"}方案</h2>
      <div className="card-desc">
        三套都写满了细节——<b>方案一直读你的想法,另两套是发散</b>。点一张选中;
        差点意思就用「改改再选」说一句话(只改这张卡);都不对味就再来三套。
        "每套卡上带 AI 推荐篇幅档,拍板即用它(屏 0/配置屏手选过则以手选为准)。"
      </div>

      {p.planBusy && (
        <div className="sub-summary mt-2"><span className="spin" />{p.planBusy}</div>
      )}

      <div className="plans-wall mt-3">
        {p.plans.map((plan, i) => {
          const on = p.selectedPlan === i;
          return (
            <div key={i} className={"plan-card" + (on ? " on" : "")}>
              <div className="plan-head">
                <b className="plan-title">《{plan.title}》</b>
                {plan.label && <span className="pitch-label">{plan.label}</span>}
                {on && <span className="badge">已选这套</span>}
              </div>
              {PLAN_FIELDS.map(({ key, label }) => {
                const v = plan[key];
                if (typeof v !== "string" || !v.trim()) return null;
                return (
                  <div className="plan-field" key={key}>
                    <span className="plan-k">{label}</span>
                    <span className="plan-v">{v}</span>
                  </div>
                );
              })}
              {!!plan.flavor?.length && (
                <div className="plan-field">
                  <span className="plan-k">味道</span>
                  <span className="plan-v">{plan.flavor.join(" / ")}</span>
                </div>
              )}
              {plan.scale && (
                <div className="plan-field">
                  <span className="plan-k">篇幅</span>
                  <span className="plan-v">{plan.scale}{plan.scale_reason ? ` — ${plan.scale_reason}` : ""}</span>
                </div>
              )}
              <div className="actions mt-2">
                <button className={on ? "primary" : ""}
                  onClick={() => p.onSelect(on ? null : i)}>
                  {on ? "✓ 就是这套" : "选这套"}
                </button>
                <button className="btn-sm" disabled={!!p.planBusy}
                  onClick={() => { setReviseOpen(reviseOpen === i ? null : i); setReviseText(""); }}>
                  ✎ 改改再选
                </button>
              </div>
              {reviseOpen === i && (
                <div className="input-row mt-2">
                  <input type="text" value={reviseText} maxLength={300} disabled={!!p.planBusy}
                    placeholder="如:主角换成女性,基调再冷一点"
                    onChange={(e) => setReviseText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && reviseText.trim()) {
                        p.onRevise(i, reviseText.trim());
                        setReviseOpen(null);
                      }
                    }} />
                  <button className="btn-sm primary" disabled={!reviseText.trim() || !!p.planBusy}
                    onClick={() => { p.onRevise(i, reviseText.trim()); setReviseOpen(null); }}>
                    按这句改这张卡
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="actions mt-3">
        <button className="btn-sm" disabled={!!p.planBusy} onClick={p.onGenPlans}>
          🎲 再来三套
        </button>
        <button className="btn-sm" onClick={() => setRegenFeedbackOpen(v => !v)}>
          这批不对味?带句话重出
        </button>
        <span className="grow" />
        <ConfirmGate
          confirmed={false}
          confirmText="✓ 就写这套,拍板"
          disabled={p.selectedPlan === null}
          confirmTitle={`拍板《${p.selectedPlan !== null ? p.plans[p.selectedPlan].title : ""}》,渲染成开书订单、解锁概念深化`}
          onConfirm={() => p.selectedPlan !== null && p.onConfirmPlan(p.selectedPlan)} />
      </div>
      {regenFeedbackOpen && (
        <div className="input-row mt-2">
          <input type="text" value={p.planFeedback} maxLength={300}
            placeholder="如:太灰了,来点亮堂的;不要权谋,要小人物的热血"
            onChange={(e) => p.onFeedback(e.target.value)} />
          <button className="btn-sm primary" disabled={!!p.planBusy}
            onClick={() => { p.onGenPlans(); setRegenFeedbackOpen(false); }}>
            带话重出三套
          </button>
        </div>
      )}
      <div className="actions mt-2 onboard-nav">
        <button onClick={p.onBack}>← 上一步</button>
      </div>
    </div>
  );
}

// 供 OnboardingFlow 的错误面板复用:方案流错误由 hook 的 err 统一展示,这里不重复。
export function planErrMsg(e: unknown): string {
  return errMsg(e);
}
