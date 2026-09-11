// src/scriptsApi.ts — 剧本工坊 API 客户端(对齐 backend/app/api/scripts.py)。
// 独立模块(同 clipsApi/seriesApi 的理由);传输层已统一到 ./http。
// 大纲/单集生成是同步长调用(后端直接等 LLM 返回),超时给足 15 分钟。
import { req } from "./http";

const LLM_TIMEOUT = 900_000;

export interface Script {
  id: number;
  title: string;
  genre: string;
  logline: string;
  target_episodes: number;
  status: string;
  style_memo: string;
  source_project_id: number | null;
}

export interface ScriptEpisode {
  id: number;
  episode_number: number;
  title: string;
  synopsis: string;
  opening_hook: string;
  ending_hook: string;
  status: string;
  content: string;
  word_count: number;
}

export interface AdaptResult {
  script_id: number;
  title: string;
  episodes: number;
  logline: string;
}

export const scriptsApi = {
  // 后端响应是裸对象/裸数组(不走 {xxx: ...} 包装)
  list: () => req<Script[]>("GET", "/api/scripts"),
  create: (body: { title?: string; genre?: string; logline?: string; target_episodes?: number }) =>
    req<Script>("POST", "/api/scripts", body),
  get: (id: number) => req<Script>("GET", `/api/scripts/${id}`),
  update: (id: number, body: {
    title?: string; genre?: string; logline?: string; style_memo?: string; target_episodes?: number;
  }) => req<Script>("PATCH", `/api/scripts/${id}`, body),
  remove: (id: number) => req<{ deleted: boolean }>("DELETE", `/api/scripts/${id}`),

  episodes: (id: number) => req<ScriptEpisode[]>("GET", `/api/scripts/${id}/episodes`),
  // 注意:响应模型对齐后端——大纲是 {"episodes": [...]},单集生成直接回 EpisodeOut
  outline: (id: number) =>
    req<{ episodes: ScriptEpisode[] }>("POST", `/api/scripts/${id}/generate-outline`, {}, LLM_TIMEOUT),
  genEpisode: (id: number, n: number, extraDirection = "") =>
    req<ScriptEpisode>(
      "POST", `/api/scripts/${id}/episodes/${n}/generate`,
      extraDirection ? { extra_direction: extraDirection } : {}, LLM_TIMEOUT),
  saveEpisode: (id: number, n: number, body: { title?: string; content?: string; status?: string }) =>
    req<ScriptEpisode>("PATCH", `/api/scripts/${id}/episodes/${n}`, body),

  // 小说改编:定稿章 → 分集大纲 → 建剧本
  adapt: (projectId: number, body: { target_episodes: number; chapter_numbers?: number[] }) =>
    req<AdaptResult>("POST", `/api/projects/${projectId}/adapt-to-script`, body, LLM_TIMEOUT),
};
