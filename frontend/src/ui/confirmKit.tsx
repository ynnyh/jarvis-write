// 统一确认语言组件库(拍板链四件套的缺省三件,CandidateCards 是第四件「提案卡」)。
// 全项目「AI 发散、人收敛」的交互在这里定语义,各线换装接入:
//   - ConfirmGate:AI 产出后「人点头」——拍板/撤回/可选锁定。拍板态是下游的硬约束。
//   - StaleBadge:上游改动后下游产物的「已作废」可见标记(改动明示作废,人决定重跑)。
//   - DirectiveBar:带话重出的统一输入(一次性话 + 常驻话管理,常驻话直到人摘掉才失效)。
// 语义约定(docs 确认是权利不是门槛):任何拍板都可撤回;作废只标记不自动销毁。
import { useState } from "react";

export function ConfirmGate({
  confirmed, onConfirm, onUnconfirm,
  confirmText = "拍板", confirmedText = "✓ 已拍板", unconfirmText = "撤回",
  locked, onToggleLock,
  disabled, confirmTitle, unconfirmTitle, lockTitleOn, lockTitleOff,
}: {
  confirmed: boolean;
  /** 未拍板态渲染;缺省则不渲染拍板钮 */
  onConfirm?: () => void;
  /** 已拍板态渲染;缺省则不渲染撤回钮 */
  onUnconfirm?: () => void;
  confirmText?: string;
  confirmedText?: string;
  unconfirmText?: string;
  /** 锁定(可选):锁定的产物在批量重出时保留 */
  locked?: boolean;
  onToggleLock?: () => void;
  disabled?: boolean;
  confirmTitle?: string;
  unconfirmTitle?: string;
  lockTitleOn?: string;
  lockTitleOff?: string;
}) {
  return (
    <span className="ck-gate">
      {confirmed ? (
        <>
          <span className="ck-state" data-testid="ck-confirmed">{confirmedText}</span>
          {onUnconfirm && (
            <button className="btn-sm" disabled={disabled} title={unconfirmTitle ?? "撤回拍板,回到可编辑"}
              onClick={onUnconfirm}>{unconfirmText}</button>
          )}
        </>
      ) : (
        onConfirm && (
          <button className="btn-sm ck-confirm" disabled={disabled} title={confirmTitle ?? "这一版我认了"}
            onClick={onConfirm}>{confirmText}</button>
        )
      )}
      {onToggleLock !== undefined && (
        <button className="btn-sm" disabled={disabled}
          title={locked ? (lockTitleOff ?? "解锁(重出时会重出这一项)") : (lockTitleOn ?? "锁定(重出时保留这一项)")}
          onClick={onToggleLock}>{locked ? "🔒" : "🔓"}</button>
      )}
    </span>
  );
}

export function StaleBadge({
  stale, reason, onAction, actionText, actionTitle,
}: {
  stale: boolean;
  reason?: string;
  onAction?: () => void;
  actionText?: string;
  actionTitle?: string;
}) {
  if (!stale) return null;
  return (
    <span className="ck-stale" data-testid="ck-stale">
      <span className="ck-stale-text">⚠ 已作废{reason ? `:${reason}` : ",需重跑"}</span>
      {onAction && actionText && (
        <button className="btn-sm" title={actionTitle ?? "按上游最新内容重出这一项"} onClick={onAction}>
          {actionText}
        </button>
      )}
    </span>
  );
}

export function DirectiveBar({
  onSend, persistent, onClearPersistent, busy, disabled,
  placeholder = "带句话重出,如「节奏再快一点」", sendText = "带话重出",
  sendTitle, persistentLabel = "常驻要求",
}: {
  onSend: (text: string) => void;
  /** 常驻要求:带话重出后一直生效,直到人点掉 */
  persistent?: string;
  onClearPersistent?: () => void;
  busy?: boolean;
  disabled?: boolean;
  placeholder?: string;
  sendText?: string;
  sendTitle?: string;
  persistentLabel?: string;
}) {
  const [text, setText] = useState("");
  const send = () => {
    const t = text.trim();
    if (!t || disabled || busy) return;
    onSend(t);
    setText("");
  };
  return (
    <div className="ck-directive">
      <input value={text} disabled={disabled || busy} maxLength={200} placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); send(); } }} />
      <button className="btn-sm" disabled={disabled || busy || !text.trim()} title={sendTitle ?? "AI 会带着这句话重新出这一项"}
        onClick={send}>{busy ? "重出中…" : sendText}</button>
      {persistent ? (
        <span className="ck-persistent" title={`${persistentLabel}:${persistent}`}>
          <span className="muted">{persistentLabel}:</span>
          <span>{persistent}</span>
          {onClearPersistent && (
            <button aria-label="不再带这条" title="不再带这条" onClick={onClearPersistent}>✕</button>
          )}
        </span>
      ) : null}
    </div>
  );
}
