# tests/test_continuation.py
# -*- coding: utf-8 -*-
"""章尾续写引擎(mock LLM):清洗规则 + 上下文注入 + 边界。"""
from __future__ import annotations

import asyncio

import pytest

from app.engines.pipeline import continuation


class _CaptureAdapter:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompt: str | None = None

    async def ask(self, prompt: str, system: str | None = None) -> str:
        self.prompt = prompt
        return self.reply


def test_clean_continuation_strips_fence_and_takes_first_para():
    """去代码围栏;只取首个自然段;段内单换行压成连续正文。"""
    raw = "```\n他停下脚步,\n侧耳听了听。\n\n第二段不该带出来。\n```"
    assert continuation._clean_continuation(raw) == "他停下脚步, 侧耳听了听。"


def test_clean_continuation_truncates_at_sentence_boundary():
    """超长按句末标点回退,不截半句。"""
    long = "。".join(["他往前走了一步" for _ in range(120)]) + "。"
    out = continuation._clean_continuation(long)
    assert len(out) <= continuation._MAX_CONT_CHARS
    assert out.endswith("。")  # 落在句末标点


def test_continue_tail_injects_context_and_returns_clean():
    adapter = _CaptureAdapter("他握紧了剑,一步步逼近。")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(continuation, "get_adapter_for", lambda task: adapter)
        out = asyncio.run(
            continuation.continue_tail("本章:决斗", "前情:他进了城", "……城门在身后合上。", "紧张些")
        )
    assert out == "他握紧了剑,一步步逼近。"
    # 蓝图 / 前情 / 尾部 / 额外要求都注入 prompt
    assert "本章:决斗" in adapter.prompt
    assert "前情:他进了城" in adapter.prompt
    assert "城门在身后合上。" in adapter.prompt
    assert "紧张些" in adapter.prompt


def test_continue_tail_slices_tail_to_window():
    """超长正文只注入尾部 _MAX_TAIL_CHARS 字(控 token)。"""
    adapter = _CaptureAdapter("续一句。")
    head = "A" * 2000
    tail_mark = "这是结尾标记。"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(continuation, "get_adapter_for", lambda task: adapter)
        asyncio.run(continuation.continue_tail("蓝图", "前情", head + tail_mark))
    assert tail_mark in adapter.prompt          # 尾部保留
    assert "A" * 2000 not in adapter.prompt      # 开头被截掉


def test_continue_tail_empty_tail_raises():
    with pytest.raises(ValueError):
        asyncio.run(continuation.continue_tail("蓝图", "前情", "   "))


def test_continue_tail_empty_model_output_raises():
    adapter = _CaptureAdapter("```\n\n```")  # 清洗后为空
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(continuation, "get_adapter_for", lambda task: adapter)
        with pytest.raises(ValueError):
            asyncio.run(continuation.continue_tail("蓝图", "前情", "有正文。"))


def test_continue_tail_injects_voice_block():
    """续写(此前完全裸奔)现在也吃文风范本正向锚:voice_block 注入 prompt。"""
    from app.prompts.style_capsules import render_voice_block

    adapter = _CaptureAdapter("他握紧了剑,一步步逼近。")
    voice = render_voice_block(voice_key="plain")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(continuation, "get_adapter_for", lambda task: adapter)
        asyncio.run(
            continuation.continue_tail("蓝图", "前情", "有正文。", voice_block=voice)
        )
    assert "文风范本" in adapter.prompt
    assert "母亲把最后一件毛衣叠好" in adapter.prompt  # plain 胶囊 sample 进了 prompt
