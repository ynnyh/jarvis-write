# tests/test_ai_flavor.py
# -*- coding: utf-8 -*-
"""AI 味指数:规则统计 + 生成响应字段。"""
from app.api.chapters import _flavor_dict
from app.engines.polish.ai_flavor import ai_flavor_report


def test_ai_flavor_detects_ai_tone():
    ai_text = (
        "她感到无比的悲伤,心中五味杂陈。仿佛整个世界都在旋转,宛如一场梦。"
        "不是命运的捉弄,而是时代的必然。是勇气,是信念,是永不言弃的精神。"
    )
    r = ai_flavor_report(ai_text)
    assert r.score > 0
    assert "情绪标签直喊" in r.hits
    assert "仿佛式比喻" in r.hits
    assert "排比堆砌" in r.hits
    assert f"{r.score:.1f}" in r.summary()


def test_ai_flavor_clean_text_scores_zero():
    human_text = "老张把烟头摁灭在墙上,说了句走吧。巷子里没人,风把门带上了。"
    r = ai_flavor_report(human_text)
    assert r.score == 0
    assert not r.hits
    assert "未检出" in r.summary()


def test_flavor_dict_shape():
    d = _flavor_dict("仿佛一切都在某种意义上注定了。")
    assert set(d) == {"score", "summary"}
    assert isinstance(d["score"], float)
    assert isinstance(d["summary"], str)
