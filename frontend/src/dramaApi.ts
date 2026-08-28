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

function authHeaders(): Record<string, string> {
  const tk = token.get();
  return tk ? { Authorization: `Bearer ${tk}` } : {};
}

/** 上传一张图:multipart(不能手设 Content-Type,浏览器要自己带 boundary)。 */
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

/** 读一张鉴权图 → 本地 blob URL(读取端点要 Authorization 头,<img src> 带不了)。 */
async function imageBlobUrl(path: string): Promise<string> {
  const res = await fetch(path, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

// 画风方向(auto/comic_cn/anime_jp/render3d/live/ink_wash/cyber,后端 common.DRAMA_DIRECTIONS)
export interface DramaDirection {
  key: string;
  label: string;
  directive: string;
  tip: string;
}
export interface DramaStyleCard {
  id: number;
  direction: string;
  direction_label: string;
  style_name: string;
  style_cn: string;
  style_en: string;
  negative: string;
  ratio: string;
}

// 按生图站拼好的「粘贴版」(后端 engines/drama/paste.py 拼,前端只渲染):
// 单框站(GPT-image/豆包/通义)没有负面词框,负面词已改写成否定句并入 main;
// 双框站(即梦/可灵/SD)正反分开;MJ 走英文 + --ar/--no 参数。
export interface PasteVariant {
  label: string;     // 平台展示名(下拉选项)
  main: string;      // 粘进主描述框的整段
  negative: string;  // 有负面框的站粘这里;单框站为空(已并入 main)
  hint: string;      // 一句人话操作提示
}
// key = oneframe / dualbox / mj(生图);视频侧 i2v / i2v_en / t2v / r2v;顺序即后端给的展示顺序
export type PasteSet = Record<string, PasteVariant>;

// 定妆照资产:上传的存相对路径(读取走鉴权端点),外链存 http(s) 地址
export interface DramaRefImage { kind: "upload" | "url"; src: string; note: string }

export interface DramaCharacterCard {
  id: number;
  entity_id: number | null;
  name: string;
  // 性别:""=未定。单列一栏才改得动——写在外貌自由文本里的性别,英文轨那边改不到
  gender: "" | "female" | "male" | "other";
  // 描述与标定性别打架时后端给的一句人话提示(不打架为空串)
  gender_conflict: string;
  appearance_cn: string;
  appearance_en: string;
  outfit_cn: string;
  voice_desc: string;
  tts_hint: string;
  reading_notes: string;
  // 定妆照:先出一张角色参考图,后面每格「参考图 + 提示词」出图才真锁得住脸
  ref_prompt_cn: string;
  ref_prompt_en: string;
  ref_images: DramaRefImage[];
  // 音色参考音频(完整档对白出片:indextts2 克隆原料;空=未传,对白格回退普通出片)
  voice_ref?: string;
  ref_paste: PasteSet | null;
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
  source_chapter: number; // 主源章号 = source_chapters 最小值(排序锚)
  source_chapters: number[]; // 源章号全集(一集可由数章合并而来)
  source_label: string; // 人话标签:「第 3 章」/「第 3-5 章」/「第 3、7 章」
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
  // 配音情绪(完整档对白出片喂 indextts2;空=平静,选项见后端 engines/render/emotion.py)
  emotion?: string;
  duration_s: number;
  prompt_cn: string;
  prompt_en: string;
  negative: string;
  // 按平台拼好的粘贴版(后端算,导出手册与这里同一套规则);老响应可能没有
  paste?: PasteSet | null;
  // 运动轨:图生视频只吃这一条(首帧图已锁长相,再写外貌会让模型重画脸)
  motion_cn?: string;
  motion_en?: string;
  // 生视频的粘贴版:key = i2v / i2v_en / t2v(与出图那套同构,复用 PasteBox)
  video_paste?: PasteSet | null;
  // 施工进度:出好的静帧挂回这一格(段计划的首帧图就取段首格的这个),
  // clip_ref 只记「成片在哪」(视频动辄几十 MB,刻意不收上传),两个勾是进度条
  assets?: DramaRefImage[];
  clip_ref?: string;
  done_still?: boolean;
  done_video?: boolean;
}

// 集级施工进度:一集几十格,做到哪儿要一眼看见(后端 common.shot_progress 算)
export interface DramaShotProgress {
  shots: number;
  stills_done: number;
  videos_done: number;
  assets: number;
}

// 视频段:视频站单次只能出 5-15 秒,分镜格是 2-8 秒——所以要把相邻格并成「一次
// 生成一段」,再在画布/剪映里按段号首尾相接(后端 engines/drama/video.py 算)。
export interface ClipSegment {
  index: number;         // 段号 = 拼接顺序
  seqs: number[];        // 这一段包含哪几格
  label: string;         // 「第 1-2 格」
  scene_name: string;
  characters: string[];
  duration_s: number;    // 整段秒数(并段后的和)
  runs: number;          // 实际要生成几次(超上限时 >1)
  over_limit: boolean;   // 这一段超了单次上限
  first_frame: string;   // 首帧用哪一格的静帧
  // 那一格的静帧挂上来了没有:没挂这一段根本开不了工,所以段表直接标
  first_frame_ready: boolean;
  motion: string;        // 这一段怎么动(多格并段时串成一句)
  dialogue: string;      // 这一段要压的字幕
  split_hint: string;    // 超上限时的接法(尾帧续接);不超为空串
  paste?: PasteSet | null;
}
export interface ClipPlan {
  limit_s: number;       // 当前按哪个单次上限并的段
  options: number[];     // 常见档位 [5, 10, 15]
  segments: ClipSegment[];
  totals: {
    segments: number;
    duration_s: number;
    over_limit: number;
    extra_runs: number;
    first_frames_ready: number;  // 首帧图已就位的段数
  };
  note: string;          // 一句人话总结(几段/合计多少秒/哪几段要分两次)
}

export interface DramaMeta {
  approved_chapters: number[];
  approved_count: number;
  modes: { key: string; label: string }[];
  directions: DramaDirection[];
}

// 拆分镜结果:notice 是引擎的如实交代(被截断 / 总时长短于目标),空串 = 一切正常
export interface DramaBoardResult {
  episode: DramaEpisode;
  shots: DramaShot[];
  truncated: boolean;
  notice: string;
}

// 方向推荐:按书的气质荐前 3(带理由与优先级),AI 荐、用户选
export interface DramaDirectionRec {
  key: string;
  label: string;
  tip: string;
  reason: string;
  priority: number;
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

// 预告片:从各集高能素材混剪的 30-60 秒宣传片(项目级,一条)
export interface TrailerLine { speaker: string; text: string }
export interface TrailerShot {
  seq: number;
  source_ep: number; // 参考来源集号,0 = 预告片新创
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
export interface DramaTrailer {
  target_s: number;
  title: string;
  lines: TrailerLine[];
  shots: TrailerShot[];
  totals: { shots: number; duration_s: number };
}

export const dramaApi = {
  meta: (pid: number) => req<DramaMeta>("GET", `/api/projects/${pid}/drama/meta`),
  getStyle: (pid: number) =>
    req<{ style: DramaStyleCard | null }>("GET", `/api/projects/${pid}/drama/style`),
  saveStyle: (pid: number, body: Partial<DramaStyleCard>) =>
    req<{ style: DramaStyleCard }>("PUT", `/api/projects/${pid}/drama/style`, body),
  generateStyle: (pid: number, direction = "auto") =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/style/generate`,
      { direction }, LLM_TIMEOUT),
  recommendDirections: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/style/recommend-directions`,
      undefined, LLM_TIMEOUT),

  getCharacters: (pid: number) =>
    req<{ cards: DramaCharacterCard[]; scenes: DramaSceneCard[] }>(
      "GET", `/api/projects/${pid}/drama/characters`),
  generateCharacters: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/characters/generate`, undefined, LLM_TIMEOUT),
  patchCharacter: (pid: number, cid: number, body: Partial<DramaCharacterCard>) =>
    req<{ card: DramaCharacterCard }>("PATCH", `/api/projects/${pid}/drama/characters/${cid}`, body),
  // 只重出这一张角色卡:卡上拍板的性别当硬约束(治「女角色被写成男的」)
  regenCharacter: (pid: number, cid: number) =>
    req<{ job_id: string }>("POST",
      `/api/projects/${pid}/drama/characters/${cid}/regenerate`, undefined, LLM_TIMEOUT),
  // 定妆照:出提示词(names 空 = 只补还没有的;给了名字 = 强制重出那几张)
  genRefPrompts: (pid: number, names: string[] = []) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/characters/ref-prompts`,
      { names }, LLM_TIMEOUT),
  // 上传定妆照:multipart(不能手设 Content-Type,浏览器要自己带 boundary)
  uploadRef: (pid: number, cid: number, file: File, note = "") =>
    postImage<{ card: DramaCharacterCard }>(
      `/api/projects/${pid}/drama/characters/${cid}/reference`, file, note),
  linkRef: (pid: number, cid: number, url: string, note = "") =>
    req<{ card: DramaCharacterCard }>(
      "POST", `/api/projects/${pid}/drama/characters/${cid}/reference/link`, { url, note }),
  deleteRef: (pid: number, cid: number, index: number) =>
    req<{ card: DramaCharacterCard }>(
      "DELETE", `/api/projects/${pid}/drama/characters/${cid}/reference/${index}`),
  // 缩略图:读取端点要 Authorization 头,<img src> 带不了,只能取 blob 转本地 URL
  refBlobUrl: (pid: number, cid: number, index: number) =>
    imageBlobUrl(`/api/projects/${pid}/drama/characters/${cid}/reference/${index}`),

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
    req<{ episode: DramaEpisode; shots: DramaShot[]; progress?: DramaShotProgress }>(
      "GET", `/api/projects/${pid}/drama/episodes/${eid}`),
  writeScript: (pid: number, eid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/episodes/${eid}/script`, undefined, LLM_TIMEOUT),
  storyboard: (pid: number, eid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/episodes/${eid}/storyboard`, undefined, LLM_TIMEOUT),
  prompts: (pid: number, eid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/episodes/${eid}/prompts`, undefined, LLM_TIMEOUT),
  patchShot: (pid: number, sid: number, body: Partial<DramaShot>) =>
    req<{ shot: DramaShot }>("PATCH", `/api/projects/${pid}/drama/shots/${sid}`, body),
  // 视频段计划:按站点单次时长上限把分镜格并段(确定性,不调 LLM,改上限即时重算)
  getClips: (pid: number, eid: number, limitS = 10) =>
    req<{ plan: ClipPlan }>(
      "GET", `/api/projects/${pid}/drama/episodes/${eid}/clips?limit_s=${limitS}`),
  // 单格重出提示词:只动这一格(整集重跑慢且会盖掉手改),note = 这一格的额外要求
  regenShotPrompt: (pid: number, sid: number, note = "") =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/shots/${sid}/prompt`,
      { note }, LLM_TIMEOUT),
  // 逐格施工单的进度层:出好的静帧挂回这一格(挂上即自动勾「静帧」),
  // 段计划的首帧图就取段首格挂的这张。视频不收上传,只在 clip_ref 记「成片在哪」。
  uploadShotAsset: (pid: number, sid: number, file: File, note = "") =>
    postImage<{ shot: DramaShot }>(
      `/api/projects/${pid}/drama/shots/${sid}/asset`, file, note),
  linkShotAsset: (pid: number, sid: number, url: string, note = "") =>
    req<{ shot: DramaShot }>(
      "POST", `/api/projects/${pid}/drama/shots/${sid}/asset/link`, { url, note }),
  deleteShotAsset: (pid: number, sid: number, index: number) =>
    req<{ shot: DramaShot }>(
      "DELETE", `/api/projects/${pid}/drama/shots/${sid}/asset/${index}`),
  shotAssetBlobUrl: (pid: number, sid: number, index: number) =>
    imageBlobUrl(`/api/projects/${pid}/drama/shots/${sid}/asset/${index}`),
  // 音色参考(完整档对白出片:克隆角色嗓音的原料;重传即换)
  uploadVoice: (pid: number, cid: number, file: File) =>
    postImage<{ card: DramaCharacterCard }>(
      `/api/projects/${pid}/drama/characters/${cid}/voice`, file),
  deleteVoice: (pid: number, cid: number) =>
    req<{ card: DramaCharacterCard }>(
      "DELETE", `/api/projects/${pid}/drama/characters/${cid}/voice`),
  voiceBlobUrl: (pid: number, cid: number) =>
    imageBlobUrl(`/api/projects/${pid}/drama/characters/${cid}/voice`),
  // 成片包(阶段 2):配音稿 + 剪辑清单
  buildPack: (pid: number, eid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/episodes/${eid}/pack`, undefined, LLM_TIMEOUT),
  getPack: (pid: number, eid: number) =>
    req<{ pack: DramaProductionPack | null }>(
      "GET", `/api/projects/${pid}/drama/episodes/${eid}/pack`),

  // 预告片:从各集高能素材混剪 30-60 秒宣传片
  generateTrailer: (pid: number, body: { from_ep: number; to_ep: number; target_s: number }) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/drama/trailer/generate`, body, LLM_TIMEOUT),
  getTrailer: (pid: number) =>
    req<{ trailer: DramaTrailer | null }>("GET", `/api/projects/${pid}/drama/trailer`),
  exportTrailer: (pid: number, format: "md" | "srt") =>
    downloadFile(`/api/projects/${pid}/drama/trailer/export?format=${format}`, `trailer.${format}`),

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
