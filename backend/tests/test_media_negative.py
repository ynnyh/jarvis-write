# tests/test_media_negative.py
# -*- coding: utf-8 -*-
"""负面词基座单点(app/engines/media/negative.py):合流、保序去重、脏值容忍。

为什么值得单测:负面词是「图生视频」最容易漏的一轨,漏了就直接出片出现多指/
脸部变形——而这类 bug 只在用户拿成片时暴露,测试里看不见。所以这个叶子件必须
被钉死:三线共用一份、重复项要去重(重复的否定会让模型过度加权,把主体也压掉)。
"""
from __future__ import annotations

from app.engines.media.negative import BASE, LINE_EXTRA, as_text, for_line, merge


def test_merge_keeps_base_and_order():
    """基座原样保留,顺序稳定——顺序即权重,伤主体的排前面。"""
    got = merge()
    assert got == list(BASE)
    assert got[0] in ("脸部变形", "多指", "肢体扭曲")


def test_merge_dedups_and_appends():
    """与基座重复的项只留一次,本格规避追加在后。"""
    got = merge(["水印", "新的规避"], ["新的规避"])
    assert got.count("水印") == 1
    assert got.count("新的规避") == 1
    assert got[-1] == "新的规避"
    assert len(got) == len(BASE) + 1


def test_merge_tolerates_dirty_values():
    """LLM 回的脏值不该让整格生成失败——非序列一律跳过,空串/None 丢弃。"""
    got = merge(None, 123, [], ["  "], ["水印", None, "表情僵硬"])
    assert "表情僵硬" in got
    assert "" not in got and "None" not in got


def test_for_line_adds_line_specific():
    """每条线的追加项只在它自己那条线上出现——防串味。"""
    drama = for_line("drama")
    clips = for_line("clips")
    assert LINE_EXTRA["drama"][0] in drama
    assert LINE_EXTRA["drama"][0] not in clips
    assert LINE_EXTRA["clips"][0] in clips


def test_unknown_line_is_base_only():
    """未知线不报错,退化成共享基座。"""
    assert for_line("does-not-exist") == list(BASE)


def test_as_text_renders_one_line():
    text = as_text("clips", ["本格规避"])
    assert "、" in text and text.startswith(BASE[0])
    assert text.endswith("本格规避")
