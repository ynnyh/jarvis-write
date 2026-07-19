# app/engines/consistency/repetition.py
# -*- coding: utf-8 -*-
"""重复用词检测(借鉴 KazKozDev coherenceManager 的 RepetitionConstraints)。

纯 Python n-gram 统计,不调 LLM:扫描最近几章正文,找出高频重复的
词组/短语,生成"避免清单"注入写作 Prompt,防止 AI 老用同一个比喻。
"""
from __future__ import annotations

import re
from collections import Counter

# 检测的 n-gram 长度(中文字符),过短误报多,过长命中少
_NGRAM_SIZES = (4, 5, 6)
_MIN_REPEAT = 3          # 出现 >= 3 次算重复
_MAX_ITEMS = 12          # 避免清单最多几条
# 常见虚词开头的组合不算(误报过滤)
_STOP_HEADS = tuple("的了是在有和与就都也又还被把对从向让")


def _clean_text(text: str) -> str:
    return re.sub(r"[\s\W a-zA-Z0-9]+", "", text)


def find_repeated_phrases(texts: list[str]) -> list[tuple[str, int]]:
    """跨章统计高频 n-gram,返回 [(短语, 次数)],按次数降序。"""
    joined = _clean_text("".join(texts))
    counter: Counter[str] = Counter()
    for n in _NGRAM_SIZES:
        for i in range(len(joined) - n + 1):
            gram = joined[i : i + n]
            if gram[0] in _STOP_HEADS:
                continue
            counter[gram] += 1

    # 去掉被更长短语覆盖的子串(如"心如刀绞"覆盖"如刀绞")
    frequent = [(g, c) for g, c in counter.items() if c >= _MIN_REPEAT]
    frequent.sort(key=lambda x: (-x[1], -len(x[0])))
    kept: list[tuple[str, int]] = []
    for gram, cnt in frequent:
        if any(gram in longer for longer, _ in kept):
            continue
        kept.append((gram, cnt))
        if len(kept) >= _MAX_ITEMS:
            break
    return kept


def avoid_block(texts: list[str]) -> str:
    """渲染注入 Prompt 的"避免重复"块;无重复时返回空串。"""
    repeated = find_repeated_phrases(texts)
    if not repeated:
        return ""
    lines = [f"- “{g}”(近期已用 {c} 次)" for g, c in repeated]
    return "【避免重复的表达】以下短语近期使用过于频繁,请换用不同表达:\n" + "\n".join(lines)
