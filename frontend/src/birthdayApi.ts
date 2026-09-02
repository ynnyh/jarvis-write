// src/birthdayApi.ts — 生日祝福工坊 API 客户端(对齐 backend/app/api/birthday.py)。
// 独立模块(同 clipsApi/promoApi 的理由);导出用鉴权 fetch(复用 api.ts 的 token)。
import { ApiError, imageBlobUrl, postImage, token } from "./api";

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

export interface BirthdayTone { key: string; label: string; directive: string }
export interface BirthdayDirection { key: string; label: string; tip: string }
/** 关系目录项(决定视角与口吻) */
export interface RelationOption { key: string; label: string }
/** 风格包目录项:儿童向角色世界包(佩奇式/奥特曼式…) */
export interface BirthdayPack { key: string; label: string; directive: string }

export interface WishShot {
  seq: number;
  scene_name: string;
  characters: string[];
  character_desc?: string;
  action_desc: string;
  shot_type: string;
  camera: string;
  dialogue: string;
  duration_s: number;
  prompt_cn: string;
  prompt_en: string;
  negative: string;
}

export interface WishCharacterCard {
  name: string;
  desc: string;
}

export interface WishChunk {
  index: number; start_s: number; end_s: number; duration_s: number;
  over_limit: boolean; shot_seqs: number[]; scenes: string[]; subtitle: string;
}

/** 一个本子(候选或最终态)。与 ClipCard 同形,但无 quote_source(生日片无金句溯源)。 */
export interface WishCard {
  take: string;
  logline: string;
  emotion_curve: string;
  lines: { speaker: string; text: string; action?: string }[];
  shots: WishShot[];
  character_cards?: WishCharacterCard[];
  punchline: string;
  chunks: WishChunk[];
  hook_text: string;
  cautions: string[];
}

export interface BirthdayWish {
  id: number;
  occasion: string;
  tone: string;
  custom_tone: string;
  tone_display: string;
  honoree_name: string;
  relationship: string;
  relationship_label: string;
  milestone: string;
  memories: string[];
  sender_desc: string;
  duration_s: number;
  /** 风格包 key(空=不用包);选包后画风与世界以包为准 */
  pack: string;
  pack_label: string;
  direction: string;
  direction_label: string;
  style_hints: string;
  style_name: string;
  style_cn: string;
  style_en: string;
  negative: string;
  chosen: number;
  clip: WishCard | Record<string, never>;
  status: string;
  status_cn: string;
  candidates?: WishCard[];
}

// ---- 出片工作台(BirthdayShoot.shoot 单元素)----
export interface WishRefImage { kind: "upload" | "url"; src: string; note: string }
export interface WishShootUnit {
  index: number;
  start_s: number;
  end_s: number;
  duration_s: number;
  over_limit: boolean;
  subtitle: string;
  shot_seqs: number[];
  scenes: string[];
  ref_images: WishRefImage[];
  done: boolean;
  result_link: string;
  note: string;
}

export interface BirthdayWishInput {
  honoree_name: string;
  relationship: string;
  milestone?: string;
  memories: string[];
  sender_desc?: string;
  tone?: string;
  custom_tone?: string;
  duration_s: number;
  /** 风格包 key(佩奇式/奥特曼式…;空=不用包走通用画风) */
  pack?: string;
  direction: string;
  style_hints?: string;
}

export const birthdayApi = {
  meta: () =>
    req<{
      tones: BirthdayTone[]; packs: BirthdayPack[]; relationships: RelationOption[];
      milestones: string[]; durations: number[]; directions: BirthdayDirection[];
      max_memories: number; memory_max_chars: number;
    }>("GET", "/api/birthday/meta"),
  list: () => req<{ wishes: BirthdayWish[] }>("GET", "/api/birthday"),
  create: (body: BirthdayWishInput) =>
    req<{ wish_row: BirthdayWish }>("POST", "/api/birthday", body),
  get: (id: number) => req<{ wish_row: BirthdayWish }>("GET", `/api/birthday/${id}`),
  patch: (id: number, body: Partial<BirthdayWishInput>) =>
    req<{ wish_row: BirthdayWish }>("PATCH", `/api/birthday/${id}`, body),
  generate: (id: number, feedback?: string) =>
    req<{ job_id: string }>(
      "POST", `/api/birthday/${id}/generate`, feedback ? { feedback } : {}, LLM_TIMEOUT),
  reexpand: (id: number, index: number, feedback?: string) =>
    req<{ job_id: string }>(
      "POST", `/api/birthday/${id}/reexpand`, { index, feedback: feedback || "" }, LLM_TIMEOUT),
  pick: (id: number, index: number) =>
    req<{ wish_row: BirthdayWish }>("POST", `/api/birthday/${id}/pick`, { index }),
  saveCard: (id: number, card: WishCard) =>
    req<{ wish_row: BirthdayWish }>("PUT", `/api/birthday/${id}/card`, { card }),
  remove: (id: number) => req<{ ok: boolean }>("DELETE", `/api/birthday/${id}`),
  export: (id: number, format: "md" | "srt" | "json") => {
    const tk = token.get();
    return fetch(`/api/birthday/${id}/export?format=${format}`, {
      headers: tk ? { Authorization: `Bearer ${tk}` } : {},
    }).then(async (res) => {
      if (!res.ok) {
        // 兜底名必须保留 HTTP 状态:后端没给 detail 时抛空串,前端 toast 就弹一个空白框
        let detail = `HTTP ${res.status}`;
        try { const j = await res.json(); if (j.detail) detail = j.detail; } catch { /* ignore */ }
        throw new Error(detail);
      }
      const name = (res.headers.get("Content-Disposition") || "").split("filename*=UTF-8''")[1];
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name ? decodeURIComponent(name) : `wish.${format}`;
      document.body.appendChild(a);
      a.click(); a.remove();
      URL.revokeObjectURL(url);
    });
  },
  // ---- 出片工作台 ----
  getShoot: (id: number) => req<{ shoot: WishShootUnit[] }>("GET", `/api/birthday/${id}/shoot`),
  updateShoot: (id: number, shoot: WishShootUnit[]) =>
    req<{ shoot: WishShootUnit[] }>("PUT", `/api/birthday/${id}/shoot`, { shoot }),
  uploadRef: (id: number, index: number, file: File, note = "") =>
    postImage<{ shoot: WishShootUnit[] }>(`/api/birthday/${id}/shoot/${index}/reference`, file, note),
  linkRef: (id: number, index: number, url: string, note = "") =>
    req<{ shoot: WishShootUnit[] }>("POST", `/api/birthday/${id}/shoot/${index}/reference/link`, { url, note }),
  deleteRef: (id: number, index: number, imgIndex: number) =>
    req<{ shoot: WishShootUnit[] }>("DELETE", `/api/birthday/${id}/shoot/${index}/reference/${imgIndex}`),
  refReadUrl: (id: number, index: number, imgIndex: number) => `/api/birthday/${id}/shoot/${index}/reference/${imgIndex}`,
  refBlobUrl: (id: number, index: number, imgIndex: number) =>
    imageBlobUrl(`/api/birthday/${id}/shoot/${index}/reference/${imgIndex}`),
};
