// 向导候选签名:记录 AI 候选生成时依赖的输入,回改上游后用于判定候选是否过期。
// 纯函数,便于单测;签名是 JSON 字符串,随候选一起进 wiz-cache localStorage。
import { Concept, CONCEPT_FIELDS, Tendency } from "../api";

// 倾向对象规范化:滤掉空值、按 key 排序,保证同一语义输入必得同一签名
function stableTendency(t: Tendency): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(t).sort()) {
    const v = t[k];
    if (Array.isArray(v) ? v.length > 0 : !!v) out[k] = v;
  }
  return out;
}

/** concept 屏候选签名:灵感文本 + 倾向(题材等,与 inspireAsync 入参一致) */
export function conceptSig(spark: string, tendency: Tendency): string {
  return JSON.stringify({ s: spark.trim(), t: stableTendency(tendency) });
}

/** title 屏候选签名:主题 + 题材 + 概念六字段(与 suggestTitle 入参一致) */
export function titleSig(topic: string, genre: string, concept: Concept): string {
  return JSON.stringify({
    p: topic.trim(),
    g: genre.trim(),
    c: CONCEPT_FIELDS.map((f) => concept[f.key]?.trim() ?? ""),
  });
}

/** 候选是否过期:有候选、有签名、且与当前输入签名不一致 */
export function isStale<T>(items: T[] | null, sig: string | null, currentSig: string): boolean {
  return !!items?.length && sig !== null && sig !== currentSig;
}

/** 概念屏过期提示文案:点出变了什么,点不出就给通用说法 */
export function conceptStaleText(sig: string, spark: string, tendency: Tendency): string {
  try {
    const old = JSON.parse(sig) as { s?: string; t?: Record<string, unknown> };
    if ((old.s ?? "") !== spark.trim()) return "灵感已更换,这批候选基于之前的灵感";
    const g = ((tendency.genre as string) ?? "").trim();
    const og = ((old.t?.genre as string) ?? "").trim();
    if (og !== g) {
      return g ? `题材已更换为「${g}」,这批候选基于之前的题材` : "题材已更换,这批候选基于之前的题材";
    }
  } catch { /* 旧缓存解析失败按通用处理 */ }
  return "上游设定已变化,这批候选基于之前的输入";
}

/** 书名屏过期提示文案 */
export function titleStaleText(sig: string, topic: string, genre: string, concept: Concept): string {
  try {
    const old = JSON.parse(sig) as { p?: string; g?: string; c?: string[] };
    const curConcept = CONCEPT_FIELDS.map((f) => concept[f.key]?.trim() ?? "");
    if (JSON.stringify(old.c ?? []) !== JSON.stringify(curConcept)) {
      return "故事概念已更换,这批书名基于之前的概念";
    }
    if ((old.g ?? "") !== genre.trim()) return "题材已更换,这批书名基于之前的题材";
    if ((old.p ?? "") !== topic.trim()) return "主题已更换,这批书名基于之前的主题";
  } catch { /* 同上 */ }
  return "上游设定已变化,这批书名基于之前的输入";
}
