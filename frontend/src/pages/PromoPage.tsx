// 宣传片工坊入口(/promo):列表 + 新建企划,单条进 panels/promo 的工作台。
// 与小说项目无关的独立线;工作台六步(素材点→研讨简报→视觉锚→解说词→分镜提示词→切段/成片包)
// 拆在 panels/promo/ 下,这里只留路由分发与列表——页面文件不再是一坨 600 行。
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { PROMO_STATUS_CN, PromoAngle, PromoDirection, PromoPlanSlim, promoApi } from "../promoApi";
import { toast } from "../ui/Toaster";
import { errMsg } from "../pollJob";
import EmptyState from "../ui/EmptyState";
import { confirmDialog } from "../ui/ConfirmDialog";
import PromoWorkbench from "../panels/promo/PromoWorkbench";

/** 角度/画风选项:列表页新建和工作台①企划表单都要用,一处拉一次。 */
function useMeta() {
  const [meta, setMeta] = useState<{ angles: PromoAngle[]; directions: PromoDirection[] } | null>(null);
  useEffect(() => { void promoApi.meta().then(setMeta).catch(() => {}); }, []);
  return meta;
}

export default function PromoPage() {
  const { id } = useParams();
  const planId = id ? Number(id) : null;
  const meta = useMeta();
  return planId ? <PromoWorkbench pid={planId} meta={meta} /> : <PromoList meta={meta} />;
}

// ================= 列表 + 新建 =================
function PromoList({ meta }: { meta: { angles: PromoAngle[]; directions: PromoDirection[] } | null }) {
  const nav = useNavigate();
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

  async function remove(p: PromoPlanSlim) {
    const ok = await confirmDialog({
      title: `删除企划「${p.title || p.subject}」?`,
      body: "研讨记录、简报、分镜与提示词都会一起删掉,不可恢复。",
      confirmText: "确认删除",
      danger: true,
    });
    if (!ok) return;
    try { await promoApi.remove(p.id); await reload(); }
    catch (err) { toast.err("删除失败", errMsg(err)); }
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
        <div className="form-grid">
          <div className="field field-full">
            <label className="fl" htmlFor="promo-subject">
              主题<span className="hint">城市 / 景区 / 品牌都行,如「西安」</span>
            </label>
            <input id="promo-subject" value={subject} maxLength={60}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="西安" />
          </div>
          <div className="field field-full">
            <span className="fl">角度<span className="hint">可多选,研讨中还会细调</span></span>
            <div className="chips">
              {(meta?.angles ?? []).map((a) => (
                <button key={a.key} type="button"
                  className={"chip" + (angles.includes(a.key) ? " on" : "")}
                  aria-pressed={angles.includes(a.key)}
                  onClick={() => setAngles(angles.includes(a.key)
                    ? angles.filter((x) => x !== a.key) : [...angles, a.key])}>
                  {a.label}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <label className="fl" htmlFor="promo-duration">成片时长</label>
            <select id="promo-duration" value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
              <option value={60}>60 秒</option>
              <option value={90}>90 秒</option>
              <option value={120}>2 分钟</option>
              <option value={180}>3 分钟</option>
            </select>
          </div>
          <div className="field">
            <label className="fl" htmlFor="promo-direction">画风</label>
            <select id="promo-direction" value={direction} onChange={(e) => setDirection(e.target.value)}>
              {(meta?.directions ?? []).map((d) => (
                <option key={d.key} value={d.key}>{d.label}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="form-actions">
          <button className="primary" disabled={busy} onClick={create}>
            {busy ? "建企划中…" : "建企划,开始研讨"}
          </button>
          <span className="form-actions-tip">这些都是研讨的起点,进工作台后还能改。</span>
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
            <button className="btn-sm"
              onClick={(e) => { e.stopPropagation(); void remove(p); }}>删除</button>
          </div>
        </div>
      ))}
    </>
  );
}
