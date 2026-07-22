// 全局 Toast:Radix Toast + 极简 store。用法:toast.ok("已保存") / toast.err("失败", "详情")
import * as RadixToast from "@radix-ui/react-toast";
import { useSyncExternalStore } from "react";

export interface ToastItem {
  id: number;
  kind: "ok" | "err" | "info";
  title: string;
  detail?: string;
}

let _items: ToastItem[] = [];
let _nextId = 1;
const _listeners = new Set<() => void>();

function emit(kind: ToastItem["kind"], title: string, detail?: string) {
  _items = [..._items, { id: _nextId++, kind, title, detail }];
  _listeners.forEach((l) => l());
}

function dismiss(id: number) {
  _items = _items.filter((t) => t.id !== id);
  _listeners.forEach((l) => l());
}

export const toast = {
  ok: (title: string, detail?: string) => emit("ok", title, detail),
  err: (title: string, detail?: string) => emit("err", title, detail),
  info: (title: string, detail?: string) => emit("info", title, detail),
};

const ICONS = { ok: "✓", err: "✕", info: "ⓘ" } as const;

export function Toaster() {
  const items = useSyncExternalStore(
    (cb) => { _listeners.add(cb); return () => _listeners.delete(cb); },
    () => _items,
  );
  return (
    <RadixToast.Provider swipeDirection="right" duration={4500}>
      {items.map((t) => (
        <RadixToast.Root
          key={t.id}
          className={`toast toast-${t.kind}`}
          onOpenChange={(open) => { if (!open) dismiss(t.id); }}
        >
          <span className="toast-icon">{ICONS[t.kind]}</span>
          <div className="toast-body">
            <RadixToast.Title className="toast-title">{t.title}</RadixToast.Title>
            {t.detail && (
              <RadixToast.Description className="toast-detail">{t.detail}</RadixToast.Description>
            )}
          </div>
          <RadixToast.Close className="toast-close" aria-label="关闭">×</RadixToast.Close>
        </RadixToast.Root>
      ))}
      <RadixToast.Viewport className="toast-viewport" />
    </RadixToast.Provider>
  );
}
