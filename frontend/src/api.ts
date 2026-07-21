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
  synopsis?: string | null;
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
  ai_flavor: { score: number; summary: string };
}
export interface Chip { label: string; directive: string; }
export interface Dimension { key: string; label: string; select: "single" | "multi"; chips: Chip[]; }
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
export interface PolishResult {
  polished: string; locked_facts: string[]; violations: Record<string, string>[];
  flavor_before: { score: number; summary: string }; flavor_after: { score: number; summary: string };
}
export interface ProviderState { deepseek: boolean; openai: boolean; gemini: boolean; }
export interface AuthResult { token: string; username: string; is_admin: boolean; }
export interface Me { id: number; username: string; is_admin: boolean; }
export interface Idea { title: string; logline: string; hook: string; twist: string; }
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

// ---------- 接口 ----------
export const api = {
  health: () => req<{ status: string; providers: ProviderState }>("GET", "/api/health"),
  // 当前用户是否配置了至少一个可用模型(全局引导横幅用)
  providerStatus: () =>
    req<{ configured: boolean; providers: Record<string, boolean> }>(
      "GET", "/api/settings/providers/status"),
  suggestTitle: (topic: string, genre: string) =>
    req<{ titles: string[] }>("POST", "/api/projects/title-suggestion", { topic, genre }, 60000),

  listProjects: () => req<Project[]>("GET", "/api/projects"),
  createProject: (p: Partial<Project>) => req<Project>("POST", "/api/projects", p),
  getProject: (id: number) => req<Project>("GET", `/api/projects/${id}`),
  patchProject: (id: number, patch: Partial<Project>) =>
    req<Project>("PATCH", `/api/projects/${id}`, patch),
  renameProject: (id: number, title: string) =>
    req<Project>("PATCH", `/api/projects/${id}`, { title }),
  deleteProject: (id: number) =>
    req<{ ok: boolean; deleted_chapters: number }>("DELETE", `/api/projects/${id}`),

  inspire: (spark: string, tendency: Tendency, count = 4) =>
    req<{ ideas: Idea[] }>("POST", "/api/inspire", { spark, tendency, count }, LLM_TIMEOUT),
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

  tendencyCatalog: (node: string) => req<NodeCatalog>("GET", `/api/tendency/catalog/${node}`),

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
    req<{ score: number; summary: string; hits: Record<string, number> }>("POST", "/api/polish/ai-flavor", { text }),

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
