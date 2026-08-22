// src/dramaApi.ts — 漫剧工坊 API 客户端(对齐 backend/app/api/drama.py)。
// 独立模块说明:api.ts 正被并行开发占用,为避免同文件编辑冲突,漫剧接口自成
// 模块并复用 api.ts 的既有导出(token/ApiError/downloadFile);api.ts 稳定后可并入。
import { ApiError, downloadFile, token } from "./api";

// 复刻 api.ts 的 req 行为(401 统一跳登录由 ApiError 抛出方处理,这里保持一致简化)
async function req<T>(method: string, path: string, body?: unknown, timeoutMs = 30000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const headers: Record<string, string> = {};
    if (body) headers["Content-Type"] = "application/json";
    const tk = token.get();
    if (tk) headers["Authorization"] = `Bearer ${tk}`;
    const res = await fetch(path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
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

// LLM 长任务(规划/剧本/分镜/提示词)统一超时:与 api.ts 的章节生成对齐
const LLM_TIMEOUT = 900_000;

export interface DramaStyleCard {
  id: number;
  style_name: string;
  style_cn: string;
  style_en: string;
  negative: string;
  ratio: string;
}

export interface DramaCharacterCard {
  id: number;
  entity_id: number | null;
  name: string;
  appearance_cn: string;
  appearance_en: string;
  outfit_cn: string;
  voice_desc: string;
  tts_hint: string;
  reading_notes: string;
  locked: boolean;
}

export interface DramaSceneCard {
  id: number;
  name: string;
  appearance_cn: string;
  appearance_en: string;
}

export interface DramaScriptLine { speaker: string; text: string; action?: string }
export interface DramaEpisode {
  id: number;
  ep_index: number;
  title: string;
  source_chapter: number;
  hook: string;
  recap: string;
  cliffhanger: string;
  mode: "dialogue" | "narration";
  duration_target_s: number;
  script: { mode?: string; synopsis?: string; lines?: DramaScriptLine[] };
  status: "planned" | "scripted" | "storyboarded" | "ready";
}

export interface DramaShot {
  id: number;
  episode_id: number;
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

export interface DramaMeta {
  approved_chapters: number[];
  approved_count: number;
  modes: { key: string; label: string }[];
}

// 成片包(阶段 2):配音稿 + 剪辑清单
export interface DubbingLine {
  seq: number;
  speaker: string;
  voice: string;
  tts_hint: string;
  reading_notes: string;
  text: string;
  tts_text: string;
  est_s: number;
  shot_duration_s: number;
}
export interface ChecklistItem {
  seq: number;
  scene: string;
  duration_s: number;
  subtitle: string;
  transition: string;
  bgm_tag: string;
  note: string;
}
export interface DramaProductionPack {
  mode: string;
  synopsis: string;
  dubbing: DubbingLine[];
  narration_full: string;
  checklist: ChecklistItem[];
  totals: { shots: number; target_s: number; storyboard_s: number; voice_s: number };
}

export const dramaApi = {
  meta: (pid: number) => req<DramaMeta>("GET", `/api/projects/${pid}/drama/meta`),
  getStyle: (pid: number) =>
    req<{ style: DramaStyleCard | null }>("GET", `/api/projects/${pid}/drama/style`),
  saveStyle: (pid: number, body: Partial<DramaStyleCard>) =>
    req<{ style: DramaStyleCard }>("PUT", `/api/projects/${pid}/drama/style`, body),
  generateStyle: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/style/generate`, undefined, LLM_TIMEOUT),

  getCharacters: (pid: number) =>
    req<{ cards: DramaCharacterCard[]; scenes: DramaSceneCard[] }>(
      "GET", `/api/projects/${pid}/drama/characters`),
  generateCharacters: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/characters/generate`, undefined, LLM_TIMEOUT),
  patchCharacter: (pid: number, cid: number, body: Partial<DramaCharacterCard>) =>
    req<{ card: DramaCharacterCard }>("PATCH", `/api/projects/${pid}/drama/characters/${cid}`, body),
  // 声线选型卡(阶段 2):给角色卡补 TTS 平台选型建议 + 朗读指示
  generateVoiceCast: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/voice-cast/generate`, undefined, LLM_TIMEOUT),

  getEpisodes: (pid: number) =>
    req<{ episodes: DramaEpisode[] }>("GET", `/api/projects/${pid}/drama/episodes`),
  plan: (pid: number, body: { from_chapter: number; to_chapter: number; mode: string; duration_s: number }) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/episodes/plan`, body, LLM_TIMEOUT),
  deleteEpisode: (pid: number, eid: number) =>
    req<{ ok: boolean }>("DELETE", `/api/projects/${pid}/drama/episodes/${eid}`),
  getEpisode: (pid: number, eid: number) =>
    req<{ episode: DramaEpisode; shots: DramaShot[] }>(
      "GET", `/api/projects/${pid}/drama/episodes/${eid}`),
  writeScript: (pid: number, eid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/episodes/${eid}/script`, undefined, LLM_TIMEOUT),
  storyboard: (pid: number, eid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/episodes/${eid}/storyboard`, undefined, LLM_TIMEOUT),
  prompts: (pid: number, eid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/episodes/${eid}/prompts`, undefined, LLM_TIMEOUT),
  patchShot: (pid: number, sid: number, body: Partial<DramaShot>) =>
    req<{ shot: DramaShot }>("PATCH", `/api/projects/${pid}/drama/shots/${sid}`, body),
  // 成片包(阶段 2):配音稿 + 剪辑清单
  buildPack: (pid: number, eid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/episodes/${eid}/pack`, undefined, LLM_TIMEOUT),
  getPack: (pid: number, eid: number) =>
    req<{ pack: DramaProductionPack | null }>(
      "GET", `/api/projects/${pid}/drama/episodes/${eid}/pack`),

  exportEpisode: (pid: number, eid: number, format: "md" | "csv" | "json" | "pack" | "srt") =>
    downloadFile(`/api/projects/${pid}/drama/episodes/${eid}/export?format=${format}`,
      `drama-export.${format}`),
};

export const DRAMA_STATUS_CN: Record<string, string> = {
  planned: "已规划",
  scripted: "已有剧本",
  storyboarded: "已有分镜",
  ready: "提示词就绪",
};
