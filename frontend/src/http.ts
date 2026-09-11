// src/http.ts — 统一 HTTP 传输层(全部 API 模块共用)。
//
// 为什么单独成文件:api.ts 一度同时承担「传输原语 + 全部业务接口」,又因并行开发被
// 8 个工坊模块(剧本/漫剧/宣传片/生日/情绪短片/系列短片/出片/分享)各自复刻了一份 req,
// 注释均写「api.ts 稳定后可并入」。后果不是「多几行」,而是行为悄悄分叉——
//   · 4 份用裸 path、4 份用 apiBase() + path —— 服务器前缀只有一半模块认;
//   · 8 份里没有一份处理 401 —— token 过期时只弹报错、不跳登录(真 bug);
//   · 超时/网络失败的人话翻译(netError)只有 api.ts 有,其余只会吐 "Failed to fetch"。
// 本文件收敛为唯一出处:api.ts 与各工坊模块一律 import 这里,不再自写 req。
//
// 分层约定:本文件只碰「网络 + 鉴权 + 错误翻译」,不认识任何业务路径或数据类型。
// 业务方法留在各自模块,免得 http.ts 又长成第二个 api.ts。

// 服务器地址:默认空 = 同源。安卓壳(Capacitor)为热更新模式——WebView 直接加载
// 官方服务器上的界面(server.url),页面与 API 天然同源;浏览器/桌面亦同源。
// 若未来出现「本地壳 + 远程 API」形态,再恢复从 localStorage 读取服务器前缀。
const SERVER_KEY = "jarvis_server";

export function apiBase(): string {
  try {
    return (localStorage.getItem(SERVER_KEY) || "").trim().replace(/\/+$/, "");
  } catch {
    return "";
  }
}

const TOKEN_KEY = "jarvis_token";

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY) || "",
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

// 收到 401 时的回调:由 App 注册,统一跳登录
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn; }

/** 401 统一处置:清本地 token + 通知 App 跳登录。
 *  收在一处的原因:此前 8 个模块各写 req,没有一个做这件事——于是 token 过期时,
 *  只有走 api.ts 的请求会跳登录,走工坊模块的只会报错停在原地。 */
export function notifyUnauthorized(): void {
  token.clear();
  onUnauthorized?.();
}

/** 带 HTTP 状态码的 API 错误:调用方可据 status 分流(如 409 冲突需显性处理,而非当普通报错)。
 *  仍是 Error 子类——errMsg 照常取 message,现有 `e instanceof Error` 判断不受影响。 */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 把 fetch 的网络层失败翻成人话。
 *  fetch 对「连不上 / 被中途掐断 / 请求超时」一律只给一句 `TypeError: Failed to fetch`,
 *  原样上屏用户没法判断是自己断网、是等太久,还是服务挂了——线上就吃过这个:起名的
 *  同步长请求被链路掐断,页面只显示 "Failed to fetch",后端日志里连这条请求都没有。
 *  status 用 0 表示「压根没拿到 HTTP 状态」,调用方按 status 分流的逻辑(如 409)不受影响。 */
function netError(timedOut: boolean, timeoutMs: number): ApiError {
  return new ApiError(
    0,
    timedOut
      ? `请求超时:等了 ${Math.round(timeoutMs / 1000)} 秒没有响应。服务可能正忙,请稍后重试。`
      : "网络请求失败:连不上服务器,或连接被中途掐断。请检查网络后重试。",
  );
}

/** 鉴权头(只含 Authorization,不含 Content-Type):SSE / 下载 / 图片读取等共用。
 *  multipart 不能用它——上传必须让浏览器自己带 boundary。 */
export function authHeaders(): Record<string, string> {
  const tk = token.get();
  return tk ? { Authorization: `Bearer ${tk}` } : {};
}

/** 非 2xx 响应 → ApiError;401 顺带清 token 并跳登录。
 *  抽出来的原因:这段此前在 api.ts 内被 req / reqForm / postImage / sseStream 等
 *  抄了 5 遍,改一处文案必漏其余。 */
async function failResponse(res: Response): Promise<never> {
  if (res.status === 401) notifyUnauthorized();
  let detail = `HTTP ${res.status}`;
  try {
    const j = await res.json();
    detail = j.detail ?? JSON.stringify(j);
  } catch { /* ignore */ }
  throw new ApiError(res.status, detail);
}

/** multipart 上传(文件导入等):不设 Content-Type(浏览器自动带 boundary),大文件放宽超时。 */
export async function reqForm<T>(path: string, form: FormData, timeoutMs = 120000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    let res: Response;
    try {
      res = await fetch(apiBase() + path, { method: "POST", headers: authHeaders(), body: form, signal: ctrl.signal });
    } catch {
      throw netError(ctrl.signal.aborted, timeoutMs);
    }
    if (!res.ok) await failResponse(res);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/** 唯一 JSON 请求入口。timeoutMs 默认 30s;LLM 长任务(生成/研讨)显式传大值。
 *  body 一律按 `!== undefined` 判定,故 `false` / `0` / `""` 也是合法载荷。 */
export async function req<T>(method: string, path: string, body?: unknown, timeoutMs = 30000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const tk = token.get();
    if (tk) headers["Authorization"] = `Bearer ${tk}`;
    let res: Response;
    try {
      res = await fetch(apiBase() + path, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: ctrl.signal,
      });
    } catch {
      // 连 HTTP 状态都没拿到:自己 abort 的(超时)/ 断网 / 连接被掐
      throw netError(ctrl.signal.aborted, timeoutMs);
    }
    if (!res.ok) await failResponse(res);
    try {
      return (await res.json()) as T;
    } catch (e) {
      // 响应头到了但正文没读完:仍是连接层断的;SyntaxError 例外(服务端返了非 JSON)
      if (e instanceof SyntaxError) {
        throw new ApiError(res.status, "服务返回了无法解析的内容,请重试。");
      }
      throw netError(ctrl.signal.aborted, timeoutMs);
    }
  } finally {
    clearTimeout(timer);
  }
}

/** multipart 上传一张图(note 随表单走)。不能手设 Content-Type——浏览器要自己带 boundary。 */
export async function postImage<T>(path: string, file: File, note = ""): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("note", note);
  const res = await fetch(apiBase() + path, { method: "POST", headers: authHeaders(), body: fd });
  if (!res.ok) await failResponse(res);
  return (await res.json()) as T;
}

/** 读一张鉴权图 → 本地 blob URL:读取端点要 Authorization 头,<img src> 带不了。
 *  调用方负责 URL.revokeObjectURL 释放(共享 RefThumb 组件已带释放逻辑)。 */
export async function imageBlobUrl(path: string): Promise<string> {
  const res = await fetch(apiBase() + path, { headers: authHeaders() });
  if (!res.ok) {
    if (res.status === 401) notifyUnauthorized();
    throw new ApiError(res.status, `HTTP ${res.status}`);
  }
  return URL.createObjectURL(await res.blob());
}

// 鉴权下载:导出接口需要 Bearer token,普通 <a href> 不会带 Authorization 头,
// 所以用 fetch 拿 blob 再触发浏览器下载。filename 优先取 Content-Disposition。
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const res = await fetch(apiBase() + path, { headers: authHeaders() });
  if (!res.ok) {
    if (res.status === 401) notifyUnauthorized();
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      detail = j.detail ?? JSON.stringify(j);
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  let name = fallbackName;
  const disp = res.headers.get("Content-Disposition") || "";
  // 兼容 filename*=UTF-8''xxx 与 filename="xxx" 两种写法
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disp);
  const plain = /filename="?([^";]+)"?/i.exec(disp);
  if (star) name = decodeURIComponent(star[1]);
  else if (plain) name = plain[1].trim();
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
