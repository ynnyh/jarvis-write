# tests/test_body_reactions.py
# -*- coding: utf-8 -*-
"""身体反应复读的判据(v0.58 文风升级,频次维度而不是禁词表)。

守两条最容易回退的语义:
1. **单独一次不罚**——用户原句「他攥紧了拳头,指甲渗进肉里,流出血来」是**一次**拿身体
   反应代替情绪,不是三次(按子模式数会数成三处,本轮实测踩到过);
2. **复读才罚,且只计超出的那几次**——阈值按族给(手部痛感 2、咬唇 3),第一次永远不算。
"""
from __future__ import annotations

import pytest

from app.engines.polish.ai_flavor import (
    _BODY_REACTIONS_CATEGORY,
    ai_flavor_report,
    body_reaction_families,
    body_reaction_hits,
)

SINGLE = "他攥紧了拳头,指甲渗进肉里,流出血来。他慢慢松开手,把刀放回鞘里。"
TWICE = (
    "他攥紧了拳头,指甲渗进肉里。\n\n"
    "过了半炷香,他又捏住拳头,指甲渗入肉里,掌心一片湿。\n\n"
    "窗外的雪停了,他松开手,血珠落在案上。"
)
BITES = (
    "她咬紧了牙关,没出声。\n\n"
    "他咬破了嘴唇,血味漫开。\n\n"
    "她咬住了下唇,把话咽回去。\n\n"
    "他终于咬了咬牙,转身走了。"
)


def test_single_occurrence_is_not_penalized():
    """用户原句只算一次:不罚、不进分类、不给命中明细。"""
    report = ai_flavor_report(SINGLE)
    assert _BODY_REACTIONS_CATEGORY not in report.categories
    assert body_reaction_hits(SINGLE) == []
    assert report.metrics["body_reactions"] == {}


def test_same_sentence_counts_once():
    """同一句里命中多个子模式(攥拳 + 指甲渗肉 + 流血)只算一次。"""
    families = body_reaction_families(SINGLE)
    assert families == {"攥拳掐肉/掌心渗血"}
    # 出现过的族照报(生成端预防要的是「这本书哪几族用滥了」),但命中明细为空
    assert body_reaction_hits(SINGLE) == []


def test_repeat_counts_only_the_excess():
    """同族出现两次 → 只计多余的那一次(index=1),第一次保留。"""
    report = ai_flavor_report(TWICE)
    cat = report.categories[_BODY_REACTIONS_CATEGORY]
    assert cat["count"] == 1
    assert report.metrics["body_reactions"] == {"攥拳掐肉/掌心渗血": 1}
    hits = body_reaction_hits(TWICE)
    assert [(h["family"], h["index"]) for h in hits] == [("攥拳掐肉/掌心渗血", 1)]
    # 多余的那次确实落在第二段(定点重写要靠它定位)
    assert hits[0]["start"] > TWICE.index("过了半炷香")
    assert _BODY_REACTIONS_CATEGORY in report.summary()


def test_per_family_threshold():
    """咬唇族阈值 3:两次不罚,第三次才计一次。"""
    two = "她咬紧了牙关。\n\n他咬破了嘴唇。"
    assert _BODY_REACTIONS_CATEGORY not in ai_flavor_report(two).categories

    report = ai_flavor_report(BITES)   # 四句里四次咬 → 超出的两次(index 2、3)才算
    cat = report.categories[_BODY_REACTIONS_CATEGORY]
    assert cat["count"] == 2
    assert [h["index"] for h in body_reaction_hits(BITES)] == [2, 3]


def test_dialogue_hits_are_discounted():
    """对白里的命中按 _DIALOGUE_DISCOUNT 打折计分(不改原文 count)。"""
    text = "「他攥紧了拳头,指甲渗进肉里。」\n\n「他又捏住拳头,指甲渗入肉里。」"
    report = ai_flavor_report(text)
    cat = report.categories[_BODY_REACTIONS_CATEGORY]
    assert cat["count"] == 1
    assert cat["dialogue_count"] == 1
    assert cat["score"] == pytest.approx(1.5 * 0.3)   # 打折后只剩对白那一处


def test_families_ignore_threshold():
    """body_reaction_families 只问「出现过没有」,用于跨章预防判断。"""
    assert body_reaction_families(SINGLE) == {"攥拳掐肉/掌心渗血"}
    assert body_reaction_families("窗外落雪,无人说话。") == set()
