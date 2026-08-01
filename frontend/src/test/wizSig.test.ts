// 向导候选签名:同输入同签名、上游变更即过期、过期提示点出变化字段
import { describe, it, expect } from "vitest";
import { conceptSig, titleSig, isStale, conceptStaleText, titleStaleText } from "../pages/wizSig";
import { EMPTY_CONCEPT } from "../api";

describe("conceptSig", () => {
  it("相同语义输入(key 顺序不同 / 含空值)得到相同签名", () => {
    const a = conceptSig("复仇故事", { genre: "武侠", pace: "快" });
    const b = conceptSig("复仇故事", { pace: "快", genre: "武侠", tone: "" });
    expect(a).toBe(b);
  });

  it("灵感或题材变化 → 签名变化", () => {
    const base = conceptSig("复仇故事", { genre: "武侠" });
    expect(conceptSig("寻宝故事", { genre: "武侠" })).not.toBe(base);
    expect(conceptSig("复仇故事", { genre: "科幻" })).not.toBe(base);
  });
});

describe("titleSig / isStale", () => {
  const concept = { ...EMPTY_CONCEPT, logline: "镖师开箱" };

  it("概念/题材变化 → 过期;签名一致 → 不过期", () => {
    const sig = titleSig("", "武侠", concept);
    expect(isStale(["甲"], sig, titleSig("", "武侠", { ...concept, logline: "改了的" }))).toBe(true);
    expect(isStale(["甲"], sig, titleSig("", "科幻", concept))).toBe(true);
    expect(isStale(["甲"], sig, sig)).toBe(false);
  });

  it("无候选、候选为空或无签名 → 不过期(避免误报)", () => {
    const sig = titleSig("", "武侠", concept);
    expect(isStale(null, sig, "x")).toBe(false);
    expect(isStale([], sig, "x")).toBe(false);
    expect(isStale(["甲"], null, "x")).toBe(false);
  });
});

describe("过期提示文案", () => {
  it("概念屏:题材/灵感变化分别点出", () => {
    const sig = conceptSig("复仇故事", { genre: "武侠" });
    expect(conceptStaleText(sig, "复仇故事", { genre: "科幻" })).toContain("题材");
    expect(conceptStaleText(sig, "寻宝故事", { genre: "武侠" })).toContain("灵感");
  });

  it("书名屏:概念变化点出概念", () => {
    const c = { ...EMPTY_CONCEPT, logline: "镖师开箱" };
    const sig = titleSig("", "武侠", c);
    expect(titleStaleText(sig, "", "武侠", { ...c, logline: "别的" })).toContain("概念");
    expect(titleStaleText(sig, "", "科幻", c)).toContain("题材");
  });
});
