// src/animeApi.ts — 动画短剧工坊 API 客户端(对齐 backend/app/api/anime.py)。
// 独立模块(同 dramaApi/promoApi/clipsApi 的理由);传输层已统一到 ./http。
import { req } from "./http";

const LLM_TIMEOUT = 900_000;

export interface AnimeGenre { key: string; label: string; framing: string }
export interface AnimeDirection { key: string; label: string; tip: string }
export interface AnimeMeta {
  genres: AnimeGenre[];
  directions: AnimeDirection[];
  episode_s: number[];
  segment_s: number[];
  max_shots: number;
}

/** 卡司成员:定妆(appearance/wardrobe)是跨集一致性的锚,生成提示词时逐字注入 */
export interface AnimeCastMember {
  name: string;
  role: string;           // "主角" | "配角";全组恰好 1 个主角
  appearance: string;
  wardrobe: string;
  personality: string;
  catchphrase: string;
  locked: boolean;        // 锁定后 AI 重出卡司时原样保留
}

/** 梗纲:三选一;beats 按类型节奏库展开 */
export interface AnimeTake {
  logline: string;
  beats: string[];
  punchline: string;
  highlight: string;
}

/** 一镜:台词/动作全开(音频原生视频模型直接生成语音与动作) */
export interface AnimeShot {
  seq: number;
  shot_type: string;
  camera: string;
  duration_s: number;
  action_desc: string;
  dialogue: string;
  speaker: string;
  characters: string[];
  sfx: string;
}

export interface AnimeSeries {
  id: number;
  title: string;
  premise: string;
  genre: string;
  genre_label: string;
  direction: string;
  style_cn: string;
  cast: AnimeCastMember[];
  episode_s: number;
  status: string;
}

export interface AnimeEpisode {
  id: number;
  series_id: number;
  seq: number;
  title: string;
  premise: string;
  /** 点子聊天线程:[{role:'user'|'assistant', content}] */
  chat: { role: string; content: string }[];
  /** 本集简介:聊天打磨的草稿或确认稿 */
  synopsis: string;
  /** 简介已确认(分镜解锁);AI 每出一版新草稿会重新上锁 */
  synopsis_ok: boolean;
  takes: AnimeTake[];
  chosen: number;         // -1 未选
  shots: AnimeShot[];
  film_prompt: string;
  status: string;
}

export const animeApi = {
  meta: () => req<AnimeMeta>("GET", "/api/anime/meta"),
  list: () => req<{ series: AnimeSeries[] }>("GET", "/api/anime"),
  create: (body: { title: string; premise: string; genre: string; direction: string; episode_s: number }) =>
    req<{ series: AnimeSeries }>("POST", "/api/anime", body),
  get: (id: number) =>
    req<{ series: AnimeSeries; episodes: AnimeEpisode[] }>("GET", `/api/anime/${id}`),
  patch: (id: number, body: {
    title?: string; premise?: string; genre?: string; direction?: string;
    style_cn?: string; episode_s?: number;
  }) => req<{ series: AnimeSeries }>("PATCH", `/api/anime/${id}`, body),
  remove: (id: number) => req<{ ok: boolean }>("DELETE", `/api/anime/${id}`),

  // ---- 卡司 ----
  buildCast: (id: number) =>
    req<{ job_id: string }>("POST", `/api/anime/${id}/cast`, {}, LLM_TIMEOUT),
  saveCast: (id: number, cast: AnimeCastMember[]) =>
    req<{ series: AnimeSeries }>("PUT", `/api/anime/${id}/cast`, { cast }),

  // ---- 剧集 ----
  createEpisode: (id: number, premise: string) =>
    req<{ episode: AnimeEpisode }>("POST", `/api/anime/${id}/episodes`, { premise }),
  patchEpisode: (eid: number, body: { premise?: string; title?: string }) =>
    req<{ episode: AnimeEpisode }>("PATCH", `/api/anime/episodes/${eid}`, body),
  removeEpisode: (eid: number) =>
    req<{ ok: boolean }>("DELETE", `/api/anime/episodes/${eid}`),
  buildTakes: (eid: number) =>
    req<{ job_id: string }>("POST", `/api/anime/episodes/${eid}/takes`, {}, LLM_TIMEOUT),
  pick: (eid: number, index: number) =>
    req<{ episode: AnimeEpisode }>("POST", `/api/anime/episodes/${eid}/pick`, { index }),
  // 点子聊天:AI 接住用户的话,补充完善出当前完整版简介(同步长调用)
  chat: (eid: number, message: string) =>
    req<{ reply: string; synopsis: string; episode: AnimeEpisode }>(
      "POST", `/api/anime/episodes/${eid}/chat`, { message }, LLM_TIMEOUT),
  // 用户拍板确认简介:分镜解锁;AI 每出新草稿或换梗纲都会重新上锁
  confirmSynopsis: (eid: number, synopsis?: string) =>
    req<{ episode: AnimeEpisode }>(
      "POST", `/api/anime/episodes/${eid}/confirm-synopsis`,
      synopsis === undefined ? {} : { synopsis }),
  buildShots: (eid: number) =>
    req<{ job_id: string }>("POST", `/api/anime/episodes/${eid}/shots`, {}, LLM_TIMEOUT),
  saveShots: (eid: number, shots: AnimeShot[]) =>
    req<{ episode: AnimeEpisode }>("PUT", `/api/anime/episodes/${eid}/shots`, { shots }),

  // ---- 整集分段提示词 ----
  buildFilmPrompt: (eid: number, segmentS: 15 | 30 = 15) =>
    req<{ job_id: string }>(
      "POST", `/api/anime/episodes/${eid}/film-prompt`, { segment_s: segmentS }, LLM_TIMEOUT),
  getFilmPrompt: (eid: number) =>
    req<{ film_prompt: string }>("GET", `/api/anime/episodes/${eid}/film-prompt`),
  saveFilmPrompt: (eid: number, film_prompt: string) =>
    req<{ film_prompt: string }>("PUT", `/api/anime/episodes/${eid}/film-prompt`, { film_prompt }),
};
