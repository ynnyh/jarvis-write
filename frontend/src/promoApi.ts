// src/promoApi.ts — 宣传片工坊 API 客户端(对齐 backend/app/api/promo.py)。
// 独立模块(与 dramaApi 同理由:api.ts 并行开发占用);SSE 研讨流复用 api.ts 的导出
// (token/createSseDecoder/ApiError),api.ts 稳定后可并入。
import { ApiError, createSseDecoder, token } from "./api";

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
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        detail = j.detail ?? JSON.stringify(j);
      } catch { /* ignore */ }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// ---------- 类型 ----------

export interface PromoAngle { key: string; label: string; directive: string }
export interface PromoDirection { key: string; label: string; tip: string }

export interface PromoBriefSegment { title: string; angle: string; beat: string; seconds: number }
export interface PromoBrief {
  positioning: string;
  audience: string;
  tone: string[];
  key_messages: string[];
  structure: PromoBriefSegment[];
  slogan_candidates: string[];
  cautions: string[];
  duration_s?: number;
}

export interface Landmark { name: string; appearance_cn: string; appearance_en: string }

export interface ChatTurn { role: "user" | "assistant"; text: string }

export interface PromoChunk {
  index: number;
  start_s: number;
  end_s: number;
  duration_s: number;
  over_limit: boolean;
  shot_seqs: number[];
  scenes: string[];
  subtitle: string;
  motion_prompt_cn: string;
  motion_prompt_en: string;
  first_frame_hint: string;
  link_note: string;
}

export interface PromoPlan {
  id: number;
  subject: string;
  title: string;
  angles: string[];
  duration_s: number;
  direction: string;
  direction_label: string;
  style_name: string;
  style_cn: string;
  style_en: string;
  negative: string;
  landmarks: Landmark[];
  material_notes: string;
  chat_log: ChatTurn[];
  brief: PromoBrief | Record<string, never>;
  brief_locked: boolean;
  script: { synopsis?: string; lines?: { speaker: string; text: string; action?: string }[] };
  pack: Record<string, unknown>;
  chunks: { chunk_s?: number; items?: PromoChunk[] };
  status: string;
}
export type PromoPlanSlim = Pick<PromoPlan,
  "id" | "subject" | "title" | "angles" | "duration_s" | "direction" | "direction_label" | "status">
  & { brief_locked: boolean };

export interface PromoShot {
  id: number;
  promo_id: number;
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

export const PROMO_STATUS_CN: Record<string, string> = {
  draft: "企划中",
  briefed: "简报已定",
  scripted: "解说词已定",
  storyboarded: "分镜已定",
  ready: "提示词就绪",
};

// ---------- SSE 研讨流(打字机) ----------

/** POST 一条 SSE 对话流:token 帧喂 onToken,done 帧作最终结果;error 帧抛 ApiError。 */
async function chatStream(
  path: string,
  body: unknown,
  onToken: (text: string) => void,
): Promise<ChatTurn[]> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const tk = token.get();
  if (tk) headers["Authorization"] = `Bearer ${tk}`;
  const res = await fetch(path, { method: "POST", headers, body: JSON.stringify(body) });
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      detail = j.detail ?? JSON.stringify(j);
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  const feed = createSseDecoder();
  let done: ChatTurn[] | null = null;
  let errDetail: string | null = null;
  const handle = (event: string, data: unknown) => {
    if (event === "token") {
      const d = data as { text?: unknown };
      if (typeof d?.text === "string") onToken(d.text);
    } else if (event === "done") {
      const d = data as { reply?: unknown };
      done = [{ role: "assistant", text: typeof d?.reply === "string" ? d.reply : "" }];
    } else if (event === "error") {
      const d = data as { detail?: unknown };
      errDetail = typeof d?.detail === "string" ? d.detail : "对话失败,请重试";
    }
  };
  for (;;) {
    const { value, done: finished } = await reader.read();
    if (finished) break;
    let ev = "message";
    for (const frame of feed(decoder.decode(value, { stream: true }))) {
      ev = frame.event;
      handle(ev, frame.data);
    }
  }
  for (const frame of feed(decoder.decode())) handle(frame.event, frame.data);
  if (errDetail !== null) throw new ApiError(502, errDetail);
  if (done === null) throw new ApiError(0, "对话意外中断,请重试");
  return done;
}

// ---------- API ----------

export const promoApi = {
  meta: () =>
    req<{ angles: PromoAngle[]; directions: PromoDirection[] }>("GET", "/api/promos/meta"),
  list: () => req<{ plans: PromoPlanSlim[] }>("GET", "/api/promos"),
  create: (body: { subject: string; angles: string[]; duration_s: number; direction: string }) =>
    req<{ plan: PromoPlan }>("POST", "/api/promos", body),
  get: (id: number) =>
    req<{ plan: PromoPlan; shots: PromoShot[] }>("GET", `/api/promos/${id}`),
  patch: (id: number, body: Partial<Pick<PromoPlan, "subject" | "title" | "angles" | "duration_s" | "direction" | "material_notes" | "brief" | "brief_locked" | "landmarks">>) =>
    req<{ plan: PromoPlan }>("PATCH", `/api/promos/${id}`, body),
  remove: (id: number) => req<{ ok: boolean }>("DELETE", `/api/promos/${id}`),

  // 整片提示词(端到端音频原生视频模型用):生成 / 读取 / 整段保存(手改或粘贴自己的版本)
  buildFilmPrompt: (id: number) =>
    req<{ job_id: string }>("POST", `/api/promos/${id}/film-prompt`, undefined, LLM_TIMEOUT),
  getFilmPrompt: (id: number) =>
    req<{ film_prompt: string }>("GET", `/api/promos/${id}/film-prompt`),
  saveFilmPrompt: (id: number, film_prompt: string) =>
    req<{ film_prompt: string }>("PUT", `/api/promos/${id}/film-prompt`, { film_prompt }),

  chat: (id: number, messages: ChatTurn[], onToken: (t: string) => void) =>
    chatStream(`/api/promos/${id}/chat`, { messages }, onToken),

  brief: (id: number) =>
    req<{ job_id: string }>("POST", `/api/promos/${id}/brief`, undefined, LLM_TIMEOUT),
  style: (id: number) =>
    req<{ job_id: string }>("POST", `/api/promos/${id}/style`, undefined, LLM_TIMEOUT),
  landmarks: (id: number) =>
    req<{ job_id: string }>("POST", `/api/promos/${id}/landmarks`, undefined, LLM_TIMEOUT),
  script: (id: number) =>
    req<{ job_id: string }>("POST", `/api/promos/${id}/script`, undefined, LLM_TIMEOUT),
  storyboard: (id: number) =>
    req<{ job_id: string }>("POST", `/api/promos/${id}/storyboard`, undefined, LLM_TIMEOUT),
  prompts: (id: number) =>
    req<{ job_id: string }>("POST", `/api/promos/${id}/prompts`, undefined, LLM_TIMEOUT),
  pack: (id: number) =>
    req<{ job_id: string }>("POST", `/api/promos/${id}/pack`, undefined, LLM_TIMEOUT),
  chunks: (id: number, chunkS: number) =>
    req<{ job_id: string }>("POST", `/api/promos/${id}/chunks`, { chunk_s: chunkS }, LLM_TIMEOUT),

  patchShot: (id: number, sid: number, body: Partial<PromoShot>) =>
    req<{ shot: PromoShot }>("PATCH", `/api/promos/${id}/shots/${sid}`, body),

  export: (id: number, format: "md" | "csv" | "srt" | "json") => {
    const tk = token.get();
    return fetch(`/api/promos/${id}/export?format=${format}`, {
      headers: tk ? { Authorization: `Bearer ${tk}` } : {},
    }).then(async (res) => {
      if (!res.ok) {
        // 与 clipsApi 同一口径:优先后端 detail,没有就留 HTTP 状态(别抛空消息)
        let detail = `HTTP ${res.status}`;
        try { const j = await res.json(); if (j.detail) detail = j.detail; } catch { /* ignore */ }
        throw new Error(detail);
      }
      const name = (res.headers.get("Content-Disposition") || "").split("filename*=UTF-8''")[1];
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name ? decodeURIComponent(name) : `promo.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    });
  },
};
