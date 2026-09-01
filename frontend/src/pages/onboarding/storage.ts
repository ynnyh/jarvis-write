// 起步流的 localStorage 缓存:候选内容缓存 + 回改影响标记。拆自 OnboardingFlow.tsx。
import type { Concept, EngineCard } from "../../api";
import type { SetupStep } from "./steps";

export interface WizCache {
  spark: string; ideas: Concept[] | null; titleIdeas: string[] | null;
  ideaSig?: string | null; titleSig?: string | null;
  engineCards?: EngineCard[] | null;   // 两段式第一段的引擎卡(恢复后不再自动 fetch)
}
export interface Dirty { from: SetupStep; ok: SetupStep[]; }

export function loadJSON<T>(key: string): T | null {
  try { const s = localStorage.getItem(key); return s ? JSON.parse(s) as T : null; }
  catch { return null; }
}
export function saveJSON(key: string, v: unknown) {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* 缓存失败不阻塞 */ }
}
