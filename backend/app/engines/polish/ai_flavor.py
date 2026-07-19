# app/engines/polish/ai_flavor.py
# -*- coding: utf-8 -*-
"""AI 味量化检测:纯规则统计,不调 LLM。

检测维度(对应用户痛点"去 AI 味"):
- 造句定式:"不是……而是……""与其说……不如说"等
- 比喻连接词密度:仿佛/宛如/好似/彷佛
- 排比堆砌:"是A,是B,是C"三连式
- 情绪标签直喊:"感到/心中 + 抽象情绪词"
- 金句癖:段末短句率(每段结尾都是"点题短句"是 AI 特征)

产出报告 + 分数,用于:润色前后对比展示"AI 味降了多少"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 造句定式
_PATTERNS = {
    "不是A而是B": r"不是[^。,,;;]{1,20}[,,]?\s*而是",
    "与其说不如说": r"与其说",
    "仿佛式比喻": r"仿佛|宛如|彷佛|好似|恍若",
    "像X又像Y": r"像[^。,,]{1,15}[,,]\s*又像",
    "某种意义上": r"某种(意义|程度)上",
    "值得一提": r"值得一提|不得不说|毫无疑问",
}

# 情绪标签:感到/觉得/心中 + 直白情绪词
_EMOTION_LABEL = re.compile(
    r"(感到|觉得|心中|心里|内心)[^。]{0,6}"
    r"(绝望|愤怒|悲伤|喜悦|恐惧|无比|难以言喻|五味杂陈|百感交集|复杂)"
)

# 排比:是X,是Y,是Z
_PARALLEL = re.compile(r"是[^。,,;;]{2,12}[,,]\s*是[^。,,;;]{2,12}[,,]\s*是")


@dataclass
class FlavorReport:
    total_chars: int
    hits: dict[str, int] = field(default_factory=dict)
    score: float = 0.0  # 每千字 AI 味命中数,越低越好

    def summary(self) -> str:
        if not self.hits:
            return f"AI 味指数 {self.score:.1f}/千字(未检出明显 AI 腔)"
        top = "、".join(f"{k}×{v}" for k, v in sorted(self.hits.items(), key=lambda x: -x[1])[:5])
        return f"AI 味指数 {self.score:.1f}/千字({top})"


def ai_flavor_report(text: str) -> FlavorReport:
    """统计文本的 AI 腔特征。score = 每千字命中数。"""
    hits: dict[str, int] = {}
    for name, pat in _PATTERNS.items():
        n = len(re.findall(pat, text))
        if n:
            hits[name] = n
    n = len(_EMOTION_LABEL.findall(text))
    if n:
        hits["情绪标签直喊"] = n
    n = len(_PARALLEL.findall(text))
    if n:
        hits["排比堆砌"] = n

    total = max(len(text), 1)
    score = sum(hits.values()) / total * 1000
    return FlavorReport(total_chars=len(text), hits=hits, score=round(score, 2))
