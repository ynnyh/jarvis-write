// chapterStage 单元测试:deriveStage 六阶段判定、优先级与 is_stale 修饰
import { describe, it, expect } from "vitest";
import { deriveStage, STAGE_SPEC, type ChapterStage } from "../panels/write/chapterStage";
import type { ChapterBrief } from "../api";

function brief(status: string, is_stale = false): ChapterBrief {
  return { chapter_number: 3, status, word_count: 2000, is_stale };
}

const STAGES: ChapterStage[] = ["unselected", "empty", "generating", "blocked", "review", "approved"];

describe("deriveStage", () => {
  it("chapterNum 为 null 时恒为 unselected(无视 brief / genJob)", () => {
    expect(deriveStage({ chapterNum: null, currentBrief: undefined, genJob: null })).toBe("unselected");
    expect(deriveStage({ chapterNum: null, currentBrief: brief("approved"), genJob: { num: 3, stage: "草稿" } }))
      .toBe("unselected");
  });

  it("无 currentBrief(该章从未生成)为 empty", () => {
    expect(deriveStage({ chapterNum: 3, currentBrief: undefined, genJob: null })).toBe("empty");
  });

  it("未识别的 status 兜底为 empty", () => {
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("draft"), genJob: null })).toBe("empty");
    expect(deriveStage({ chapterNum: 3, currentBrief: brief(""), genJob: null })).toBe("empty");
  });

  it("quarantined 为 blocked,pending_review 为 review", () => {
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("quarantined"), genJob: null })).toBe("blocked");
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("pending_review"), genJob: null })).toBe("review");
  });

  it("approved 与存量 finalized 都为 approved", () => {
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("approved"), genJob: null })).toBe("approved");
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("finalized"), genJob: null })).toBe("approved");
  });

  it("本章 genJob(num=章号)在跑时为 generating", () => {
    expect(deriveStage({ chapterNum: 3, currentBrief: undefined, genJob: { num: 3, stage: "草稿" } }))
      .toBe("generating");
  });

  it("连写队列 genJob(num=0)在跑时为 generating", () => {
    expect(deriveStage({ chapterNum: 3, currentBrief: undefined, genJob: { num: 0, stage: "连写" } }))
      .toBe("generating");
  });

  it("其他章的 genJob 不影响本章阶段", () => {
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("approved"), genJob: { num: 4, stage: "草稿" } }))
      .toBe("approved");
    expect(deriveStage({ chapterNum: 3, currentBrief: undefined, genJob: { num: 4, stage: "草稿" } }))
      .toBe("empty");
  });

  it("优先级:generating 覆盖 blocked / review / approved", () => {
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("quarantined"), genJob: { num: 3, stage: "重写" } }))
      .toBe("generating");
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("pending_review"), genJob: { num: 0, stage: "连写" } }))
      .toBe("generating");
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("approved"), genJob: { num: 3, stage: "润色" } }))
      .toBe("generating");
  });

  it("is_stale 只是修饰,不改变推断出的阶段", () => {
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("approved", true), genJob: null })).toBe("approved");
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("pending_review", true), genJob: null })).toBe("review");
    expect(deriveStage({ chapterNum: 3, currentBrief: brief("quarantined", true), genJob: null })).toBe("blocked");
  });
});

describe("STAGE_SPEC", () => {
  it("覆盖全部六个阶段且 label/guide 非空", () => {
    for (const s of STAGES) {
      expect(STAGE_SPEC[s].label).toBeTruthy();
      expect(STAGE_SPEC[s].guide).toBeTruthy();
    }
    expect(Object.keys(STAGE_SPEC).sort()).toEqual([...STAGES].sort());
  });
});
