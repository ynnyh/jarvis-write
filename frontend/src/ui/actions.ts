// ui/actions.ts — 统一动作 dispatch(交互重构 D 阶段)。
// 快捷键、Ctrl+K 命令面板、Tauri 菜单事件(menu-action)共用一个入口:
//   · 章级动作(save/generate/act 类/上下章/开合栏/沉浸)由 WritePanel 挂载时注册、卸载注销;
//   · 全局动作(主题/导出/导航/open-read-window/command-palette)由 ProjectPage 注册。
// 注册表模式:每个动作是一摞 handler(栈),dispatch 只调栈顶——重复注册后者优先,
// 卸载自动回落到下一个;无 handler 时 dispatch 返回 false,调用方可静默忽略。
// handler 一律经 ref 取最新实现(见 WritePanel/ProjectPage 的 actionHandlersRef),
// 注册只在挂载/卸载发生,避免闭包过期。
export type AppAction =
  // 章级(作用于当前 ch,由 WritePanel 注册;save 已随整章编辑态删除,
  // toggle-ref 已随参考抽屉废除——「正文即界面」P1,见 docs/10 §8)
  | "generate" | "revise" | "polish" | "proofread" | "review"
  | "versions" | "queue"
  | "prev-chapter" | "next-chapter" | "toggle-rail" | "toggle-map" | "immersive"
  // 全局(由 ProjectPage 注册)
  | "command-palette" | "global-search" | "open-read-window"
  | "goto-setup" | "goto-write" | "goto-book" | "goto-settings" | "goto-help"
  | "theme-light" | "theme-dark" | "theme-auto"
  | "export-txt" | "export-epub";

const ALL_ACTIONS: AppAction[] = [
  "generate", "revise", "polish", "proofread", "review",
  "versions", "queue",
  "prev-chapter", "next-chapter", "toggle-rail", "toggle-map", "immersive",
  "command-palette", "global-search", "open-read-window",
  "goto-setup", "goto-write", "goto-book", "goto-settings", "goto-help",
  "theme-light", "theme-dark", "theme-auto",
  "export-txt", "export-epub",
];

/** Tauri 菜单事件等外部入口的 payload 校验:非法动作名直接丢弃。 */
export function isAppAction(v: unknown): v is AppAction {
  return typeof v === "string" && (ALL_ACTIONS as string[]).includes(v);
}

export type ActionHandler = () => void;

const registry = new Map<AppAction, ActionHandler[]>();

/** 注册动作 handler,返回注销函数(组件卸载时调用)。 */
export function registerActionHandler(action: AppAction, handler: ActionHandler): () => void {
  const stack = registry.get(action) ?? [];
  stack.push(handler);
  registry.set(action, stack);
  return () => {
    const s = registry.get(action);
    if (!s) return;
    const i = s.indexOf(handler);
    if (i >= 0) s.splice(i, 1);
    if (!s.length) registry.delete(action);
  };
}

/** 分发动作:只调栈顶 handler;无人认领返回 false。 */
export function dispatchAction(action: AppAction): boolean {
  const stack = registry.get(action);
  const top = stack?.[stack.length - 1];
  if (!top) return false;
  top();
  return true;
}
