# tests/test_evals_metrics.py
# -*- coding: utf-8 -*-
"""评测底座·确定性指标:复读句、对白占比、单章与跨章。

不依赖 LLM、不打数据库,只测纯字符串分析。
"""
from __future__ import annotations

from app.evals.metrics import (
    cross_chapter_metrics,
    dialogue_ratio,
    text_metrics,
    within_repeated_sentences,
)


REPEAT_TEXT = (
    "她转身走了两步,又转头看了一眼。她转身走了两步,又转头看了一眼。"
    "夜风很凉,她缩了缩肩。夜风很凉,她缩了缩肩。夜风很凉,她缩了缩肩。"
)

DIALOGUE_TEXT = (
    "老人说:「你来了。」他点头,坐进椅子里。"
    "「路上顺利吗?」她给他倒了杯热水。"
    "他沉默片刻,只说:「还行。」"
)


def test_within_repeated_sentences_finds_duplicates_above_min_chars():
    repeats = within_repeated_sentences(REPEAT_TEXT)
    texts = [t for t, c in repeats]
    # 「夜风很凉,她缩了缩肩。」 重复 ≥ 2
    assert any("夜风很凉" in t for t in texts)
    # 重复句子计数 ≥ 2
    counts = [c for t, c in repeats]
    assert max(counts) >= 2
    # 短于 _MIN_SENT_CHARS 字符的「嗯。」之类不被冤判
    assert all(len(t) >= 6 for t in texts)


def test_dialogue_ratio_uses_paired_quotes_not_loose_double_quotes():
    # 全角引号配对:「」 包起来的算对白
    assert dialogue_ratio(DIALOGUE_TEXT) > 0.2
    # 纯说明文 → 0
    plain = "他走了很长的路,看见山,看见水,看见一只老鹰。"
    assert dialogue_ratio(plain) == 0.0


def test_text_metrics_includes_flavor_and_repeat_counts():
    m = text_metrics(REPEAT_TEXT, target_words=2000)
    assert m["within_repeats"] >= 1
    assert "flavor" in m
    assert m["flavor"]["score"] >= 0
    # 给了 target_words 就算 target_ratio
    assert m["target_ratio"] is not None
    # 段数 ≥ 1
    assert m["paragraphs"] >= 1


def test_cross_chapter_metrics_detects_phrase_spill_over_two_texts():
    # 注意:_split_sentences 把中文逗号也算断句,所以测试文本不能用「,」
    # 直接放两段每章都被打散的句子会消失。只用「。」切,模拟真正可重复的长句
    t1 = "陆辰站在崖头雪粒打在他肩头。夜风从身后吹来他拉了拉衣领。"
    t2 = "陆辰站在崖头雪粒打在他肩头。风更大了他咳了一声。"
    cross = cross_chapter_metrics([t1, t2])
    assert cross["repeated_sentences"] >= 1
    assert cross["chapters"] == 2


def test_cross_chapter_metrics_no_false_positive_for_single_chapter():
    cross = cross_chapter_metrics(["单章测试,只有一句不会跨越。"])
    # 单章不应触发跨章重复
    assert cross["repeated_sentences"] == 0
