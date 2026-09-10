# tests/test_tension_bus.py
# -*- coding: utf-8 -*-
"""全书张力总线测试(纯确定性,零 LLM)。

钉住的核心契约:
1. 总线必须有起伏 —— 全书一条平线是最致命的节奏病(比 AI 味更赶读者);
2. 逐卷抬高 —— 网文的「越读越上头」等价于卷级张力递增;
3. 峰值不在最后一章 —— 最后一章是余韵,炸在最后显得为了炸而炸;
4. 章定位/悬念密度能修正张力(高潮章该比过渡章高);
5. 总线能注入 prompt(光有数据不算落地);
6. 没有卷纲/短篇也不崩,退化成仍有起伏的曲线。
"""
from __future__ import annotations

import pytest

from app.engines.pipeline.tension_bus import (
    TENSION_MAX,
    TENSION_MIN,
    adjust_scene_waves,
    bus_for_book,
    chapter_tension_target,
    flatness_report,
    is_flat,
    tension_bus_block,
)

_MP3 = [
    {"start": 1, "end": 10, "goal": "开局"},
    {"start": 11, "end": 20, "goal": "中盘"},
    {"start": 21, "end": 30, "goal": "终局"},
]


class _Outline:
    """最小蓝图替身:只需章号/定位/悬念密度。"""

    def __init__(self, n: int, role: str = "", suspense: str = ""):
        self.chapter_number = n
        self.chapter_role = role
        self.suspense_level = suspense


# ---------- 总线不平 ----------

def test_bus_is_not_flat():
    """全书的根本要求:不能是一条平线。"""
    bus = bus_for_book(target_chapters=30, macro_plan=_MP3)
    assert len(bus) == 30
    assert not is_flat(bus)
    assert max(bus) - min(bus) >= 2


def test_bus_in_range():
    bus = bus_for_book(target_chapters=30, macro_plan=_MP3)
    assert all(TENSION_MIN <= v <= TENSION_MAX for v in bus)


def test_bus_rises_by_volume():
    """逐卷抬高:后一卷的均值必须高于前一卷。"""
    bus = bus_for_book(target_chapters=30, macro_plan=_MP3)
    v1 = bus[0:10]
    v2 = bus[10:20]
    v3 = bus[20:30]
    assert sum(v1) / len(v1) < sum(v2) / len(v2)
    assert sum(v2) / len(v2) <= sum(v3) / len(v3)
    assert max(v1) <= max(v3)


def test_bus_peak_not_in_last_chapter():
    """峰值不该落在最后一章 —— 最后一章是余韵。"""
    bus = bus_for_book(target_chapters=30, macro_plan=_MP3)
    peak_index = bus.index(max(bus))
    assert peak_index < len(bus) - 1


# ---------- 章级目标 ----------

def test_high_point_chapter_ranks_above_transition():
    high = chapter_tension_target(5, chapter_role="高潮", macro_plan=_MP3)
    trans = chapter_tension_target(5, chapter_role="过渡", macro_plan=_MP3)
    assert high > trans


def test_suspense_level_shifts_target():
    hi = chapter_tension_target(5, suspense_level="高", macro_plan=_MP3)
    lo = chapter_tension_target(5, suspense_level="低", macro_plan=_MP3)
    assert hi > lo


def test_target_always_clamped():
    """极端输入(高潮+高悬念叠在末卷)不许越界。"""
    for n in range(1, 31):
        v = chapter_tension_target(
            n, chapter_role="高潮", suspense_level="高", macro_plan=_MP3
        )
        assert TENSION_MIN <= v <= TENSION_MAX


def test_role_match_is_substring():
    """定位字段常是组合描述,要做包含匹配而不是等值。"""
    a = chapter_tension_target(5, chapter_role="关键转折+伏笔回收", macro_plan=_MP3)
    b = chapter_tension_target(5, chapter_role="", macro_plan=_MP3)
    assert a > b


# ---------- 退化路径 ----------

def test_no_macro_plan_still_has_wave():
    """没有卷纲(短篇/早期项目)也必须给出有起伏的曲线。"""
    bus = bus_for_book(target_chapters=12)
    assert len(bus) == 12
    assert not is_flat(bus)


def test_single_volume_still_works():
    bus = bus_for_book(target_chapters=10, macro_plan=[{"start": 1, "end": 10, "goal": "全"}])
    assert len(bus) == 10
    assert not is_flat(bus)


def test_chapter_outside_plan_falls_back():
    """章号落在卷纲之外(连载续订的中间态)时不许崩。"""
    v = chapter_tension_target(99, macro_plan=_MP3)
    assert TENSION_MIN <= v <= TENSION_MAX


def test_zero_target_chapters():
    bus = bus_for_book(target_chapters=0, macro_plan=_MP3)
    assert bus == []


def test_flatness_report_shape():
    r = flatness_report([3, 3, 3, 3])
    assert r["flat"] is True and r["span"] == 0 and r["longest_run"] == 4
    r2 = flatness_report([2, 5, 3, 4])
    assert r2["flat"] is False and r2["span"] == 3 and r2["peak_at"] == 2
    assert flatness_report([])["flat"] is True


# ---------- prompt 注入 ----------

def test_block_contains_position_and_directive():
    block = tension_bus_block(15, target_chapters=30, macro_plan=_MP3)
    assert "第15章" in block and "共30章" in block
    assert "目标张力" in block
    # 邻近章节坐标要有,模型才知道自己在爬还是在下
    assert "上一章张力" in block and "下一章张力" in block


def test_block_directive_differs_by_level():
    """压的章和爆的章,注入的指令必须不同 —— 否则等于没注入。"""
    low = tension_bus_block(1, target_chapters=30, macro_plan=_MP3)
    high = tension_bus_block(27, target_chapters=30, macro_plan=_MP3)
    assert low != high
    assert "蓄力" in low or "压" in low
    assert "爆发" in high or "兑现" in high


def test_block_empty_when_no_chapters():
    assert tension_bus_block(0, target_chapters=30) == ""
    assert tension_bus_block(1, target_chapters=0) == ""


def test_block_handles_first_and_last_chapter():
    """首章没有上一章、末章没有下一章,不能出现 "上一章张力 None"。"""
    first = tension_bus_block(1, target_chapters=10, macro_plan=[{"start": 1, "end": 10, "goal": "g"}])
    last = tension_bus_block(10, target_chapters=10, macro_plan=[{"start": 1, "end": 10, "goal": "g"}])
    for b in (first, last):
        assert "None" not in b


# ---------- 场间曲线(章内)与总线的接口 ----------

def test_flat_scene_wave_gets_shaped():
    """平的场间曲线会被重塑成「抬升 → 峰值 → 回落」。"""
    shaped = adjust_scene_waves([3, 3, 3, 3], chapter_target=4)
    assert len(shaped) == 4
    assert max(shaped) - min(shaped) >= 2
    # 峰值不在最后一场
    assert shaped.index(max(shaped)) < len(shaped) - 1


def test_existing_curve_is_respected():
    """模型给的曲线本来就有起伏 → 原样保留,不越权改。"""
    orig = [1, 5, 3]
    assert adjust_scene_waves(orig, chapter_target=4) == orig


def test_shaped_wave_stays_in_range():
    for target in range(1, 6):
        shaped = adjust_scene_waves([target] * 4, chapter_target=target)
        assert all(TENSION_MIN <= v <= TENSION_MAX for v in shaped), target


def test_single_and_empty_scene_wave():
    assert adjust_scene_waves([], chapter_target=4) == []
    assert adjust_scene_waves([3], chapter_target=4) == [4]


def test_scene_wave_peaks_at_least_four():
    """章目标 3 的章也该有一个拎得起来的点,否则整章平着走。"""
    shaped = adjust_scene_waves([3, 3, 3, 3], chapter_target=3)
    assert max(shaped) >= 4


def test_two_scene_wave():
    shaped = adjust_scene_waves([3, 3], chapter_target=4)
    assert len(shaped) == 2
    assert max(shaped) - min(shaped) >= 1
