// src/renderApi.ts — 出片引擎 API 客户端(对齐 backend/app/api/render.py)。
// 轻量档:文+图 → 视频,生成外包给 autodl.art 托管的 ComfyUI 工作流;
// 这里只管「配置读写 / 提交出片 / 版本历史 / 采用某版 / 读草片」。
// 独立成模块的理由与 dramaApi 相同:api.ts 被主线占用,避免同文件编辑冲突。
import { ApiError, token } from "./api";
import type { DramaShot } from "./dramaApi";

async function req<T>(method: string, path: string, body?: unknown, timeoutMs = 30000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const headers: Record<string, string> = {};
    if (body) headers["Content-Type"] = "application/json";
    const tk = token.get();
    if (tk) headers["Authorization"] = `Bearer ${tk}`;
    const res = await fetch(path, {
      method, headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); detail = j.detail ?? JSON.stringify(j); } catch { /* ignore */ }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

function authHeaders(): Record<string, string> {
  const tk = token.get();
  return tk ? { Authorization: `Bearer ${tk}` } : {};
}

/** 出片配置(token 打码回显;token 留空提交 = 不改动已存)。 */
export interface RenderConfigOut {
  base_url: string;
  token_masked: string;
  has_token: boolean;
  resolution: string;      // 480p | 768p(竖横由各线画幅折算)
  workflow_i2v: string;    // 首尾帧工作流 ID(有静帧走它)
  workflow_t2v: string;    // 文生视频工作流 ID(无静帧走它)
  workflow_tts: string;    // 配音工作流 ID(完整档对白链第一步)
  workflow_talk: string;   // 对口型工作流 ID(完整档对白链第二步)
  configured: boolean;     // false = 前端把出片按钮换成「先去设置」空态
  // 末帧自动接力:部署里有 ffmpeg 才 true;false 时前端整体隐藏候选按钮
  last_frame_available: boolean;
}
export interface RenderConfigIn {
  base_url?: string;
  token?: string;
  resolution?: string;
  workflow_i2v?: string;
  workflow_t2v?: string;
  workflow_tts?: string;
  workflow_talk?: string;
}

/** 一次出片尝试(重 roll 攒版本,新版在前)。params.prompt 截到 400 字,只够辨认。 */
export interface RenderTaskOut {
  id: number;
  line: "drama" | "clips";
  kind: "i2v" | "t2v" | string;
  workflow_id: string;
  provider_task_id: string;
  status: "queued" | "running" | "success" | "failed" | string;
  params: { prompt?: string; duration_s?: number; resolution?: string };
  result_path: string;
  error: string;
  created_at: string;
}

export interface SubmitRenderResult {
  job_id: string;
  task_id: number | null;
  deduped: boolean; // true = 该单元已有任务在跑,复用旧 job(前端照常轮询即可)
}

/** 上一镜末帧候选(整集清单按 seq 给;seq 从 1 起,第 1 格天然没有)。 */
export interface PrevFrameInfo {
  task_id: number;
  from_seq: number;
}

export const renderApi = {
  getConfig: () => req<RenderConfigOut>("GET", "/api/render/config"),
  saveConfig: (body: RenderConfigIn) => req<RenderConfigOut>("PUT", "/api/render/config", body),

  // 漫剧:按「格」出片
  submitDramaShot: (pid: number, sid: number) =>
    req<SubmitRenderResult>("POST", `/api/projects/${pid}/drama/shots/${sid}/render`, {}),
  dramaShotTasks: (pid: number, sid: number) =>
    req<{ tasks: RenderTaskOut[] }>("GET", `/api/projects/${pid}/drama/shots/${sid}/render/tasks`),

  // 情绪短片:按「段」出片
  submitClipChunk: (clipId: number, chunkIndex: number) =>
    req<SubmitRenderResult>("POST", `/api/clips/${clipId}/shoot/${chunkIndex}/render`, {}),
  clipChunkTasks: (clipId: number, chunkIndex: number) =>
    req<{ tasks: RenderTaskOut[] }>("GET", `/api/clips/${clipId}/shoot/${chunkIndex}/render/tasks`),

  // 改用某一版当成片(回写 clip_ref / result_link 指针;打勾仍由人工)
  adoptTask: (taskId: number) =>
    req<{ adopted: boolean; clip_ref?: string; result_link?: string }>(
      "POST", `/api/render/tasks/${taskId}/adopt`),

  /** 读草片视频 → 本地 blob URL(<video src> 带不了 Authorization 头,同图片缩略图的思路)。 */
  async taskBlobUrl(taskId: number): Promise<string> {
    const res = await fetch(`/api/render/tasks/${taskId}/file`, { headers: authHeaders() });
    if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
    return URL.createObjectURL(await res.blob());
  },

  // ---- 末帧自动接力:上一镜末帧 → 下一镜首帧 ----
  // 整集一次拉齐(seq → 候选);有出片才有候选,与轻量/完整档无关
  episodePrevFrames: (pid: number, eid: number) =>
    req<{ by_seq: Record<string, PrevFrameInfo> }>(
      "GET", `/api/projects/${pid}/drama/episodes/${eid}/prev-frames`),
  adoptPrevFrame: (pid: number, sid: number) =>
    req<{ shot: DramaShot; from_seq: number }>(
      "POST", `/api/projects/${pid}/drama/shots/${sid}/adopt-prev-frame`, {}),
  /** 末帧缩略图(<img> 带不了 Authorization 头,取 blob 转本地 URL)。 */
  async lastFrameBlobUrl(taskId: number): Promise<string> {
    const res = await fetch(`/api/render/tasks/${taskId}/last-frame`, { headers: authHeaders() });
    if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
    return URL.createObjectURL(await res.blob());
  },
};

/** 出片状态的中文展示(版本列表/徽标用)。 */
export const RENDER_STATUS_CN: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  success: "已出片",
  failed: "失败",
};

/** 上传静帧/参考图前的宽高比软校验:读图片真实尺寸,与目标画幅差太多就提醒。
 *
 *  为什么只提醒不拦:比例差一点(如 9:16.5)视频站会自己居中裁,硬拦反而烦人;
 *  但横竖搞反(如竖屏片传了横图)会被裁到没法看,必须出声。返回 null 表示放行。
 */
export function checkImageAspect(
  file: File, ratio: string,
): Promise<string | null> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const [rw, rh] = ratio.split(":").map((n) => Number(n) || 0);
      if (!rw || !rh) { resolve(null); return; }
      const target = rw / rh;
      const actual = img.naturalWidth / img.naturalHeight;
      // 差异超过 35% 才算「横竖搞反」级别:一般裁切容差远小于此
      if (Math.abs(actual - target) / target > 0.35) {
        resolve(
          `这张图是 ${img.naturalWidth}×${img.naturalHeight}(约 ${actual.toFixed(2)}:1),` +
          `和本片画幅 ${ratio} 差得比较远——出视频会被大幅裁切或留黑边。仍要上传可忽略本提示。`,
        );
      } else resolve(null);
    };
    img.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
    img.src = url;
  });
}
