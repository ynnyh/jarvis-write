// 篇幅档位显示(docs/22 P0 前置小改):本书档案/总检墙/步骤条缩略共用 scaleDisplay。
// 建库默认 30×3000 不匹配任何预设,必须如实显示「未定」而非伪装成已选篇幅。
import { describe, expect, it } from "vitest";
import { SCALE_PRESETS, scaleDisplay } from "../pages/onboarding/presets";

describe("scaleDisplay 篇幅档位显示", () => {
  it("建库默认 30×3000 显示未定,不伪装成已选", () => {
    const r = scaleDisplay(30, 3000);
    expect(r.decided).toBe(false);
    expect(r.tag).toBe("未定");
    expect(r.text).toContain("未定");
    expect(r.text).not.toContain("30 章");
  });

  it("命中预设显示档位名与万字合计", () => {
    expect(scaleDisplay(20, 3000)).toMatchObject({ tag: "短篇", decided: true });
    expect(scaleDisplay(20, 3000).text).toBe("短篇 · 20 章 × 3000 字 ≈ 6 万字");
    expect(scaleDisplay(60, 3000).tag).toBe("中篇");
    expect(scaleDisplay(150, 3000).text).toContain("≈ 45 万字");
  });

  it("连载档不拼死数,提示按卷滚动规划", () => {
    const r = scaleDisplay(500, 3000);
    expect(r.tag).toBe("连载");
    expect(r.decided).toBe(true);
    expect(r.text).toBe("连载 · 500 章,按卷滚动规划");
  });

  it("不匹配预设的非默认值显示自定并给出合计", () => {
    const r = scaleDisplay(37, 2500);
    expect(r.tag).toBe("自定");
    expect(r.decided).toBe(true);
    expect(r.text).toContain("37 章 × 2500 字");
    expect(r.text).toContain("≈ 9 万字");
  });

  it("四个预设档都能被数值反推命中", () => {
    for (const p of SCALE_PRESETS) {
      expect(scaleDisplay(p.chapters, p.words).tag).toBe(p.label);
      expect(scaleDisplay(p.chapters, p.words).decided).toBe(true);
    }
  });
});
