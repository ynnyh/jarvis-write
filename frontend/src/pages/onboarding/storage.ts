// 起步流的 localStorage 缓存:候选内容缓存 + 回改影响标记。拆自 OnboardingFlow.tsx。
//
// 键按版本编址(v2):v1 直接用 pid 裸编址,而后端老库的 projects.id 是按
// max(rowid)+1 分配的——删掉 id 最大的书再新建,新草稿会复用同一个 id,v1
// 缓存就被灌进新书,表现为「删书重开,提示文字和卡片还是上一次的」(开书串档)。
// v2 双保险:键升版甩开历史遗留 + createdAt 所有权校验(缓存属于已删除的
// 同号新旧项目时丢弃)。服务端配套:projects.id 改 AUTOINCREMENT 只增不复用。
import type { Pitch } from "../../api";
import type { SetupStep } from "./steps";

export interface WizCache {
  spark: string; titleIdeas: string[] | null;
  titleSig?: string | null;
  // 简介屏的 🎲 提案(刷新回到当前屏接着挑;聊过天则以服务端 chat_log/brief 为准)
  ideaCards?: Pitch[] | null;
  createdAt?: string;                  // 项目创建时间:所有权校验,对不上即弃
}

export interface Dirty { from: SetupStep; ok: SetupStep[]; }

// v1 -> v2:键里带版本,老库 id 复用时新键不可能命中旧缓存
const KEY_VERSION = "v2";

export function wizKeys(pid: number) {
  return {
    cache: `wiz-cache:${KEY_VERSION}:${pid}`,
    dirty: `wiz-dirty:${KEY_VERSION}:${pid}`,
    pipe: `wiz-pipe:${KEY_VERSION}:${pid}`,
  };
}

// 一次性清扫 v1 遗留键:它们没有所有权标记,一旦 id 被复用就会串档,直接清掉
let swept = false;
export function sweepLegacyWizKeys() {
  if (swept) return;
  swept = true;
  try {
    const stale: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && /^wiz-(cache|dirty|pipe):\d+$/.test(k)) stale.push(k);
    }
    stale.forEach((k) => localStorage.removeItem(k));
  } catch { /* 清扫失败不阻塞 */ }
}

export function loadJSON<T>(key: string): T | null {
  try { const s = localStorage.getItem(key); return s ? JSON.parse(s) as T : null; }
  catch { return null; }
}
export function saveJSON(key: string, v: unknown) {
  try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* 缓存失败不阻塞 */ }
}
