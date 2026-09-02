# tests/test_sanitize_style_memo.py
# -*- coding: utf-8 -*-
"""scripts.sanitize_style_memo 清洗逻辑落表:_remove_all / _clean_artifacts 单测。

覆盖:带标点的原句在 memo 里能按"纯文字"命中并整段剔除(连标点一起)、
多本/多句遍历、括号等装饰标点不误伤、剔除后残留标点碎片被归一。
"""
from scripts.sanitize_style_memo import _remove_all, _clean_artifacts


def test_remove_sentence_with_punctuation():
    memo = "复现意象:流水的触感(水流冲过他的手指,凉凉的,有点麻。)\n风吹热气。"
    out = _remove_all(memo, "水流冲过他的手指凉凉的有点麻")
    assert "水流冲过他的手指" not in out
    assert "流水的触感" in out
    assert "风吹热气" in out


def test_remove_leaves_clean_wording():
    memo = "4. 复现意象:水流冲过他的手指,凉凉的,有点麻。\n树影也常出现。"
    out = _clean_artifacts(_remove_all(memo, "水流冲过他的手指凉凉的有点麻"))
    assert out == "4. 复现意象:\n树影也常出现。"
    assert out.count("，") == 0  # 残留分隔符归一,不再有裸逗号赝余


def test_noise_punctuation_not_flagged():
    # 只有装饰标点、无正文重复 → 不改动(remove 找不到 key,原样返回)
    memo = "调性:冷峻、克制。\n人物声音:老猫话短。"
    assert _remove_all(memo, "流水的触感凉微麻") == memo


def test_multiple_occurrences_all_removed():
    memo = "（水流冲过他的手指凉凉的）又（水流冲过他的手指凉凉的）"
    out = _clean_artifacts(_remove_all(memo, "水流冲过他的手指凉凉的"))
    # 两处都被剔除,空括号被拆空清理,只剩真正的连接词
    assert "水流冲过他的手指凉凉的" not in out
    assert out == "又"


def test_empty_key_noop():
    assert _remove_all("你好,世界。", "") == "你好,世界。"