# tests/test_blueprint_chunking.py
# -*- coding: utf-8 -*-
"""蓝图分块的欠章续补行为(字数过多 → 模型单次输出被截断的主修复)。

此前块欠章时原样重摇整块:同样的输入会得到同样的截断,3 次重摇后整块报废。
现在从「最后一个已解析章」之后续补尾部区间。用按序吐回复的假适配器验证:
1. 分块模式:块内截断 → 续补提示词只请求缺失区间,最终章节补齐;
2. 整书模式(≤20 章一块装下):截断后同样续补;
3. 连续欠章到重试用尽:明确报错(带续补次数),不静默放行残缺蓝图;
4. 上下文超限的上游报错被归一化成可操作的中文指引。
"""
from __future__ import annotations

import asyncio

import pytest

from app.engines.pipeline import blueprint as bp_mod
from app.jobs import normalize_job_error


class ScriptedAdapter:
    """按调用次序吐预置回复的假适配器;stream() 一次吐完,记录每次收到的提示词。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def to_messages(self, prompt, system=None):
        self.prompts.append(prompt)
        return [prompt]

    async def stream(self, messages):
        text = self.replies.pop(0) if self.replies else ""
        for line in text.splitlines(keepends=True):
            yield line

    async def ask(self, prompt, system=None):
        self.prompts.append(prompt)
        return self.replies.pop(0) if self.replies else ""


def _chapters(start: int, end: int) -> str:
    rows = []
    for n in range(start, end + 1):
        rows.append(f"第{n}章 - 标题{n}")
        rows.append("本章定位:过渡")
        rows.append("本章简述:推进调查,发现新的线索。")
        rows.append("本章节拍:起|承|转|合")
    return "\n".join(rows)


def _run(adapter, number_of_chapters: int):
    from unittest.mock import patch

    with patch("app.engines.pipeline.blueprint.get_adapter_for", return_value=adapter):
        chapters, _warnings = asyncio.run(
            bp_mod.generate_blueprint(
                novel_architecture="架构文本",
                number_of_chapters=number_of_chapters,
            )
        )
    return chapters, adapter


def test_truncated_chunk_continues_from_last_parsed():
    """块内截断(1-20 只吐到 13)→ 续补只请求 14-20,最终 20 章齐。"""
    ad = ScriptedAdapter([
        _chapters(1, 13),   # 首次调用:第 13 章后被截断
        _chapters(14, 20),  # 续补
        _chapters(21, 25),  # 第二块
    ])
    chapters, ad = _run(ad, 25)

    assert [c["chapter_number"] for c in chapters] == list(range(1, 26))
    # 续补的提示词只请求缺失区间
    assert "继续生成第14章到第20章" in ad.prompts[1]
    # 首块请求仍是完整区间 1-20
    assert "继续生成第1章到第20章" in ad.prompts[0]


def test_whole_book_mode_truncation_also_continues():
    """≤20 章走整书模板:截断后续补切到分块模板,整书补齐。"""
    ad = ScriptedAdapter([
        _chapters(1, 7),   # 整书一次生成,第 7 章后被截断
        _chapters(8, 12),  # 续补
    ])
    chapters, ad = _run(ad, 12)

    assert [c["chapter_number"] for c in chapters] == list(range(1, 13))
    assert "继续生成" not in ad.prompts[0]  # 首次是整书模板
    assert "继续生成第8章到第12章" in ad.prompts[1]


def test_persistent_underparse_fails_loudly():
    """模型反复欠章(续补要 3-20 却总回 1-2):重试用尽后明确报错,不静默放行。"""
    ad = ScriptedAdapter([_chapters(1, 2)] * 4)  # 首次 + 3 次续补全部欠章

    with pytest.raises(RuntimeError, match="2/20"):
        _run(ad, 20)


def test_context_overflow_error_humanized():
    """上游「上下文超限」的英文长文被翻译成可操作的中文指引。"""
    raw = (
        "上游返回 HTTP 400: This model's maximum context length is 65536 tokens. "
        "However, you requested 70002 tokens."
    )
    out = normalize_job_error(RuntimeError(raw))
    assert "上下文" in out and "章数" in out
