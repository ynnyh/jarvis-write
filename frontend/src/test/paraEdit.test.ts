// paraEdit 单元测试:段落定点替换(正常替换/重复段落不串位/原文守卫/边界)
import { describe, it, expect } from "vitest";
import { spliceParagraph, spliceSelectionInParagraph } from "../panels/write/paraEdit";

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

describe("spliceSelectionInParagraph", () => {
  const SEL = "他很快地跑。\n\n她慢慢地走。"; // 段0: 他(0)很(1)快(2)地(3)跑(4)。(5)

  it("替换段内选区子串,段落其余字与分隔原样保留", () => {
    expect(spliceSelectionInParagraph(SEL, 0, 1, 3, "非常迅速", "他很快地跑。"))
      .toBe("他非常迅速地跑。\n\n她慢慢地走。");
  });

  it("expectedPara 与当前段落对不上(段落已被别处改动)时返回 null", () => {
    expect(spliceSelectionInParagraph(SEL, 0, 1, 3, "X", "旧的段落快照")).toBeNull();
    expect(spliceSelectionInParagraph(SEL, 0, 1, 3, "X", "他很快地跑。")).not.toBeNull();
  });

  it("from/to 越界或 from>=to 时返回 null", () => {
    expect(spliceSelectionInParagraph(SEL, 0, 0, 7, "X", "他很快地跑。")).toBeNull(); // to 超段长
    expect(spliceSelectionInParagraph(SEL, 0, 3, 3, "X", "他很快地跑。")).toBeNull(); // 空选区
    expect(spliceSelectionInParagraph(SEL, 0, -1, 2, "X", "他很快地跑。")).toBeNull(); // from<0
  });

  it("replacement 为空(或纯空白)时返回 null,并对 replacement 做 trim", () => {
    expect(spliceSelectionInParagraph(SEL, 0, 1, 3, "   ", "他很快地跑。")).toBeNull();
    expect(spliceSelectionInParagraph(SEL, 0, 1, 3, "  换  ", "他很快地跑。"))
      .toBe("他换地跑。\n\n她慢慢地走。");
  });

  it("重复段落按序号定位,选区替换不串到前一处同文段", () => {
    const dup = "重复。\n\n中间。\n\n重复。"; // 段2: 重(0)复(1)。(2)
    expect(spliceSelectionInParagraph(dup, 2, 0, 2, "崭新", "重复。"))
      .toBe("重复。\n\n中间。\n\n崭新。");
  });

  it("段号越界返回 null", () => {
    expect(spliceSelectionInParagraph(SEL, 5, 0, 1, "X", "他很快地跑。")).toBeNull();
  });
});
