// 全站统一的「一键复制」。用法:<CopyBtn text={x} label="复制整段" /> 或 await copyOrPrompt(x)
//
// 为什么要自己写三层,而不是一句 navigator.clipboard.writeText:
// Clipboard API 只在**安全上下文**(https / localhost)里存在。本站常以
// `http://某个IP:8080` 打开,那里 `navigator.clipboard` 直接是 undefined,
// 调用就抛 —— 这正是「复制失败,请手动选中文本复制」的真凶(不是浏览器不给权限,
// 是这个 API 根本没挂上来)。所以:
//   ① Clipboard API:有就用,最稳,也支持异步权限提示;
//   ② textarea + execCommand("copy"):HTTP 页面照样能用,旧浏览器也吃;
//   ③ 手动复制弹层:文本已替用户全选,按 Ctrl/⌘+C 即可,还能点「再试一次」。
// 前两层覆盖了实际会遇到的全部情况,③ 只是绝不把用户堵死的保险。
import * as Dialog from "@radix-ui/react-dialog";
import { FocusEvent, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { toast } from "./Toaster";

/** 兜底:临时 textarea + execCommand,HTTP 下没有 Clipboard API 时走这条。 */
function execCopy(text: string): boolean {
  const active = document.activeElement as HTMLElement | null;
  const ta = document.createElement("textarea");
  ta.value = text;
  // 必须在渲染树里才能被选中,所以用 1px + opacity:0,不能 display:none;
  // position:fixed 顶到左上角,免得焦点跳动把页面滚走。
  ta.style.cssText =
    "position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;";
  ta.readOnly = true;            // 移动端别弹软键盘
  ta.contentEditable = "true";   // iOS Safari 只肯选「可编辑」元素(与 readOnly 并用是通行解法)
  document.body.appendChild(ta);
  try {
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);  // iOS 只认这个
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    ta.remove();
    active?.focus?.();
  }
}

/** 复制到剪贴板,成功返回 true。纯工具,不弹任何 UI。 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 非安全上下文 / 被权限策略拒绝 / 用户拒授权 → 落到 ② */
  }
  try {
    return execCopy(text);
  } catch {
    return false;
  }
}

// ---- 手动复制兜底层:模块级 store + 一个挂在 App 根上的 Host(与 ConfirmDialog 同款) ----
interface ManualCopy { text: string; label: string; }

let _manual: ManualCopy | null = null;
const _listeners = new Set<() => void>();

function setManual(v: ManualCopy | null) {
  _manual = v;
  _listeners.forEach((l) => l());
}

/** 复制;两层都失败就弹「手动复制」兜底层。返回是否已进剪贴板。 */
export async function copyOrPrompt(text: string, label = "内容"): Promise<boolean> {
  if (!text.trim()) {
    toast.err("内容为空", "没有可复制的内容");
    return false;
  }
  if (await copyText(text)) return true;
  setManual({ text, label });
  return false;
}

/** 手动复制弹层宿主:挂一次在 App 根上即可(见 App.tsx)。 */
export function CopyHost() {
  const manual = useSyncExternalStore(
    (cb) => { _listeners.add(cb); return () => _listeners.delete(cb); },
    () => _manual,
  );
  const ref = useRef<HTMLTextAreaElement>(null);
  const [retried, setRetried] = useState(false);

  // 一打开就全选:用户只需按 Ctrl/⌘+C
  useEffect(() => {
    if (!manual) { setRetried(false); return; }
    const ta = ref.current;
    if (ta) { ta.focus(); ta.select(); }
  }, [manual]);

  if (!manual) return null;

  async function retry() {
    if (await copyText(manual!.text)) {
      toast.ok("已复制", "可以去粘贴了");
      setManual(null);
    } else {
      setRetried(true);
    }
  }

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) setManual(null); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dlg-overlay" />
        <Dialog.Content className="dlg-content dlg-copy" onEscapeKeyDown={() => setManual(null)}>
          <Dialog.Title className="dlg-title">手动复制{manual.label}</Dialog.Title>
          <Dialog.Description className="dlg-body">
            这个浏览器不让页面自动写剪贴板(多见于用 <code>http://</code> 地址打开本站)。
            下面的文本<b>已经替你全选</b>,按 Ctrl+C(Mac 是 ⌘+C)就复制走了。
          </Dialog.Description>
          <textarea ref={ref} className="dlg-copy-text" rows={8} readOnly value={manual.text}
            onFocus={(e) => e.currentTarget.select()} />
          {retried && <p className="hint">还是不行——请在上面的框里按 Ctrl/⌘+C。</p>}
          <div className="dlg-actions">
            <button onClick={() => void retry()}>再试一次</button>
            <button className="primary" onClick={() => setManual(null)}>好了</button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** 只读文本框的便利:一点进去就整段全选,想自己按 Ctrl+C 也只需一步。 */
export function selectAll(e: FocusEvent<HTMLTextAreaElement | HTMLInputElement>) {
  e.currentTarget.select();
}

/** 复制按钮:成功就地变「✓ 已复制」,失败自动弹手动复制层(不会走进死胡同)。 */export function CopyBtn({ text, label = "复制", title }: {
  text: string; label?: string; title?: string;
}) {
  const [done, setDone] = useState(false);
  async function go() {
    if (await copyOrPrompt(text, label.replace(/^复制/, "") || "内容")) {
      setDone(true);
      setTimeout(() => setDone(false), 1200);
    }
  }
  return (
    <button className="btn-sm" title={title ?? `复制${label.replace(/^复制/, "")}`}
      onClick={() => void go()}>
      {done ? "✓ 已复制" : label}
    </button>
  );
}
