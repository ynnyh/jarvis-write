// 宣传片工坊:主题(城市/景区/品牌)→ 多轮研讨(流式)→ 创作简报 → 风格/地标 →
// 解说词 → 分镜 → 三轨提示词 → 生成切段(≤15s 一段一次生成,画布拼接)→ 成片包/导出。
// 与小说项目无关,独立入口(/promo);沿用「只产提示词」哲学与素材点事实红线。
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  PROMO_STATUS_CN,
  PromoAngle,
  PromoDirection,
  PromoPlan,
  PromoPlanSlim,
  PromoShot,
  promoApi,
} from "../promoApi";
import { useJob } from "../ui/useJob";
import { toast } from "../ui/Toaster";
import { errMsg } from "../pollJob";
import EmptyState from "../ui/EmptyState";

function CopyBtn({ text, label = "复制" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  async function go() {
    if (!text.trim()) { toast.err("内容为空", "没有可复制的内容"); return; }
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1200);
    } catch { toast.err("复制失败", "请手动选中文本复制"); }
  }
  return <button className="btn-sm" onClick={go}>{done ? "✓ 已复制" : label}</button>;
}

function Banner({ stage, text }: { stage: string; text: string }) {
  return (
    <div className="gen-banner"><span className="spin" /><span className="gen-banner-text">{stage || text}</span></div>
  );
}

export default function PromoPage() {
  const { id } = useParams();
  const planId = id ? Number(id) : null;
  return planId ? <PromoWorkspace pid={planId} /> : <PromoList />;
}

// ================= 列表 + 新建 =================
function PromoList() {
  const nav = useNavigate();
  const meta = useMeta();
  const [plans, setPlans] = useState<PromoPlanSlim[] | null>(null);
  const [subject, setSubject] = useState("");
  const [angles, setAngles] = useState<string[]>(["food"]);
  const [duration, setDuration] = useState(90);
  const [direction, setDirection] = useState("live");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try { setPlans((await promoApi.list()).plans); } catch (e) { toast.err("加载失败", errMsg(e)); }
  }, []);
  useEffect(() => { void reload(); }, [reload]);

  async function create() {
    if (!subject.trim()) { toast.err("先填主题", "比如「西安」「某景区」「某品牌」"); return; }
    setBusy(true);
    try {
      const r = await promoApi.create({
        subject: subject.trim(), angles, duration_s: duration, direction,
      });
      toast.ok("企划已建", "下一步:和策划总监把方向聊透");
      nav(`/promo/${r.plan.id}`);
    } catch (e) { toast.err("创建失败", errMsg(e)); } finally { setBusy(false); }
  }

  return (
    <>
      <div className="page-head">
        <h1>宣传片工坊</h1>
        <button className="btn" onClick={() => nav("/")}>← 我的小说</button>
      </div>

      <div className="card">
        <div className="card-head"><h3 className="grow">新建宣传片企划</h3></div>
        <p className="card-desc">
          城市 / 景区 / 品牌都能做:先选个大概角度和画风,建完进工作台和 AI 策划总监
          <b>多轮研讨</b>把方向聊透,收敛成创作简报后再生成解说词与分镜——先聊后做,不一版定稿。
        </p>
        <div className="media-field">
          <div className="card-head mb-2"><span className="muted">主题(如「西安」)</span></div>
          <input value={subject} maxLength={60}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="城市 / 景区 / 品牌" />
        </div>
        <div className="card-head mb-2 plan-form">
          <label>角度(可多选,研讨中还会细调)
            <span className="chips">
              {(meta?.angles ?? []).map((a) => (
                <button key={a.key} type="button"
                  className={"chip" + (angles.includes(a.key) ? " on" : "")}
                  onClick={() => setAngles(angles.includes(a.key)
                    ? angles.filter((x) => x !== a.key) : [...angles, a.key])}>
                  {a.label}
                </button>
              ))}
            </span>
          </label>
        </div>
        <div className="card-head mb-2 plan-form">
          <label>时长
            <select value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
              <option value={60}>60 秒</option>
              <option value={90}>90 秒</option>
              <option value={120}>2 分钟</option>
              <option value={180}>3 分钟</option>
            </select>
          </label>
          <label>画风
            <select value={direction} onChange={(e) => setDirection(e.target.value)}>
              {(meta?.directions ?? []).map((d) => (
                <option key={d.key} value={d.key}>{d.label}</option>
              ))}
            </select>
          </label>
          <button className="primary" disabled={busy} onClick={create}>建企划,开始研讨</button>
        </div>
      </div>

      {plans === null ? <p className="muted">加载中…</p> : plans.length === 0 ? (
        <EmptyState>还没有企划。上面建一个——90 秒城市宣传片的完整拍摄手册,从研讨到切段不到十分钟。</EmptyState>
      ) : plans.map((p) => (
        <div key={p.id} className="sub-summary ep-row" onClick={() => nav(`/promo/${p.id}`)}>
          <div className="card-head mb-2">
            <b>{p.title || p.subject}</b>
            <span className="badge">{p.duration_s}s</span>
            <span className="badge">{p.direction_label}</span>
            <span className="badge">{PROMO_STATUS_CN[p.status] ?? p.status}</span>
            <span className="grow" />
            <button className="btn-sm" onClick={(e) => {
              e.stopPropagation();
              if (confirm(`删除企划「${p.title || p.subject}」?`)) {
                void promoApi.remove(p.id).then(reload).catch((err) => toast.err("删除失败", errMsg(err)));
              }
            }}>删除</button>
          </div>
        </div>
      ))}
    </>
  );
}

function useMeta() {
  const [meta, setMeta] = useState<{ angles: PromoAngle[]; directions: PromoDirection[] } | null>(null);
  useEffect(() => { void promoApi.meta().then(setMeta).catch(() => {}); }, []);
  return meta;
}

// ================= 工作台 =================
function PromoWorkspace({ pid }: { pid: number }) {
  const nav = useNavigate();
  const { run } = useJob();
  const [plan, setPlan] = useState<PromoPlan | null>(null);
  const [shots, setShots] = useState<PromoShot[]>([]);
  const meta = useMeta();
  const [busy, setBusy] = useState(""); // brief|style|landmarks|script|board|prompts|pack|chunks|""
  const [stage, setStage] = useState("");
  const [err, setErr] = useState("");

  const reload = useCallback(async () => {
    try {
      const r = await promoApi.get(pid);
      setPlan(r.plan);
      setShots(r.shots);
    } catch (e) { setErr(errMsg(e)); }
  }, [pid]);
  useEffect(() => { void reload(); }, [reload]);

  async function act(kind: typeof busy, start: () => Promise<{ job_id: string }>, ok: string) {
    setBusy(kind); setErr(""); setStage("");
    try {
      await run(start, { kind: `promo-${kind}-${pid}`, onStage: setStage });
      await reload();
      toast.ok(ok);
    } catch (e) { setErr(errMsg(e)); } finally { setBusy(""); setStage(""); }
  }

  async function exp(fmt: "md" | "csv" | "srt" | "json") {
    try { await promoApi.export(pid, fmt); } catch (e) { toast.err("导出失败", errMsg(e)); }
  }

  if (plan === null && !err) return <p className="muted">加载中…</p>;
  if (plan === null) return <div className="msg-err">{err}</div>;

  const brief = plan.brief as NonNullable<PromoPlan["brief"]> | undefined;
  const hasBrief = !!(brief && (brief as PromoPlan["brief"]).positioning);

  return (
    <>
      <div className="page-head">
        <h1>{plan.title || plan.subject} <span className="badge">{PROMO_STATUS_CN[plan.status] ?? plan.status}</span></h1>
        <button className="btn" onClick={() => nav("/promo")}>← 企划列表</button>
      </div>

      {/* ① 企划信息 */}
      <PlanForm pid={pid} plan={plan} meta={meta} onSaved={setPlan} />

      {/* ② 研讨对话 */}
      <ChatSection pid={pid} plan={plan} onChatSaved={setPlan}
        onDistill={() => act("brief", () => promoApi.brief(pid), "简报已收敛——检查后锁定,再往下走")} />

      {/* ③ 创作简报 */}
      <section className="card">
        <div className="card-head">
          <h3 className="grow">③ 创作简报 <span className="muted">研讨结论的契约</span></h3>
          <button className={"btn-sm" + (plan.brief_locked ? " primary" : "")}
            disabled={!hasBrief}
            onClick={() => void promoApi.patch(pid, { brief_locked: !plan.brief_locked })
              .then((r) => { setPlan(r.plan); toast.ok(plan.brief_locked ? "已解锁(可继续研讨)" : "已锁定简报"); })
              .catch((e) => toast.err("操作失败", errMsg(e)))}>
            {plan.brief_locked ? "🔒 已锁定" : "锁定简报"}
          </button>
          <button className="primary" disabled={!!busy || (plan.chat_log?.length ?? 0) === 0}
            onClick={() => act("brief", () => promoApi.brief(pid), "简报已收敛")}>重新收敛</button>
        </div>
        {!hasBrief ? (
          <p className="hint">还没有简报——先在上方和策划总监研讨,聊透后点「收敛成创作简报」。</p>
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
                    {brief.structure!.map((s, i) => (
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
              <div className="msg-err">
                ⚠ 需人工核实:{brief.cautions.join(";")}
              </div>
            )}
          </>
        )}
      </section>

      {/* ④ 视觉资产:风格 + 地标 */}
      <section className="card">
        <div className="card-head">
          <h3 className="grow">④ 视觉风格与地标卡 <span className="muted">锚段一致性</span></h3>
          <button className="primary" disabled={!!busy || !plan.subject}
            onClick={() => act("style", () => promoApi.style(pid), "视觉风格已定")}>
            {plan.style_cn ? "重新生成风格" : "生成视觉风格"}
          </button>
          <button disabled={!!busy || !hasBrief}
            onClick={() => act("landmarks", () => promoApi.landmarks(pid), "地标卡已生成")}>
            {plan.landmarks?.length ? "重新生成地标" : "生成地标卡"}
          </button>
        </div>
        {plan.style_cn && (
          <div className="sub-summary">
            <div className="card-head mb-2"><b>{plan.style_name || "风格卡"}</b><CopyBtn text={plan.style_cn} /></div>
            <div className="hint">{plan.style_cn}</div>
            <div className="hint">EN: {plan.style_en}</div>
            <div className="hint">负面词: {plan.negative}</div>
          </div>
        )}
        {(plan.landmarks ?? []).length > 0 && (
          <div className="sub-summary">
            <div className="card-head mb-2"><b>地标卡({plan.landmarks.length})</b></div>
            {plan.landmarks.map((l, i) => (
              <div key={i} className="mb-2"><b>{l.name}</b><div className="muted">{l.appearance_cn}</div></div>
            ))}
          </div>
        )}
      </section>

      {/* ⑤ 解说词 */}
      <section className="card">
        <div className="card-head">
          <h3 className="grow">⑤ 解说词 <span className="muted">素材点是唯一事实来源</span></h3>
          <button className="primary" disabled={!!busy || !hasBrief}
            onClick={() => act("script", () => promoApi.script(pid), "解说词已生成")}>
            {plan.script?.lines?.length ? "重写解说词" : "写解说词"}
          </button>
        </div>
        {plan.script?.lines?.length ? (
          <div className="sub-summary">
            {plan.script.synopsis && <div className="card-head mb-2"><span className="muted">{plan.script.synopsis}</span></div>}
            {plan.script.lines.map((l, i) => (
              <div key={i} className="script-line"><b>{l.speaker}</b>:{l.text}
                {l.action && <span className="muted">(画面:{l.action})</span>}
              </div>
            ))}
          </div>
        ) : (
          <p className="hint">需要简报(上方③)就绪——解说词按简报段落推进,事实只用素材点。</p>
        )}
      </section>

      {/* ⑥ 分镜 + 三轨提示词 */}
      <section className="card">
        <div className="card-head">
          <h3 className="grow">⑥ 分镜与三轨提示词</h3>
          <button className="primary" disabled={!!busy || !plan.script?.lines?.length}
            onClick={() => act("board", () => promoApi.storyboard(pid), "分镜已生成(旧分镜已覆盖)")}>
            {shots.length ? "重新拆分镜" : "拆分镜"}
          </button>
          <button className="primary" disabled={!!busy || shots.length === 0 || !plan.style_cn}
            onClick={() => act("prompts", () => promoApi.prompts(pid), "三轨提示词已生成")}>出提示词</button>
        </div>
        {busy && <Banner stage={stage} text="AI 正在处理…" />}
        {err && <div className="msg-err">{err}</div>}
        {shots.length > 0 && (
          <>
            <div className="card-head mb-2"><b>分镜表({shots.length} 格 · 约 {shots.reduce((s, x) => s + x.duration_s, 0)} 秒)</b></div>
            <div className="tbl-wrap">
              <table className="tbl">
                <thead><tr><th>#</th><th>场景</th><th>景别</th><th>运镜</th><th>秒</th><th>画面</th><th>解说词</th></tr></thead>
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
          </>
        )}
        {shots.filter((s) => s.prompt_cn || s.prompt_en).map((s) => (
          <div key={s.id} className="sub-summary">
            <div className="card-head mb-2"><b>镜头 {s.seq}({s.shot_type}/{s.camera}/{s.duration_s}s)</b></div>
            <div className="media-field">
              <div className="card-head mb-2"><span className="muted">静帧中文提示词(即梦/可灵)</span><CopyBtn text={s.prompt_cn} /></div>
              <textarea rows={3} readOnly value={s.prompt_cn} />
            </div>
            <div className="media-field">
              <div className="card-head mb-2"><span className="muted">静帧英文提示词(MJ)</span><CopyBtn text={s.prompt_en} /></div>
              <textarea rows={2} readOnly value={s.prompt_en} />
            </div>
            {s.negative && <div className="hint">负面:{s.negative}</div>}
          </div>
        ))}
      </section>

      {/* ⑦ 生成切段(画布拼接) */}
      <ChunksSection plan={plan} busy={!!busy}
        onBuild={(cs) => act("chunks", () => promoApi.chunks(pid, cs), `切段已生成(每段 ≤${cs}s)`)} />

      {/* ⑧ 成片包 + 导出 */}
      <section className="card">
        <div className="card-head">
          <h3 className="grow">⑧ 成片包与导出</h3>
          <button className="primary" disabled={!!busy || shots.length === 0}
            onClick={() => act("pack", () => promoApi.pack(pid), "成片包已生成")}>
            {(plan.pack as { checklist?: unknown[] }).checklist ? "重建成片包" : "出成片包"}
          </button>
        </div>
        <div className="card-head mb-2 plan-form">
          <span className="muted">导出:</span>
          <button className="btn-sm" onClick={() => exp("md")}>拍摄手册</button>
          <button className="btn-sm" disabled={shots.length === 0} onClick={() => exp("csv")}>分镜CSV</button>
          <button className="btn-sm" disabled={shots.length === 0} onClick={() => exp("srt")}>字幕SRT</button>
          <button className="btn-sm" onClick={() => exp("json")}>JSON</button>
        </div>
        {(() => {
          const pack = plan.pack as {
            narration_full?: string;
            dubbing?: { seq: number; text: string; est_s: number; shot_duration_s: number }[];
            checklist?: { seq: number; scene: string; duration_s: number; subtitle: string; transition: string; bgm_tag: string; note: string }[];
            totals?: { shots: number; storyboard_s: number; target_s: number; voice_s: number };
          };
          if (!pack?.checklist?.length) return <p className="hint">出成片包:配音稿(解说逐镜对位+估时)+ 剪辑清单(转场/配乐标注)。</p>;
          return (
            <>
              <div className="card-head mb-2">
                <b>成片包</b>
                <span className="muted">
                  {pack.totals?.shots} 格 · 分镜 {pack.totals?.storyboard_s}s(目标 {pack.totals?.target_s}s)· 解说估时 {pack.totals?.voice_s}s
                </span>
              </div>
              {pack.narration_full && (
                <div className="sub-summary">
                  <div className="card-head mb-2"><b>整段解说(粘给 TTS)</b><CopyBtn text={pack.narration_full} /></div>
                  <div className="script-line" style={{ whiteSpace: "pre-wrap" }}>{pack.narration_full}</div>
                </div>
              )}
              <div className="tbl-wrap">
                <table className="tbl">
                  <thead><tr><th>#</th><th>场景</th><th>秒</th><th>字幕</th><th>转场</th><th>配乐</th><th>备注</th></tr></thead>
                  <tbody>
                    {pack.checklist.map((c) => (
                      <tr key={c.seq}>
                        <td>{c.seq}</td><td>{c.scene}</td><td>{c.duration_s}s</td><td>{c.subtitle}</td>
                        <td>{c.transition}</td><td>{c.bgm_tag}</td><td className="muted">{c.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          );
        })()}
      </section>
    </>
  );
}

// ================= ① 企划表单 =================
function PlanForm({ pid, plan, meta, onSaved }: {
  pid: number;
  plan: PromoPlan;
  meta: { angles: PromoAngle[]; directions: PromoDirection[] } | null;
  onSaved: (p: PromoPlan) => void;
}) {
  const [draft, setDraft] = useState(plan);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setDraft(plan); setDirty(false); }, [plan]);

  async function save() {
    try {
      const r = await promoApi.patch(pid, {
        subject: draft.subject, title: draft.title, angles: draft.angles,
        duration_s: draft.duration_s, direction: draft.direction,
        material_notes: draft.material_notes,
      });
      onSaved(r.plan);
      setDirty(false);
      toast.ok("企划已保存");
    } catch (e) { toast.err("保存失败", errMsg(e)); }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">① 企划信息 <span className="muted">研讨的输入</span></h3>
        {dirty && <button className="btn-sm primary" onClick={save}>保存修改</button>}
      </div>
      <div className="card-head mb-2 plan-form">
        <label>主题 <input value={draft.subject} maxLength={60}
          onChange={(e) => { setDraft({ ...draft, subject: e.target.value }); setDirty(true); }} /></label>
        <label>企划名 <input value={draft.title} maxLength={60} placeholder="如「西安·烟火食事」"
          onChange={(e) => { setDraft({ ...draft, title: e.target.value }); setDirty(true); }} /></label>
        <label>时长
          <select value={draft.duration_s} onChange={(e) => { setDraft({ ...draft, duration_s: Number(e.target.value) }); setDirty(true); }}>
            <option value={60}>60 秒</option><option value={90}>90 秒</option>
            <option value={120}>2 分钟</option><option value={180}>3 分钟</option>
          </select>
        </label>
        <label>画风
          <select value={draft.direction} onChange={(e) => { setDraft({ ...draft, direction: e.target.value }); setDirty(true); }}>
            {(meta?.directions ?? []).map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
          </select>
        </label>
      </div>
      <div className="card-head mb-2 plan-form">
        <label>角度(多选)
          <span className="chips">
            {(meta?.angles ?? []).map((a) => (
              <button key={a.key} type="button"
                className={"chip" + (draft.angles.includes(a.key) ? " on" : "")}
                onClick={() => {
                  setDraft({ ...draft, angles: draft.angles.includes(a.key)
                    ? draft.angles.filter((x) => x !== a.key) : [...draft.angles, a.key] });
                  setDirty(true);
                }}>{a.label}</button>
            ))}
          </span>
        </label>
      </div>
      <div className="media-field">
        <div className="card-head mb-2">
          <span className="muted">素材点(史实/数据/slogan——解说词的唯一事实来源,拿不准的宁可不写)</span>
        </div>
        <textarea rows={4} value={draft.material_notes}
          placeholder="如:回民街头汤凌晨四点开熬;城墙明代扩建;slogan 候选「长安烟火,不散」"
          onChange={(e) => { setDraft({ ...draft, material_notes: e.target.value }); setDirty(true); }} />
      </div>
    </section>
  );
}

// ================= ② 研讨对话(流式打字机) =================
function ChatSection({ pid, plan, onChatSaved, onDistill }: {
  pid: number;
  plan: PromoPlan;
  onChatSaved: (p: PromoPlan) => void;
  onDistill: () => void;
}) {
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "nearest" });
  }, [plan.chat_log?.length, streaming]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setBusy(true); setStreaming(""); setInput("");
    const messages = [...(plan.chat_log ?? []), { role: "user" as const, text }];
    try {
      await promoApi.chat(pid, messages, (t) => setStreaming((s) => s + t));
      // 服务端已把 user+assistant 落库;本地直接重拉企划拿权威 chat_log
      const fresh = await promoApi.get(pid);
      onChatSaved(fresh.plan);
    } catch (e) {
      toast.err("对话失败", errMsg(e));
      setInput(text); // 失败退回输入框,不丢字
    } finally { setBusy(false); setStreaming(""); }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">② 与策划总监研讨 <span className="muted">先聊透方向,再收敛简报</span></h3>
        <button className="primary" disabled={busy || (plan.chat_log?.length ?? 0) === 0}
          onClick={onDistill}>收敛成创作简报</button>
      </div>
      <p className="card-desc">
        告诉他你的倾向(「我想从吃的入手」),他会反问关键问题、给具体建议、每轮复述共识——
        方向/结构/基调/开场都清楚了再点「收敛成创作简报」。聊岔了就继续掰,简报可以随时重新收敛。
      </p>
      <div className="promo-chat">
        {(plan.chat_log ?? []).map((m, i) => (
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
      <div className="card-head mt-2">
        <input className="grow" value={input} maxLength={2000}
          placeholder={busy ? "总监正在回复…" : "说说你的想法(Enter 发送)"}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
        <button className="primary" disabled={busy || !input.trim()} onClick={send}>发送</button>
      </div>
    </section>
  );
}

// ================= ⑦ 生成切段 =================
function ChunksSection({ plan, busy, onBuild }: {
  plan: PromoPlan;
  busy: boolean;
  onBuild: (chunkS: number) => void;
}) {
  const [chunkS, setChunkS] = useState(plan.chunks?.chunk_s ?? 15);
  const items = plan.chunks?.items ?? [];

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">⑦ 生成切段 <span className="muted">一段一次生成,画布拼接</span></h3>
        <label className="muted">每段
          <select value={chunkS} onChange={(e) => setChunkS(Number(e.target.value))}>
            <option value={5}>≤5s</option>
            <option value={10}>≤10s</option>
            <option value={15}>≤15s</option>
          </select>
        </label>
        <button className="primary" disabled={busy}
          onClick={() => onBuild(chunkS)}>{items.length ? "重新切段" : "切段出视频提示词"}</button>
      </div>
      <p className="card-desc">
        按你的画布拼接工作流:镜头边界贪心聚段(绝不在一个镜头中间断开),每段 ≤{chunkS} 秒——
        一段一次文生视频 / 图生视频,生成后按段拖上画布拼接;单镜头超限的段会标 ⚠(可降速或分段重生成)。
      </p>
      {items.length > 0 && (
        <>
          <div className="card-head mb-2">
            <b>{items.length} 段 · 每段 ≤{plan.chunks.chunk_s}s</b>
            <span className="muted">时间码与 SRT/剪辑清单同轴,直接对齐画布</span>
          </div>
          {items.map((c) => (
            <div key={c.index} className="sub-summary">
              <div className="card-head mb-2">
                <b>段 {c.index}({c.start_s}-{c.end_s}s · {c.duration_s}s)</b>
                <span className="badge">镜头 {c.shot_seqs.join("、")}</span>
                {c.over_limit && <span className="badge drama-warn-tip">⚠ 超限</span>}
              </div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">视频提示词(文生视频,整段一次生成)</span><CopyBtn text={c.motion_prompt_cn} /></div>
                <textarea rows={3} readOnly value={c.motion_prompt_cn} />
              </div>
              <div className="media-field">
                <div className="card-head mb-2"><span className="muted">英文视频提示词</span><CopyBtn text={c.motion_prompt_en} /></div>
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
