// paraEdit 单元测试:段落定点替换(正常替换/重复段落不串位/原文守卫/边界)
import { describe, it, expect } from "vitest";
import { spliceParagraph } from "../panels/write/paraEdit";

const TEXT = "第一段。\n\n第二段。\n\n第三段。";

describe("spliceParagraph", () => {
  it("正常替换中间段,其余段落与分隔原样保留", () => {
    expect(spliceParagraph(TEXT, 1, "改过的第二段。"))
      .toBe("第一段。\n\n改过的第二段。\n\n第三段。");
  });

  it("重复段落按序号定位,不会改到前一处", () => {
    const dup = "同一句。\n\n不一样的。\n\n同一句。";
    expect(spliceParagraph(dup, 2, "换新。")).toBe("同一句。\n\n不一样的。\n\n换新。");
    expect(spliceParagraph(dup, 0, "换新。")).toBe("换新。\n\n不一样的。\n\n同一句。");
  });

  it("expected 与当前原文对不上(正文已被别处改动)时返回 null", () => {
    expect(spliceParagraph(TEXT, 1, "X", "旧的第二段快照")).toBeNull();
    expect(spliceParagraph(TEXT, 1, "X", "第二段。")).not.toBeNull();
  });

  it("首段/末段边界都能替换", () => {
    expect(spliceParagraph(TEXT, 0, "新首段。")).toBe("新首段。\n\n第二段。\n\n第三段。");
    expect(spliceParagraph(TEXT, 2, "新末段。")).toBe("第一段。\n\n第二段。\n\n新末段。");
  });

  it("段号越界或替换文本为空时返回 null", () => {
    expect(spliceParagraph(TEXT, 3, "X")).toBeNull();
    expect(spliceParagraph(TEXT, 1, "   ")).toBeNull();
  });

  it("段内前导空白不入替换区间:只动 trim 后的文字,行尾空白原样保留", () => {
    const indented = "  首段。\n\n  次段。  \n\n末段。";
    expect(spliceParagraph(indented, 1, "改。")).toBe("  首段。\n\n  改。  \n\n末段。");
  });
});
