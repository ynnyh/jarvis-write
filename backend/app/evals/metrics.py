# app/evals/metrics.py
# -*- coding: utf-8 -*-
"""确定性质量指标:零 LLM、秒回、任何机器上结果一致。

全部复用线上同一套判据(ai_flavor_report / repetition),评测出来的数字和用户在
质感看板上看到的是同一口径——评测不另造一把尺子。

单章指标 text_metrics:篇幅与目标偏差、段/句结构、对白占比、章内复读句、AI 味
(指数 + 各类命中数 + 节奏统计)。跨章指标 cross_chapter_metrics:跨章逐字重复句、
高频 n-gram——这是「连续章节写同一描写」的数字化。
"""
from __future__ import annotations

import re
from collections import Counter

from app.engines.consistency.repetition import (
    find_repeated_phrases,
    find_repeated_sentences,
)
from app.engines.polish.ai_flavor import ai_flavor_report

_QUOTE_PAIRS = {"“": "”", "「": "」", "『": "』"}
_PARA_SPLIT = re.compile(r"\n+")
_SENT_SPLIT = re.compile(r"[。!?!?…]+")
_HANZI_RE = re.compile(r"[一-鿿]")
_MIN_SENT_CHARS = 6  # 短于此的句子(「嗯。」「走。」)不参与复读统计,避免冤枉对白


def dialogue_ratio(text: str) -> float:
    """配对引号内字符占全文比例(0-1)。粗口径,够看「对白密度」的趋势。"""
    total = len(text.strip())
    if not total:
        return 0.0
    inside = 0
    closer: str | None = None
    for ch in text:
        if closer is None:
            closer = _QUOTE_PAIRS.get(ch)
        elif ch == closer:
            closer = None
        else:
            inside += 1
    return round(inside / total, 3)


def within_repeated_sentences(text: str) -> list[tuple[str, int]]:
    """同一章里逐字重复的句子(>= _MIN_SENT_CHARS 字),[(句, 次数)] 按次数降序。

    repetition.find_repeated_sentences 是「跨章」口径(单章内先去重),测不到章内复读,
    所以这里单独数。
    """
    counter: Counter[str] = Counter(
        s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) >= _MIN_SENT_CHARS
    )
    hits = [(s, c) for s, c in counter.items() if c >= 2]
    hits.sort(key=lambda x: (-x[1], -len(x[0])))
    return hits


def text_metrics(text: str, target_words: int | None = None) -> dict:
    """单章确定性指标。target_words 给了就多算一项 target_ratio(实际/目标)。"""
    text = text or ""
    report = ai_flavor_report(text)
    m = report.metrics or {}
    paragraphs = [p for p in _PARA_SPLIT.split(text) if p.strip()]
    sentences = [s for s in _SENT_SPLIT.split(text) if s.strip()]
    hanzi = len(_HANZI_RE.findall(text))
    within = within_repeated_sentences(text)
    out: dict = {
        "chars": len(text.strip()),
        "hanzi": hanzi,
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "avg_sentence_len": round(hanzi / len(sentences), 1) if sentences else 0.0,
        "dialogue_ratio": dialogue_ratio(text),
        "within_repeats": len(within),
        "within_repeats_top": [{"text": s, "count": c} for s, c in within[:3]],
        "flavor": {
            "score": round(float(report.score), 2),
            "categories": {
                k: v["count"] for k, v in report.categories.items() if v.get("count")
            },
            "burstiness": m.get("burstiness"),
            "burstiness_flag": bool(m.get("burstiness_flag")),
            "metronome_groups": int(m.get("metronome_groups") or 0),
            "tail_summary_count": int(m.get("tail_summary_count") or 0),
            "para_uniform": bool(m.get("para_uniform")),
            "de_ratio": m.get("de_ratio"),
            "novelty_4gram": m.get("novelty_4gram"),
            "long_repeats": len(m.get("repeats") or []),
        },
    }
    if target_words:
        out["target_ratio"] = round(out["chars"] / target_words, 2)
    return out


def cross_chapter_metrics(texts: list[str]) -> dict:
    """跨章指标:逐字重复句与高频 n-gram(与写新章前注入的「避免清单」同一判据)。"""
    texts = [t for t in texts if (t or "").strip()]
    sentences = find_repeated_sentences(texts) if len(texts) >= 2 else []
    phrases = find_repeated_phrases(texts) if texts else []
    return {
        "chapters": len(texts),
        "repeated_sentences": len(sentences),
        "repeated_sentences_top": [{"text": s, "count": c} for s, c in sentences[:5]],
        "repeated_phrases": len(phrases),
        "repeated_phrases_top": [{"text": p, "count": c} for p, c in phrases[:8]],
    }
