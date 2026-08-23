// src/clipsApi.ts — 情绪短片工坊 API 客户端(对齐 backend/app/api/clips.py)。
// 独立模块(同 dramaApi/promoApi 的理由);导出用鉴权 fetch(复用 api.ts 的 token)。
import { ApiError, token } from "./api";

const LLM_TIMEOUT = 900_000;

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

export interface ClipTheme { key: string; label: string; directive: string }
export interface ClipDirection { key: string; label: string; tip: string }

export interface ClipShot {
  seq: number;
  scene_name: string;
  characters: string[];
  action_desc: string;
  shot_type: string;
  camera: string;
  dialogue: string;
  duration_s: number;
  prompt_cn: string;
  prompt_en: string;
  negative: string;
}

export interface ClipChunk {
  index: number; start_s: number; end_s: number; duration_s: number;
  over_limit: boolean; shot_seqs: number[]; scenes: string[]; subtitle: string;
}

/** 一个本子(候选或最终态) */
export interface ClipCard {
  take: string;
  logline: string;
  emotion_curve: string;
  lines: { speaker: string; text: string; action?: string }[];
  shots: ClipShot[];
  punchline: string;
  chunks: ClipChunk[];
  hook_text: string;
  quote_source: string;
  cautions: string[];
}

export interface MoodClip {
  id: number;
  source_project_id: number | null;
  theme: string;
  custom_theme: string;
  theme_display: string;
  duration_s: number;
  direction: string;
  direction_label: string;
  inspiration: string;
  style_name: string;
  style_cn: string;
  style_en: string;
  negative: string;
  chosen: number;
  clip: ClipCard | Record<string, never>;
  status: string;
  status_cn: string;
  candidates?: ClipCard[];
}

export const clipsApi = {
  meta: () =>
    req<{ themes: ClipTheme[]; durations: number[]; directions: ClipDirection[] }>(
      "GET", "/api/clips/meta"),
  list: (projectId?: number) =>
    req<{ clips: MoodClip[] }>(
      "GET", `/api/clips${projectId ? `?project_id=${projectId}` : ""}`),
  create: (body: {
    theme?: string; custom_theme?: string; duration_s: number; direction: string;
    inspiration?: string; source_project_id?: number;
  }) => req<{ clip_row: MoodClip }>("POST", "/api/clips", body),
  get: (id: number) => req<{ clip_row: MoodClip }>("GET", `/api/clips/${id}`),
  patch: (id: number, body: { inspiration?: string; duration_s?: number; direction?: string }) =>
    req<{ clip_row: MoodClip }>("PATCH", `/api/clips/${id}`, body),
  generate: (id: number) =>
    req<{ job_id: string }>("POST", `/api/clips/${id}/generate`, undefined, LLM_TIMEOUT),
  pick: (id: number, index: number) =>
    req<{ clip_row: MoodClip }>("POST", `/api/clips/${id}/pick`, { index }),
  remove: (id: number) => req<{ ok: boolean }>("DELETE", `/api/clips/${id}`),
  export: (id: number, format: "md" | "srt" | "json") => {
    const tk = token.get();
    return fetch(`/api/clips/${id}/export?format=${format}`, {
      headers: tk ? { Authorization: `Bearer ${tk}` } : {},
    }).then(async (res) => {
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try { const j = await res.json(); detail = j.detail ?? ""; } catch { /* ignore */ }
        throw new Error(detail);
      }
      const name = (res.headers.get("Content-Disposition") || "").split("filename*=UTF-8''")[1];
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name ? decodeURIComponent(name) : `clip.${format}`;
      document.body.appendChild(a);
      a.click(); a.remove();
      URL.revokeObjectURL(url);
    });
  },
};
