// jobLabel 单元测试:job kind 字符串 → 人话标签。
// 重点锁两类易回归的行为:
//  1) 带章号的 kind 提取的是「第二个数字(章号)」而非第一个(pid);
//  2) 分支顺序敏感 —— polish-segment 不能被数字版 polish 规则先吞掉,
//     inspire-refine 必须排在 inspire(前缀)之前,否则标签会错。
import { describe, it, expect } from "vitest";
import { jobLabel } from "../ui/TaskCenter";

describe("jobLabel", () => {
  it("章节生成:提取第二个数字(章号)而非 pid", () => {
    expect(jobLabel("chapter-3-5")).toBe("第 5 章生成");
    // pid 多位也不串到章号上
    expect(jobLabel("chapter-12-7")).toBe("第 7 章生成");
  });

  it("连写队列单独成标签,不被章节生成规则匹配", () => {
    expect(jobLabel("chapter-3-queue")).toBe("连写队列");
  });

  it("各类带章号任务分别映射,章号取第二个数字", () => {
    expect(jobLabel("re-extract-3-5")).toBe("第 5 章一致性同步");
    expect(jobLabel("polish-3-5")).toBe("第 5 章润色");
    expect(jobLabel("impact-3-5")).toBe("第 5 章影响分析");
    expect(jobLabel("review-3-5")).toBe("第 5 章主编评分");
    expect(jobLabel("proofread-3-5")).toBe("第 5 章校对");
  });

  it("前缀类任务(无章号)映射固定标签", () => {
    expect(jobLabel("architecture-3")).toBe("架构生成");
    expect(jobLabel("blueprint-3")).toBe("蓝图生成");
    expect(jobLabel("cascade-3")).toBe("级联重生成");
    expect(jobLabel("directive-3")).toBe("指令改分析");
    expect(jobLabel("synopsis-3")).toBe("简介生成");
  });

  it("顺序敏感:polish-segment 不被数字版 polish 规则吞掉", () => {
    // /^polish-\d+-(\d+)$/ 因 segment 非数字而不匹配,须落到 startsWith 分支
    expect(jobLabel("polish-segment-3-5")).toBe("选段润色");
    expect(jobLabel("polish-segment")).toBe("选段润色");
  });

  it("顺序敏感:inspire-refine 先于 inspire 前缀命中", () => {
    expect(jobLabel("inspire-refine-1")).toBe("概念改写");
    expect(jobLabel("inspire")).toBe("灵感方案");
    expect(jobLabel("inspire-2")).toBe("灵感方案");
  });

  it("未识别的 kind 原样返回(兜底,不抛错)", () => {
    expect(jobLabel("gate-release-3-5")).toBe("gate-release-3-5");
    expect(jobLabel("diag-3")).toBe("diag-3");
    expect(jobLabel("")).toBe("");
  });
});
