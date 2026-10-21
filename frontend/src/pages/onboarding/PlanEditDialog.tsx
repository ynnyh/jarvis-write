// PlanEditDialog — 方向卡手动编辑弹层(2026-10-21 用户反馈「方向卡内容无法修改」)。
//
// 为什么是弹层而不是卡面原地 textarea:方向卡一格动辄几百字(开场试读尤其长),
// 原地文本框要么高度撑爆卡墙、要么缩成一条看不见内容的缝;卡面保持只读全文展示,
// 点「✎ 手动改」进弹层整卡铺开,每格高度自适应内容,改完一次保存。
// 与「✎ 改改再选」的分工:那个指挥 AI 改意思(烧一次 LLM 调用),这里改字面
// (纯落库,不调模型)。上限与后端 _sanitize_manual_plan 同一套,前端先拦、后端再裁。
import { useLayoutEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { api, BookPlan, Project } from "../../api";
import { errMsg } from "../../pollJob";
import { toast } from "../../ui/Toaster";

const TEXT_CAP = 600;        // 文本格上限(与后端 LLM 出卡同一套)
const FLAVOR_ITEM_CAP = 20;  // 味道单条上限
const FLAVOR_MAX = 4;        // 味道最多几条

type TextKey = "title" | "kernel" | "opening" | "payoff" | "escalation" | "mechanism"
  | "opening_sample" | "protagonist" | "world" | "arc" | "engine" | "ending";

// 编辑格顺序与卡面展示(IntentScreen PLAN_FIELDS)一致,书名排最前;rows 只是起始高度
const FIELDS: { key: TextKey; label: string; rows: number }[] = [
  { key: "title", label: "书名", rows: 1 },
  { key: "kernel", label: "内核", rows: 3 },
  { key: "opening", label: "开场冲突", rows: 3 },
  { key: "payoff", label: "第一回报", rows: 3 },
  { key: "escalation", label: "后续困境/代价", rows: 3 },
  { key: "mechanism", label: "故事机制", rows: 3 },
  { key: "opening_sample", label: "开场试读", rows: 6 },
  { key: "protagonist", label: "主角", rows: 3 },
  { key: "world", label: "世界观", rows: 3 },
  { key: "arc", label: "首卷走向", rows: 3 },
  { key: "engine", label: "连载引擎", rows: 2 },
  { key: "ending", label: "结尾落在", rows: 2 },
];

// 必填格与后端同一套:连载要 engine,短故事要 ending
const REQUIRED_SERIAL: TextKey[] = ["title", "kernel", "protagonist", "world", "arc", "engine"];
const REQUIRED_SHORT: TextKey[] = ["title", "kernel", "protagonist", "world", "arc", "ending"];

const LABEL: Record<string, string> = Object.fromEntries(FIELDS.map((f) => [f.key, f.label]));

/** 自适应高度 textarea:高度跟内容走,绝不出现「缝里看内容」 */
function GrowTextarea(props: {
  value: string; rows: number; maxLength?: number;
  onChange: (v: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + 2}px`;
  }, [props.value]);
  return (
    <textarea ref={ref} className="plan-edit-ta" rows={props.rows}
      maxLength={props.maxLength} value={props.value}
      onChange={(e) => props.onChange(e.target.value)} />
  );
}

interface Props {
  pid: number;
  mode: string;          // serial | short | drama
  index: number;         // 改的是第几套
  plan: BookPlan;
  onSaved: (plans: BookPlan[], project: Project) => void;
  onClose: () => void;
}

export default function PlanEditDialog({ pid, mode, index, plan, onSaved, onClose }: Props) {
  const [draft, setDraft] = useState<BookPlan>(plan);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const required = mode === "short" ? REQUIRED_SHORT : REQUIRED_SERIAL;

  function set(key: TextKey, value: string) {
    setDraft((d) => ({ ...d, [key]: value }) as BookPlan);
  }

  async function save() {
    const missing = required.filter((k) => !String(draft[k] ?? "").trim());
    if (missing.length) {
      setErr(`这些格子不能空着:${missing.map((k) => LABEL[k]).join("、")}`);
      return;
    }
    const flavor = draft.flavor ?? [];
    if (flavor.length > FLAVOR_MAX) {
      setErr(`味道最多 ${FLAVOR_MAX} 条,现在有 ${flavor.length} 条`);
      return;
    }
    if (flavor.some((x) => x.length > FLAVOR_ITEM_CAP)) {
      setErr(`味道每条不超过 ${FLAVOR_ITEM_CAP} 字`);
      return;
    }
    setErr(""); setBusy(true);
    try {
      const r = await api.updatePlan(pid, index, draft);
      toast.ok("已保存", "改的是这张卡的原文,后续确认就按改后的来");
      onSaved(r.plans, r.project);
      onClose();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog.Root open onOpenChange={(o) => { if (!o && !busy) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dlg-overlay" />
        <Dialog.Content className="dlg-content plan-edit-dlg"
          onEscapeKeyDown={(e) => { if (busy) e.preventDefault(); }}>
          <Dialog.Title className="dlg-title">手动改这套方向</Dialog.Title>
          <div className="card-desc mt-2">
            直接改字面,不经过 AI——保存后这张卡就按你改的算,后续确认用它渲染开书订单。
            想让 AI 按一句话重写,用卡上的「✎ 改改再选」。
          </div>

          <div className="plan-edit-grid mt-3">
            {FIELDS.map(({ key, label, rows }) => {
              // 连载不展示「结尾落在」、短故事不展示「连载引擎」(与开书订单字段对齐)
              if (mode !== "short" && key === "ending") return null;
              if (mode === "short" && key === "engine") return null;
              return (
                <div className="field" key={key}>
                  <label className="fl">{label}</label>
                  <GrowTextarea value={String(draft[key] ?? "")} rows={rows}
                    maxLength={TEXT_CAP} onChange={(v) => set(key, v)} />
                </div>
              );
            })}

            <div className="field">
              <label className="fl">味道</label>
              <GrowTextarea value={(draft.flavor ?? []).join("\n")} rows={2}
                onChange={(v) => setDraft((d) => ({
                  ...d,
                  flavor: v.split("\n").map((s) => s.trim()).filter(Boolean),
                }))} />
              <p className="field-note">一行一条,每条 {FLAVOR_ITEM_CAP} 字内,最多 {FLAVOR_MAX} 条</p>
            </div>

            <div className="plan-edit-meta">
              <div className="field">
                <label className="fl">标签</label>
                <input type="text" maxLength={60} value={draft.label ?? ""}
                  onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))} />
              </div>
              <div className="field">
                <label className="fl">篇幅</label>
                <input type="text" maxLength={10} value={draft.scale ?? ""}
                  onChange={(e) => setDraft((d) => ({ ...d, scale: e.target.value }))} />
              </div>
              <div className="field">
                <label className="fl">篇幅理由</label>
                <input type="text" maxLength={150} value={draft.scale_reason ?? ""}
                  onChange={(e) => setDraft((d) => ({ ...d, scale_reason: e.target.value }))} />
              </div>
            </div>
          </div>

          {err && <div className="msg-err mt-2">{err}</div>}

          <div className="dlg-actions">
            <button disabled={busy} onClick={onClose}>取消</button>
            <button className="primary" disabled={busy} onClick={() => void save()}>
              {busy && <span className="spin" />}保存修改
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
