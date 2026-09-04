// hooks/useDesktopHotkeys.ts — 桌面快捷键(仅 !isMobile 时启用,见 ProjectPage)。
// 所有键位统一经 dispatchAction 分发,与 Ctrl+K 命令面板、Tauri 菜单同一入口(ui/actions.ts):
//   Ctrl+Enter 生成本章   Ctrl+[ / Ctrl+] 上一章/下一章(已生成章范围内)
//   Ctrl+B 开合目录抽屉   Ctrl+M 故事地图   F11 沉浸模式   Ctrl+K 命令面板
//   Ctrl+Shift+F 全书检索(输入框内也可呼出)
// (Ctrl+S 只拦浏览器「另存为」:整章编辑态已删,无保存动作;Ctrl+\ 随参考抽屉一起废除)
// 输入控件(input/textarea/select/可编辑元素)聚焦时不拦,例外:Ctrl+Enter 照拦。
import { useEffect } from "react";
import { dispatchAction } from "../ui/actions";

function isEditableTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName);
}

export function useDesktopHotkeys(opts: { enabled: boolean; paletteOpen: boolean }) {
  const { enabled, paletteOpen } = opts;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      // 命令面板打开时:其余快捷键全让位(面板自有键盘交互),只留 Ctrl+K 关面板
      if (paletteOpen) {
        if (mod && e.key.toLowerCase() === "k") {
          e.preventDefault();
          dispatchAction("command-palette");
        }
        return;
      }
      // Ctrl+S:无保存动作(整章编辑态已删),仅拦浏览器「另存为」;Ctrl+Enter 生成本章
      if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        return;
      }
      if (mod && e.key === "Enter") {
        e.preventDefault();
        dispatchAction("generate");
        return;
      }
      // Ctrl+Shift+F 全书检索:在输入框/编辑器里也能呼出(写作中途搜设定最常见)
      if (mod && e.shiftKey && e.key.toLowerCase() === "f") {
        e.preventDefault();
        dispatchAction("global-search");
        return;
      }
      if (isEditableTarget(e.target)) return;
      if (mod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        dispatchAction("command-palette");
        return;
      }
      // 以下键位只在有 handler(write 区挂载)时拦,否则放行浏览器默认行为
      if (mod && e.key === "[") {
        if (dispatchAction("prev-chapter")) e.preventDefault();
        return;
      }
      if (mod && e.key === "]") {
        if (dispatchAction("next-chapter")) e.preventDefault();
        return;
      }
      if (mod && e.key.toLowerCase() === "b") {
        if (dispatchAction("toggle-rail")) e.preventDefault();
        return;
      }
      if (mod && e.key.toLowerCase() === "m") {
        if (dispatchAction("toggle-map")) e.preventDefault();
        return;
      }
      if (e.key === "F11") {
        if (dispatchAction("immersive")) e.preventDefault();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled, paletteOpen]);
}
