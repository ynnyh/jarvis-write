// 小说与动画共用的参考方向；所有入口读取同一份已采用目标。
import { req } from "./http";

export const CREATIVE_DIMS = {
  structure: "结构", engine: "故事机制", pacing: "节奏", voice: "语言与人物", experience: "体验", detail: "细节取舍",
};
export type CreativeDimension = keyof typeof CREATIVE_DIMS;
export interface CreativeReference { name: string; description: string; excerpt: string; locator: string; url: string; read_scope?: string }
export interface CreativeObservation {
  dimension: CreativeDimension; instruction: string; source_index: number;
  evidence: string; basis: "excerpt" | "description" | "inferred";
}
export interface CreativeGoal {
  intent: string; form: "serial" | "short" | "continuous" | "sketch" | "anthology";
  references: CreativeReference[]; selected: CreativeDimension[];
  observations: CreativeObservation[]; unknowns: string[]; must: string; avoid: string;
  enabled: boolean; expected_version?: number; version?: number; history?: CreativeGoal[];
  fetch_links?: boolean;
}
export type CreativeScope = "project" | "anime" | "original";
export const creativeApi = {
  get: (scope: CreativeScope, id: number) => req<{ goal: Partial<CreativeGoal> }>("GET", `/api/creative/${scope}/${id}`),
  analyze: (scope: CreativeScope, id: number, goal: CreativeGoal) => req<{ job_id: string }>("POST", `/api/creative/${scope}/${id}/analyze`, goal),
  save: (scope: CreativeScope, id: number, goal: CreativeGoal) => req<{ goal: CreativeGoal }>("PUT", `/api/creative/${scope}/${id}`, goal),
};
