// src/seriesApi.ts — 角色系列短片工坊 API 客户端(对齐 backend/app/api/series.py)。
// 独立模块(同 clipsApi/birthdayApi 的理由);导出用鉴权 fetch(复用 api.ts 的 token)。
import { ApiError, token } from "./api";

// AI 代写定妆是同步 LLM 调用(单发),给长超时
const LLM_TIMEOUT = 300_000;

async function req<T>(method: string, path: string, body?: unknown, timeoutMs = 30000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const tk = token.get();
    if (tk) headers["Authorization"] = `Bearer ${tk}`;
    const res = await fetch(path, {
      method, headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); detail = j.detail ?? JSON.stringify(j); } catch { /* ignore */ }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as T;
  } finally { clearTimeout(timer); }
}

export interface SeriesDirection { key: string; label: string; tip: string }

export interface SeriesMeta {
  directions: SeriesDirection[];
  min_duration_s: number;
  max_duration_s: number;
  name_max: number;
  brief_max: number;
  look_max: number;
  plot_max: number;
  hints_max: number;
}

export interface SeriesRefImage { kind: "upload" | "url"; src: string; note: string }

/** 主角档案:look(定妆描述)是全系列一致性的锚 */
export interface SeriesCharacter {
  id: number;
  name: string;
  look: string;
  direction: string;
  direction_label: string;
  default_duration_s: number;
  style_hints: string;
  ref_images: SeriesRefImage[];
}

/** 一集:剧情输入 + 生成输出 */
export interface SeriesEpisode {
  id: number;
  character_id: number;
  plot: string;
  duration_s: number;
  status: "draft" | "generating" | "done";
  status_cn: string;
  output: { title: string; prompt_cn: string; negative: string } | Record<string, never>;
}

export interface SeriesCharacterInput {
  name: string;
  look: string;
  direction: string;
  default_duration_s: number;
  style_hints?: string;
}

function authHeaders(): Record<string, string> {
  const tk = token.get();
  return tk ? { Authorization: `Bearer ${tk}` } : {};
}

/** 上传一张定妆参考图:multipart(不能手设 Content-Type,浏览器要自己带 boundary)。 */
async function postImage<T>(path: string, file: File, note = ""): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("note", note);
  const res = await fetch(path, { method: "POST", headers: authHeaders(), body: fd });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { const j = await res.json(); detail = j.detail ?? detail; } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

/** 读一张鉴权参考图 → 本地 blob URL(读取端点要 Authorization 头,<img src> 带不了)。 */
async function imageBlobUrl(path: string): Promise<string> {
  const res = await fetch(path, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

export const seriesApi = {
  meta: () => req<SeriesMeta>("GET", "/api/series/meta"),
  listCharacters: () => req<{ characters: SeriesCharacter[] }>("GET", "/api/series/characters"),
  createCharacter: (body: SeriesCharacterInput) =>
    req<{ character_row: SeriesCharacter }>("POST", "/api/series/characters", body),
  getCharacter: (cid: number) =>
    req<{ character_row: SeriesCharacter; episodes: SeriesEpisode[] }>(
      "GET", `/api/series/characters/${cid}`),
  patchCharacter: (cid: number, body: Partial<SeriesCharacterInput>) =>
    req<{ character_row: SeriesCharacter }>("PATCH", `/api/series/characters/${cid}`, body),
  removeCharacter: (cid: number) => req<{ ok: boolean }>("DELETE", `/api/series/characters/${cid}`),
  /** AI 代写定妆草稿:同步单发,不落库(用户确认后走 create/patch 保存) */
  draftLook: (brief: string, direction: string, style_hints = "") =>
    req<{ look: string }>("POST", "/api/series/characters/draft-look",
      { brief, direction, style_hints }, LLM_TIMEOUT),
  createEpisode: (cid: number, plot: string, duration_s?: number) =>
    req<{ episode_row: SeriesEpisode }>("POST",
      `/api/series/characters/${cid}/episodes`, { plot, duration_s }),
  generateEpisode: (eid: number) =>
    req<{ job_id: string }>("POST", `/api/series/episodes/${eid}/generate`, {}, LLM_TIMEOUT),
  patchEpisode: (eid: number, body: { plot?: string; duration_s?: number; output?: unknown }) =>
    req<{ episode_row: SeriesEpisode }>("PUT", `/api/series/episodes/${eid}`, body),
  removeEpisode: (eid: number) => req<{ ok: boolean }>("DELETE", `/api/series/episodes/${eid}`),
  // ---- 定妆参考图 ----
  uploadRef: (cid: number, file: File, note = "") =>
    postImage<{ character_row: SeriesCharacter }>(`/api/series/characters/${cid}/reference`, file, note),
  linkRef: (cid: number, url: string, note = "") =>
    req<{ character_row: SeriesCharacter }>("POST",
      `/api/series/characters/${cid}/reference/link`, { url, note }),
  deleteRef: (cid: number, imgIndex: number) =>
    req<{ character_row: SeriesCharacter }>("DELETE", `/api/series/characters/${cid}/reference/${imgIndex}`),
  refReadUrl: (cid: number, imgIndex: number) => `/api/series/characters/${cid}/reference/${imgIndex}`,
  refBlobUrl: (cid: number, imgIndex: number) =>
    imageBlobUrl(`/api/series/characters/${cid}/reference/${imgIndex}`),
};
