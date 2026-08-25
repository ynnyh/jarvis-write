// 生日祝福工作台共用件:切段聚合、状态色调、元数据(基调/关系/画风)一次拉一份。
// 与 clips 线同一取舍:串行的「出本子→定手卡→出片」,不套 wb-cols 双栏,
// 统一的是「现在该点哪个按钮」的步骤条心智——与另两条出片线共用 StepBar。
import { useEffect, useState } from "react";
import { BirthdayDirection, BirthdayPack, BirthdayTone, RelationOption, WishShot, birthdayApi } from "../../birthdayApi";

export interface BirthdayMeta {
  tones: BirthdayTone[];
  packs: BirthdayPack[];
  relationships: RelationOption[];
  milestones: string[];
  durations: number[];
  directions: BirthdayDirection[];
  max_memories: number;
  memory_max_chars: number;
}

export function useBirthdayMeta() {
  const [meta, setMeta] = useState<BirthdayMeta | null>(null);
  useEffect(() => { void birthdayApi.meta().then(setMeta).catch(() => {}); }, []);
  return meta;
}

/** 企划状态 → 徽章色调:待生成(warn) / 候选已出(中性 brand,默认) / 已选定(ok)。 */
export function wishStatusTone(status: string): "" | "ok" | "warn" {
  if (status === "draft") return "warn";
  if (status === "picked") return "ok";
  return "";
}

// 按切段聚合该段镜头的中文提示词:即梦/可灵/minimax 这类图文生视频是一段一生成,
// 要的是"这一段直接贴进去"的一整段;散在分镜表里的逐格词得自己拼,那不算一键。
export function chunkPromptText(shots: WishShot[], seqs: number[]): string {
  const set = new Set(seqs);
  return shots
    .filter((s) => set.has(s.seq))
    .map((s) => `镜头${s.seq}(${s.duration_s}s): ${s.prompt_cn}`)
    .join("\n\n");
}
