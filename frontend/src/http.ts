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
 *  code 是后端结构化业务错误码(如 NO_PROVIDER=未配置写作渠道,detail 为 {code,message}
 *  形态时提取),供传输层/调用方程序化分流;普通报错无 code。
 *  仍是 Error 子类——errMsg 照常取 message,现有 `e instanceof Error` 判断不受影响。 */
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

// 「未配置写作渠道」的统一处置:任何生成类请求撞上 NO_PROVIDER,当场弹
// 「配置渠道」内联弹窗,用户配好保存后**自动重放原请求**——不跳设置页、
// 不丢已填的内容、不用再点一次生成。http 层不认识任何 UI,弹窗由 App 注册
// (setProviderSetupHandler);任务路径(job 轮询)拿不到 HTTP 响应,
// pollJob 里凭 [NO_PROVIDER] 标记触发同一个弹窗。
export const NO_PROVIDER = "NO_PROVIDER";
/** 后端 job.error 里「未配置渠道」的机器标记(normalize_job_error 打的)。 */
export const NO_PROVIDER_MARKER = "[NO_PROVIDER]";

/** 用户关掉配置弹窗:调用方应回退到原错误文案,而不是当作「配好了」。 */
export class ProviderSetupCancelled extends Error {
  constructor() {
    super("已取消配置");
    this.name = "ProviderSetupCancelled";
  }
}

let providerSetupHandler: (() => Promise<void>) | null = null;
let providerSetupInFlight: Promise<void> | null = null;

/** 注册「打开配置渠道弹窗」的处理器(App 挂载 ProviderSetupHost 时调用一次)。 */
export function setProviderSetupHandler(fn: () => Promise<void>): void {
  providerSetupHandler = fn;
}

/** 打开「配置渠道」弹窗:保存成功 resolve;用户关掉 reject(ProviderSetupCancelled)。
 *  并发请求同时撞上 NO_PROVIDER 时共用同一次弹窗(in-flight 复用);
 *  处理器未注册(极端时序)时静默返回,调用方按「没配成」走原路。 */
export function requestProviderSetup(): Promise<void> {
  if (!providerSetupHandler) return Promise.resolve();
  if (!providerSetupInFlight) {
    providerSetupInFlight = providerSetupHandler().finally(() => {
      providerSetupInFlight = null;
    });
  }
  return providerSetupInFlight;
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

/** 用户主动取消(页面终止按钮 abort)——与「超时/断网」不同,调用方应静默处理
 *  (回空闲态,不弹错误)。ApiError 子类,`e instanceof Error` 判断不受影响。 */
export class RequestCancelled extends Error {
  constructor() {
    super("已取消");
    this.name = "RequestCancelled";
  }
}

function isCancelled(external?: AbortSignal): boolean {
  return external?.aborted ?? false;
}

/** 错误响应体 → 人话。detail 常见是字符串,但 FastAPI 422 校验错误是对象数组——
 *  原样塞进 Error(message) 会被 String 化成 "[object Object]" 上屏(2026-10-20
 *  「挑一整套书方向」页实踩:真实报错被吞,用户与排查都看不到内容)。 */
function detailText(j: unknown): string {
  if (typeof j === "string") return j;
  const d = (j as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  try {
    return JSON.stringify(d ?? j);
  } catch {
    return "服务返回了无法解析的错误信息";
  }
}

/** 错误响应体 → ApiError(throw)。detail 常见是字符串,但:
 *  - FastAPI 422 校验错误是对象数组——原样塞进 Error(message) 会被 String 化成
 *    "[object Object]" 上屏(2026-10-20 「挑一整套书方向」页实踩:真实报错被吞,
 *    用户与排查都看不到内容);
 *  - 业务结构化错误是 {code, message} 对象(如 NO_PROVIDER)——code 提出来供
 *    传输层分流(弹配置引导),message 给人看。 */
async function failResponse(res: Response): Promise<never> {
  if (res.status === 401) notifyUnauthorized();
  let message = `HTTP ${res.status}`;
  let code: string | undefined;
  try {
    const j: unknown = await res.json();
    const d = (j as { detail?: unknown } | null)?.detail;
    if (
      d && typeof d === "object" && !Array.isArray(d)
      && "code" in (d as Record<string, unknown>)
    ) {
      const o = d as { code?: unknown; message?: unknown };
      if (typeof o.code === "string") code = o.code;
      message =
        typeof o.message === "string" && o.message
          ? o.message
          : JSON.stringify(d);
    } else {
      message = detailText(j);
    }
  } catch { /* ignore */ }
  throw new ApiError(res.status, message, code);
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
 *  body 一律按 `!== undefined` 判定,故 `false` / `0` / `""` 也是合法载荷。
 *  signal:调用方主动取消(页面「终止」按钮)。外部 abort 抛 RequestCancelled
 *  (调用方静默处理),与自身超时(netError 的「等了 N 秒」)分开——LLM 超时
 *  放宽到分钟级后,干等没有出口,可终止是放宽的前提。
 *  NO_PROVIDER 内联引导:撞上「未配置写作渠道」→ 弹配置弹窗(并发请求共用
 *  同一个),保存成功自动重放原请求一次;用户关掉弹窗则原错误照常上屏。
 *  _setupRetried 内部参数:重放只做一次,防止「配了但没生效」时无限循环。 */
export async function req<T>(
  method: string,
  path: string,
  body?: unknown,
  timeoutMs = 30000,
  signal?: AbortSignal,
  _setupRetried = false,
): Promise<T> {
  const ctrl = new AbortController();
  const onExternalAbort = () => ctrl.abort();
  signal?.addEventListener("abort", onExternalAbort);
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
      if (isCancelled(signal)) throw new RequestCancelled();
      // 连 HTTP 状态都没拿到:自己 abort 的(超时)/ 断网 / 连接被掐
      throw netError(ctrl.signal.aborted, timeoutMs);
    }
    if (!res.ok) {
      try {
        await failResponse(res);
      } catch (e) {
        if (
          e instanceof ApiError && e.code === NO_PROVIDER
          && !_setupRetried && providerSetupHandler && !isCancelled(signal)
        ) {
          try {
            await requestProviderSetup();
          } catch {
            throw e; // 用户关掉弹窗:保持原错误文案上屏
          }
          return req<T>(method, path, body, timeoutMs, signal, true);
        }
        throw e;
      }
    }
    try {
      return (await res.json()) as T;
    } catch (e) {
      if (isCancelled(signal)) throw new RequestCancelled();
      // 响应头到了但正文没读完:仍是连接层断的;SyntaxError 例外(服务端返了非 JSON)
      if (e instanceof SyntaxError) {
        throw new ApiError(res.status, "服务返回了无法解析的内容,请重试。");
      }
      throw netError(ctrl.signal.aborted, timeoutMs);
    }
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onExternalAbort);
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
      detail = detailText(await res.json());
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
