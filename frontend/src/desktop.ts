// src/desktop.ts — 桌面版(Tauri 壳)与前端之间的桥。
//
// 关键背景:桌面窗口加载的是本机后端页面 http://127.0.0.1:<随机端口>,对 Tauri
// 而言是「远程源」,默认拿不到 IPC。已在 capabilities/default.json 用 remote.urls
// 把本机源列入白名单,并开 withGlobalTauri,故这里可经 window.__TAURI__ 调命令/插件。
//
// Web / server 端没有 __TAURI__,isDesktop() 为 false,所有桌面能力优雅降级——
// 调用方据此决定走 Tauri 更新流还是保留原有 Web 轮询横幅。

// withGlobalTauri 注入的全局。只声明用到的部分,避免引入整包类型依赖。
interface TauriGlobal {
  core: { invoke: <T>(cmd: string, args?: Record<string, unknown>) => Promise<T> };
  event?: {
    listen: <T>(
      event: string,
      handler: (e: { payload: T }) => void,
    ) => Promise<() => void>;
  };
}
declare global {
  interface Window {
    __TAURI__?: TauriGlobal;
  }
}

/** 是否运行在桌面(Tauri)壳内。Web/server 端为 false。 */
export function isDesktop(): boolean {
  return typeof window !== "undefined" && !!window.__TAURI__;
}

/** 调用一个 Tauri 命令;非桌面环境抛错(调用方应先 isDesktop() 判定)。 */
async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const t = window.__TAURI__;
  if (!t) throw new Error("not running in desktop shell");
  return t.core.invoke<T>(cmd, args);
}

/** 桌面壳的应用版本(CARGO_PKG_VERSION)。也用于桥连通性探测。 */
export function desktopPing(): Promise<string> {
  return invoke<string>("desktop_ping");
}

/** check_update 的返回:是否有更新 + 版本/说明/当前版本。 */
export interface UpdateInfo {
  available: boolean;
  version: string;
  notes: string;
  current: string;
}

/**
 * 检查更新。调用即让 Rust 侧「前端已接管更新 UI」置真,启动兜底原生框会让位。
 * 桥不通(非桌面/IPC 未注入)时抛错,调用方据此降级。
 */
export function checkUpdate(): Promise<UpdateInfo> {
  return invoke<UpdateInfo>("check_update");
}

/**
 * 下载并安装更新(静默,不自动重启)。下载进度经 update://progress 事件推送,
 * 用 onUpdateProgress 订阅。装好后 resolve,由调用方提示「重启生效」。
 */
export function downloadAndInstallUpdate(): Promise<void> {
  return invoke<void>("download_and_install_update");
}

/** 重启应用使更新生效(下载安装完成后调用)。 */
export function restartApp(): Promise<void> {
  return invoke<void>("restart_app");
}

/**
 * 保存更新代理(检查与下载更新都走它)。空串=清除,恢复直连;
 * 非空须形如 http://host:port 或 socks5://host:port,非法由 Rust 侧校验并抛错。
 */
export function setUpdateProxy(proxy: string): Promise<void> {
  return invoke<void>("set_update_proxy", { proxy });
}

/** 读取已保存的更新代理(未设置返回空串),用于设置页回显。 */
export function getUpdateProxy(): Promise<string> {
  return invoke<string>("get_update_proxy");
}

/**
 * 订阅下载进度。回调收到 [已下载字节, 总字节];总字节可能为 0(服务端没给)。
 * 返回取消订阅函数。event 插件不可用时返回 no-op(不影响下载,仅进度条不动)。
 */
export async function onUpdateProgress(
  cb: (downloaded: number, total: number) => void,
): Promise<() => void> {
  const t = window.__TAURI__;
  if (!t?.event) return () => {};
  return t.event.listen<[number, number]>("update://progress", (e) => {
    cb(e.payload[0], e.payload[1]);
  });
}
