# tests/test_blueprint_stream.py
# -*- coding: utf-8 -*-
"""Pillar 1:蓝图流式逐章进度 _generate_chunk 的行为。

不碰真实 LLM:用假适配器验证三条路径——
1. 有 stream() 且正常吐字 → 逐章上报「已生成 N/M 章」且不回落 ask();
2. stream() 抛错 → 回落 ask() 仍拿到内容;
3. stream() 吐空 → 回落 ask()。
"""
from __future__ import annotations

import asyncio

from app.engines.pipeline import blueprint as bp_mod

# 两章样例(含 markdown 加粗章节头,验证宽松匹配)
_TWO_CHAPTERS = """\
第1章 - 雨夜
本章简述:开场。

**第2章 - 追猎**
本章简述:升级。
"""


class StreamAdapter:
    """有 stream() 的假适配器:把预置文本按行逐块吐出。"""

    def __init__(self, text: str, ask_reply: str = "ASK_FALLBACK"):
        self._text = text
        self._ask_reply = ask_reply
        self.asked = False

    @staticmethod
    def to_messages(prompt, system=None):
        return [prompt]

    async def stream(self, messages):
        for line in self._text.splitlines(keepends=True):
            yield line

    async def ask(self, prompt, system=None):
        self.asked = True
        return self._ask_reply


class RaisingAdapter(StreamAdapter):
    async def stream(self, messages):
        raise RuntimeError("boom")
        yield ""  # noqa: 让其成为 async generator(不可达)


class EmptyStreamAdapter(StreamAdapter):
    async def stream(self, messages):
        return
        yield ""  # noqa: 生成器但不吐任何东西


def _run(adapter, expected=2, base_done=0, total=2):
    reports: list[str] = []
    raw = asyncio.run(
        bp_mod._generate_chunk(
            adapter,
            "PROMPT",
            expected=expected,
            base_done=base_done,
            total=total,
            report=reports.append,
        )
    )
    return raw, reports


def test_count_heads_matches_plain_and_bold():
    assert bp_mod._count_heads(_TWO_CHAPTERS) == 2
    assert bp_mod._count_heads("第 10 章 空格容错") == 1
    assert bp_mod._count_heads("没有章节头的文本") == 0


def test_stream_reports_per_chapter_progress():
    ad = StreamAdapter(_TWO_CHAPTERS)
    raw, reports = _run(ad)
    assert raw.strip()
    assert ad.asked is False  # 流式成功,不该回落 ask()
    progress = [r for r in reports if "已生成" in r]
    assert progress, "应至少上报一次逐章进度"
    # 单调递增且报到 2/2 章
    assert progress[0] == "已生成 1/2 章"
    assert progress[-1] == "已生成 2/2 章"


def test_progress_never_exceeds_total_with_base_offset():
    # 第二块:前面已有 20 章,本块 2 章,总 22
    ad = StreamAdapter(_TWO_CHAPTERS)
    _, reports = _run(ad, expected=2, base_done=20, total=22)
    progress = [r for r in reports if "已生成" in r]
    assert progress[-1] == "已生成 22/22 章"
    # 不越界
    assert all(int(r.split("/")[0].replace("已生成", "").strip()) <= 22 for r in progress)


def test_stream_error_falls_back_to_ask():
    ad = RaisingAdapter(_TWO_CHAPTERS)
    raw, _ = _run(ad)
    assert ad.asked is True
    assert raw == "ASK_FALLBACK"


def test_empty_stream_falls_back_to_ask():
    ad = EmptyStreamAdapter(_TWO_CHAPTERS)
    raw, _ = _run(ad)
    assert ad.asked is True
    assert raw == "ASK_FALLBACK"
