# backend/scripts/stage5_test.py
# -*- coding: utf-8 -*-
"""阶段 5 验证:润色引擎(mock LLM + 真实 AI 味检测)。

验收:润色改文笔不改情节;去 AI 味有量化;事实校验能抓违规。

用法: .venv/Scripts/python -m scripts.stage5_test
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    results.append(ok)
    return ok


# ---------- 1. AI 味检测(纯规则,真实) ----------
def test_ai_flavor() -> None:
    from app.engines.polish.ai_flavor import ai_flavor_report

    ai_text = (
        "他感到无比绝望。这不是结束,而是开始。夜色仿佛一张网,"
        "宛如深渊,又像命运的手。他是战士,是孤儿,是复仇者。"
        "某种意义上,这一切早已注定。"
    )
    r = ai_flavor_report(ai_text)
    ok = (r.score > 0 and "不是A而是B" in r.hits
          and "仿佛式比喻" in r.hits and "排比堆砌" in r.hits
          and "情绪标签直喊" in r.hits)
    check("AI味检测: 命中多种定式", ok, r.summary())

    human_text = "他把烟摁灭在铁皮桌角。窗外的雨还在下,楼下有人在骂街。他数了数口袋里的钱,不够。"
    r2 = ai_flavor_report(human_text)
    check("AI味检测: 干净文本低分", r2.score == 0.0, r2.summary())

    check("AI味检测: 润色后应降分", r2.score < r.score)


# ---------- 2. 润色主流程(mock LLM) ----------
class SeqAdapter:
    """按 prompt 特征返回对应 mock 结果。"""
    def __init__(self):
        self.prompts = []

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        if "抽取" in prompt and "事实清单" in prompt:
            return '{"facts": ["凯恩捡到芯片", "芯片里有伊芙的人格", "维修铺被盯上"]}'
        if "润色铁律" in prompt:
            # 返回一个"去了AI味"的干净稿
            return "凯恩从废弃义体里抠出那枚芯片。屏幕亮了一下,一个声音在他颅骨内侧响起。门外,有人在盯着这间铺子。"
        if "check" in prompt or "violations" in prompt or "检查润色" in prompt:
            return '{"violations": []}'
        return "{}"


async def test_polish_flow() -> None:
    from app.engines.polish import polisher as pmod

    adapter = SeqAdapter()
    ai_draft = (
        "凯恩感到无比震撼地从义体中取出芯片。屏幕仿佛活过来一般闪烁,"
        "宛如一颗心脏。这不是普通的芯片,而是承载着某个灵魂的容器。"
        "门外,危险正在逼近。"
    )
    with patch.object(pmod, "get_adapter_for", return_value=adapter):
        result = await pmod.polish_text(ai_draft, {"polish_style": ["去AI味", "精简冗余"]})

    check("润色: 抽取锁定事实", result["locked_facts"] == ["凯恩捡到芯片", "芯片里有伊芙的人格", "维修铺被盯上"])
    check("润色: 产出润色稿", "抠出" in result["polished"] or "芯片" in result["polished"])
    check("润色: 无事实违规", result["violations"] == [])

    # 倾向注入:去AI味 + 精简冗余的指令进了润色 prompt
    polish_prompt = [p for p in adapter.prompts if "润色铁律" in p][0]
    ok = ("去除 AI 腔" in polish_prompt and "机器写作腔调" in polish_prompt
          and "凝练" in polish_prompt)
    check("润色: 去AI味规则+风格标签注入", ok)

    # AI 味前后对比:后 < 前
    ok = result["flavor_after"]["score"] < result["flavor_before"]["score"]
    check("润色: AI味量化下降",
          ok, f"{result['flavor_before']['score']} → {result['flavor_after']['score']}")


# ---------- 3. 事实校验能抓违规 ----------
async def test_fact_violation() -> None:
    from app.engines.polish import polisher as pmod

    class ViolatingAdapter:
        def __init__(self): self.prompts = []
        async def ask(self, prompt, system=None):
            self.prompts.append(prompt)
            if "事实清单" in prompt:
                return '{"facts": ["张三活着"]}'
            if "润色铁律" in prompt:
                return "李四死了。"  # 擅自改了剧情
            if "violations" in prompt or "检查" in prompt:
                return '{"violations": [{"fact": "张三活着", "problem": "润色稿把张三写死了"}]}'
            return "{}"

    with patch.object(pmod, "get_adapter_for", return_value=ViolatingAdapter()):
        result = await pmod.polish_text("张三站在门口。")
    ok = len(result["violations"]) == 1 and "写死" in result["violations"][0]["problem"]
    check("润色: 事实违规被抓到", ok, str(result["violations"]))


# ---------- 4. 边界 ----------
async def test_bounds() -> None:
    from app.engines.polish import polisher as pmod
    try:
        await pmod.polish_text("   ")
        check("润色: 空文本报错", False)
    except ValueError:
        check("润色: 空文本报错", True)

    try:
        await pmod.polish_text("字" * 12001)
        check("润色: 超长报错", False)
    except ValueError:
        check("润色: 超长报错", True)


def main() -> int:
    print("=" * 56)
    print("阶段 5 验证:润色引擎(锁情节 + 去AI味)")
    print("=" * 56)
    test_ai_flavor()
    asyncio.run(test_polish_flow())
    asyncio.run(test_fact_violation())
    asyncio.run(test_bounds())
    print("-" * 56)
    passed, total = sum(results), len(results)
    print(f"结果: {passed}/{total} 通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
