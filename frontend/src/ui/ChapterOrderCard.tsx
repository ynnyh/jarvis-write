// ChapterOrderCard — 本章订单编辑器(docs/20 订单制 §5):写前确认单。
// 六单(人物进出/关系变动/节拍/钩子/伏笔/自由指令)逐项编辑,「确认并按单生成」
// 把确认后的订单作为生成依据(后端槽位替换蓝图行);未确认的章照旧按蓝图行生成,
// 结果卡会如实标注。预填零 LLM:大纲 + 上章契约钩子 + 到期伏笔,与作战图同源。
import { useMemo, useState, ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, OrderPayload, OrderPrefill } from "../api";
import { qk } from "../hooks/queries";
import { errMsg } from "../pollJob";
import { toast } from "./Toaster";

function emptyPayload(): OrderPayload {
  return {
    cast: { entering: [], present: [], exiting: [] },
    relations: [],
    beats: [],
    hooks: { carry_in: [], leave: "" },
    foreshadow: { plant: [] },
    scenes: [],
    free_directive: "",
  };
}

function mergePrefill(p: OrderPrefill): OrderPayload {
  const base = emptyPayload();
  return {
    ...base,
    cast: { entering: p.cast.entering, present: p.cast.present, exiting: p.cast.exiting },
    relations: p.relations,
    beats: p.beats,
    hooks: p.hooks,
    foreshadow: p.foreshadow,
  };
}

const EXIT_MODES = ["死别", "远行", "暂别", "退隐"];

export default function ChapterOrderCard({ pid, n, onGenerate }: {
  pid: number;
  n: number;
  /** 「确认并按单生成」要触发的本章生成动作(写作区传入;无则只存不生成) */
  onGenerate?: (n: number) => void;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<OrderPayload | null>(null);
  const [busy, setBusy] = useState(false);
  const order = useQuery({
    queryKey: qk.chapterOrder(pid, n),
    queryFn: () => api.getChapterOrder(pid, n),
  });

  const confirmed = order.data?.order?.status === "confirmed";
  const version = order.data?.order?.version ?? 0;

  // 展开时初始化编辑稿:已有订单用订单,否则用预填(保存前不动后端)
  const effective: OrderPayload = useMemo(() => {
    if (draft) return draft;
    if (order.data?.order) return order.data.order.payload;
    if (order.data?.prefill) return mergePrefill(order.data.prefill);
    return emptyPayload();
  }, [draft, order.data]);

  function edit(fn: (d: OrderPayload) => OrderPayload) {
    setDraft(fn(structuredClone(effective)));
  }

  async function save(): Promise<OrderPayload | null> {
    setBusy(true);
    try {
      await api.saveChapterOrder(pid, n, effective);
      await qc.invalidateQueries({ queryKey: qk.chapterOrder(pid, n) });
      setDraft(null);
      toast.ok("订单已存草稿", "确认后才按单生成");
      return effective;
    } catch (e) {
      toast.err("保存失败", errMsg(e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function confirmAndGenerate() {
    setBusy(true);
    try {
      await api.confirmChapterOrder(pid, n, effective);
      await qc.invalidateQueries({ queryKey: qk.chapterOrder(pid, n) });
      setDraft(null);
      toast.ok(`订单 v${version + 1} 已确认`, "本次生成按订单执行");
      onGenerate?.(n);
    } catch (e) {
      toast.err("确认失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function unconfirm() {
    setBusy(true);
    try {
      await api.unconfirmChapterOrder(pid, n);
      await qc.invalidateQueries({ queryKey: qk.chapterOrder(pid, n) });
      toast.ok("已撤回确认", "之后的生成按蓝图行执行");
    } catch (e) {
      toast.err("撤回失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  const statusChip = confirmed
    ? <span className="chip">已确认 v{version} · 按单生成</span>
    : order.data?.order
      ? <span className="chip chip-warn">草稿未确认 · 按蓝图行生成</span>
      : <span className="chip chip-warn">未建单 · 按蓝图行生成</span>;

  return (
    <div className="dossier-block order-card" data-testid="chapter-order">
      <div className="dossier-block-title">
        📜 本章订单
        <span className="grow" />
        {statusChip}
        <button className="btn-sm" onClick={() => setOpen(!open)}>
          {open ? "收起" : "编辑订单"}
        </button>
      </div>

      {!open ? (
        // 收起态:一屏看清订单要点(确认过的是什么,一眼可核)
        <OrderSummary payload={effective} confirmed={confirmed} />
      ) : (
        <div className="order-edit mt-1">
          {/* 人物进出单 */}
          <OrderSec title="人物进出">
            <div className="muted">必登场(本章首秀/必须出场)</div>
            {effective.cast.entering.map((e, i) => (
              <div className="order-row" key={i}>
                <input className="order-in name" placeholder="人名" value={e.name}
                  onChange={(ev) => edit((d) => { d.cast.entering[i].name = ev.target.value; return d; })} />
                <input className="order-in grow" placeholder="为何此时入场(一句)" value={e.reason}
                  onChange={(ev) => edit((d) => { d.cast.entering[i].reason = ev.target.value; return d; })} />
                <button className="btn-sm" onClick={() => edit((d) => { d.cast.entering.splice(i, 1); return d; })}>删</button>
              </div>
            ))}
            <button className="btn-sm" onClick={() => edit((d) => { d.cast.entering.push({ name: "", reason: "" }); return d; })}>+ 必登场</button>

            <div className="muted mt-1">在场(逗号分隔,可增删)</div>
            <input className="order-in full" value={effective.cast.present.join("、")}
              onChange={(ev) => edit((d) => {
                d.cast.present = ev.target.value.split(/[、,，]/).map((s) => s.trim()).filter(Boolean);
                return d;
              })} />

            <div className="muted mt-1">本章退场(下场方式 + 退场前须收的线)</div>
            {effective.cast.exiting.map((e, i) => (
              <div className="order-row" key={i}>
                <input className="order-in name" placeholder="人名" value={e.name}
                  onChange={(ev) => edit((d) => { d.cast.exiting[i].name = ev.target.value; return d; })} />
                <select value={e.mode} onChange={(ev) => edit((d) => { d.cast.exiting[i].mode = ev.target.value; return d; })}>
                  {!EXIT_MODES.includes(e.mode) && <option value="">方式</option>}
                  {EXIT_MODES.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
                <input className="order-in grow" placeholder="退场前须收的线(可空)" value={e.threads}
                  onChange={(ev) => edit((d) => { d.cast.exiting[i].threads = ev.target.value; return d; })} />
                <button className="btn-sm" onClick={() => edit((d) => { d.cast.exiting.splice(i, 1); return d; })}>删</button>
              </div>
            ))}
            <button className="btn-sm" onClick={() => edit((d) => { d.cast.exiting.push({ name: "", mode: "远行", threads: "" }); return d; })}>+ 退场</button>
          </OrderSec>

          {/* 关系变动单 */}
          <OrderSec title="关系变动">
            {effective.relations.map((r, i) => (
              <div className="order-row" key={i}>
                <input className="order-in name" placeholder="谁" value={r.from}
                  onChange={(ev) => edit((d) => { d.relations[i].from = ev.target.value; return d; })} />
                <input className="order-in name" placeholder="与谁" value={r.to}
                  onChange={(ev) => edit((d) => { d.relations[i].to = ev.target.value; return d; })} />
                <input className="order-in" placeholder="由(现状)" value={r.before}
                  onChange={(ev) => edit((d) => { d.relations[i].before = ev.target.value; return d; })} />
                <input className="order-in" placeholder="变(变为)" value={r.after}
                  onChange={(ev) => edit((d) => { d.relations[i].after = ev.target.value; return d; })} />
                <input className="order-in grow" placeholder="触发事件" value={r.event}
                  onChange={(ev) => edit((d) => { d.relations[i].event = ev.target.value; return d; })} />
                <button className="btn-sm" onClick={() => edit((d) => { d.relations.splice(i, 1); return d; })}>删</button>
              </div>
            ))}
            <button className="btn-sm" onClick={() => edit((d) => {
              d.relations.push({ from: "", to: "", before: "", after: "", event: "" });
              return d;
            })}>+ 关系变动</button>
          </OrderSec>

          {/* 节拍单 */}
          <OrderSec title="节拍(一行一拍,3-5 拍)">
            <textarea className="order-in full" rows={4}
              placeholder={"1. 这拍砸什么\n2. …"}
              value={effective.beats.join("\n")}
              onChange={(ev) => edit((d) => {
                d.beats = ev.target.value.split("\n").map((s) => s.replace(/^\d+[.、]\s*/, "").trim()).filter(Boolean);
                return d;
              })} />
          </OrderSec>

          {/* 钩子单 */}
          <OrderSec title="钩子">
            <div className="muted">承上(上章末留下的线;勾选 = 本章必须回收)</div>
            {effective.hooks.carry_in.map((h, i) => (
              <div className="order-row" key={i}>
                <label className="order-check">
                  <input type="checkbox" checked={h.must}
                    onChange={(ev) => edit((d) => { d.hooks.carry_in[i].must = ev.target.checked; return d; })} />
                  必收
                </label>
                <input className="order-in grow" value={h.text}
                  onChange={(ev) => edit((d) => { d.hooks.carry_in[i].text = ev.target.value; return d; })} />
                <button className="btn-sm" onClick={() => edit((d) => { d.hooks.carry_in.splice(i, 1); return d; })}>删</button>
              </div>
            ))}
            <div className="muted mt-1">章末留钩</div>
            <input className="order-in full" placeholder="本章末留下什么钩子(可空)" value={effective.hooks.leave}
              onChange={(ev) => edit((d) => { d.hooks.leave = ev.target.value; return d; })} />
          </OrderSec>

          {/* 伏笔单 */}
          <OrderSec title="伏笔">
            {!!effective.foreshadow.due?.length && (
              <>
                <div className="muted">到期应收(自动带出)</div>
                {effective.foreshadow.due.map((f) => (
                  <div className="order-row" key={f.id}><span className="chip">应收</span>{f.description}</div>
                ))}
              </>
            )}
            <div className="muted mt-1">本章新埋(一行一条)</div>
            <textarea className="order-in full" rows={2} value={(effective.foreshadow.plant ?? []).join("\n")}
              onChange={(ev) => edit((d) => {
                d.foreshadow.plant = ev.target.value.split("\n").map((s) => s.trim()).filter(Boolean);
                return d;
              })} />
          </OrderSec>

          {/* 自由指令 */}
          <OrderSec title="作者指令(必须落实,可空)">
            <textarea className="order-in full" rows={2} value={effective.free_directive}
              onChange={(ev) => edit((d) => { d.free_directive = ev.target.value; return d; })} />
          </OrderSec>

          <div className="order-actions mt-2">
            <button className="btn-sm" disabled={busy} onClick={() => { void save(); }}>存草稿</button>
            {confirmed && <button className="btn-sm" disabled={busy} onClick={() => { void unconfirm(); }}>撤回确认</button>}
            <button className="primary" disabled={busy}
              title="确认订单并用它生成本章;生成提示词里的简述/节拍/人物/伏笔将按订单执行"
              onClick={() => { void confirmAndGenerate(); }}>
              确认订单 · 按单生成
            </button>
            {!onGenerate && <span className="muted">(当前视图只存不生成)</span>}
          </div>
        </div>
      )}
    </div>
  );
}

/** 收起态摘要:确认过的订单要点一眼可核;空单如实说 */
function OrderSummary({ payload, confirmed }: { payload: OrderPayload; confirmed: boolean }) {
  const bits: string[] = [];
  const { entering, present, exiting } = payload.cast;
  if (entering.length) bits.push(`必登场:${entering.map((e) => e.name).filter(Boolean).join("、")}`);
  if (present.length) bits.push(`在场:${present.join("、")}`);
  if (exiting.length) bits.push(`退场:${exiting.map((e) => e.name).filter(Boolean).join("、")}`);
  if (payload.relations.length) {
    bits.push(`关系变动:${payload.relations.map((r) => `${r.from}—${r.to}→${r.after}`).filter((s) => !s.endsWith("→")).join(";")}`);
  }
  if (payload.beats.length) bits.push(`节拍 ${payload.beats.length} 拍`);
  const mustHooks = payload.hooks.carry_in.filter((h) => h.must && h.text).length;
  if (mustHooks) bits.push(`承上必收 ${mustHooks} 条`);
  if (payload.hooks.leave) bits.push(`留钩:${payload.hooks.leave}`);
  if ((payload.foreshadow.plant ?? []).length) bits.push(`新埋伏笔 ${(payload.foreshadow.plant ?? []).length} 条`);
  if (payload.free_directive) bits.push("含作者指令");
  if (!bits.length) {
    return <div className="muted">还没写订单——按蓝图行生成;想精确控制本章人物与节拍,点「编辑订单」。</div>;
  }
  return (
    <div className="order-summary">
      {bits.map((b, i) => <div className="dossier-line" key={i}>{b}</div>)}
      {!confirmed && <div className="muted">以上为草稿,确认后才按单生成。</div>}
    </div>
  );
}

function OrderSec({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="order-sec">
      <div className="order-sec-title">{title}</div>
      {children}
    </div>
  );
}
