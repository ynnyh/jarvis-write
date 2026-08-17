// charDiff 单元测试:字符级 diff(重建不变量/插入/删除/替换/合并/码点)+ 整章逐段 diff
import { describe, it, expect } from "vitest";
import { diffChars, diffParagraphs, DiffOp } from "../panels/write/charDiff";

// 由 diff 结果重建两端原文:same+del 还原 a,same+ins 还原 b(最强通用不变量)
function rebuildOld(ops: DiffOp[]): string {
  return ops.filter((o) => o.type !== "ins").map((o) => o.text).join("");
}
function rebuildNew(ops: DiffOp[]): string {
  return ops.filter((o) => o.type !== "del").map((o) => o.text).join("");
}

describe("diffChars", () => {
  it("完全相同 → 单个 same;两端皆空 → 空序列", () => {
    expect(diffChars("他走进城门。", "他走进城门。")).toEqual([{ type: "same", text: "他走进城门。" }]);
    expect(diffChars("", "")).toEqual([]);
  });

  it("一侧为空 → 纯插入 / 纯删除", () => {
    expect(diffChars("", "新增。")).toEqual([{ type: "ins", text: "新增。" }]);
    expect(diffChars("删掉。", "")).toEqual([{ type: "del", text: "删掉。" }]);
  });

  it("中间插入:公共前后缀保留,只插入新增部分", () => {
    expect(diffChars("他走进城门。", "他慢慢走进城门。")).toEqual([
      { type: "same", text: "他" },
      { type: "ins", text: "慢慢" },
      { type: "same", text: "走进城门。" },
    ]);
  });

  it("中间删除:只删掉去掉的部分", () => {
    expect(diffChars("他慢慢走进城门。", "他走进城门。")).toEqual([
      { type: "same", text: "他" },
      { type: "del", text: "慢慢" },
      { type: "same", text: "走进城门。" },
    ]);
  });

  it("替换:重建不变量对任意改写成立(del+same=旧,ins+same=新)", () => {
    const cases: [string, string][] = [
      ["夜色四合,他站在门口。", "暮色低垂,他倚在门边,一言不发。"],
      ["ABCDEF", "AXCYEZ"],
      ["完全不同的一句话", "另起炉灶重写过"],
    ];
    for (const [a, b] of cases) {
      const ops = diffChars(a, b);
      expect(rebuildOld(ops)).toBe(a);
      expect(rebuildNew(ops)).toBe(b);
    }
  });

  it("合并同类相邻:不产生逐字碎片(相邻同型 op 合成一段)", () => {
    const ops = diffChars("ac", "axyzc");
    // 期望 same a / ins xyz / same c —— 中间三个新增字合成一个 ins,而非三个
    expect(ops).toEqual([
      { type: "same", text: "a" },
      { type: "ins", text: "xyz" },
      { type: "same", text: "c" },
    ]);
  });

  it("按码点切分:emoji(代理对)整体处理,不被拆成半个字符", () => {
    const ops = diffChars("😀甲", "😀乙");
    expect(ops).toEqual([
      { type: "same", text: "😀" },
      { type: "del", text: "甲" },
      { type: "ins", text: "乙" },
    ]);
    // 重建后 emoji 完好
    expect(rebuildNew(ops)).toBe("😀乙");
  });
});

describe("diffParagraphs", () => {
  const OLD = "第一段原样。\n\n第二段要改。\n\n第三段原样。";

  it("只改中间一段:首尾 same,中间 changed 带字符级 ops", () => {
    const res = diffParagraphs(OLD, "第一段原样。\n\n第二段改过了。\n\n第三段原样。");
    expect(res.map((r) => r.status)).toEqual(["same", "changed", "same"]);
    const mid = res[1];
    expect(mid.oldIdx).toBe(1);
    expect(mid.newIdx).toBe(1);
    expect(mid.ops).not.toBeNull();
    expect(rebuildOld(mid.ops!)).toBe("第二段要改。");
    expect(rebuildNew(mid.ops!)).toBe("第二段改过了。");
  });

  it("新增一段:末尾 added,原有段落判为 same", () => {
    const res = diffParagraphs(OLD, OLD + "\n\n第四段是新加的。");
    expect(res.map((r) => r.status)).toEqual(["same", "same", "same", "added"]);
    expect(res[3].newText).toBe("第四段是新加的。");
    expect(res[3].oldIdx).toBeNull();
  });

  it("删掉一段:该段判为 removed", () => {
    const res = diffParagraphs(OLD, "第一段原样。\n\n第三段原样。");
    expect(res.map((r) => r.status)).toEqual(["same", "removed", "same"]);
    expect(res[1].oldText).toBe("第二段要改。");
    expect(res[1].newIdx).toBeNull();
  });
});
