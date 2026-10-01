// src/animeApi.ts — 动画短剧工坊 API 客户端(对齐 backend/app/api/anime.py)。
// 独立模块(同 dramaApi/promoApi/clipsApi 的理由);传输层已统一到 ./http。
import { req } from "./http";
import { CreativeGoal } from "./creativeApi";

const LLM_TIMEOUT = 900_000;
export type AnimeWorkspace = "anime" | "original";
const workspaceQuery = (workspace: AnimeWorkspace = "anime") =>
  workspace === "anime" ? "" : `?workspace=${encodeURIComponent(workspace)}`;

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
  workspace: AnimeWorkspace;
  title: string;
  premise: string;
  genre: string;
  genre_label: string;
  direction: string;
  style_cn: string;
  cast: AnimeCastMember[];
  episode_s: number;
  status: string;
  creative_goal?: CreativeGoal | null;
}

export interface AnimeScriptLine { speaker: string; text: string; action: string; duration_s: number; pause_s: number }
export interface AnimeScript {
  title: string; scenes: { slug: string; purpose: string; lines: AnimeScriptLine[] }[];
  total_s: number; goal_version: number; synopsis: string; stale?: boolean; history?: AnimeScript[];
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
  script?: AnimeScript | null;
  creative_stale?: boolean;
  guests?: AnimeCastMember[];
}

export const animeApi = {
  meta: () => req<AnimeMeta>("GET", "/api/anime/meta"),
  list: (workspace: AnimeWorkspace = "anime") => req<{ series: AnimeSeries[] }>("GET", `/api/anime${workspaceQuery(workspace)}`),
  create: (body: { title: string; premise: string; genre: string; direction: string; episode_s: number; workspace?: AnimeWorkspace }) =>
    req<{ series: AnimeSeries }>("POST", "/api/anime", body),
  get: (id: number, workspace: AnimeWorkspace = "anime") =>
    req<{ series: AnimeSeries; episodes: AnimeEpisode[] }>("GET", `/api/anime/${id}${workspaceQuery(workspace)}`),
  patch: (id: number, body: {
    title?: string; premise?: string; genre?: string; direction?: string;
    style_cn?: string; episode_s?: number;
  }, workspace: AnimeWorkspace = "anime") => req<{ series: AnimeSeries }>("PATCH", `/api/anime/${id}${workspaceQuery(workspace)}`, body),
  remove: (id: number, workspace: AnimeWorkspace = "anime") => req<{ ok: boolean }>("DELETE", `/api/anime/${id}${workspaceQuery(workspace)}`),

  // ---- 没灵感:AI 出点子(不落库,选中由前端回填) ----
  suggestPremise: (genre: string) =>
    req<{ premises: string[] }>("POST", "/api/anime/suggest-premise", { genre }, LLM_TIMEOUT),
  suggestEpisode: (id: number, workspace: AnimeWorkspace = "anime") =>
    req<{ premises: string[] }>("POST", `/api/anime/${id}/suggest-episode${workspaceQuery(workspace)}`, {}, LLM_TIMEOUT),

  // ---- 卡司 ----
  buildCast: (id: number, workspace: AnimeWorkspace = "anime") =>
    req<{ job_id: string }>("POST", `/api/anime/${id}/cast${workspaceQuery(workspace)}`, {}, LLM_TIMEOUT),
  saveCast: (id: number, cast: AnimeCastMember[], workspace: AnimeWorkspace = "anime") =>
    req<{ series: AnimeSeries }>("PUT", `/api/anime/${id}/cast${workspaceQuery(workspace)}`, { cast }),

  // ---- 剧集 ----
  createEpisode: (id: number, premise: string, workspace: AnimeWorkspace = "anime") =>
    req<{ episode: AnimeEpisode }>("POST", `/api/anime/${id}/episodes${workspaceQuery(workspace)}`, { premise }),
  patchEpisode: (eid: number, body: { premise?: string; title?: string }, workspace: AnimeWorkspace = "anime") =>
    req<{ episode: AnimeEpisode }>("PATCH", `/api/anime/episodes/${eid}${workspaceQuery(workspace)}`, body),
  removeEpisode: (eid: number, workspace: AnimeWorkspace = "anime") =>
    req<{ ok: boolean }>("DELETE", `/api/anime/episodes/${eid}${workspaceQuery(workspace)}`),
  buildTakes: (eid: number, workspace: AnimeWorkspace = "anime") =>
    req<{ job_id: string }>("POST", `/api/anime/episodes/${eid}/takes${workspaceQuery(workspace)}`, {}, LLM_TIMEOUT),
  pick: (eid: number, index: number, workspace: AnimeWorkspace = "anime") =>
    req<{ episode: AnimeEpisode }>("POST", `/api/anime/episodes/${eid}/pick${workspaceQuery(workspace)}`, { index }),
  // 点子聊天:AI 接住用户的话,补充完善出当前完整版简介(同步长调用)
  chat: (eid: number, message: string, workspace: AnimeWorkspace = "anime") =>
    req<{ reply: string; synopsis: string; episode: AnimeEpisode }>(
      "POST", `/api/anime/episodes/${eid}/chat${workspaceQuery(workspace)}`, { message }, LLM_TIMEOUT),
  // 用户拍板确认简介:分镜解锁;AI 每出新草稿或换梗纲都会重新上锁
  confirmSynopsis: (eid: number, synopsis?: string, workspace: AnimeWorkspace = "anime") =>
    req<{ episode: AnimeEpisode }>(
      "POST", `/api/anime/episodes/${eid}/confirm-synopsis${workspaceQuery(workspace)}`,
      synopsis === undefined ? {} : { synopsis }),
  buildShots: (eid: number, workspace: AnimeWorkspace = "anime") =>
    req<{ job_id: string }>("POST", `/api/anime/episodes/${eid}/shots${workspaceQuery(workspace)}`, {}, LLM_TIMEOUT),
  buildScript: (eid: number, feedback = "", workspace: AnimeWorkspace = "anime") =>
    req<{ job_id: string }>("POST", `/api/anime/episodes/${eid}/script${workspaceQuery(workspace)}`, { feedback }),
  saveScript: (eid: number, script: AnimeScript, workspace: AnimeWorkspace = "anime") =>
    req<{ episode: AnimeEpisode }>("PUT", `/api/anime/episodes/${eid}/script${workspaceQuery(workspace)}`, { script }),
  saveGuests: (eid: number, guests: AnimeCastMember[], workspace: AnimeWorkspace = "anime") =>
    req<{ episode: AnimeEpisode }>("PUT", `/api/anime/episodes/${eid}/guests${workspaceQuery(workspace)}`, { guests }),
  saveShots: (eid: number, shots: AnimeShot[], workspace: AnimeWorkspace = "anime") =>
    req<{ episode: AnimeEpisode }>("PUT", `/api/anime/episodes/${eid}/shots${workspaceQuery(workspace)}`, { shots }),

  // ---- 整集分段提示词 ----
  buildFilmPrompt: (eid: number, segmentS: 15 | 30 = 15, workspace: AnimeWorkspace = "anime") =>
    req<{ job_id: string }>(
      "POST", `/api/anime/episodes/${eid}/film-prompt${workspaceQuery(workspace)}`, { segment_s: segmentS }, LLM_TIMEOUT),
  getFilmPrompt: (eid: number, workspace: AnimeWorkspace = "anime") =>
    req<{ film_prompt: string }>("GET", `/api/anime/episodes/${eid}/film-prompt${workspaceQuery(workspace)}`),
  saveFilmPrompt: (eid: number, film_prompt: string, workspace: AnimeWorkspace = "anime") =>
    req<{ film_prompt: string }>("PUT", `/api/anime/episodes/${eid}/film-prompt${workspaceQuery(workspace)}`, { film_prompt }),
};
