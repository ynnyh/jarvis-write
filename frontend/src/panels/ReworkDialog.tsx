// 重来向导:把「写差了想从头重来」这一整条跨层决策收进一个对话框。
// 先亮现状与代价账,再选「从哪一层重来」,按链带你去起点——不自动删东西,
// 每个被跳转的面板都各自带防误删确认(且确认框里已算好代价)。
// 复用 Radix Dialog + 既有 dlg-* 结构,与 confirmDialog 一个观感(遮挡点击/ESC 关闭免费)。
import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Architecture } from "../api";
import { confirmDialog } from "../ui/ConfirmDialog";
import { errMsg } from "../pollJob";

export type ReworkStart = "inspire" | "arch" | "outline" | "write";

interface Props {
  arch: Architecture | null;
  outlinesCount: number; doneCount: number; wordsTotal: number;
  onGo: (start: ReworkStart) => void;
  onWipe: () => Promise<void>;
  onClose: () => void;
}

const DEPTHS: { key: ReworkStart; label: string; keep: string; redo: string; costWhen: (o: number) => string }[] = [
  {
    key: "inspire", label: "从概念重来(最深)",
    keep: "不保留(概念一并换)",
    redo: "推倒重出一批 → 定概念 → 重生成架构 → 重铺蓝图 → 正文",
    costWhen: () => "整条链都换,现有正文不做保留即标为失配。",
  },
  {
    key: "arch", label: "从架构重来",
    keep: "概念 / DNA",
    redo: "重生成架构 → 重铺蓝图 → 正文",
    costWhen: (done) => done > 0 ? "现有正文会标「失配」,可逐章重写或清空重来。" : "正文尚未写,无正文代价。",
  },
  {
    key: "outline", label: "重铺蓝图",
    keep: "概念 / 架构",
    redo: "重铺蓝图 → 正文",
    costWhen: (done) => done > 0 ? "内容变化的蓝图会令对应章正文标「失配」。" : "",
  },
  {
    key: "write", label: "只重写正文",
    keep: "概念 / 架构 / 蓝图",
    redo: "对失配章逐章重写",
    costWhen: (o) => `不碰大纲;仅处理已失配的章(现有蓝图 ${o} 章)。`,
  },
];

export default function ReworkDialog({ arch, outlinesCount, doneCount, wordsTotal, onGo, onWipe, onClose }: Props) {
  const [depth, setDepth] = useState<ReworkStart>("arch");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const opt = DEPTHS.find((d) => d.key === depth)!;

  async function wipeAll() {
    const ok = await confirmDialog({
      title: "清空正文与蓝图,从新架构重来?",
      body: `将删除 ${doneCount} 章正文与 ${outlinesCount} 章蓝图(含正文历史/摘要/事实账本/伏笔,不可恢复)。架构、概念、DNA、简介、手法卡保留。`,
      confirmText: "清空并重来",
      danger: true,
    });
    if (!ok) return;
    setBusy("正在清空正文与大纲…"); setErr("");
    try { await onWipe(); setBusy(""); onClose(); }
    catch (e) { setErr(errMsg(e)); setBusy(""); }
  }

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dlg-overlay" />
        <Dialog.Content className="rework-dlg dlg-content" onEscapeKeyDown={() => { if (!busy) onClose(); }}>
          <Dialog.Title className="dlg-title">从哪一层开始重来?</Dialog.Title>
          <Dialog.Description className="dlg-body" asChild>
            <div>
              当前:架构 {arch ? `v${arch.version}` : "未生成"} · 蓝图 {outlinesCount ? `${outlinesCount} 章` : "未生成"} · 正文 {doneCount ? `${doneCount} 章 · ${wordsTotal.toLocaleString()} 字` : "未开始"}。选重来深度,带去起点;每一层都能随时停下,不自动删数据。
            </div>
          </Dialog.Description>

          <div className="rework-depths">
            {DEPTHS.map((d) => (
              <button key={d.key} type="button"
                className={"rework-depth" + (depth === d.key ? " on" : "")}
                onClick={() => setDepth(d.key)}>
                <span className="rd-label">{d.label}</span>
                <span className="rd-meta muted">保留:{d.keep}<br />重做:{d.redo}</span>
              </button>
            ))}
          </div>

          <div className="card card-warn mt-3">
            <span className="rd-cost"><b>这一步的代价:</b> {opt.costWhen(doneCount)}</span>
          </div>
          {err && <div className="msg-err mt-2">{err}</div>}

          <div className="dlg-actions">
            {busy && <span className="muted">{busy}</span>}
            <button className="btn-sm" disabled={!!busy} onClick={onClose}>取消</button>
            {(doneCount > 0 || outlinesCount > 0) && (
              <button className="btn-sm danger" disabled={!!busy} onClick={wipeAll}>清空正文与蓝图重来</button>
            )}
            <button className="btn-sm primary" disabled={!!busy} onClick={() => { onGo(depth); onClose(); }}>
              开始,带去起点 →
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}