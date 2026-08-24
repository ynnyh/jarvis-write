# tests/test_media_segments.py
# -*- coding: utf-8 -*-
"""切段/画风锚单点件的口径测试。

这一层是三条出片线(漫剧/宣传片/情绪短片)共用的确定性内核,口径一歪,
导出的 SRT 就和视频段对不上,所以把两条铁律钉在这里:
①段边界永远落在镜头边界上;②单格超上限时独立成段并标 over_limit。
"""
from __future__ import annotations

from app.engines.media.anchors import ensure_anchor, ensure_style_anchors, merge_negative
from app.engines.media.segments import chunk_rows, group_by_limit, plan_chunks


class _Shot:
    """ORM 行的替身:验证同一套函数吃 dict 也吃对象。"""

    def __init__(self, seq, duration_s, scene_name="", dialogue="", characters=None):
        self.seq = seq
        self.duration_s = duration_s
        self.scene_name = scene_name
        self.dialogue = dialogue
        self.characters = characters or []


def _d(seq, dur, scene="", dia=""):
    return {"seq": seq, "duration_s": dur, "scene_name": scene, "dialogue": dia}


# =============== 并段 ===============

def test_greedy_fills_then_opens_new_segment():
    shots = [_d(1, 4), _d(2, 4), _d(3, 4), _d(4, 4)]
    groups = group_by_limit(shots, 10)
    # 4+4=8 装得下,再加 4 就 12 超了 → 开新段
    assert [[s["seq"] for s in g] for g in groups] == [[1, 2], [3, 4]]


def test_never_splits_inside_a_shot():
    """单格 9 秒、上限 5 秒:不许把这一格劈开,它自己独占一段。"""
    groups = group_by_limit([_d(1, 9), _d(2, 3)], 5)
    assert [[s["seq"] for s in g] for g in groups] == [[1], [2]]


def test_over_limit_shot_is_marked_not_truncated():
    rows = plan_chunks([_d(1, 9), _d(2, 3)], 5)
    assert rows[0]["over_limit"] is True
    assert rows[0]["duration_s"] == 9  # 标出来,但绝不悄悄截成 5
    assert rows[1]["over_limit"] is False


def test_works_on_orm_like_objects():
    shots = [_Shot(1, 4, "教室"), _Shot(2, 4, "走廊")]
    rows = plan_chunks(shots, 10)
    assert rows[0]["shot_seqs"] == [1, 2]
    assert rows[0]["scenes"] == ["教室", "走廊"]


def test_empty_input_gives_no_segments():
    assert group_by_limit([], 10) == []
    assert plan_chunks([], 10) == []


def test_can_join_only_tightens():
    """漫剧口径:同场景才并。跨场景即使时长装得下也要断开。"""

    def same_scene(cur, s, acc_s):
        return acc_s + s["duration_s"] <= 15 and cur[0]["scene_name"] == s["scene_name"]

    shots = [_d(1, 2, "雪道"), _d(2, 2, "雪道"), _d(3, 2, "客栈")]
    groups = group_by_limit(shots, 15, can_join=same_scene)
    assert [[s["seq"] for s in g] for g in groups] == [[1, 2], [3]]


# =============== 时间码 ===============

def test_timecodes_are_contiguous_from_zero():
    rows = plan_chunks([_d(1, 3), _d(2, 4), _d(3, 5)], 7)
    assert [(r["start_s"], r["end_s"]) for r in rows] == [(0, 7), (7, 12)]
    # 段首尾相接、无空洞:SRT 与视频段共用这根轴
    for prev, nxt in zip(rows, rows[1:]):
        assert prev["end_s"] == nxt["start_s"]


def test_subtitle_joins_lines_and_skips_empty():
    rows = chunk_rows([[_d(1, 3, dia="我不该走"), _d(2, 3, dia=""), _d(3, 2, dia="那你留下")]], 10)
    assert rows[0]["subtitle"] == "我不该走\n那你留下"  # 空台词不占行,否则 SRT 出空行


def test_dirty_duration_counts_as_zero():
    rows = plan_chunks([{"seq": 1, "duration_s": "四"}, _d(2, 3)], 10)
    assert rows[0]["duration_s"] == 3


# =============== 画风锚 ===============

def test_anchor_injected_when_missing():
    cn, en = ensure_style_anchors("空教室全景", "empty classroom", "国风厚涂", "ink-wash")
    assert cn == "【画风锚】国风厚涂。空教室全景"
    assert en == "ink-wash, empty classroom"  # 英文用逗号,不许出现中文句号


def test_anchor_not_duplicated_when_already_present():
    cn, en = ensure_style_anchors("含国风厚涂的空教室", "ink-wash classroom", "国风厚涂", "ink-wash")
    assert cn == "含国风厚涂的空教室"
    assert en == "ink-wash classroom"


def test_empty_anchor_is_noop():
    assert ensure_style_anchors("原文", "raw", "", "") == ("原文", "raw")
    assert ensure_anchor("原文", "") == "原文"


def test_merge_negative():
    assert merge_negative("低分辨率", "多手多指") == "多手多指,低分辨率"
    assert merge_negative("", "多手多指") == "多手多指"
    assert merge_negative("多手多指,低分辨率", "多手多指") == "多手多指,低分辨率"
    assert merge_negative("低分辨率", "") == "低分辨率"
