# tests/test_batch_retitle.py
# -*- coding: utf-8 -*-
"""Pillar 3:批量重拟标题的解析容错 + 分批 + 过滤 + 失败处理。

不碰真实 LLM:patch get_adapter_for,喂各种(规整/跑偏)输出。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from app.engines import outline_retitle as otr


class _BatchAdapter:
    """按顺序返回预置回复的假适配器。"""

    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls: list[str] = []

    async def ask(self, prompt: str, system=None) -> str:
        self.calls.append(prompt)
        return self._replies.pop(0)


def _obj_reply(nums, prefix="新") -> str:
    return json.dumps(
        {"titles": [{"chapter": n, "title": f"{prefix}{n}"} for n in nums]},
        ensure_ascii=False,
    )


# ---------- _parse_chapter_num ----------
def test_parse_chapter_num_tolerant():
    assert otr._parse_chapter_num("第3章") == 3
    assert otr._parse_chapter_num("3") == 3
    assert otr._parse_chapter_num(5) == 5
    assert otr._parse_chapter_num("ch12") == 12
    assert otr._parse_chapter_num("无") is None


# ---------- _coerce_batch 三形态 ----------
def test_coerce_object_array_with_chapter():
    parsed = {"titles": [{"chapter": 1, "title": "雨夜"}, {"chapter": 2, "title": "追猎"}]}
    assert otr._coerce_batch(parsed, [1, 2]) == {1: "雨夜", 2: "追猎"}


def test_coerce_bare_object_array():
    assert otr._coerce_batch([{"chapter": 3, "title": "交易"}], [3]) == {3: "交易"}


def test_coerce_dict_keyed_by_number():
    assert otr._coerce_batch({"1": "雨夜", "2": "追猎"}, [1, 2]) == {1: "雨夜", 2: "追猎"}


def test_coerce_positional_array():
    assert otr._coerce_batch(["雨夜", "追猎"], [5, 6]) == {5: "雨夜", 6: "追猎"}
    # 位置数组塞在约定外层键里
    assert otr._coerce_batch({"titles": ["雨夜", "追猎"]}, [5, 6]) == {5: "雨夜", 6: "追猎"}


def test_coerce_strips_wrappers():
    assert otr._coerce_batch([{"chapter": 1, "title": "《雨夜》"}], [1]) == {1: "雨夜"}


# ---------- suggest_all_chapter_titles ----------
def test_suggest_all_returns_only_changed_and_reports_progress():
    reply = _obj_reply([1, 2])  # 新1 / 新2
    ad = _BatchAdapter([reply])
    reports: list[str] = []
    with patch.object(otr, "get_adapter_for", return_value=ad):
        items = asyncio.run(
            otr.suggest_all_chapter_titles(
                architecture_brief="brief",
                chapters=[
                    {"chapter_number": 1, "title": "旧一", "summary": "s1"},
                    {"chapter_number": 2, "title": "新2", "summary": "s2"},  # 与新相同→过滤
                ],
                progress=reports.append,
            )
        )
    assert items == [{"chapter_number": 1, "old_title": "旧一", "new_title": "新1"}]
    assert len(ad.calls) == 1  # 2 章一次调用装下
    assert any("重拟" in r and "章标题" in r for r in reports)


def test_suggest_all_chunks_long_book():
    chapters = [
        {"chapter_number": n, "title": f"旧{n}", "summary": "s"} for n in range(1, 31)
    ]
    ad = _BatchAdapter([_obj_reply(range(1, 26)), _obj_reply(range(26, 31))])
    with patch.object(otr, "get_adapter_for", return_value=ad):
        items = asyncio.run(
            otr.suggest_all_chapter_titles(architecture_brief="b", chapters=chapters)
        )
    assert len(ad.calls) == 2  # 30 章按 25 分两批
    assert len(items) == 30
    assert items[0] == {"chapter_number": 1, "old_title": "旧1", "new_title": "新1"}
    assert items[-1]["chapter_number"] == 30


def test_suggest_all_raises_when_nothing_parses():
    ad = _BatchAdapter(["一段没有任何标题结构的废话"])
    with patch.object(otr, "get_adapter_for", return_value=ad):
        with pytest.raises(ValueError):
            asyncio.run(
                otr.suggest_all_chapter_titles(
                    architecture_brief="b",
                    chapters=[{"chapter_number": 1, "title": "旧", "summary": "s"}],
                )
            )
