// 桌面关闭守卫:拦截窗口 X,按「后台任务 / 托盘偏好」决定去向。
//   · 有后台任务进行中 → 应用内确认框:直接关闭(任务会中断)/ 最小化到托盘 / 取消
//   · 无任务 → 按偏好:开了「关闭时最小化到托盘」就进托盘,否则直接关闭(不打扰)
// 拦截开关在 Rust 侧(本组件挂载后才调 enable_close_guard):
// 未挂载时(锁屏早期/JS 崩溃)点 X 照旧直接关,窗口永远关得掉。
import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import {
  closeApp,
  enableCloseGuard,
  hideToTray,
  isDesktop,
  onCloseRequested,
} from "../desktop";
import { jobLabel } from "./TaskCenter";

// 偏好:点 X 最小化到托盘而非直接退出。默认关。
// 持久化走 localStorage,与主题/自动更新偏好同一套(见 theme.ts、SettingsPage)。
const CLOSE_TO_TRAY_KEY = "jarvis_close_to_tray";

export function getCloseToTrayPref(): boolean {
  try {
    return localStorage.getItem(CLOSE_TO_TRAY_KEY) === "true";
  } catch {
    return false;
  }
}

export function setCloseToTrayPref(v: boolean): void {
  try {
    localStorage.setItem(CLOSE_TO_TRAY_KEY, v ? "true" : "false");
  } catch {
    /* 隐私模式等写失败:静默,本次会话内仍生效 */
  }
}

// 关闭决策(纯函数,便于单测):
//   有任务 → 一律弹确认框(即便开了托盘偏好,也不默默把任务关进后台或杀掉);
//   无任务 → 按偏好进托盘或直接关。
export function decideCloseAction(
  runningCount: number,
  closeToTray: boolean,
): "ask" | "tray" | "close" {
  if (runningCount > 0) return "ask";
  return closeToTray ? "tray" : "close";
}

export default function CloseGuard() {
  // 待确认的关闭:非空即弹框,内容是有哪些任务会被中断
  const [pending, setPending] = useState<string[] | null>(null);

  const decide = useCallback(async () => {
    // 直接问后端拿最新任务:任务中心轮询有滞后,锁屏状态下它甚至没挂载
    let running: string[] = [];
    try {
      const r = await api.myJobs(true);
      running = r.jobs
        .filter((j) => j.status === "running")
        .map((j) => jobLabel(j.kind));
    } catch {
      /* 探测失败按无任务处理,别挡住用户关窗 */
    }
    switch (decideCloseAction(running.length, getCloseToTrayPref())) {
      case "ask":
        // 已有弹框未决时不重复覆盖(用户可能正读着任务清单)
        setPending((p) => p ?? running);
        break;
      case "tray":
        hideToTray().catch(() => {});
        break;
      default:
        closeApp().catch(() => {});
    }
  }, []);

  useEffect(() => {
    if (!isDesktop()) return;
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    enableCloseGuard().catch(() => {});
    onCloseRequested(() => { void decide(); })
      .then((u) => { if (!cancelled) unlisten = u; })
      .catch(() => {});
    return () => { cancelled = true; unlisten?.(); };
  }, [decide]);

  if (!pending) return null;
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) setPending(null); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dlg-overlay" />
        <Dialog.Content className="dlg-content" onEscapeKeyDown={() => setPending(null)}>
          <Dialog.Title className="dlg-title">关闭 jarvis-write?</Dialog.Title>
          <Dialog.Description className="dlg-body">
            还有 {pending.length} 个后台任务进行中:{pending.join("、")}。
            直接关闭会中断这些任务;最小化到托盘可让任务继续跑完。
          </Dialog.Description>
          <div className="dlg-actions">
            <button onClick={() => setPending(null)}>取消</button>
            <button onClick={() => { setPending(null); hideToTray().catch(() => {}); }}>
              最小化到托盘
            </button>
            <button
              className="danger"
              autoFocus
              onClick={() => { setPending(null); closeApp().catch(() => {}); }}
            >
              直接关闭
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
