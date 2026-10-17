// steps.parseStep 兼容映射:开书前置重构(docs/32)把六屏砍成四屏后,存量书的
// setup_state 与老书签 URL 必须还能落回一屏——落错屏的表现是「点继续创建进了一片
// 空白/报错」,这条用例就是钉住这张映射表。
import { describe, expect, it } from "vitest";
import { SETUP_STATE, STEP_LABEL, STEP_ORDER, parseStep } from "../pages/onboarding/steps";

describe("parseStep 兼容映射", () => {
  it("新四步原样认", () => {
    expect(STEP_ORDER).toEqual(["mode", "idea", "intent", "sample"]);
    for (const s of STEP_ORDER) expect(parseStep(s)).toBe(s);
  });

  it("旧六步:产物层与点火屏落样稿,方案屏落理解", () => {
    expect(parseStep("brief")).toBe("intent");
    expect(parseStep("concept")).toBe("sample");
    expect(parseStep("setup")).toBe("sample");
    expect(parseStep("launch")).toBe("sample");
    expect(parseStep("generating")).toBe("sample");
  });

  it("更早的八步路由(生成前配置)落理解屏", () => {
    for (const s of ["genre", "tone", "title", "scale", "confirm"]) {
      expect(parseStep(s), s).toBe("intent");
    }
  });

  it("未知/空 → 想法屏", () => {
    expect(parseStep(undefined)).toBe("idea");
    expect(parseStep("")).toBe("idea");
    expect(parseStep("who-knows")).toBe("idea");
  });

  it("每步都有中文标签与服务端 setup_state 值", () => {
    for (const s of STEP_ORDER) {
      expect(STEP_LABEL[s]).toBeTruthy();
      expect(SETUP_STATE[s]).toBeTruthy();
    }
  });
});
