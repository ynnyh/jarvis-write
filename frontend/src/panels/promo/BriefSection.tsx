// ② 研讨与简报(主区):先聊透方向,再把研讨结论收敛成「创作简报」这份契约。
// 工坊的产品红线就在这一步——先聊后做,不一版定稿;简报锁定后,后面每一步都按它执行。
import { useEffect, useRef, useState } from "react";
import { PromoBrief, PromoPlan, promoApi } from "../../promoApi";
import Banner from "../../ui/Banner";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";
import { PromoJobs } from "./usePromoJobs";

export default function BriefSection({ pid, plan, jobs, onPlan, onDistill }: {
  pid: number;
  plan: PromoPlan;
  jobs: PromoJobs;
  onPlan: (p: PromoPlan) => void;
  onDistill: () => void;
}) {
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState("");
  const [chatting, setChatting] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const turns = plan.chat_log ?? [];

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length, streaming]);

  async function send() {
    const text = input.trim();
    if (!text || chatting) return;
    setChatting(true); setStreaming(""); setInput("");
    try {
      await promoApi.chat(pid, [...turns, { role: "user", text }], (t) => setStreaming((s) => s + t));
      // 服务端已把 user+assistant 落库;这里重拉企划拿权威 chat_log,不本地拼
      onPlan((await promoApi.get(pid)).plan);
    } catch (e) {
      toast.err("对话失败", errMsg(e));
      setInput(text); // 失败退回输入框,不丢字
    } finally { setChatting(false); setStreaming(""); }
  }

  async function toggleLock() {
    try {
      const r = await promoApi.patch(pid, { brief_locked: !plan.brief_locked });
      onPlan(r.plan);
      toast.ok(plan.brief_locked ? "已解锁(可继续研讨)" : "已锁定简报");
    } catch (e) { toast.err("操作失败", errMsg(e)); }
  }

  const brief = plan.brief as PromoBrief;
  const hasBrief = !!brief?.positioning;

  return (
    <>
      <section className="card">
        <div className="card-head">
          <h3 className="grow">② 与策划总监研讨 <span className="muted">先聊透方向,再收敛简报</span></h3>
          <button className="primary" disabled={!!jobs.busy || chatting || turns.length === 0}
            onClick={onDistill}>
            {hasBrief ? "重新收敛简报" : "收敛成创作简报"}
          </button>
        </div>
        <p className="card-desc">
          告诉他你的倾向(「我想从吃的入手」),他会反问关键问题、给具体建议、每轮复述共识——
          方向/结构/基调/开场都清楚了再收敛。聊岔了就继续掰,简报随时能重新收敛。
        </p>
        <div className="promo-chat">
          {turns.map((m, i) => (
            <div key={i} className={"promo-bubble " + (m.role === "user" ? "mine" : "")}>
              <b>{m.role === "user" ? "我" : "策划总监"}</b>
              <div>{m.text}</div>
            </div>
          ))}
          {streaming && (
            <div className="promo-bubble"><b>策划总监</b><div>{streaming}</div></div>
          )}
          <div ref={bottomRef} />
        </div>
        <div className="input-row mt-2">
          <input className="grow" value={input} maxLength={2000}
            placeholder={chatting ? "总监正在回复…" : "说说你的想法(Enter 发送)"}
            disabled={chatting}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
          <button className="primary" disabled={chatting || !input.trim()} onClick={send}>发送</button>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h3 className="grow">②-2 创作简报 <span className="muted">研讨结论的契约</span></h3>
          <button className={"btn-sm" + (plan.brief_locked ? " primary" : "")}
            disabled={!hasBrief} onClick={toggleLock}>
            {plan.brief_locked ? "🔒 已锁定" : "锁定简报"}
          </button>
        </div>
        {jobs.busy === "brief" && <Banner stage={jobs.stage} text="AI 正在收敛简报…" />}
        {!hasBrief ? (
          <p className="hint">还没有简报——先在上面和策划总监研讨,聊透后点「收敛成创作简报」。</p>
        ) : (
          <>
            <div className="sub-summary">
              <div><b>定位:</b>{brief.positioning}</div>
              <div><b>受众:</b>{brief.audience}</div>
              <div><b>基调:</b>{(brief.tone ?? []).join("、")}</div>
              {(brief.key_messages ?? []).map((m, i) => <div key={i}>· {m}</div>)}
            </div>
            {(brief.structure ?? []).length > 0 && (
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead><tr><th>段落</th><th>角度</th><th>秒</th><th>内容</th></tr></thead>
                  <tbody>
                    {brief.structure.map((s, i) => (
                      <tr key={i}><td>{s.title}</td><td>{s.angle}</td><td>{s.seconds}s</td><td>{s.beat}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {(brief.slogan_candidates ?? []).length > 0 && (
              <p className="hint"><b>Slogan 候选:</b>{brief.slogan_candidates.join(" / ")}</p>
            )}
            {(brief.cautions ?? []).length > 0 && (
              <div className="msg-err">⚠ 需人工核实:{brief.cautions.join(";")}</div>
            )}
            {!plan.brief_locked && (
              <p className="hint">核对无误就「锁定简报」:锁了之后往下走,解说词与分镜都按这份契约执行。</p>
            )}
          </>
        )}
      </section>
    </>
  );
}
