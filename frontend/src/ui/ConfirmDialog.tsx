// 全局确认弹层:替代散落各处的行内二次确认。
// 用法:const ok = await confirmDialog({ title: "删除项目?", body: "不可恢复", danger: true });
import * as Dialog from "@radix-ui/react-dialog";
import { useSyncExternalStore } from "react";

interface ConfirmOptions {
  title: string;
  body?: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

interface Pending extends ConfirmOptions { resolve: (ok: boolean) => void; }

let _pending: Pending | null = null;
const _listeners = new Set<() => void>();

export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    // 已有弹层未决时,新请求直接取消旧的(不排队,后者优先)
    _pending?.resolve(false);
    _pending = { ...opts, resolve };
    _listeners.forEach((l) => l());
  });
}

function settle(ok: boolean) {
  _pending?.resolve(ok);
  _pending = null;
  _listeners.forEach((l) => l());
}

export function ConfirmHost() {
  const pending = useSyncExternalStore(
    (cb) => { _listeners.add(cb); return () => _listeners.delete(cb); },
    () => _pending,
  );
  if (!pending) return null;
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) settle(false); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dlg-overlay" />
        <Dialog.Content className="dlg-content" onEscapeKeyDown={() => settle(false)}>
          <Dialog.Title className="dlg-title">{pending.title}</Dialog.Title>
          {pending.body && (
            <Dialog.Description className="dlg-body">{pending.body}</Dialog.Description>
          )}
          <div className="dlg-actions">
            <button onClick={() => settle(false)}>{pending.cancelText ?? "取消"}</button>
            <button
              className={pending.danger ? "danger" : "primary"}
              autoFocus
              onClick={() => settle(true)}
            >
              {pending.confirmText ?? "确认"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
