// entityLink 单元测试:索引构建(名+别名/去重/长度过滤/长词优先)与段落非重叠切分。
import { describe, it, expect } from "vitest";
import { CharacterCard } from "../api";
import { buildEntityIndex, segmentParagraph, entitySummary, isEntitySeg } from "../panels/write/entityLink";

function char(over: Partial<CharacterCard> & { name: string }): CharacterCard {
  return {
    id: 1, aliases: [], entity_type: "character", retired: false, profile: "",
    key_facts: [], appearance_chapters: [], relations: [], ...over,
  };
}

describe("buildEntityIndex", () => {
  it("收名字与别名,长度<2 的词条丢弃(单字噪声)", () => {
    const idx = buildEntityIndex([char({ name: "林渊", aliases: ["小渊", "王"] })]);
    expect(idx.map((t) => t.term)).toEqual(expect.arrayContaining(["林渊", "小渊"]));
    expect(idx.map((t) => t.term)).not.toContain("王"); // 单字丢弃
  });

  it("同名词条去重(名字先于别名),按长度降序", () => {
    const a = char({ id: 1, name: "叶清歌", aliases: ["清歌"] });
    const b = char({ id: 2, name: "清歌" }); // 与 a 的别名撞词 → 后者被去重丢弃
    const idx = buildEntityIndex([a, b]);
    const terms = idx.map((t) => t.term);
    expect(terms).toEqual(["叶清歌", "清歌"]); // 长词在前;"清歌" 只留一份且归属 a
    expect(idx.find((t) => t.term === "清歌")!.entity.id).toBe(1);
  });

  it("空/仅空白名字与空列表安全", () => {
    expect(buildEntityIndex([])).toEqual([]);
    expect(buildEntityIndex([char({ name: "  " })])).toEqual([]);
  });
});

describe("segmentParagraph", () => {
  const idx = buildEntityIndex([
    char({ id: 1, name: "林渊", aliases: ["小渊"] }),
    char({ id: 2, name: "叶清歌" }),
  ]);

  it("无命中/空索引:返回单个纯文本片段(不包裹)", () => {
    expect(segmentParagraph("今天天气不错。", idx)).toEqual([{ text: "今天天气不错。" }]);
    expect(segmentParagraph("林渊", [])).toEqual([{ text: "林渊" }]);
  });

  it("单命中:文本-实体-文本 三段,实体段携带人物卡", () => {
    const segs = segmentParagraph("于是林渊笑了。", idx);
    expect(segs).toHaveLength(3);
    expect(segs[0]).toEqual({ text: "于是" });
    expect(isEntitySeg(segs[1])).toBe(true);
    expect(segs[1]).toMatchObject({ text: "林渊" });
    expect((segs[1] as { entity: CharacterCard }).entity.id).toBe(1);
    expect(segs[2]).toEqual({ text: "笑了。" });
  });

  it("多命中(含别名),相邻实体不并入纯文本", () => {
    const segs = segmentParagraph("小渊看着叶清歌", idx);
    expect(segs.filter(isEntitySeg).map((s) => s.text)).toEqual(["小渊", "叶清歌"]);
    expect(segs.map((s) => s.text).join("")).toBe("小渊看着叶清歌"); // 无损拼回
  });

  it("长词优先:'叶清歌' 不被更短的词条从中间截断", () => {
    const withShort = buildEntityIndex([
      char({ id: 2, name: "叶清歌" }),
      char({ id: 3, name: "清歌" }),
    ]);
    const segs = segmentParagraph("叶清歌来了", withShort);
    expect(segs[0]).toMatchObject({ text: "叶清歌" }); // 整体命中,而非「叶」+「清歌」
    expect((segs[0] as { entity: CharacterCard }).entity.id).toBe(2);
  });
});

describe("entitySummary", () => {
  it("带类型与简介;退场标注;无简介只给名字与类型", () => {
    expect(entitySummary(char({ name: "林渊", profile: "少年剑客" }))).toBe("林渊(人物):少年剑客");
    expect(entitySummary(char({ name: "林渊", retired: true }))).toBe("林渊(人物 · 已退场)");
  });

  it("简介超长截断到 80 字加省略号", () => {
    const long = "刀".repeat(120);
    const s = entitySummary(char({ name: "林渊", profile: long }));
    expect(s.endsWith("…")).toBe(true);
    expect(s.length).toBeLessThan(90);
  });
});
