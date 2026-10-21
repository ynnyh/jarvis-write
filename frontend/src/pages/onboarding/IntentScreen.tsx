// IntentScreen — 理解屏(docs/30 / docs/32 屏 2):理解卡一次确认 + 方向卡墙。
//
// 这一屏取代旧链的三屏(brief 三问方案 / concept 概念打磨 / setup 配置三页签)。
// 为什么合成一屏:那三屏其实是同一件事的三个侧面——「AI 以为你要写什么」。
// 拆成三屏就变成三道签字,每道都要作者在没读到任何正文时点头(传国玉玺案例:
// 五道签字 12+ 次交互,而题材还被一次 13 token 的推断静默改成了架空)。
//
// 现在:AI 先把「我听到的」回读成一张卡(世界坐标/视角与主角/人物/味道/必须保留/
// 不要/题材),每个字段可逐条改、拿不准的带候选;方向卡墙选一套;点一次
// 「✓ 就按这个来,先写三章给我看」→ 落库 + 后台起样稿链。
//
// 本组件自管数据与请求(仿 SkeletonWall):不进 useOnboarding 的状态机,
// 因为它的生命周期就这一屏,刷新回来能自己重建(卡从 project.intent_card 续接)。
import { useEffect, useRef, useState } from "react";
import {
  api, BookPlan, IntentCard, Project, ThreeQuestions,
} from "../../api";
import { RequestCancelled } from "../../http";
import { errMsg } from "../../pollJob";
import { ThinkingText } from "../../ui/ThinkingText";
import { toast } from "../../ui/Toaster";
import PlanEditDialog from "./PlanEditDialog";
import { THINK_INTENT } from "./presets";

// 理解卡字段的渲染顺序与中文标签(与后端 app.engines.intent.INTENT_FIELDS 对齐)
const TEXT_FIELDS = ["world", "viewpoint", "cast", "genre"] as const;
const ARRAY_FIELDS = ["flavor", "must", "avoid"] as const;
type TextField = typeof TEXT_FIELDS[number];
type ArrayField = typeof ARRAY_FIELDS[number];

const FIELD_LABEL: Record<string, string> = {
  world: "世界坐标", viewpoint: "视角与主角", cast: "主要人物",
  flavor: "味道", must: "必须保留", avoid: "不要出现", genre: "题材",
};
const FIELD_HINT: Record<string, string> = {
  world: "时代/地域/架空还是真实历史——任何下游生成都不得改写这一条",
  viewpoint: "谁在讲故事、他是什么人",
  cast: "主要人物与关系(可留空,后面还能加)",
  flavor: "想要读起来是什么感觉(可多选,一行一条)",
  must: "必须保留的东西:一行一条,写下来的就不会被生成漂掉",
  avoid: "不要出现的东西:一行一条",
  genre: "题材:确认后以这里的显式值为准落库,不再由 AI 推断静默覆盖",
};

// 方向卡展示字段(与 PlanFlow 同一套:一屏看全一套方案的骨架)
const PLAN_FIELDS: { key: keyof BookPlan; label: string }[] = [
  { key: "kernel", label: "内核" },
  { key: "opening", label: "开场冲突" },
  { key: "payoff", label: "第一回报" },
  { key: "escalation", label: "后续困境/代价" },
  { key: "mechanism", label: "故事机制" },
  { key: "opening_sample", label: "开场试读" },
  { key: "protagonist", label: "主角" },
  { key: "world", label: "世界观" },
  { key: "arc", label: "首卷走向" },
  { key: "engine", label: "连载引擎" },
  { key: "ending", label: "结尾落在" },
];

function cardIsEmpty(c: IntentCard | null): boolean {
  if (!c) return true;
  return !c.world.trim() && !c.viewpoint.trim() && !c.cast.trim() && !c.genre.trim();
}

/** 数组字段在 textarea 里的文本形态:一行一条(只在换行处切,打字过程不会被拆散) */
function arrText(items: string[] | undefined): string {
  return (items ?? []).join("\n");
}
function textArr(text: string): string[] {
  return text.split("\n").map((s) => s.trim()).filter(Boolean);
}

interface Props {
  pid: number;
  mode: string;               // serial | short | drama
  /** 想法屏勾过的排斥项(可选):作为回读的硬约束带上 */
  avoid?: string[];
  /** 确认① 成功:带着样稿 job 与最新项目态跳样稿屏 */
  onConfirmed: (r: { sampleJobId: string; project: Project }) => void;
  onBack: () => void;
}

export default function IntentScreen({ pid, mode, avoid = [], onConfirmed, onBack }: Props) {
  const [project, setProject] = useState<Project | null>(null);
  const [card, setCard] = useState<IntentCard | null>(null);
  const [cardBusy, setCardBusy] = useState("");
  const [err, setErr] = useState("");
  // 整卡重出:作者写一句「不是架空,就是真实历史」,重出时作为最高优先级补充
  const [feedback, setFeedback] = useState("");
  // 方向卡墙
  const [plans, setPlans] = useState<BookPlan[] | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [planBusy, setPlanBusy] = useState("");
  const [planFeedback, setPlanFeedback] = useState("");
  const [reviseOpen, setReviseOpen] = useState<number | null>(null);
  const [reviseText, setReviseText] = useState("");
  const [lockedFields, setLockedFields] = useState<string[]>([]);
  // 手动改卡弹层:改的是第几套(null=关着)。AI 改意思走「改改再选」,这里改字面
  const [editIndex, setEditIndex] = useState<number | null>(null);
  // 三问降级为可选工具(不再是关卡):拿不准时让 AI 出候选,点选即填进 answers
  const [questions, setQuestions] = useState<ThreeQuestions[] | null>(null);
  const [qAnswers, setQAnswers] = useState<Record<string, string>>({});
  const [confirmBusy, setConfirmBusy] = useState(false);
  // 当前请求的取消器(回读/出方向/修订共用一位):LLM 超时放宽后干等必须有出口
  const abortRef = useRef<AbortController | null>(null);
  // 回读只自动跑一次(StrictMode 下 effect 会重入,重入就重复烧一次强档调用)
  const bootPidRef = useRef<number | null>(null);
  const isShort = mode === "short";

  useEffect(() => () => abortRef.current?.abort(), []);

  // ---------- 进屏:续接已有理解卡,没有就自动回读一次 ----------
  useEffect(() => {
    if (bootPidRef.current === pid) return;
    bootPidRef.current = pid;
    (async () => {
      try {
        const p = await api.getProject(pid);
        setProject(p);
        if (p.book_plans?.length) setPlans(p.book_plans);
        if (!cardIsEmpty(p.intent_card ?? null)) setCard(p.intent_card ?? null);
        else await readback(p);
      } catch (e) {
        setErr(errMsg(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  /** 回读/重出理解卡。草稿不落库——作者点确认① 才进库。 */
  async function readback(p: Project, fb = "") {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setCardBusy(fb ? "正在按你这句重出理解卡…" : "正在回读你说的话…");
    setErr("");
    try {
      const r = await api.intentReadback(pid, {
        mode,
        topic: (p.topic ?? "").trim(),
        genre: (p.genre ?? "").trim(),
        feedback: fb.trim() || undefined,
        avoid: avoid.length ? avoid : undefined,
      }, ac.signal);
      setCard(r.card);
      setProject(r.project);
    } catch (e) {
      // 作者主动终止:回空闲态即可,不弹错误
      if (!(e instanceof RequestCancelled)) setErr(errMsg(e));
    } finally {
      if (abortRef.current === ac) abortRef.current = null;
      setCardBusy("");
    }
  }

  /** 终止当前等待(回读/出方向/修订共用):abort 掉连着后端的 HTTP 请求 */
  function cancelBusy() {
    abortRef.current?.abort();
    abortRef.current = null;
    setCardBusy("");
    setPlanBusy("");
  }

  // ---------- 理解卡:逐字段编辑 ----------
  function setTextField(key: TextField, value: string) {
    setCard((c) => (c ? { ...c, [key]: value } : c));
  }
  function setArrayField(key: ArrayField, items: string[]) {
    setCard((c) => (c ? { ...c, [key]: items } : c));
  }
  /** 候选 chip 点选:文本字段直接替换,数组字段追加(已在里面则取消,可反悔) */
  function pickCandidate(field: string, text: string) {
    if ((ARRAY_FIELDS as readonly string[]).includes(field)) {
      const key = field as ArrayField;
      setCard((c) => {
        if (!c) return c;
        const cur = c[key] ?? [];
        return { ...c, [key]: cur.includes(text) ? cur.filter((x) => x !== text) : [...cur, text] };
      });
      return;
    }
    if ((TEXT_FIELDS as readonly string[]).includes(field)) {
      setTextField(field as TextField, text);
    }
  }
  function uncertainOf(field: string) {
    return card?.uncertain?.find((u) => u.field === field) ?? null;
  }

  // ---------- 方向卡墙 ----------
  /** 出一批方向卡(book-plans);avoid 带上上一批的差异坐标防趋同 */
  async function genPlans() {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setPlanBusy("AI 正在出三套方向…");
    setErr("");
    try {
      const prev = plans ?? [];
      const avoidPlans = prev
        .map((p) => `${p.label ?? ""}·${p.title}·${(p.kernel ?? "").slice(0, 40)}`)
        .map((s) => s.replace(/^[·\s]+|[·\s]+$/g, ""))
        .filter(Boolean);
      const r = await api.bookPlans(pid, {
        mode,
        topic: (project?.topic ?? "").trim(),
        genre: (card?.genre ?? project?.genre ?? "").trim(),
        answers: qAnswers,
        feedback: planFeedback.trim() || undefined,
        avoid: avoidPlans,
      }, ac.signal);
      setPlans(r.plans);
      setProject(r.project);
      setSelected(null);
    } catch (e) {
      if (!(e instanceof RequestCancelled)) setErr(errMsg(e));
    } finally {
      if (abortRef.current === ac) abortRef.current = null;
      setPlanBusy("");
    }
  }

  /** 定向修订:一句话只改第 index 套,其余套与勾选「保持不变」的字段不动 */
  async function revisePlan(index: number, directive: string, locked: string[]) {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setPlanBusy(`AI 正在改第 ${index + 1} 套…`);
    setErr("");
    try {
      const r = await api.revisePlan(pid, { index, directive, locked_fields: locked }, ac.signal);
      setPlans(r.plans);
      setProject(r.project);
    } catch (e) {
      if (!(e instanceof RequestCancelled)) setErr(errMsg(e));
    } finally {
      if (abortRef.current === ac) abortRef.current = null;
      setPlanBusy("");
    }
  }

  /** 三问(可选工具):拿不准时出候选 */
  async function fetchQuestions() {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setPlanBusy("AI 正在出候选…");
    setErr("");
    try {
      const prev = (questions ?? []).flatMap((q) => q.candidates.map((c) => c.text));
      const r = await api.threeQuestions(pid, {
        mode,
        topic: (project?.topic ?? "").trim(),
        genre: (card?.genre ?? project?.genre ?? "").trim(),
        feedback: planFeedback.trim() || undefined,
        avoid: prev,
      }, ac.signal);
      setQuestions(r.questions);
    } catch (e) {
      if (!(e instanceof RequestCancelled)) setErr(errMsg(e));
    } finally {
      if (abortRef.current === ac) abortRef.current = null;
      setPlanBusy("");
    }
  }

  // ---------- 确认① ----------
  async function confirmIntent() {
    if (!card || selected === null) return;
    setConfirmBusy(true);
    setErr("");
    try {
      // 手选过篇幅(非建库默认 30×3000)→ 尊重现值;否则用方向卡自带推荐档
      const ch = Number(project?.target_chapters ?? 30);
      const w = Number(project?.target_words_per_chapter ?? 3000);
      const manualScale = ch !== 30 || w !== 3000;
      const r = await api.intentConfirm(pid, {
        card, mode, index: selected,
        scale_override: manualScale ? { chapters: ch, words: w } : null,
      });
      setProject(r.project);
      toast.ok("就按这个来", "正在后台写前三章——先看正文,再决定整本怎么写");
      onConfirmed({ sampleJobId: r.sample_job_id, project: r.project });
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setConfirmBusy(false);
    }
  }

  const busy = !!cardBusy || !!planBusy || confirmBusy;

  // ---------- 等待区(带终止出口) ----------
  if (!card && cardBusy) {
    return (
      <div className="card">
        <h2>AI 先复述一遍它听到的</h2>
        <div className="card-desc">
          这一次调用决定整本书的世界坐标,慢是正常的;AI 拿不准的地方会自己标出来。
        </div>
        <div className="sub-summary mt-3">
          <span className="spin" />
          <ThinkingText phrases={THINK_INTENT} />{cardBusy}
          <span style={{ marginLeft: 12 }}>
            <button className="btn-sm" type="button" onClick={cancelBusy}>终止等待</button>
          </span>
        </div>
        {err && <div className="msg-err mt-2">{err}</div>}
      </div>
    );
  }

  if (!card) {
    return (
      <div className="card">
        <h2>AI 还没读到你的想法</h2>
        <div className="card-desc">
          理解卡要从一句灵感回读出来——回想法屏写一句(哪怕一句话),或在这里重试。
        </div>
        {err && <div className="msg-err mt-2">{err}</div>}
        <div className="form-actions mt-3">
          <button className="primary" disabled={busy}
            onClick={() => project && void readback(project)}>↻ 重试回读</button>
          <button onClick={onBack}>← 回想法屏</button>
        </div>
      </div>
    );
  }

  return (
    <>
      {/* ---------- ① 理解卡 ---------- */}
      <div className="card">
        <h2>AI 听到的是这个吗?</h2>
        <div className="card-desc">
          这是 AI 从你的话里读出来的「这本书要写什么」——<b>确认之后它就是全书的硬约束</b>:
          概念、架构、骨架、每一章正文都从这张卡派生,世界坐标任何下游不得改写。
          逐条改到对味,拿不准的字段下面有候选可点。
        </div>

        <div className="form-grid mt-3">
          {TEXT_FIELDS.map((key) => {
            const unc = uncertainOf(key);
            return (
              <div className="field field-full" key={key}>
                <label className="fl" htmlFor={`intent-${key}`}>
                  {FIELD_LABEL[key]}
                  <span className={"intent-tag" + (unc ? " unsure" : "")}>
                    {unc ? "拿不准" : "确定"}
                  </span>
                </label>
                {key === "world" || key === "cast" ? (
                  <textarea id={`intent-${key}`} rows={2} value={card[key] ?? ""}
                    placeholder={FIELD_HINT[key]}
                    onChange={(e) => setTextField(key, e.target.value)} />
                ) : (
                  <input id={`intent-${key}`} type="text" value={card[key] ?? ""}
                    placeholder={FIELD_HINT[key]}
                    onChange={(e) => setTextField(key, e.target.value)} />
                )}
                <p className="field-note">{FIELD_HINT[key]}</p>
                {unc && (
                  <div className="intent-uncertain">
                    <p className="field-note">AI 说:{unc.reason}</p>
                    <div className="title-chips">
                      {unc.candidates.map((c) => (
                        <button key={c} type="button" className="title-chip"
                          onClick={() => pickCandidate(key, c)}>{c}</button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}

          {ARRAY_FIELDS.map((key) => {
            const unc = uncertainOf(key);
            return (
              <div className="field field-full" key={key}>
                <label className="fl" htmlFor={`intent-${key}`}>
                  {FIELD_LABEL[key]}
                  <span className={"intent-tag" + (unc ? " unsure" : "")}>
                    {unc ? "拿不准" : "确定"}
                  </span>
                </label>
                <textarea id={`intent-${key}`} rows={2} value={arrText(card[key])}
                  placeholder={FIELD_HINT[key]}
                  onChange={(e) => setArrayField(key, textArr(e.target.value))} />
                <p className="field-note">{FIELD_HINT[key]}</p>
                {unc && (
                  <div className="intent-uncertain">
                    <p className="field-note">AI 说:{unc.reason}</p>
                    <div className="title-chips">
                      {unc.candidates.map((c) => (
                        <button key={c} type="button"
                          className={"title-chip" + ((card[key] ?? []).includes(c) ? " on" : "")}
                          onClick={() => pickCandidate(key, c)}>{c}</button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* 整卡重出:一句人话比逐字段改快得多(「不是架空,就是真实历史」) */}
        <div className="input-row mt-2">
          <input type="text" value={feedback} maxLength={500} disabled={busy}
            placeholder="哪里读歪了?跟 AI 说一句,整张卡重出(如:不是架空,就是真实历史)"
            onChange={(e) => setFeedback(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && feedback.trim() && project) {
                void readback(project, feedback);
              }
            }} />
          <button className="btn-sm" disabled={busy || !feedback.trim() || !project}
            onClick={() => project && void readback(project, feedback)}>按这句重出理解卡</button>
        </div>
        {cardBusy && (
          <div className="sub-summary mt-2">
            <span className="spin" /><ThinkingText phrases={THINK_INTENT} />{cardBusy}
            <span style={{ marginLeft: 12 }}>
              <button className="btn-sm" type="button" onClick={cancelBusy}>终止等待</button>
            </span>
          </div>
        )}

        {/* 三问降级为可选工具:不再是关卡,只在拿不准时点开要点候选 */}
        <details className="intent-tools mt-3">
          <summary>拿不准?让 AI 出候选(可选)</summary>
          <div className="card-desc mt-2">
            候选只是给你个下手的地方:点了就填进下面的答案,出方向卡时会带上;不点也行。
          </div>
          {questions?.map((q) => (
            <div className="pref-row mt-2" key={q.key}>
              <span className="pref-label">{q.title}<small>跳过 = AI 定</small></span>
              <div className="title-chips">
                {q.candidates.map((c) => (
                  <button key={c.text} type="button"
                    className={"title-chip" + (qAnswers[q.key] === c.text ? " on" : "")}
                    title={c.recommended ? `★ AI 首推:${c.reason}` : c.reason || undefined}
                    onClick={() => setQAnswers((prev) => ({
                      ...prev, [q.key]: prev[q.key] === c.text ? "" : c.text,
                    }))}>
                    {c.recommended ? "★ " : ""}{c.text}
                  </button>
                ))}
              </div>
            </div>
          ))}
          <div className="actions mt-2">
            <button className="btn-sm" disabled={busy} onClick={() => void fetchQuestions()}>
              {questions ? "🎲 换一批候选" : "🎲 出候选"}
            </button>
            {!!questions && (
              <button className="btn-sm" onClick={() => {
                const next: Record<string, string> = {};
                for (const q of questions) {
                  next[q.key] = q.candidates.find((c) => c.recommended)?.text ?? "";
                }
                setQAnswers(next);
              }}>★ 全部按推荐来</button>
            )}
          </div>
        </details>
      </div>

      {/* ---------- ② 方向卡墙 ---------- */}
      <div className="card">
        <h2>挑一套{isShort ? "短故事" : "整书"}方向</h2>
        <div className="card-desc">
          方向卡是这套书的骨架(内核/开场冲突/第一回报/首卷走向…);挑中一套,确认① 会把它
          渲染成开书订单——后续所有生成都照它走。差点意思就「改改再选」说一句,或再来三套。
        </div>

        {planBusy && (
          <div className="sub-summary mt-2">
            <span className="spin" />{planBusy}
            <span style={{ marginLeft: 12 }}>
              <button className="btn-sm" type="button" onClick={cancelBusy}>终止等待</button>
            </span>
          </div>
        )}

        {!plans && !planBusy && (
          <div className="actions mt-3">
            <button className="primary" onClick={() => void genPlans()}>
              🎲 出三套{isShort ? "短故事" : "整书"}方向 →
            </button>
            <span className="hint">理解卡已经定住了大方向,这里出的是同一方向上的三套具体走法。</span>
          </div>
        )}

        {!!plans && (
          <div className="plans-wall mt-3">
            {plans.map((plan, i) => {
              const on = selected === i;
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
                    <button className={on ? "primary" : ""} disabled={busy}
                      onClick={() => setSelected(on ? null : i)}>
                      {on ? "✓ 就是这套" : "选这套"}
                    </button>
                    <button className="btn-sm" disabled={busy}
                      onClick={() => {
                        setReviseOpen(reviseOpen === i ? null : i);
                        setReviseText(""); setLockedFields([]);
                      }}>✎ 改改再选</button>
                    <button className="btn-sm" disabled={busy}
                      onClick={() => setEditIndex(i)}>✎ 手动改</button>
                  </div>
                  {reviseOpen === i && (
                    <>
                      <details className="mt-2"><summary>这次保持不变的部分</summary>
                        <div className="form-grid">
                          {PLAN_FIELDS.filter((f) => plan[f.key]).map((f) => (
                            <label className="field" key={f.key}>
                              <span>
                                <input type="checkbox" disabled={busy}
                                  checked={lockedFields.includes(f.key)}
                                  onChange={(e) => setLockedFields((keys) => e.target.checked
                                    ? [...keys, f.key] : keys.filter((k) => k !== f.key))} /> {f.label}
                              </span>
                            </label>
                          ))}
                        </div>
                      </details>
                      <div className="input-row mt-2">
                        <input type="text" value={reviseText} maxLength={300} disabled={busy}
                          placeholder="如:主角换成女性,基调再冷一点"
                          onChange={(e) => setReviseText(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && reviseText.trim()) {
                              void revisePlan(i, reviseText.trim(), lockedFields);
                              setReviseOpen(null);
                            }
                          }} />
                        <button className="btn-sm primary" disabled={busy || !reviseText.trim()}
                          onClick={() => {
                            void revisePlan(i, reviseText.trim(), lockedFields);
                            setReviseOpen(null);
                          }}>按这句改这张卡</button>
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {!!plans && (
          <>
            <div className="input-row mt-3">
              <input type="text" value={planFeedback} maxLength={300} disabled={busy}
                placeholder="想引导方向?跟 AI 说一句想要什么(再来三套/出候选都会带上)"
                onChange={(e) => setPlanFeedback(e.target.value)} />
            </div>
            <div className="actions mt-2">
              <button className="btn-sm" disabled={busy} onClick={() => void genPlans()}>🎲 再来三套</button>
            </div>
          </>
        )}

        {/* 确认①:主按钮就在卡墙正下方——旧链的拍板出口藏在底部一行 btn-sm 里,
            移动端换行后极易漏看(用户反馈「选完没有按钮进下一步」,2026-10-05) */}
        <div className="form-actions mt-3">
          <button className="primary" disabled={busy || selected === null} onClick={() => void confirmIntent()}>
            {confirmBusy ? "正在起样稿链…"
              : selected === null ? "先挑一套方向,再确认 →"
              : `✓ 就按这个来,先写三章给我看 →`}
          </button>
          {selected === null && (
            <span className="form-actions-tip">确认① 之后 AI 先写前三章正文(分钟级),读完再决定整本。</span>
          )}
          <span className="grow" />
          <button onClick={onBack} disabled={busy}>← 回想法屏</button>
        </div>
        {err && <div className="msg-err mt-2">{err}</div>}
      </div>

      {/* 手动改方向卡弹层(改完原地更新卡面) */}
      {editIndex !== null && plans?.[editIndex] && (
        <PlanEditDialog pid={pid} mode={mode} index={editIndex} plan={plans[editIndex]}
          onSaved={(next, p) => { setPlans(next); setProject(p); }}
          onClose={() => setEditIndex(null)} />
      )}
    </>
  );
}
