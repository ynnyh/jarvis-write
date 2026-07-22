// src/api.ts — 后端 API 客户端(对齐 backend/app/api/*)
const BASE = "";
const TOKEN_KEY = "jarvis_token";

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY) || "",
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

// 收到 401 时的回调:由 App 注册,统一跳登录
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) { onUnauthorized = fn; }

async function req<T>(method: string, path: string, body?: unknown, timeoutMs = 30000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const headers: Record<string, string> = {};
    if (body) headers["Content-Type"] = "application/json";
    const tk = token.get();
    if (tk) headers["Authorization"] = `Bearer ${tk}`;
    const res = await fetch(BASE + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    if (!res.ok) {
      if (res.status === 401) {
        token.clear();
        onUnauthorized?.();
      }
      let detail = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        detail = j.detail ?? JSON.stringify(j);
      } catch { /* ignore */ }
      throw new Error(detail);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

// LLM 长任务统一超时:章节生成/架构生成可能 3-10 分钟
const LLM_TIMEOUT = 900_000;

// ---------- 类型 ----------
export type Tendency = Record<string, unknown>;

export interface Project {
  id: number; title: string; topic: string; genre: string;
  target_chapters: number; target_words_per_chapter: number;
  global_tendency: Tendency; status: string;
  concept?: Concept | null;
  synopsis?: string | null;
  // 起步流:非空 = 创建未完成,值为停留步骤(idea/tone/title/scale/launch)
  setup_state?: string | null;
  // 灵感对话记录(对话式捏概念的持久化)
  chat_log?: ChatTurn[] | null;
  // 列表页进度聚合(仅 GET /projects 填充)
  written_chapters?: number;
  total_words?: number;
}
export interface Architecture {
  core_seed: string; character_dynamics: string;
  world_building: string; plot_architecture: string; version: number;
}
export interface Outline {
  id: number; chapter_number: number; title: string; chapter_role: string;
  chapter_purpose: string; suspense_level: string; foreshadowing: string;
  plot_twist_level: string; summary: string; characters_involved: string[] | null;
  key_items: unknown[]; scene_location: string; current_version: number;
}
export interface ChapterBrief {
  chapter_number: number; status: string; word_count: number; is_stale: boolean;
}
export interface ChapterDetail extends ChapterBrief {
  draft_content: string; final_content: string; outline_version_used: number;
}
export interface GenerateChapterResponse extends ChapterDetail {
  consistency_issues: Record<string, string>[];
  extraction_stats: Record<string, unknown>;
  ai_flavor: FlavorInfo;
}
/** 章节正文历史版本(覆盖前的快照)。source: generated/polished/edited/restored */
export interface ChapterVersionBrief {
  id: number; version: number; source: string; word_count: number; created_at: string;
}
export interface ChapterVersionDetail extends ChapterVersionBrief {
  final_content: string; draft_content: string;
}
/** 版本来源的中文说明 */
export const VERSION_SOURCE_CN: Record<string, string> = {
  generated: "重写前", polished: "润色前", edited: "编辑前", restored: "回滚前",
};
/** AI 味报告:score/summary 必备;categories 分类得分明细(新版后端返回,旧格式没有) */
export interface FlavorInfo {
  score: number;
  summary: string;
  categories?: Record<string, { count: number; weight: number; score: number }>;
}
/** hover 展示用:summary + 分类得分明细(兼容无明细的旧格式) */
export function flavorTitle(f: FlavorInfo): string {
  if (!f.categories || !Object.keys(f.categories).length) return f.summary;
  const cats = Object.entries(f.categories)
    .sort((a, b) => b[1].score - a[1].score)
    .map(([k, v]) => `${k}×${v.count}`)
    .join("、");
  return `${f.summary}\n分类明细:${cats}`;
}
export interface Chip {
  label: string; directive: string;
  // 两级题材库(仅 genre 维度):所属大类 key / 用户向一句话卖点
  category?: string | null; desc?: string | null;
}
export interface Dimension {
  key: string; label: string; select: "single" | "multi"; chips: Chip[];
  categories?: { key: string; label: string }[] | null;
}
export interface NodeCatalog { node: string; label: string; dimensions: Dimension[]; }
export interface EditResult {
  status: string; change_type: string | null; change_summary: string;
  changed_fields: string[]; own_chapter_stale: boolean;
  needs_impact_analysis: boolean; outline: Outline;
}
export interface ImpactItem { chapter_number: number; reason: string; action: string; }
export interface ImpactReport { source_chapter: number; affected: ImpactItem[]; overall: string; }
export interface CascadeResult {
  updated: number[]; stale_chapters: number[]; warnings: string[]; outlines: Outline[];
}
export interface DirectiveItem {
  chapter_number: number; new_title: string | null;
  new_summary: string; change_reason: string;
}
export interface DirectivePreview {
  analysis: string; items: DirectiveItem[]; suggest_retire: string[];
}
export interface DirectiveApplyResult { updated: number[]; stale_chapters: number[]; }
export interface FactOut {
  entity: string; fact_type: string; content: string;
  valid_from: number; valid_until: number | null; importance: string;
}
export interface BibleSnapshot { chapter: number; facts: FactOut[]; entities_count: number; }
export interface ForeshadowOut {
  id: number; description: string; status: string; chapter_planted: number;
  expected_payoff_chapter: number | null; payoff_chapter: number | null;
  reinforcement_chapters: number[]; importance: string; is_due: boolean;
}
export interface CharacterFact {
  id: number; fact_type: string; content: string;
  valid_from: number; valid_until: number | null; importance: string;
}
export interface CharacterRelation {
  other_name: string; description: string; valid_from: number; other_retired: boolean;
}
export interface CharacterCard {
  id: number; name: string; aliases: string[]; entity_type: string; retired: boolean;
  profile: string; key_facts: CharacterFact[]; appearance_chapters: number[];
  relations: CharacterRelation[];
}
export interface CharactersOut { characters: CharacterCard[]; other_entities_count: number; }
// 全书概览(看板「概览」页签):一次聚合章节状态/版本对照、伏笔区间、人物出场
export interface OverviewChapter {
  chapter_number: number; title: string; chapter_role: string;
  status: string; word_count: number; is_stale: boolean;
  outline_version_used: number | null; outline_current_version: number;
  characters_involved: string[];
}
export interface OverviewForeshadow {
  content: string; status: string;
  planted_chapter: number; expected_chapter: number | null; resolved_chapter: number | null;
}
export interface OverviewCharacter { name: string; retired: boolean; chapters: number[]; }
export interface OverviewOut {
  chapters: OverviewChapter[];
  foreshadowings: OverviewForeshadow[];
  characters: OverviewCharacter[];
}
export interface PolishResult {
  polished: string; locked_facts: string[]; violations: Record<string, string>[];
  flavor_before: FlavorInfo; flavor_after: FlavorInfo;
}
export interface ProviderState { deepseek: boolean; openai: boolean; gemini: boolean; }
export interface AuthResult { token: string; username: string; is_admin: boolean; }
export interface Me { id: number; username: string; is_admin: boolean; }
/** 结构化故事概念(灵感工坊产出)。六字段全可空,渐进成形。 */
export interface Concept {
  logline: string; hook: string; twist: string;
  protagonist: string; conflict: string; setting: string;
}
/** 概念字段的展示顺序与中文标签(与后端 CONCEPT_FIELDS 一致) */
export const CONCEPT_FIELDS: { key: keyof Concept; label: string; hint: string }[] = [
  { key: "logline", label: "一句话故事", hint: "主角 + 核心冲突 + 赌注" },
  { key: "hook", label: "核心钩子", hint: "读者为什么想追下去" },
  { key: "twist", label: "潜在反转", hint: "藏着的大转折方向" },
  { key: "protagonist", label: "主角", hint: "身份 / 目标 / 困境" },
  { key: "conflict", label: "核心冲突", hint: "主要对立面" },
  { key: "setting", label: "世界·背景", hint: "时代 / 场景 / 基调" },
];
export const EMPTY_CONCEPT: Concept = {
  logline: "", hook: "", twist: "", protagonist: "", conflict: "", setting: "",
};
/** 六字段是否全空 */
export function conceptIsEmpty(c: Concept | null | undefined): boolean {
  return !c || CONCEPT_FIELDS.every((f) => !(c[f.key] ?? "").trim());
}
export interface RefineResult { concept: Concept; changed: (keyof Concept)[]; note: string; }
export interface ChatTurn { role: "user" | "assistant"; content: string; }
export interface ChatResult { reply: string; concept: Concept; }
export interface AdminUser {
  id: number; username: string; is_admin: boolean; is_active: boolean;
  created_at: string; project_count: number;
  total_prompt_tokens: number; total_completion_tokens: number; total_calls: number;
}
export interface InviteCodeItem {
  id: number; code: string; note: string | null;
  max_uses: number | null; used_count: number; is_active: boolean; created_at: string;
}
export interface InviteCodeListOut {
  items: InviteCodeItem[];
  // 表为空时仍在生效的旧单码(app_settings/env);有记录后为 null
  legacy_fallback: { code: string; source: "db" | "env" } | null;
}
/** 编辑部预设优化动作 */
export interface EditorAction { key: string; label: string; directive: string; }
export interface ReviewSuggestion { evidence: string; issue: string; fix: string; }
export interface ChapterReview {
  chapter_number: number;
  scores: { plot: number; prose: number; pacing: number; character: number };
  comment: string;
  suggestions: ReviewSuggestion[];
}
export interface ProofIssue { type: string; original: string; suggestion: string; reason: string; }
export interface AuditReport {
  written_chapters: number;
  target_chapters: number;
  stale_chapters: number[];
  holes: number[];
  foreshadow: {
    total: number; open: number; resolved: number;
    overdue: { description: string; planted: number; expected: number | null; status: string }[];
  };
}

// ---------- 接口 ----------
export const api = {
  health: () => req<{ status: string; providers: ProviderState }>("GET", "/api/health"),
  // 当前用户是否配置了至少一个可用模型(全局引导横幅用)
  providerStatus: () =>
    req<{ configured: boolean; providers: Record<string, boolean> }>(
      "GET", "/api/settings/providers/status"),
  suggestTitle: (topic: string, genre: string, concept?: Concept | null) =>
    req<{ titles: string[] }>("POST", "/api/projects/title-suggestion",
      { topic, genre, concept: concept ?? null }, 60000),

  listProjects: () => req<Project[]>("GET", "/api/projects"),
  createProject: (p: Partial<Project>) => req<Project>("POST", "/api/projects", p),
  getProject: (id: number) => req<Project>("GET", `/api/projects/${id}`),
  patchProject: (id: number, patch: Partial<Project>) =>
    req<Project>("PATCH", `/api/projects/${id}`, patch),
  renameProject: (id: number, title: string) =>
    req<Project>("PATCH", `/api/projects/${id}`, { title }),
  deleteProject: (id: number) =>
    req<{ ok: boolean; deleted_chapters: number }>("DELETE", `/api/projects/${id}`),
  // 本项目正在运行的后台任务(切走再回来时重新接上轮询)
  runningJobs: (id: number) =>
    req<{ jobs: { job_id: string; kind: string; stage: string }[] }>(
      "GET", `/api/projects/${id}/running-jobs`),
  // 当前用户全部后台任务(全局任务中心;all=true 含近期已完成)
  myJobs: (all = false) =>
    req<{ jobs: { job_id: string; kind: string; status: string; stage: string; error?: string | null }[] }>(
      "GET", `/api/jobs${all ? "?all=true" : ""}`),

  // ---- 异步 job 版长任务(返回 job_id,配合 pollJob/任务中心) ----
  inspireAsync: (spark: string, tendency: Tendency, count = 4) =>
    req<{ job_id: string }>("POST", "/api/inspire/async", { spark, tendency, count }),
  refineConceptAsync: (concept: Concept, directive: string, tendency: Tendency = {}) =>
    req<{ job_id: string }>("POST", "/api/inspire/refine-async", { concept, directive, tendency }),
  polishChapterAsync: (pid: number, n: number, tendency: Tendency) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/polish/chapter/${n}/async`, { tendency }),
  polishSegmentAsync: (pid: number, text: string, tendency: Tendency) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/polish/segment-async`, { text, tendency }),
  impactAsync: (pid: number, n: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/outlines/${n}/impact-async`, {}),
  cascadeAsync: (pid: number, body: object) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/outlines/cascade-async`, body),
  synopsisAsync: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/synopsis-async`, {}),

  inspire: (spark: string, tendency: Tendency, count = 4) =>
    req<{ ideas: Concept[] }>("POST", "/api/inspire", { spark, tendency, count }, LLM_TIMEOUT),
  refineConcept: (concept: Concept, directive: string, tendency: Tendency = {}) =>
    req<RefineResult>("POST", "/api/inspire/refine", { concept, directive, tendency }, LLM_TIMEOUT),
  chatConcept: (messages: ChatTurn[], concept: Concept | null, tendency: Tendency = {}) =>
    req<ChatResult>("POST", "/api/inspire/chat", { messages, concept, tendency }, LLM_TIMEOUT),
  generateSynopsis: (id: number) =>
    req<{ synopsis: string }>("POST", `/api/projects/${id}/synopsis`, {}, LLM_TIMEOUT),
  patchArchitecture: (id: number, patch: Partial<Architecture>) =>
    req<Architecture>("PATCH", `/api/projects/${id}/architecture`, patch),

  getArchitecture: (id: number) => req<Architecture>("GET", `/api/projects/${id}/architecture`),
  generateArchitecture: (id: number, tendency: Tendency) =>
    req<Architecture>("POST", `/api/projects/${id}/architecture`, { tendency }, LLM_TIMEOUT),
  generateBlueprint: (id: number, tendency: Tendency) =>
    req<{ outlines: Outline[]; warnings: string[] }>("POST", `/api/projects/${id}/blueprint`, { tendency }, LLM_TIMEOUT),
  generateArchitectureAsync: (id: number, tendency: Tendency) =>
    req<{ job_id: string }>("POST", `/api/projects/${id}/architecture-async`, { tendency }),
  generateBlueprintAsync: (id: number, tendency: Tendency) =>
    req<{ job_id: string }>("POST", `/api/projects/${id}/blueprint-async`, { tendency }),
  listOutlines: (id: number) => req<Outline[]>("GET", `/api/projects/${id}/outlines`),

  editOutline: (pid: number, n: number, updates: Partial<Outline>) =>
    req<EditResult>("PUT", `/api/projects/${pid}/outlines/${n}`, updates, LLM_TIMEOUT),
  impact: (pid: number, n: number) =>
    req<ImpactReport>("POST", `/api/projects/${pid}/outlines/${n}/impact`, {}, LLM_TIMEOUT),
  cascade: (pid: number, source: number, chapters: number[], reasons: Record<number, string>) =>
    req<CascadeResult>("POST", `/api/projects/${pid}/outlines/cascade`,
      { source_chapter: source, chapter_numbers: chapters, reasons, tendency: {} }, LLM_TIMEOUT),

  // 修改指令:自然语言结构改 → 预览 → 应用(版本化 + 正文失配标记,不自动级联)
  parseEditDirective: (pid: number, directive: string) =>
    req<DirectivePreview>("POST", `/api/projects/${pid}/edit-directive`, { directive }, LLM_TIMEOUT),
  applyEditDirective: (pid: number, items: { chapter_number: number; new_title?: string | null; new_summary: string }[]) =>
    req<DirectiveApplyResult>("POST", `/api/projects/${pid}/edit-directive/apply`, { items }),

  listChapters: (pid: number) => req<ChapterBrief[]>("GET", `/api/projects/${pid}/chapters`),
  getChapter: (pid: number, n: number) => req<ChapterDetail>("GET", `/api/projects/${pid}/chapters/${n}`),
  generateChapter: (pid: number, n: number, tendency: Tendency) =>
    req<GenerateChapterResponse>("POST", `/api/projects/${pid}/chapters/${n}/generate`, { tendency }, LLM_TIMEOUT),
  generateChapterAsync: (pid: number, n: number, tendency: Tendency, revision = "") =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/generate-async`, { tendency, revision }),
  editChapterContent: (pid: number, n: number, final_content: string) =>
    req<ChapterDetail>("PUT", `/api/projects/${pid}/chapters/${n}/content`, { final_content }),
  reExtractAsync: (pid: number, n: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/re-extract-async`),
  listChapterVersions: (pid: number, n: number) =>
    req<ChapterVersionBrief[]>("GET", `/api/projects/${pid}/chapters/${n}/versions`),
  getChapterVersion: (pid: number, n: number, vid: number) =>
    req<ChapterVersionDetail>("GET", `/api/projects/${pid}/chapters/${n}/versions/${vid}`),
  restoreChapterVersion: (pid: number, n: number, vid: number) =>
    req<ChapterDetail>("POST", `/api/projects/${pid}/chapters/${n}/versions/${vid}/restore`),
  getJob: (jobId: string) =>
    req<{ status: string; stage: string; result: GenerateChapterResponse | null; error: string | null }>(
      "GET", `/api/jobs/${jobId}`),
  usage: () =>
    req<{ total_calls: number; total_prompt_tokens: number; total_completion_tokens: number }>(
      "GET", "/api/usage"),

  bible: (pid: number, chapter: number) =>
    req<BibleSnapshot>("GET", `/api/projects/${pid}/bible?chapter=${chapter}`),
  foreshadowings: (pid: number, current: number) =>
    req<ForeshadowOut[]>("GET", `/api/projects/${pid}/foreshadowings?current_chapter=${current}`),
  characters: (pid: number) =>
    req<CharactersOut>("GET", `/api/projects/${pid}/characters`),
  overview: (pid: number) =>
    req<OverviewOut>("GET", `/api/projects/${pid}/overview`),
  createCharacter: (pid: number, payload: { name: string; aliases?: string[]; profile?: string }) =>
    req<CharacterCard>("POST", `/api/projects/${pid}/characters`, payload),
  setCharacterRetired: (pid: number, entityId: number, retired: boolean) =>
    req<CharacterCard>("PATCH", `/api/projects/${pid}/characters/${entityId}`, { retired }),
  deleteFact: (pid: number, factId: number) =>
    req<{ ok: boolean }>("DELETE", `/api/projects/${pid}/facts/${factId}`),

  tendencyCatalog: (node: string) => req<NodeCatalog>("GET", `/api/tendency/catalog/${node}`),
  // 题材推断:概念文本 → 大类 + 最贴流派 + 同类推荐(起步流基调步预填)
  genreInfer: (text: string) =>
    req<{ category: string; category_label: string; genre: string; suggestions: { label: string; desc: string; category: string }[] }>(
      "POST", "/api/tendency/genre-infer", { text }, 120000),
  // 连写队列:多章排队串行生成
  generateQueue: (pid: number, chapter_numbers: number[], tendency: Tendency = {}) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/generate-queue`,
      { chapter_numbers, tendency }),

  // ---- 编辑部:主编评分 / 校对 / 审核报告 / 优化动作目录 ----
  editorialActions: () =>
    req<{ prose: EditorAction[]; outline: EditorAction[] }>("GET", "/api/editorial/actions"),
  reviewChapterAsync: (pid: number, n: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/review-async`, {}),
  proofreadAsync: (pid: number, n: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/proofread-async`, {}),
  proofreadApply: (pid: number, n: number, fixes: { original: string; suggestion: string }[]) =>
    req<{ applied: { original: string; suggestion: string }[]; failed: { original: string; reason: string }[]; word_count: number; final_content: string }>(
      "POST", `/api/projects/${pid}/chapters/${n}/proofread-apply`, { fixes }),
  auditReport: (pid: number) =>
    req<AuditReport>("GET", `/api/projects/${pid}/audit-report`),
  // 指令改异步解析(应用仍走同步 apply,纯 DB 快)
  parseEditDirectiveAsync: (pid: number, directive: string) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/outlines/edit-directive-async`, { directive }),
  // 伏笔手动操作:弃用/恢复/标记回收/改预期章
  patchForeshadow: (pid: number, fid: number, patch: { status?: string; expected_payoff_chapter?: number; payoff_chapter?: number; notes?: string }) =>
    req<{ id: number; status: string }>("PATCH", `/api/projects/${pid}/foreshadowings/${fid}`, patch),

  polishChapter: (pid: number, n: number, tendency: Tendency) =>
    req<PolishResult>("POST", `/api/projects/${pid}/polish/chapter/${n}`, { tendency }, LLM_TIMEOUT),
  applyPolish: (pid: number, n: number, polished_text: string) =>
    req<{ status: string }>("POST", `/api/projects/${pid}/polish/chapter/${n}/apply`, { polished_text }),
  polishSegment: (pid: number, text: string, tendency: Tendency) =>
    req<PolishResult>("POST", `/api/projects/${pid}/polish/segment`, { text, tendency }, LLM_TIMEOUT),
  polishFragment: (pid: number, n: number, fragment: string, direction: string) =>
    req<{ polished: string; notes: string | null }>(
      "POST", `/api/projects/${pid}/chapters/${n}/polish-fragment`, { fragment, direction }, LLM_TIMEOUT),
  aiFlavor: (text: string) =>
    req<FlavorInfo & { hits?: Record<string, unknown>[]; total_chars?: number }>(
      "POST", "/api/polish/ai-flavor", { text }),

  // ---------- 鉴权 ----------
  register: (username: string, password: string, invite_code: string) =>
    req<AuthResult>("POST", "/api/auth/register", { username, password, invite_code }),
  login: (username: string, password: string) =>
    req<AuthResult>("POST", "/api/auth/login", { username, password }),
  me: () => req<Me>("GET", "/api/auth/me"),

  // ---------- 后台管理(仅管理员可用) ----------
  adminListUsers: () => req<AdminUser[]>("GET", "/api/admin/users"),
  adminResetPassword: (id: number, password: string) =>
    req<{ ok: boolean }>("POST", `/api/admin/users/${id}/reset-password`, { password }),
  adminSetActive: (id: number, is_active: boolean) =>
    req<{ ok: boolean; is_active: boolean }>("PATCH", `/api/admin/users/${id}`, { is_active }),
  adminDeleteUser: (id: number) =>
    req<{ ok: boolean; deleted_projects: number }>("DELETE", `/api/admin/users/${id}`),
  adminListInviteCodes: () => req<InviteCodeListOut>("GET", "/api/admin/invite-codes"),
  adminCreateInviteCode: (code: string, note?: string, max_uses?: number | null) =>
    req<InviteCodeItem>("POST", "/api/admin/invite-codes", { code, note: note || null, max_uses: max_uses ?? null }),
  adminSetInviteCodeActive: (id: number, is_active: boolean) =>
    req<InviteCodeItem>("PATCH", `/api/admin/invite-codes/${id}`, { is_active }),
  adminDeleteInviteCode: (id: number) =>
    req<{ ok: boolean }>("DELETE", `/api/admin/invite-codes/${id}`),
};
