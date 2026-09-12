// src/api.ts — 后端 API 客户端(对齐 backend/app/api/*)
//
// 传输原语(apiBase / token / 401 处置 / 超时 / 错误翻译 / req)已抽到 ./http。
// 此前 8 个工坊模块各自复刻了一份 req,行为互不一致(详见 http.ts 顶部注释);
// 本文件与各模块现在共用同一份,不再各写各的。
//
// 兼容说明:下面 re-export 的符号,既有 `from "./api"` 的导入方无需改路径
// (约 100 个文件);新代码请直接 `from "./http"`。
import { apiBase, ApiError, authHeaders, notifyUnauthorized, req, reqForm, token } from "./http";

export { apiBase, token, setUnauthorizedHandler, ApiError, postImage, imageBlobUrl, downloadFile } from "./http";

// ---------- SSE 流式(真流式打字机)----------
// EventSource 不能带 Authorization 头,故用 fetch + ReadableStream 手工解帧。
export interface SseFrame {
  event: string;
  data: unknown; // JSON 解析成功则为对象,失败保留原始字符串
}

/** SSE 帧解析器(纯函数工厂,跨 chunk 累积):喂入网络分块文本,吐出已完整的帧。
 *  帧以空行(\n\n)分隔;逐行解析 event: / data:,data 尝试 JSON.parse。
 *  抽成独立工厂便于单测(不碰 fetch);经 CRLF 代理时 \n 会变 \r\n,统一剥 \r。 */
export function createSseDecoder(): (chunk: string) => SseFrame[] {
  let buf = "";
  return (chunk: string): SseFrame[] => {
    buf += chunk.replace(/\r/g, "");
    const frames: SseFrame[] = [];
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      }
      if (!dataLines.length) continue; // 注释/心跳帧,跳过
      const text = dataLines.join("\n");
      let data: unknown = text;
      try { data = JSON.parse(text); } catch { /* 非 JSON 保留原文 */ }
      frames.push({ event, data });
    }
    return frames;
  };
}

/** 发起一条 SSE 流,逐帧回调 onFrame。鉴权/401 与 req 对齐;非 2xx(流还没开始)抛 ApiError。
 *  GET/POST 都走这里:POST 用于对话流,GET 用于订阅任务的实时正文。 */
async function sseStream(
  path: string,
  init: RequestInit,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string> | undefined) };
  const tk = token.get();
  if (tk) headers["Authorization"] = `Bearer ${tk}`;
  const res = await fetch(apiBase() + path, { ...init, headers, signal });
  if (!res.ok || !res.body) {
    if (res.status === 401) notifyUnauthorized();
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
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const frame of feed(decoder.decode(value, { stream: true }))) onFrame(frame);
  }
  for (const frame of feed(decoder.decode())) onFrame(frame); // 冲净残余尾字节
}

/** POST 一个 SSE 流(JSON body)。 */
async function ssePost(
  path: string,
  body: unknown,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  return sseStream(
    path,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    onFrame,
    signal,
  );
}

/** 跑一条「AI 对话流」:token 帧喂打字机(onToken),done 帧作为最终结果 resolve,
 *  error 帧抛 ApiError。done 缺失(流意外中断)也抛错,避免半截结果被当成功。 */
async function runDiscussStream<T>(
  path: string,
  body: unknown,
  onToken: (text: string) => void,
  signal?: AbortSignal,
): Promise<T> {
  let done: T | null = null;
  let errDetail: string | null = null;
  await ssePost(path, body, (frame) => {
    if (frame.event === "token") {
      const d = frame.data as { text?: unknown };
      if (typeof d?.text === "string") onToken(d.text);
    } else if (frame.event === "done") {
      done = frame.data as T;
    } else if (frame.event === "error") {
      const d = frame.data as { detail?: unknown };
      errDetail = typeof d?.detail === "string" ? d.detail : "对话失败,请重试";
    }
  }, signal);
  if (errDetail !== null) throw new ApiError(502, errDetail);
  if (done === null) throw new ApiError(0, "对话意外中断,请重试");
  return done;
}

// LLM 长任务统一超时:章节生成/架构生成可能 3-10 分钟
const LLM_TIMEOUT = 900_000;

// ---------- 类型 ----------
export type Tendency = Record<string, unknown>;

// 创作偏好档案:贯穿全书的创作宪法,注入生成/重写/润色所有环节
export interface StyleProfile {
  style: string;    // 文风
  taboos: string;   // 禁忌/避雷
  audience: string; // 读者定位
  other: string;    // 其他创作主张
  voice_key: string;    // 名家/预设文风胶囊 key(空=未选);去 AI 味的正向锚
  voice_sample: string; // 作者自备范文 / 从已认可章节提取的文风范本正文
}

// 名家/预设文风胶囊(去 AI 味的正向锚)。sample 一律本项目自撰仿写,非原作节选,
// 故列表接口不返回 sample——前端只按 name/directive 展示与选择。
export interface VoiceCapsule {
  key: string;
  name: string;
  directive: string;
  category: "web" | "literary" | "neutral"; // 网文类型 / 文学名家 / 通用笔法
}

// 写作手法卡:本书自己的手法库,勾选启用即拼成一块注入生成/润色/重写
export interface WritingCard {
  id: number;
  title: string;
  body: string;
  enabled: boolean;
  sort: number;
}

export interface Project {
  id: number; title: string; topic: string; genre: string;
  target_chapters: number; target_words_per_chapter: number;
  // 开放式连载(结局未定):架构只定长线引擎+首批方向;蓝图铺满后「展开下一卷」自动续订
  open_ended?: boolean;
  // 字数守卫:超标自动压缩/拆章,默认关闭(写作页开关控制)
  word_guard_enabled?: boolean;
  auto_split_enabled?: boolean;
  // 编辑部审校把关(生成时):定稿后自动校对+主审打分,不达标带意见有上限回炉
  review_pass_threshold?: number;
  review_auto_revise?: boolean;
  review_max_revisions?: number;
  // 场景级生成:True=把「章」降级为容器、逐场生成+逐场验收(不合格只重写该场);
  // False(默认)= 一次调用写整章。默认关,开启为实测手感(见 docs/15)。
  scene_level_enabled?: boolean;
  // 连写前置:True=严格模式(上一章 approved 才能连写下一章,遇待审章队列暂停)
  queue_require_approved?: boolean;
  // 完本标记:True=已完本。完本后重命名/删除/清空为置灰与后端拦截态,防误删误改。
  finished?: boolean;
  global_tendency: Tendency; status: string;
  concept?: Concept | null;
  // 故事 DNA / 本书基因(坐标卡产出):定味道锚,治题材/口味漂移;驱动生成注入 + 题材硬门
  dna?: StoryDNA | null;
  synopsis?: string | null;
  // 起步流:非空 = 创建未完成,值为停留步骤(idea/tone/title/scale/launch)
  setup_state?: string | null;
  // 灵感对话记录(对话式捏概念的持久化)
  chat_log?: ChatTurn[] | null;
  // 列表页进度聚合(仅 GET /projects 填充)
  written_chapters?: number;
  total_words?: number;
  // 架构状态(只读派生):是否已有架构 / 架构是否仍挂在旧概念上(建议重新生成)
  has_architecture?: boolean;
  architecture_stale?: boolean;
  // 架构已重写、但大纲仍挂在旧架构上(True=建议重铺蓝图或清空重来)
  outline_stale?: boolean;
  // 卷纲(滚动规划指南针,长书才有)
  macro_plan?: { start: number; end: number; goal: string }[] | null;
  // 文风备忘(随书累积的文风基线;翻新面板可手动编辑,后续生成以此为底继续累积)
  style_memo?: string | null;
  // 世界观硬规则钉板:每行一条不可违背的设定/常识,注入后续所有生成;规则扫描以此体检正文
  world_rules?: string | null;
  // 出片模式:lite=轻量档(文+图出片,逐镜筛选)/ full=完整档(对白链/自动接力/一键合成,分期点亮)
  render_mode?: "lite" | "full" | string;
  // 故事宪法(结构化):书级恒真声明——刻意留白 / 常驻装置+复现节奏 / 倒计时;
  // 与 world_rules 在后端合并成一张「宪法块」,全程注入生成端 + 全程门禁比对(治长程一致性 #1/#2)
  canon?: StoryCanon | null;
}
export interface Architecture {
  core_seed: string; character_dynamics: string;
  world_building: string; plot_architecture: string; version: number;
  // 概念变更后置 true(架构仍挂在旧概念上);重新生成架构后复位 false
  concept_stale?: boolean;
}
export interface Outline {
  id: number; chapter_number: number; title: string; chapter_role: string;
  chapter_purpose: string; suspense_level: string; foreshadowing: string;
  // 情绪基调:蓝图层定的本章底色(压抑/紧绷/荒诞/温热…),正文照此定调
  emotional_tone: string;
  plot_twist_level: string; summary: string; characters_involved: string[] | null;
  key_items: unknown[]; scene_location: string; current_version: number;
  // 本章戏核:这一章必须让读者记住的那一个瞬间,全章围着它铺
  scene_anchor: string;
  beats?: string[] | null;
}
/** 全书张力总线的节奏体检:用来判断"整本书是不是一条平线" */
export interface TensionReport {
  span: number;          // 极差(峰值 - 谷值),小于 2 就该警惕
  mean: number;          // 均值
  flat: boolean;         // True = 平线,全书节奏最致命的病
  longest_run: number;   // 最长同值连续段(连续 N 章一个温度)
  peak_at: number;       // 峰值落在第几章
  chapters: number;
}
/**
 * 全书张力曲线:每章目标张力 1-5。
 * 1=蓄力压制 2=暗流收紧 3=常规推进 4=情绪高点 5=全力爆发。
 * 章内分场以它为基准做起伏,正文生成时把"这一章在全书的坐标"注入 prompt。
 */
export interface TensionCurve {
  chapters: number[];
  tension: number[];
  roles: string[];
  report: TensionReport;
}
// ---------- 场景(§2.6 可干预性) ----------
// 场景卡 = 生成单元。改卡比改正文划算:卡是 prompt 的输入,卡准了正文才对。
export interface SceneCard {
  id: number;
  chapter_number: number;
  seq: number;
  title: string;
  summary: string;
  location: string;
  characters: string[];
  goal: string;
  conflict: string;
  emotion_target: string;
  tension_level: number;     // 1-5
  target_words: number;
  fact_hints: string[];
  status: string;            // planned | drafting | drafted | accepted | rejected | discarded
  word_count: number;
  rewrite_count: number;
  version: number;
  accept_note: string;
  accept_scores: Record<string, unknown>;
  anchor_start: number;
  anchor_end: number;
  joins_previous: boolean;
  content?: string;          // 仅详情端点返回;列表端点不带(看板只要卡片)
}
// PATCH 只接受可编辑的场景卡字段(tension_level 1-5,后端校验)
export interface SceneCardPatch {
  title: string;
  summary: string;
  location: string;
  characters: string[];
  goal: string;
  conflict: string;
  emotion_target: string;
  tension_level: number;
  target_words: number;
  fact_hints: string[];
}
export interface SceneList {
  chapter_number: number;
  scene_count: number;
  scenes: SceneCard[];
}
export interface ChapterBrief {
  chapter_number: number; status: string; word_count: number; is_stale: boolean;
}
/** 章节反馈(docs/17 M2):差评四桶 style_flavor/fact_error/pacing/format_trunc */
export interface ChapterFeedbackOut {
  rating: "good" | "bad";
  categories: string[];
  comment: string;
  stale: boolean;
  created_at: string;
  updated_at: string;
}
export interface ChapterDetail extends ChapterBrief {
  draft_content: string; final_content: string; outline_version_used: number;
  // 章末交接契约(docs/08 §5.2):approve 等接口回显;none=从未提取 / ok / failed
  handoff_contract?: Record<string, unknown> | null;
  handoff_extract_status?: string;
  handoff_extract_error?: string;
}
/** 写前审核警告(docs/08 §5.3):severity 一律 major,只警告不阻断 */
export interface PreflightWarning {
  severity: string;           // "major"
  type: string;               // state | timeline
  description: string;
  evidence: string;
  conflicting_fact?: string;
  suggestion: string;
}
/** 章节一致性问题记录(docs/08 §5.7):门禁/预审/诊断/审校/规则扫描/宪法建议/时间线/常驻装置产出,可操作流转 */
export interface ChapterIssue {
  id: number;
  source: string;             // gate | preflight | diag | review | rules | canon | clock | devices
  severity: string;           // blocker | major | minor
  issue_type: string;         // source=canon 时为 absence | device | deadline;source=clock 时为 timeline;source=devices 时为 worldrule
  description: string;
  evidence: string;
  suggestion: string;
  status: string;             // open | resolved | ignored
  created_at?: string;
  // 仅 source=canon 的建议带结构化载荷 {kind, ...},供「采纳进宪法」;余者 null
  payload?: Record<string, unknown> | null;
}
export interface GenerateChapterResponse extends ChapterDetail {
  consistency_issues: Record<string, string>[];
  extraction_stats: Record<string, unknown>;
  ai_flavor: FlavorInfo | null;
  word_guard_action?: "none" | "compressed" | "split";
  split_info?: {
    original_chapter: number;
    new_chapter: number;
    new_title: string;
    part_a_words: number;
    part_b_words: number;
    total_chapters_now: number;
    reason: string;
  };
  // 生成时编辑部审校把关结果(校对+主审+有上限回炉;分级回炉含门禁定点修复)
  review?: {
    // 五维评分;continuity(连续性)为新维度,旧四维快照无该键(渲染时兼容)
    scores: { plot: number; prose: number; pacing: number; character: number; continuity?: number };
    comment: string;
    suggestions: ReviewSuggestion[];
    passed: boolean;
    revision_rounds: number;
    threshold: number;
    // 定点修复轮数与明细(旧响应无该键,渲染时兼容)
    repair_rounds?: number;
    repairs?: GateRepairDetail;
    // 锚点化评分依据 / 回炉轨迹 / 止损与死锁提示(旧响应无该键)
    score_reasons?: Record<string, string>;
    rework_log?: {
      round: number;
      trigger: string;
      failing?: string[];
      stalled?: string[];
      blockers?: string[];
      note?: string;
    }[];
    gate_note?: string;
    stall_note?: string;
    hints?: string[];
    // AI 味自愈(P4):定稿超标时自动定向去味重写的分数变化;采纳了重写才有
    deai?: { before: number; after: number };
  };
  // 一致性门禁结果(docs/08 §5.4):quarantined 时正文已存但未进圣经/摘要
  gate?: { status: "passed" | "quarantined"; blockers: PreflightWarning[] };
  // 写前审核警告(docs/08 §5.3):只警告不阻断
  preflight?: { warnings: PreflightWarning[] };
}
/** 连写队列任务结果。正常完成 error=null;中断(欠费 402/门禁拦截/严格模式暂停)
 *  时 error 带原因、remaining 是待续跑章号——异常中断含失败章本尊(重跑即续),
 *  门禁拦截不含(该章须先去写作页处理,续跑从下一章起)。 */
export interface GenerateQueueResult {
  completed: { chapter_number: number; word_count: number }[];
  total: number;
  stopped_at: number | null;
  remaining: number[];
  quarantined: boolean;
  error: string | null;
}
/** 章节正文历史版本(覆盖前的快照)。source: generated/polished/edited/restored/spot_repair */
export interface ChapterVersionBrief {
  id: number; version: number; source: string; word_count: number; created_at: string;
}
export interface ChapterVersionDetail extends ChapterVersionBrief {
  final_content: string; draft_content: string;
}
/** 版本来源的中文说明 */
export const VERSION_SOURCE_CN: Record<string, string> = {
  generated: "重写前", polished: "润色前", edited: "编辑前", restored: "回滚前",
  spot_repair: "定点修复前", deai: "去味前",
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
// 批量重拟标题(一键换一批,不动剧情):预览项 old→new,应用后返回更新的章号+大纲
export interface RetitleItem { chapter_number: number; old_title: string; new_title: string; }
export interface RetitleAllResult { items: RetitleItem[]; }
export interface ApplyRetitleResult { updated: number[]; outlines: Outline[]; }
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
// 桥段台账(跨章重复描写追踪)+ 雷区清单(作者明令禁止的桥段)
export interface BannedMotif { id: number; label: string; detail: string; }
export interface LedgerMotif { label: string; detail: string; chapters: number[]; count: number; }
export interface MotifsOut { banned: BannedMotif[]; ledger: LedgerMotif[]; }
export interface CharacterFact {
  id: number; fact_type: string; content: string;
  valid_from: number; valid_until: number | null; importance: string;
}
export interface RelationEvidence { chapter: number; content: string; }
export interface CharacterRelation {
  other_name: string; description: string; valid_from: number; other_retired: boolean;
  evidence: RelationEvidence[]; // 支撑这条边的原文事实(带章节引用),新→旧最多 3 条
}
export interface CharacterCard {
  id: number; name: string; aliases: string[]; entity_type: string; retired: boolean;
  profile: string; key_facts: CharacterFact[]; appearance_chapters: number[];
  relations: CharacterRelation[];
}
export interface CharactersOut { characters: CharacterCard[]; other_entities_count: number; }
/** 方案轮廓推荐:阅读手感(tone/elements 标签)+ 篇幅档位,附一句话依据 */
export interface ShapeSuggestion {
  tone: string[];
  elements: string[];
  scale: "short" | "mid" | "long" | "serial";
  tone_reason: string;
  scale_reason: string;
}
/** 核心梗卡(docs/19):高概念/兑现机制/边界禁忌/钩子计划——一本书的「纲」 */
export interface Premise {
  high_concept: string;
  payoff: string;
  beats: string[];
  boundaries: string[];
  hook_plan: { opening?: string; mid?: string; climax?: string };
  source: "ai" | "human";
}
export interface PremiseInput {
  high_concept: string;
  payoff: string;
  beats: string[];
  boundaries: string[];
  hook_plan: { opening?: string; mid?: string; climax?: string };
}
/** 本章作战图(写前一屏):纯确定性投影,缺数据如实缺省 */
export interface DossierRelation { from_name: string; to_name: string; relation: string; }
export interface DossierCharacter {
  name: string; entity_id: number | null; matched: boolean;
  relations: DossierRelation[];
}
export interface DossierForeshadowItem {
  id: number; description: string; status: string; expected_payoff_chapter: number | null;
}
export interface DossierScene {
  seq: number; title: string; location: string; emotion_target: string;
  tension_level: number; status: string;
}
export interface ChapterDossier {
  chapter_number: number;
  outline: {
    chapter_number: number; title: string; chapter_role: string; chapter_purpose: string;
    suspense_level: string; emotional_tone: string; foreshadowing: string; summary: string;
    scene_anchor: string; premise_beat: string;
    characters_involved: string[]; beats: string[];
  };
  premise: Premise | null;
  scenes: DossierScene[];
  characters: DossierCharacter[];
  foreshadows: {
    planted: DossierForeshadowItem[]; paid_off: DossierForeshadowItem[];
    reinforced: DossierForeshadowItem[]; overdue: DossierForeshadowItem[];
  };
  prev_threads: string[];
  prev_location: string;
}
export interface FactSpan {
  content: string; fact_type: string; importance: string;
  valid_from: number; valid_until: number | null;
}
export interface FactTrack {
  entity_id: number; name: string; retired: boolean; facts: FactSpan[];
}
export interface FactsTimelineOut {
  tracks: FactTrack[]; other_entities_count: number; max_chapter: number;
}
// 全书概览(看板「概览」页签):一次聚合章节状态/版本对照、伏笔区间、人物出场
export interface OverviewChapter {
  chapter_number: number; title: string; chapter_role: string;
  status: string; word_count: number; is_stale: boolean;
  outline_version_used: number | null; outline_current_version: number;
  characters_involved: string[];
  review_scores?: Record<string, number>;
  ai_flavor_score?: number | null;
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
/** 成书体检报告(docs/15 §7.3):把散在各页签的质量信号收成一份报告,只读零 LLM。
 *  缺数据的块留空并附 notes 口径说明——不填 0 冒充「没问题」 */
export interface HealthFlavorPoint { chapter: number; score: number; chars: number; }
export interface HealthTensionPoint {
  chapter: number; scenes: number; mean: number; peak: number; swing: number;
}
export interface HealthOverdue {
  content: string; planted: number; expected: number; importance: string;
}
export interface HealthReportOut {
  project_id: number; title: string;
  chapters_planned: number; chapters_written: number;
  total_words: number; avg_chapter_words: number; completion: number;
  flavor_curve: HealthFlavorPoint[]; mean_flavor: number | null;
  worst_flavor: HealthFlavorPoint[];
  tension_curve: HealthTensionPoint[]; tension_flat_chapters: number[];
  open_issues: number; issues_by_type: Record<string, number>; issue_chapters: number[];
  foreshadow_total: number; foreshadow_by_status: Record<string, number>;
  overdue: HealthOverdue[]; debt_ratio: number;
  prompt_tokens: number; completion_tokens: number; tokens_per_chapter: number;
  notes: string[]; markdown: string;
}
/** 剧情时间线一格:该章章末契约聚合(零 LLM);无契约的章断档不显示 */
export interface TimelineItem {
  chapter: number; in_story_time: string | null; location: string | null;
  scene_continues: boolean; time_jump_hint: string;
}
export interface PolishResult {
  polished: string; locked_facts: string[]; violations: Record<string, string>[];
  flavor_before: FlavorInfo; flavor_after: FlavorInfo;
}
/** 选区 craft 微工具结果:describe/expand 走 rewrite(diff 替换),brainstorm 走 ideas(点子列表) */
export type CraftMode = "describe" | "expand" | "brainstorm";
export interface CraftResult {
  mode: CraftMode;
  rewrite: string | null;
  ideas: string[] | null;
  notes: string | null;
}
/** ②档多处批注改(revise-annotated-async job)返回的逐段旧/新对:ok=false 时 new 空、notes 载失败原因 */
export interface RevisePair {
  para_idx: number; old: string; new: string; notes: string | null; ok: boolean;
}
// 设定级级联:规则变更 / 扫描结果 / 定点修提案(与 MarksReviseResult 同构,复用验收卡)
export interface SettingChange {
  kind: "changed" | "added" | "removed"; old: string; new: string;
  entity?: string;  // 人物卡级联:变更所属人物名
}
export interface SettingPassage {
  chapter_number: number; para_idx: number; para_excerpt: string;
  quote: string; reason: string;
}
export interface SettingScanResult {
  changes: SettingChange[];  // 后端权威 diff 的回显:生成提案时原样传回
  screened: number;
  affected_chapters: { chapter_number: number; title: string; reason: string }[];
  passages: SettingPassage[];
  unlocated: number;
  failed: number[];
}
export interface SettingPatchResult {
  total: number; stale: number;
  chapters: { chapter_number: number; pairs: RevisePair[] }[];
}
// 跨章标记(作者在正文里随手记的「这里不行」,落库持久):para_idx + snapshot 判失效
export interface ChapterMark {
  id: number; chapter_number: number; para_idx: number; snapshot: string; note: string;
}
// 全书批修结果:逐章分组的待验收替换对(mark_id 用于验收接受后销账)
export interface MarkRevisePair extends RevisePair { mark_id: number; }
export interface MarksReviseResult {
  total: number; stale: number;
  chapters: { chapter_number: number; pairs: MarkRevisePair[] }[];
}
// 全书全文检索(后端 FTS5 trigram 倒排;≥3 字 MATCH,<3 字降级 LIKE)
export type SearchKind = "chapter" | "outline" | "entity" | "fact" | "foreshadowing";
export interface SearchHit {
  kind: SearchKind; kind_cn: string; ref_id: number;
  // 章节类命中=章号(正文/大纲/事实/伏笔),实体类为 null
  chapter_number: number | null;
  title: string | null;   // 大纲命中:章标题
  name: string | null;    // 实体命中:名称
  snippet: string;        // 就地裁剪的上下文摘要
  hits: number;           // 该条内容里查询词出现次数
}
export interface SearchResponse {
  q: string; total: number; elapsed_ms: number;
  grouped: Partial<Record<SearchKind, SearchHit[]>>;
}

// 各协议是否已配置可用 key。键为 interface_format(openai-compatible / anthropic /
// gemini / deepseek / openai…),随后端 _REGISTRY 动态扩展,故用 Record 不写死字段。
export type ProviderState = Record<string, boolean>;
// 模型设置(cc-switch 风格):每用户多套命名配置,回显 key 打码与协议默认值
/** /api/usage:token 用量 + 折算金额 + 峰时提示 */
export interface UsageSummary {
  total_calls: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  /** 累计估算花费(¥,按官方底价上界);null = 没有任何模型有牌价 */
  total_estimated_cost: number | null;
  /** 金额口径说明(缓存命中/峰时浮动) */
  cost_note: string | null;
  /** 没有可靠牌价、不折算金额的模型 */
  unpriced_models: string[];
  peak_hours: { is_peak: boolean; windows: string; note: string };
  by_model: Array<{
    model: string;
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    estimated_cost: number | null; // null = 无牌价,只算 token
    price_note: string | null;
  }>;
}

/** /api/settings/providers/{id}/balance:上游账户实时余额 */
export interface ProviderBalance {
  supported: boolean;
  currency: string;
  total_balance: string | null;
  granted_balance: string | null;
  topped_up_balance: string | null;
  is_available: boolean | null;
  reason: string;
}

export interface ProviderConfigOut {  id: number;
  name: string;
  interface_format: string; // openai-compatible | anthropic | gemini | deepseek | openai
  api_key_masked: string;
  has_key: boolean;
  base_url: string;
  model: string;
  timeout: number;      // 0 = 跟随全局
  max_tokens: number;   // 0 = 跟随全局/任务默认
  max_concurrency: number; // 并发上限,0=不限(按「渠道+模型」全站共享计数)
  rpm: number;             // 每分钟请求数上限,0=不限
  thinking_mode: string; // "" = 跟随全局默认(关思考);low/high/max = 按配置强制
  is_default: boolean;
  is_default_fast: boolean;
  is_default_review: boolean; // 审校档:主审评分/一致性门禁/定点修复用(未设时跟随默认)
  default_base_url: string;
  default_model: string;
  cloudflare: boolean; // base_url 套了 CF CDN,国内直连常见间歇性失败(黄条提醒用)
}

/** 模型角色分配现状(D7):创作走贵模型、校验/抽取走便宜模型,这件事要可见 */
export interface ModelRoleBrief {
  tier: string;           // quality | review | fast
  config_id: number | null;
  name: string;
  model: string;
}
export interface ModelRoles {
  available: boolean;
  reason?: string;        // available=false 时的原因(配置读不到)
  writer: ModelRoleBrief;   // 创作刀口:草稿/定稿/逐场生成
  auditor: ModelRoleBrief;  // 判定刀口:主审/一致性/场景验收
  worker: ModelRoleBrief;   // 廉价杂活:摘要/抽取/去味
  auditor_separated: boolean; // 审校是否真的与创作分模型
  self_review: boolean;       // True = 模型在给自己的输出打分
  advice: string;             // 未分离时的可操作提示
}
// 渠道体检的一项:连通 ≠ 能干活。warning=true 只提醒,不参与「是否适配」判定。
export interface ProviderProbeCheck {
  name: string;      // 中文输出 / JSON 结构 / 响应速度
  passed: boolean;
  detail: string;    // 失败原因(如「中文字符占比仅 0%——回复不像中文」)
  sample: string;    // 模型原样回复的前 80 字,便于一眼看出问题
  warning: boolean;
}
// 新增/更新配置:api_key 留空/不传 = 不改动已存 key(仅更新);
// is_default/is_default_fast/is_default_review 传 true 时后端会清掉该用户其他配置的同名标记(全用户唯一)
export interface ProviderConfigIn {
  name?: string;
  interface_format?: string;
  api_key?: string | null;
  base_url?: string;
  model?: string;
  timeout?: number;
  max_tokens?: number;
  thinking_mode?: string;
  is_default?: boolean | null;
  is_default_fast?: boolean | null;
  is_default_review?: boolean | null;
}
export interface AuthResult { token: string; username: string; is_admin: boolean; }
export interface Me { id: number; username: string; is_admin: boolean; }
/** 结构化故事概念(灵感工坊产出)。七字段全可空,渐进成形。 */
export interface Concept {
  logline: string; hook: string; twist: string;
  protagonist: string; conflict: string; setting: string;
  sell: string;
}
/** 概念字段的展示顺序与中文标签(与后端 CONCEPT_FIELDS 一致) */
export const CONCEPT_FIELDS: { key: keyof Concept; label: string; hint: string }[] = [
  { key: "logline", label: "一句话故事", hint: "主角 + 核心冲突 + 赌注" },
  { key: "hook", label: "核心钩子", hint: "读者为什么想追下去" },
  { key: "twist", label: "潜在反转", hint: "藏着的大转折方向" },
  { key: "protagonist", label: "主角", hint: "身份 / 目标 / 困境" },
  { key: "conflict", label: "核心冲突", hint: "主要对立面" },
  { key: "setting", label: "世界·背景", hint: "时代 / 场景 / 基调" },
  { key: "sell", label: "一句话卖点", hint: "勾起读者点击欲的安利句" },
];
export const EMPTY_CONCEPT: Concept = {
  logline: "", hook: "", twist: "", protagonist: "", conflict: "", setting: "",
  sell: "",
};
/** 七字段是否全空 */
export function conceptIsEmpty(c: Concept | null | undefined): boolean {
  return !c || CONCEPT_FIELDS.every((f) => !(c[f.key] ?? "").trim());
}
export interface RefineResult { concept: Concept; changed: (keyof Concept)[]; note: string; }
export interface ChatTurn { role: "user" | "assistant"; content: string; }
export interface ChatResult { reply: string; concept: Concept; }
/** 两段式构思·第一段产物:一张故事引擎卡(一句话内核 + 差异坐标 + 抓人点) */
export interface EngineCard {
  engine: string;
  angle: string;
  hook: string;
}

// ---------- 故事 DNA / 本书基因(创作坐标) ----------
/** 概念之上的「定味道」锚:治「选了青春校园却生成觉醒异能」的题材漂移。全字段可空,渐进捏成。
 *  与后端 app/schemas/dna.py 对齐;驱动生成强位注入 + 双向治漂门(越线自动毙+重生)。 */
export interface StoryDNA {
  comps: string;                 // 参照系:像《X》/《X》遇上《Y》(只指路,不搬内容)
  mode: string;                  // 题材模式:"" 未定 | realistic 现实向 | fantasy 幻想向 | mixed 混合向
  axes: Record<string, string>;  // 味道轴:轴 key → 位置标签(如 pace:"慢")
  must: string[];                // 必须有的看点(兼作硬门 opt_in:明确要=不算越界)
  must_not: string[];            // 绝不能有的元素(禁忌,喂给硬门)
  vibe: string;                  // 自备 vibe 范本(只描述味道,非原作节选)
  taste_key: string;             // 选中的味道锚胶囊 key(见后端 dna_capsules)
  pattern_key: string;           // 选中的故事骨架 key(见后端 story_patterns:情节结构配方)
  pattern_custom: string;        // 自定义骨架配方(用户手写或 AI 反推;pattern_key 为空时生效)
  capsule: string;               // 蒸馏出的『本书基因』整块文本
}
export const EMPTY_DNA: StoryDNA = {
  comps: "", mode: "", axes: {}, must: [], must_not: [], vibe: "", taste_key: "", pattern_key: "", pattern_custom: "", capsule: "",
};
/** DNA 是否所有维度都没表态(与后端 StoryDNA.is_empty 同口径) */
export function dnaIsEmpty(d: StoryDNA | null | undefined): boolean {
  if (!d) return true;
  return !(
    d.comps?.trim() || d.mode?.trim() || d.vibe?.trim() || d.taste_key?.trim() || d.pattern_key?.trim() || d.pattern_custom?.trim() || d.capsule?.trim() ||
    Object.values(d.axes || {}).some((v) => (v ?? "").trim()) ||
    (d.must || []).some((x) => (x ?? "").trim()) ||
    (d.must_not || []).some((x) => (x ?? "").trim())
  );
}
/** 味道锚胶囊选项(GET /inspire/dna/options 的 capsules 项;不含 sample 正文) */
export interface DnaCapsuleChoice {
  key: string; name: string; comps_hint: string; mode: string;
  directive: string; axes: Record<string, string>;
}
/** 故事骨架选项(GET /inspire/dna/options 的 patterns 项;不含 opener 正文) */
export interface PatternChoice {
  key: string; name: string; comps_hint: string; formula: string; rhythm: string;
}
/** AI 反推的故事骨架配方(POST /inspire/dna/pattern-derive) */
export interface PatternDerived {
  name: string; formula: string; rhythm: string; beats: string[]; opener: string;
  custom: string; // 收敛后的配方整块文本,直接存 DNA.pattern_custom
}
/** 坐标卡静态选项:味道锚胶囊 / 故事骨架 / 题材模式 / 味道轴 / 各模式会拦的套路(与硬门同口径) */
export interface DnaOptions {
  capsules: DnaCapsuleChoice[];
  patterns: PatternChoice[];
  modes: { key: string; label: string }[];
  axes: { key: string; label: string; left: string; right: string }[];
  forbidden_by_mode: Record<string, string[]>;
}
/** 品味镜:把坐标卡蒸馏成一段人话 + 矛盾检测 + 该模式会拦的套路(生成前先照镜子) */
export interface MirrorResult {
  basis: string; reflection: string; contradictions: string[]; forbidden: string[];
}

// ---------- 故事宪法 / Canon(书级恒真声明,治长程一致性 #1/#2) ----------
/** 常驻装置/金手指:立了就长期有效、该反复现身(如系统/信物)。与后端 CanonDevice 对齐。 */
export interface CanonDevice {
  name: string;        // 装置名(空则视为无效条目,保存时剔除)
  cadence: string;     // 复现节奏(如「每章都应有存在感」「关键抉择必登场」)
  importance: string;  // critical | major | minor
}
/** 倒计时:全书权威天数轴的锚(如「任务倒计时 31 天」)。与后端 CanonDeadline 对齐。 */
export interface CanonDeadline {
  name: string;           // 倒计时名(空则视为未设,保存时置 null)
  total_days: number;     // 总天数
  anchor_chapter: number; // 自第几章起算
  importance: string;     // critical | major | minor
}
/** 故事宪法:刻意留白 / 常驻装置 / 倒计时。与后端 app/schemas/canon.py 的 StoryCanon 对齐。 */
export interface StoryCanon {
  absences: string[];              // 刻意留白:这些「没有」是硬设定(如「大院只有三人、无仆役」)
  devices: CanonDevice[];
  deadline: CanonDeadline | null;
}
export const IMPORTANCE_OPTIONS: { key: string; label: string }[] = [
  { key: "critical", label: "关键" },
  { key: "major", label: "重要" },
  { key: "minor", label: "次要" },
];
export const EMPTY_CANON: StoryCanon = { absences: [], devices: [], deadline: null };
/** 宪法是否所有维度都为空(与后端 StoryCanon.is_empty 同口径) */
export function canonIsEmpty(c: StoryCanon | null | undefined): boolean {
  if (!c) return true;
  return !(
    (c.absences || []).some((a) => (a ?? "").trim()) ||
    (c.devices || []).some((d) => (d?.name ?? "").trim()) ||
    (c.deadline?.name ?? "").trim()
  );
}
/** 投稿包:对齐知乎等平台投稿表单字段(标题/频道/时空/标签/金句/简介/封面提示词) */
export interface SubmissionPackage {
  titles: string[];
  channel: string;
  era: string;
  tags: string[];
  hooks: string[];
  summaries: { short: string; medium: string; long: string };
  cover_prompts: string[];
}

// 封面提示词:一套方案含中文/英文提示词 + 负面词,拿去即梦/MJ 生成
export interface CoverPrompt {
  style: string;
  prompt_cn: string;
  prompt_en: string;
  negative: string;
}
export interface CoverPackage { covers: CoverPrompt[]; }

// 主题曲提示词(Suno):英文风格标签 + 中文对照 + 结构化中文歌词
export interface AnthemPackage {
  song_title: string;
  music_desc: string;
  style_tags: string;
  style_cn: string;
  lyrics: string;
  vibe: string;
}
export interface AdminUser {
  id: number; username: string; is_admin: boolean; is_active: boolean;  created_at: string; project_count: number;
  total_prompt_tokens: number; total_completion_tokens: number; total_calls: number;
}
/** 功能使用统计(admin/usage):各功能线的 使用人数/动作次数/最后使用时间 */
export interface FeatureUsageStat {
  feature: string;
  users: number;
  uses: number;
  last_used_at: string | null;
}
export interface QualityOverview {
  days: number;
  llm: {
    total_calls: number; truncated_calls: number; truncated_ratio: number;
    finish_length_calls: number; finish_length_ratio: number;
    prompt_tokens: number; completion_tokens: number;
    by_model: { model: string; calls: number; truncated: number; truncated_ratio: number }[];
  };
  rework: {
    chapters_reviewed: number; passed: number; pass_ratio: number;
    avg_revision_rounds: number; gate_degraded_count: number;
    stalled_hint_chapters: number; trigger_counts: Record<string, number>;
  };
  issues: { open_count: number; by_type: Record<string, number>; by_severity: Record<string, number> };
  volume: { chapters: number; avg_word_count: number };
  feedback: {
    total: number; good: number; bad: number; bad_ratio: number;
    by_category: Record<string, number>;
    cross: {
      bad_chapters: { count: number; degraded_ratio: number };
      baseline: { degraded_ratio: number };
    };
  };
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
/** AI 味检测热更配置:规则类别目录 + 自愈门槛(管理端在线调参) */
export interface AiFlavorConfig {
  gate_score: number;
  weights: Record<string, number>;
  categories: { category: string; default_weight: number; weight: number; rules: number }[];
}
/** 编辑部预设优化动作 */
export interface EditorAction { key: string; label: string; directive: string; }
export interface ReviewSuggestion { evidence: string; issue: string; fix: string; }
/** 门禁定点修复明细(分级回炉):applied=已应用的替换对;failed=未应用及原因 */
export interface GateRepairDetail {
  applied: { original: string; replacement: string }[];
  failed: { original: string; reason: string }[];
}
/** 定点修复 job 结果:ok=false 时正文未动(锚失配或复查仍有硬矛盾),reason 说明原因 */
export interface SpotRepairResult {
  ok: boolean;
  reason?: string;
  applied?: GateRepairDetail["applied"];
  failed?: GateRepairDetail["failed"];
  recheck?: Record<string, string>[];
  word_count?: number;
  status?: string;
  final_content?: string;
}
export interface ChapterReview {
  chapter_number: number;
  // 四维+continuity(连续性,新维度;旧快照无该键,Object.entries 遍历天然兼容)
  scores: { plot: number; prose: number; pacing: number; character: number; continuity?: number };
  comment: string;
  suggestions: ReviewSuggestion[];
  // 后端按项目阈值硬判是否达标(四维均需 >= threshold)
  passed?: boolean;
  threshold?: number;
  // 审校快照元信息:来源(generation=生成时审校 / manual=手动主审)、时间、
  // 回炉轮数、生成时校对自动修复的硬伤数(回显时展示)
  source?: "generation" | "manual";
  reviewed_at?: string;
  revision_rounds?: number;
  proofread_fixed?: number;
  // 分级回炉:门禁定点修复轮数与明细(回炉轮数含修复轮;旧快照无该键)
  repair_rounds?: number;
  repairs?: GateRepairDetail;
  // 锚点化评分:每维一句话依据(旧快照无该键)
  score_reasons?: Record<string, string>;
  // 逐轮回炉原因(门禁/主审触发,检查意见是否稳定一目了然)
  rework_log?: {
    round: number;
    trigger: string;
    failing?: string[];
    stalled?: string[];
    blockers?: string[];
    note?: string;
  }[];
  // 止损与死锁提示:疑似误报的 blocker / 连续无改善的维度
  gate_note?: string;
  stall_note?: string;
  hints?: string[];
}
export interface ProofIssue { type: string; original: string; suggestion: string; reason: string; }
// 校对快照回显:issues=问题清单;source=generation(生成时已自动修复,只读)/
// manual(手动待修);fixed=已修复数;proofread_at=时间。正文改动后后端返回 null。
export interface ProofreadSnapshot {
  issues: ProofIssue[];
  fixed: number;
  source: "generation" | "manual";
  proofread_at?: string;
}
export interface AuditReport {
  written_chapters: number;
  target_chapters: number;
  stale_chapters: number[];
  holes: number[];
  // 缺有效契约的已成文章(老书):引导「批量补提契约」
  contracts_missing?: number[];
  foreshadow: {
    total: number; open: number; resolved: number;
    overdue: { description: string; planted: number; expected: number | null; status: string }[];
    // 伏笔债务(§1.3):模型爱埋伏笔不爱收,这里给可量化的账
    debt?: {
      active: number;        // 当前未回收
      overdue: number;       // 已过预期回收章
      serious: number;       // 逾期超过 5 章(严重拖欠)
      avg_hanging: number;   // 平均悬空章数
      longest_hanging: number;
      cap: number;           // 按体量算的活跃数上限
      over_capacity: boolean;
    };
  };
  // 读者认知(§1.4):披露节奏 + 压着的底牌。管「多久没给读者新东西了」。
  reader?: {
    disclosed_total: number;
    per_chapter: Record<string, number>;
    dry_runs: { start: number; length: number }[];
    bursts: { chapter: number; count: number }[];
    held_cards: number;       // 压着没翻的底牌数(未披露关键事实 + 信息差)
    notes: string;            // 节奏提示(憋太久 / 一次爆太多),无问题为空串
    next_is_twist: boolean;   // 下一章是不是转折章(会激活反转预备)
  };
  // 事实引用(§1.5):管「设定有没有真的落进正文」。
  // dangling = 写在圣经里却从没进过正文的 critical 事实(要么补写要么降级);
  // unsupported_chapters = 已成文但零引用记录的章(大概率没查圣经凭感觉写)。
  fact_usage?: {
    referenced_facts: number;      // 至少被引用过一次的事实数
    critical_total: number;
    dangling: { fact_id: number; content: string; from_chapter: number | null }[];
    unsupported_chapters: number[];
    chapters_with_usage: number;
  };
}

// ---------- 接口 ----------
export const api = {
  health: () => req<{ status: string; providers: ProviderState }>("GET", "/api/health"),
  // 更新提醒:当前部署的 git commit + 最新一条更新日志(公开接口)
  getVersion: () =>
    req<{ commit: string; app_version: string; changelog: { title: string; body: string } }>(
      "GET", "/api/version"),
  // 当前用户是否配置了至少一个可用模型(全局引导横幅用)
  providerStatus: () =>
    req<{ configured: boolean; providers: Record<string, boolean> }>(
      "GET", "/api/settings/providers/status"),
  // ---- 模型设置(设置页「模型设置」分区,对齐 backend/app/api/settings.py)----
  listProviders: () =>
    req<ProviderConfigOut[]>("GET", "/api/settings/providers"),
  // 模型角色分配现状:写手/审校/杂活各用哪套配置,以及写手与审校是否分离。
  // 用途是把「主审分数是不是客观的」变成可见事实(同模型自审会放大乐观偏差)。
  modelRoles: () =>
    req<ModelRoles>("GET", "/api/editorial/model-roles"),
  // 新增一套配置:用户首套配置后端自动设为默认
  createProvider: (body: ProviderConfigIn) =>
    req<ProviderConfigOut>("POST", "/api/settings/providers", body),
  updateProvider: (id: number, body: ProviderConfigIn) =>
    req<ProviderConfigOut>("PUT", `/api/settings/providers/${id}`, body),
  // 删除:不带 confirmed 先探连通性,连通则返回 needs_confirm 由前端二次确认
  deleteProvider: (id: number, confirmed = false) =>
    req<{ deleted: boolean; needs_confirm?: boolean; reason?: string }>(
      "DELETE", `/api/settings/providers/${id}${confirmed ? "?confirmed=true" : ""}`),
  // 连通后自动跑一轮渠道体检(中文输出 / JSON 结构 / 响应速度),最多约 3 分钟
  testProvider: (id: number) =>
    req<{
      ok: boolean; provider: string; model?: string; reply?: string; error?: string;
      warnings?: string[]; checks?: ProviderProbeCheck[]; suitable?: boolean | null;
    }>(
      "POST", `/api/settings/providers/${id}/test`, undefined, 200000),
  // AI 起名走后台任务:返回 job_id,调用方用 pollJob 取 { titles }。
  // 同步版(/title-suggestion)还在后端留着给旧客户端,但前端不再用它——一轮起名
  // 是分钟级 LLM 调用,把连接挂那么久,链路空闲超时一掐就只剩一句 Failed to fetch。
  // 方案轮廓推荐:概念确认后一次轻量调用,推荐阅读手感(tone/elements)与篇幅档位
  suggestShape: (pid: number) =>
    req<ShapeSuggestion>("POST", `/api/projects/${pid}/suggest-shape`),

  // 核心梗卡:读(未建返回 null)/保存(作者保存即 human)/AI 提炼草稿(不落库)/存量章补标节拍
  getPremise: (pid: number) =>
    req<Premise | null>("GET", `/api/projects/${pid}/premise`),
  savePremise: (pid: number, p: PremiseInput) =>
    req<Premise>("PUT", `/api/projects/${pid}/premise`, p),
  suggestPremise: (pid: number) =>
    req<Premise>("POST", `/api/projects/${pid}/suggest-premise`),
  backfillPremiseBeats: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/premise/backfill-beats`),
  // 本章作战图:写前一屏聚合(梗/纲/人物/伏笔账/承上钩子)
  getChapterDossier: (pid: number, n: number) =>
    req<ChapterDossier>("GET", `/api/projects/${pid}/chapters/${n}/dossier`),

  suggestTitleAsync: (topic: string, genre: string, concept?: Concept | null) =>
    req<{ job_id: string }>("POST", "/api/projects/title-suggestion-async",
      { topic, genre, concept: concept ?? null }),

  listProjects: () => req<Project[]>("GET", "/api/projects"),
  createProject: (p: Partial<Project>) => req<Project>("POST", "/api/projects", p),
  // 整本旧书导入(TXT/DOCX):后端解析分卷/章节,建为可继续写作的新项目
  importBook: (file: File, title: string) => {
    const form = new FormData();
    form.append("file", file);
    if (title.trim()) form.append("title", title.trim());
    return reqForm<{ project_id: number; title: string; chapters: number; message: string }>(
      "/api/projects/import-book", form,
    );
  },
  getProject: (id: number) => req<Project>("GET", `/api/projects/${id}`),
  patchProject: (id: number, patch: Partial<Project>) =>
    req<Project>("PATCH", `/api/projects/${id}`, patch),
  // 创作偏好档案(贯穿全书,注入所有生成环节)
  getStyleProfile: (id: number) =>
    req<StyleProfile>("GET", `/api/projects/${id}/style-profile`),
  saveStyleProfile: (id: number, profile: Partial<StyleProfile>) =>
    req<StyleProfile>("PUT", `/api/projects/${id}/style-profile`, profile),
  absorbStyleProfile: (id: number, directive: string) =>
    req<StyleProfile>("POST", `/api/projects/${id}/style-profile/absorb`, { directive }, LLM_TIMEOUT),
  extractStyleProfile: (id: number) =>
    req<StyleProfile>("POST", `/api/projects/${id}/style-profile/extract`, undefined, LLM_TIMEOUT),
  // 文风范本(去 AI 味正向锚):列名家/预设胶囊、从已认可章节提取范本(后者不经 LLM)
  listVoiceCapsules: (id: number) =>
    req<{ capsules: VoiceCapsule[] }>("GET", `/api/projects/${id}/style-profile/voice-capsules`),
  extractVoiceSample: (id: number) =>
    req<StyleProfile>("POST", `/api/projects/${id}/style-profile/extract-voice`),
  renameProject: (id: number, title: string) =>
    req<Project>("PATCH", `/api/projects/${id}`, { title }),
  deleteProject: (id: number) =>
    req<{ ok: boolean; deleted_chapters: number }>("DELETE", `/api/projects/${id}`),
  // 清空已写正文与大纲(保留架构/概念/DNA/简介):架构重写后「从新架构重来」用的破坏性操作
  resetProjectContent: (id: number) =>
    req<{ ok: boolean; deleted_chapters: number; content_reset: boolean }>(
      "DELETE", `/api/projects/${id}/content`),
  // 本项目正在运行的后台任务(切走再回来时重新接上轮询)
  runningJobs: (id: number) =>
    req<{ jobs: { job_id: string; kind: string; stage: string }[] }>(
      "GET", `/api/projects/${id}/running-jobs`),
  // 当前用户全部后台任务(全局任务中心;all=true 含近期已完成)
  myJobs: (all = false) =>
    req<{ jobs: { job_id: string; kind: string; status: string; stage: string; error?: string | null }[] }>(
      "GET", `/api/jobs${all ? "?all=true" : ""}`),
  // 手动终止一个运行中的后台任务:进行中的 LLM 请求一并掐断,已耗 token 不退
  cancelJob: (jobId: string) =>
    req<{ ok: boolean }>("POST", `/api/jobs/${jobId}/cancel`),
  // 订阅某任务的「实时正文」(SSE):模型正在吐的字逐帧到达。
  // 帧:step(换屏/初始快照)/token(增量)/reset(整屏重置)/ping(心跳)/done(结束)。
  // cursor 传已收到的字数,断线重连可续;返回的 Promise 在流结束时 resolve。
  followJobLive: (
    jobId: string,
    cursor: number,
    onFrame: (frame: SseFrame) => void,
    signal?: AbortSignal,
  ) => sseStream(`/api/jobs/${jobId}/live?cursor=${cursor}`, { method: "GET" }, onFrame, signal),

  // ---- 异步 job 版长任务(返回 job_id,配合 pollJob/任务中心) ----
  inspireAsync: (spark: string, tendency: Tendency, count = 4, dna: StoryDNA | null = null, avoid?: Concept | null) =>
    req<{ job_id: string }>("POST", "/api/inspire/async", { spark, tendency, count, dna, avoid: avoid && !conceptIsEmpty(avoid) ? avoid : undefined }),
  // 两段式构思·第一段:方向+偏好 → 一批故事引擎卡(FAST 档,先便宜收敛)
  enginesAsync: (spark: string, tendency: Tendency, count = 8, dna: StoryDNA | null = null, avoidEngines: string[] = []) =>
    req<{ job_id: string }>("POST", "/api/inspire/engines/async",
      { spark, tendency, count, dna, avoid_engines: avoidEngines.length ? avoidEngines : undefined }),
  // 两段式构思·第二段:选中的引擎(1-2 张,可混搭)→ 深化成完整概念(强模型)
  developConceptAsync: (engines: string[], spark = "", tendency: Tendency = {}, dna: StoryDNA | null = null) =>
    req<{ job_id: string }>("POST", "/api/inspire/develop/async", { engines, spark, tendency, dna }),
  refineConceptAsync: (concept: Concept, directive: string, tendency: Tendency = {}, dna: StoryDNA | null = null) =>
    req<{ job_id: string }>("POST", "/api/inspire/refine-async", { concept, directive, tendency, dna }),
  polishChapterAsync: (pid: number, n: number, tendency: Tendency, directive = "") =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/polish/chapter/${n}/async`, { tendency, directive }),
  polishSegmentAsync: (pid: number, text: string, tendency: Tendency) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/polish/segment-async`, { text, tendency }),
  impactAsync: (pid: number, n: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/outlines/${n}/impact-async`, {}),
  cascadeAsync: (pid: number, body: object) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/outlines/cascade-async`, body),
  synopsisAsync: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/synopsis-async`, {}),
  generateSubmissionAsync: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/submission/generate`, {}),
  generateCoverAsync: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/cover/generate`, {}),
  generateAnthemAsync: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/anthem/generate`, {}),

  inspire: (spark: string, tendency: Tendency, count = 4, dna: StoryDNA | null = null, avoid?: Concept | null) =>
    req<{ ideas: Concept[] }>("POST", "/api/inspire", { spark, tendency, count, dna, avoid: avoid && !conceptIsEmpty(avoid) ? avoid : undefined }, LLM_TIMEOUT),
  refineConcept: (concept: Concept, directive: string, tendency: Tendency = {}, dna: StoryDNA | null = null) =>
    req<RefineResult>("POST", "/api/inspire/refine", { concept, directive, tendency, dna }, LLM_TIMEOUT),
  chatConcept: (messages: ChatTurn[], concept: Concept | null, tendency: Tendency = {}, dna: StoryDNA | null = null) =>
    req<ChatResult>("POST", "/api/inspire/chat", { messages, concept, tendency, dna }, LLM_TIMEOUT),
  // 坐标卡静态选项(味道锚/模式/味道轴/各模式禁忌)
  dnaOptions: () => req<DnaOptions>("GET", "/api/inspire/dna/options"),
  derivePattern: (text: string) =>
    req<PatternDerived>("POST", "/api/inspire/dna/pattern-derive", { text }, 180000),
  // 品味镜:坐标卡 → 一段人话复述 + 矛盾检测 + 会拦的套路(生成前照镜子,先核对再烧 token)
  dnaMirror: (dna: StoryDNA, spark = "") =>
    req<MirrorResult>("POST", "/api/inspire/dna/mirror", { dna, spark }, 60000),
  generateSynopsis: (id: number) =>
    req<{ synopsis: string }>("POST", `/api/projects/${id}/synopsis`, {}, LLM_TIMEOUT),
  patchArchitecture: (id: number, patch: Partial<Architecture>) =>
    req<Architecture>("PATCH", `/api/projects/${id}/architecture`, patch),

  getArchitecture: (id: number) => req<Architecture>("GET", `/api/projects/${id}/architecture`),
  generateArchitecture: (id: number, tendency: Tendency) =>
    req<Architecture>("POST", `/api/projects/${id}/architecture`, { tendency }, LLM_TIMEOUT),
  generateBlueprint: (id: number, tendency: Tendency) =>
    req<{ outlines: Outline[]; warnings: string[] }>("POST", `/api/projects/${id}/blueprint`, { tendency }, LLM_TIMEOUT),
  generateArchitectureAsync: (id: number, tendency: Tendency, directive = "") =>
    req<{ job_id: string }>("POST", `/api/projects/${id}/architecture-async`, { tendency, directive }),
  // 架构研讨:多轮对话聊清不满意在哪 → 蒸馏出「额外要求」directive,拿去重新生成
  discussArchitecture: (id: number, messages: { role: string; content: string }[]) =>
    req<{ reply: string; directive: string }>("POST", `/api/projects/${id}/architecture/discuss`, { messages }, LLM_TIMEOUT),
  generateBlueprintAsync: (id: number, tendency: Tendency, titleStyle = "", titleDirective = "") =>
    req<{ job_id: string }>("POST", `/api/projects/${id}/blueprint-async`,
      { tendency, title_style: titleStyle, title_directive: titleDirective }),
  // 滚动规划:展开下一卷蓝图(按卷纲+已成文状态)
  extendBlueprintAsync: (id: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${id}/blueprint-extend-async`, {}),
  listOutlines: (id: number) => req<Outline[]>("GET", `/api/projects/${id}/outlines`),
  // 全书张力总线:每章目标张力(1-5)+ 节奏体检(极差/均值/是否平/峰值章)。
  // 确定性计算,不花 LLM;前端用来画节奏曲线,治「全书一条平线」。
  tensionCurve: (id: number) =>
    req<TensionCurve>("GET", `/api/projects/${id}/outlines/tension-curve`),

  // 场景级干预(§2.6):列场景卡 / 改卡 / 改正文 / 定点重生成。
  // 场景是生成的最小单元,这几个端点让作者能落到那个面上接手,
  // 而不必为了一场写歪就重写整章。
  scenes: (pid: number, chapter: number) =>
    req<SceneList>("GET", `/api/projects/${pid}/scenes/${chapter}`),
  sceneDetail: (pid: number, sceneId: number) =>
    req<SceneCard>("GET", `/api/projects/${pid}/scenes/detail/${sceneId}`),
  updateSceneCard: (pid: number, sceneId: number, patch: Partial<SceneCardPatch>) =>
    req<{ changed: string[]; scene: SceneCard }>(
      "PATCH", `/api/projects/${pid}/scenes/${sceneId}`, patch),
  updateSceneText: (pid: number, sceneId: number, content: string, note = "") =>
    req<{ changed: boolean; scene: SceneCard }>(
      "PUT", `/api/projects/${pid}/scenes/${sceneId}/text`, { content, note }, LLM_TIMEOUT),
  regenerateScene: (pid: number, sceneId: number) =>
    req<{ scene: SceneCard }>(
      "POST", `/api/projects/${pid}/scenes/${sceneId}/regenerate`, {}, LLM_TIMEOUT),

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
  // 单章大纲研讨:多轮对话聊清"这章大纲哪里不对" → 蒸馏出改写提案(确认后走 applyEditDirective 落库)
  discussOutline: (pid: number, n: number, messages: { role: string; content: string }[]) =>
    req<{ reply: string; proposal: { new_title: string | null; new_summary: string; change_reason: string } | null }>(
      "POST", `/api/projects/${pid}/outlines/${n}/discuss`, { messages }, LLM_TIMEOUT),
  // 章节标题润色:基于本章大纲让 AI 给几个候选标题(不落库),作者选定后走 editOutline 只改 title
  retitleChapter: (pid: number, n: number, directive = "") =>
    req<{ titles: string[] }>("POST", `/api/projects/${pid}/outlines/${n}/retitle`, { directive }, LLM_TIMEOUT),
  // 批量重拟标题(一键换一批,不动剧情):预览 old→new,不落库;chapterNumbers 不传=全书
  retitleAllChapters: (pid: number, titleStyle = "", directive = "", chapterNumbers?: number[]) =>
    req<RetitleAllResult>("POST", `/api/projects/${pid}/outlines/retitle-all`,
      { title_style: titleStyle, directive, chapter_numbers: chapterNumbers ?? null }, LLM_TIMEOUT),
  // 应用作者确认的一批新标题:逐章只改 title(cosmetic,不标正文失配)
  applyRetitleAll: (pid: number, items: { chapter_number: number; new_title: string }[]) =>
    req<ApplyRetitleResult>("POST", `/api/projects/${pid}/outlines/retitle-all/apply`, { items }),

  listChapters: (pid: number) => req<ChapterBrief[]>("GET", `/api/projects/${pid}/chapters`),
  getChapter: (pid: number, n: number) => req<ChapterDetail>("GET", `/api/projects/${pid}/chapters/${n}`),
  getMyChapterFeedback: (pid: number, n: number) =>
    req<ChapterFeedbackOut | null>("GET", `/api/projects/${pid}/chapters/${n}/feedback`),
  setChapterFeedback: (pid: number, n: number, body: { rating: "good" | "bad"; categories?: string[]; comment?: string }) =>
    req<ChapterFeedbackOut>("POST", `/api/projects/${pid}/chapters/${n}/feedback`, body),
  generateChapter: (pid: number, n: number, tendency: Tendency) =>
    req<GenerateChapterResponse>("POST", `/api/projects/${pid}/chapters/${n}/generate`, { tendency }, LLM_TIMEOUT),
  generateChapterAsync: (pid: number, n: number, tendency: Tendency, revision = "") =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/generate-async`, { tendency, revision }),
  // ②档多处批注改(job):批注清单{段号,原文快照,意见}整批发给 LLM 逐段定点润色,返回逐段旧/新对
  reviseAnnotatedAsync: (
    pid: number, n: number, annotations: { para_idx: number; original: string; note: string }[],
  ) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/revise-annotated-async`, { annotations }),
  // 重写研讨:多轮对话聊清"这章哪里不满意" → 蒸馏出修改意见 directive(+ AI 建议档位)
  discussRevision: (pid: number, n: number, messages: { role: string; content: string }[]) =>
    req<{ reply: string; directive: string; suggested_level: string | null }>("POST", `/api/projects/${pid}/chapters/${n}/revise-discuss`, { messages }, LLM_TIMEOUT),
  // 重写研讨(真流式打字机):逐字回调 onToken,收尾 resolve 出 {reply, directive, suggested_level}
  discussRevisionStream: (
    pid: number, n: number,
    messages: { role: string; content: string }[],
    onToken: (text: string) => void,
    signal?: AbortSignal,
  ) =>
    runDiscussStream<{ reply: string; directive: string; suggested_level: string | null }>(
      `/api/projects/${pid}/chapters/${n}/revise-discuss-stream`, { messages }, onToken, signal),
  editChapterContent: (pid: number, n: number, final_content: string) =>
    req<ChapterDetail>("PUT", `/api/projects/${pid}/chapters/${n}/content`, { final_content }),
  // 人工审核通过(docs/08 §5.5):pending_review → approved;quarantined 时 400
  approveChapter: (pid: number, n: number) =>
    req<ChapterDetail>("POST", `/api/projects/${pid}/chapters/${n}/approve`),
  // 一致性问题清单(docs/08 §5.7):open/resolved/ignored 各状态,最新在前
  listChapterIssues: (pid: number, n: number) =>
    req<ChapterIssue[]>("GET", `/api/projects/${pid}/chapters/${n}/issues`),
  // 「重新检查」(GateResolve 用;原「契约重提」):重提上一章+本章契约并重检本章门禁(gate 清单重建);
  // 若本章原为 quarantined 且重检后无 blocker,后端自动放行补圣经/摘要,job 结果带 auto_released:true
  reextractContract: (pid: number, n: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/contract-reextract-async`),
  // 单条问题状态流转:open → resolved(已人工改完)/ ignored(确认忽略)
  patchChapterIssue: (pid: number, n: number, issueId: number, status: "resolved" | "ignored") =>
    req<ChapterIssue>("PATCH", `/api/projects/${pid}/chapters/${n}/issues/${issueId}`, { status }),
  // 采纳单条问题的修正建议:异步修订 job(409=本章有任务在跑),result 含 applied_issue_id
  applyIssueRevision: (pid: number, n: number, issueId: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/issues/${issueId}/apply-revision`),
  // 单条问题定点修复(分级回炉):AI 原位改句不重写,门禁复查干净才落库(409=本章有任务在跑)
  spotRepairIssue: (pid: number, n: number, issueId: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/issues/${issueId}/spot-repair`),
  // 采纳一条「故事宪法建议」(source=canon)进 project.canon,并标 issue 为 resolved;
  // changed=false 表示该建议内容此前已在宪法里(仍视作已采纳)。返回更新后的 canon + issue。
  adoptCanonSuggestion: (pid: number, n: number, issueId: number) =>
    req<{ ok: boolean; changed: boolean; canon: StoryCanon; issue: ChapterIssue }>(
      "POST", `/api/projects/${pid}/chapters/${n}/issues/${issueId}/adopt-canon`),
  // quarantined 放行:忽略全部 blocker,补走圣经/摘要等章后链路(异步 job)
  gateRelease: (pid: number, n: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/gate-release`),
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
    req<UsageSummary>("GET", "/api/usage"),
  // 实时余额(仅 deepseek 官方支持;其余协议后端 501,前端隐藏按钮)
  providerBalance: (id: number) =>
    req<ProviderBalance>("GET", `/api/settings/providers/${id}/balance`),

  bible: (pid: number, chapter: number) =>
    req<BibleSnapshot>("GET", `/api/projects/${pid}/bible?chapter=${chapter}`),
  foreshadowings: (pid: number, current: number) =>
    req<ForeshadowOut[]>("GET", `/api/projects/${pid}/foreshadowings?current_chapter=${current}`),
  characters: (pid: number) =>
    req<CharactersOut>("GET", `/api/projects/${pid}/characters`),
  overview: (pid: number) =>
    req<OverviewOut>("GET", `/api/projects/${pid}/overview`),
  // 全书剧情时间线(各章章末契约聚合,零 LLM)
  timeline: (pid: number) =>
    req<{ items: TimelineItem[] }>("GET", `/api/projects/${pid}/timeline`),
  // 成书体检报告(只读聚合,零 LLM):体量/质感曲线/节奏曲线/一致性/伏笔/成本
  healthReport: (pid: number) =>
    req<HealthReportOut>("GET", `/api/projects/${pid}/health-report`),
  // 报告原文下载(服务端已渲染好 Markdown,直接取文本)
  healthReportMarkdown: (pid: number) =>
    fetch(`${apiBase()}/api/projects/${pid}/health-report?format=markdown`, {
      headers: authHeaders(),
    }).then((r) => {
      if (!r.ok) throw new ApiError(r.status, `HTTP ${r.status}`);
      return r.text();
    }),
  // 时序事实时间线(每角色一条轨道,事实为章节区间条;与故事圣经·时间机同源)
  factsTimeline: (pid: number) =>
    req<FactsTimelineOut>("GET", `/api/projects/${pid}/facts-timeline`),
  createCharacter: (pid: number, payload: { name: string; aliases?: string[]; profile?: string }) =>
    req<CharacterCard>("POST", `/api/projects/${pid}/characters`, payload),
  setCharacterRetired: (pid: number, entityId: number, retired: boolean) =>
    req<CharacterCard>("PATCH", `/api/projects/${pid}/characters/${entityId}`, { retired }),
  // 编辑人物资料(别名/简介):简介变更时返回 changes(句级 diff,带 entity),供追问级联
  editCharacter: (pid: number, entityId: number, payload: { profile?: string; aliases?: string[] }) =>
    req<CharacterCard & { changes: SettingChange[] }>(
      "PATCH", `/api/projects/${pid}/characters/${entityId}`, payload),
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
  // 回显:最近一次主审结果(生成时或手动);正文改动后为 null
  getReview: (pid: number, n: number) =>
    req<{ review: ChapterReview | null }>("GET", `/api/projects/${pid}/chapters/${n}/review`),
  proofreadAsync: (pid: number, n: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/chapters/${n}/proofread-async`, {}),
  proofreadApply: (pid: number, n: number, fixes: { original: string; suggestion: string }[]) =>
    req<{ applied: { original: string; suggestion: string }[]; failed: { original: string; reason: string }[]; word_count: number; final_content: string }>(
      "POST", `/api/projects/${pid}/chapters/${n}/proofread-apply`, { fixes }),
  // 回显:最近一次校对结果(生成时自动修复 / 手动待修);正文改动后为 null
  getProofread: (pid: number, n: number) =>
    req<{ proofread: ProofreadSnapshot | null }>("GET", `/api/projects/${pid}/chapters/${n}/proofread`),
  auditReport: (pid: number) =>
    req<AuditReport>("GET", `/api/projects/${pid}/audit-report`),
  // 全书体检(LLM 逐章扫跨章矛盾,问题以「诊断」落各章审核报告)/ 老书批量补契约
  diagAsync: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/diag-async`),
  // 规则扫描:逐章对照世界观硬规则(world_rules)体检正文,问题以「规则」落各章审核报告
  ruleScanAsync: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/rule-scan-async`),
  // 设定级级联:设定变更 → 全书影响扫描 → 段落定点修提案(提案不落库,前端 diff 验收)
  settingCascadeScan: (pid: number, oldText: string, newText: string) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/setting-cascade/scan-async`,
      { old_text: oldText, new_text: newText }),
  // 现成变更清单入口(人物卡编辑场景:保存时后端已算好 diff,原样回传)
  settingCascadeScanChanges: (pid: number, changes: SettingChange[]) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/setting-cascade/scan-async`,
      { changes }),
  settingCascadePatch: (pid: number, changes: SettingChange[], passages: { chapter_number: number; para_idx: number }[]) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/setting-cascade/patch-async`,
      { changes, passages }),
  contractsBackfillAsync: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/contracts/backfill-async`),
  // 指令改异步解析(应用仍走同步 apply,纯 DB 快)
  parseEditDirectiveAsync: (pid: number, directive: string) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/outlines/edit-directive-async`, { directive }),
  // 伏笔手动操作:弃用/恢复/标记回收/改预期章
  patchForeshadow: (pid: number, fid: number, patch: { status?: string; expected_payoff_chapter?: number; payoff_chapter?: number; notes?: string }) =>
    req<{ id: number; status: string }>("PATCH", `/api/projects/${pid}/foreshadowings/${fid}`, patch),

  // ---- 桥段台账 + 雷区清单(跨章重复描写治理) ----
  motifs: (pid: number) =>
    req<MotifsOut>("GET", `/api/projects/${pid}/motifs`),
  addBannedMotif: (pid: number, label: string, detail = "") =>
    req<BannedMotif>("POST", `/api/projects/${pid}/motifs/banned`, { label, detail }),
  removeBannedMotif: (pid: number, mid: number) =>
    req<{ ok: boolean }>("DELETE", `/api/projects/${pid}/motifs/banned/${mid}`),
  promoteMotif: (pid: number, label: string) =>
    req<BannedMotif>("POST", `/api/projects/${pid}/motifs/banned/promote`, { label }),
  clearLedgerMotif: (pid: number, label: string) =>
    req<{ removed: number }>("DELETE", `/api/projects/${pid}/motifs/ledger?label=${encodeURIComponent(label)}`),
  scanMotifsAsync: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/motifs/scan-async`),

  // ---- 跨章标记 + 全书批修(边读边攒「这里不行」,一句总描述统一驱动成批改) ----
  marks: (pid: number) =>
    req<ChapterMark[]>("GET", `/api/projects/${pid}/marks`),
  addMark: (pid: number, payload: { chapter_number: number; para_idx: number; snapshot: string; note: string }) =>
    req<ChapterMark>("POST", `/api/projects/${pid}/marks`, payload),
  removeMark: (pid: number, mid: number) =>
    req<{ ok: boolean }>("DELETE", `/api/projects/${pid}/marks/${mid}`),
  marksReviseAsync: (pid: number, directive: string) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/marks/revise-async`, { directive }),

  // ---- 全书全文检索(FTS5 trigram 倒排,<3 字后端自动降级 LIKE) ----
  search: (pid: number, q: string) =>
    req<SearchResponse>("GET", `/api/projects/${pid}/search?q=${encodeURIComponent(q)}`),

  polishChapter: (pid: number, n: number, tendency: Tendency) =>
    req<PolishResult>("POST", `/api/projects/${pid}/polish/chapter/${n}`, { tendency }, LLM_TIMEOUT),
  applyPolish: (pid: number, n: number, polished_text: string, base_content?: string) =>
    req<{ status: string }>("POST", `/api/projects/${pid}/polish/chapter/${n}/apply`,
      // 传 base_content=优化基线 → 后端乐观并发校验(正文优化期间被手改过则 409);
      // 不传 = 用户在冲突提示里确认强制覆盖(旧内容后端已存版本历史)。
      base_content === undefined ? { polished_text } : { polished_text, base_content }),
  polishSegment: (pid: number, text: string, tendency: Tendency) =>
    req<PolishResult>("POST", `/api/projects/${pid}/polish/segment`, { text, tendency }, LLM_TIMEOUT),
  polishFragment: (pid: number, n: number, fragment: string, direction: string) =>
    req<{ polished: string; notes: string | null }>(
      "POST", `/api/projects/${pid}/chapters/${n}/polish-fragment`, { fragment, direction }, LLM_TIMEOUT),
  // 选区 craft 微工具:describe/expand 返回 rewrite(diff 替换),brainstorm 返回 ideas(点子)
  craftFragment: (pid: number, n: number, fragment: string, mode: CraftMode, note = "") =>
    req<CraftResult>(
      "POST", `/api/projects/${pid}/chapters/${n}/craft-fragment`, { fragment, mode, note }, LLM_TIMEOUT),
  // 章尾续写(ghost text):顺着已写正文续一个自然段,Tab 接受后作为新段落追加
  continueChapter: (pid: number, n: number, note = "") =>
    req<{ continuation: string }>(
      "POST", `/api/projects/${pid}/chapters/${n}/continue`, { note }, LLM_TIMEOUT),
  // 就选中段落与 AI 多轮对话:可解释、可给改写建议(suggestion 非空时可一键采用);
  // target 置空 = 整章自由问答(只答不改,后端走 DISCUSS_CHAPTER prompt)
  discussFragment: (pid: number, n: number, messages: { role: string; content: string }[], target = "") =>
    req<{ reply: string; suggestion: string | null }>(
      "POST", `/api/projects/${pid}/chapters/${n}/discuss`, { messages, target }, LLM_TIMEOUT),
  // 选段对话(真流式打字机):逐字回调 onToken,收尾 resolve 出 {reply, suggestion}
  discussFragmentStream: (
    pid: number, n: number,
    messages: { role: string; content: string }[],
    target: string,
    onToken: (text: string) => void,
    signal?: AbortSignal,
  ) =>
    runDiscussStream<{ reply: string; suggestion: string | null }>(
      `/api/projects/${pid}/chapters/${n}/discuss-stream`, { messages, target }, onToken, signal),
  aiFlavor: (text: string) =>
    req<FlavorInfo & { hits?: Record<string, unknown>[]; total_chars?: number }>(
      "POST", "/api/polish/ai-flavor", { text }),

  // ---------- 写作手法卡(项目级手法库,启用即注入) ----------
  listCards: (pid: number) => req<WritingCard[]>("GET", `/api/projects/${pid}/cards`),
  createCard: (pid: number, body: { title: string; body: string; enabled?: boolean }) =>
    req<WritingCard>("POST", `/api/projects/${pid}/cards`, body),
  updateCard: (
    pid: number,
    cardId: number,
    patch: { title?: string; body?: string; enabled?: boolean; sort?: number },
  ) => req<WritingCard>("PATCH", `/api/projects/${pid}/cards/${cardId}`, patch),
  deleteCard: (pid: number, cardId: number) =>
    req<{ status: string; id: number }>("DELETE", `/api/projects/${pid}/cards/${cardId}`),
  cardsPreview: (pid: number) =>
    req<{ block: string; enabled_count: number; max_inject: number }>(
      "GET", `/api/projects/${pid}/cards/preview`),

  // ---------- 鉴权 ----------
  register: (username: string, password: string, invite_code: string) =>
    req<AuthResult>("POST", "/api/auth/register", { username, password, invite_code }),
  login: (username: string, password: string) =>
    req<AuthResult>("POST", "/api/auth/login", { username, password }),
  me: () => req<Me>("GET", "/api/auth/me"),
  // 修改自己的密码(须验旧密码;桌面单机免登录模式后端会拒绝,前端也不显示入口)
  changePassword: (old_password: string, new_password: string) =>
    req<{ ok: boolean }>("POST", "/api/auth/change-password", { old_password, new_password }),
  // ---- 应用锁(仅桌面单机 local 模式;server 模式后端 404,前端不渲染入口) ----
  // 休闲锁:只校验,不发凭证;解锁状态由前端 sessionStorage 记。
  appLockSet: (new_password: string, old_password?: string) =>
    req<{ ok: boolean }>("POST", "/api/app-lock", { old_password: old_password ?? null, new_password }),
  appLockUnlock: (password: string) =>
    req<{ ok: boolean }>("POST", "/api/app-lock/unlock", { password }),
  appLockRemove: (password: string) =>
    req<{ ok: boolean }>("POST", "/api/app-lock/remove", { password }),
  // 忘记密码的重置口子:无需旧密码,须传 confirm="重置" 防误触
  appLockReset: (confirm: string) =>
    req<{ ok: boolean }>("POST", "/api/app-lock/reset", { confirm }),
  // 运行模式:local(桌面单机,免登录)/ server(多用户)。前端据此跳过登录页。
  // has_lock 仅 local 有意义:设了应用锁,启动时先出锁屏。
  mode: () => req<{ mode: string; is_local: boolean; has_lock: boolean }>("GET", "/api/mode"),
  // 桌面单机模式:把外链交给系统默认浏览器(WebView2 不处理 target=_blank 新窗口)
  openLink: (url: string) => req<{ ok: boolean }>("POST", "/api/system/open-link", { url }),

  // ---------- 后台管理(仅管理员可用) ----------
  adminListUsers: () => req<AdminUser[]>("GET", "/api/admin/users"),
  adminUsageStats: () =>
    req<{ usage: FeatureUsageStat[] }>("GET", "/api/admin/usage"),
  adminQualityOverview: (days = 30) =>
    req<QualityOverview>("GET", `/api/admin/quality-overview?days=${days}`),
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
  // AI 味检测热更配置:类别权重 + 自愈门槛,改完立即生效(不重启)
  adminGetAiFlavorConfig: () => req<AiFlavorConfig>("GET", "/api/admin/ai-flavor-config"),
  adminPutAiFlavorConfig: (gate_score: number, weights: Record<string, number>) =>
    req<AiFlavorConfig>("PUT", "/api/admin/ai-flavor-config", { gate_score, weights }),

  // ---- 重构翻新(已有书按新逻辑翻新):都返回 job_id,配合 pollJob ----
  refreshBackfillBeats: (pid: number, chapter_numbers: number[] = []) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/refresh/backfill-beats`, { chapter_numbers }),
  refreshSeedStyleMemo: (pid: number) =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/refresh/seed-style-memo`),
  refreshLight: (pid: number, chapter_numbers: number[] = [], directive = "") =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/refresh/light`, { chapter_numbers, directive }),
  refreshHeavy: (pid: number, chapter_numbers: number[] = [], directive = "") =>
    req<{ job_id: string }>("POST", `/api/projects/${pid}/refresh/heavy`, { chapter_numbers, directive }),
};
