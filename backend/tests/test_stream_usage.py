# tests/test_stream_usage.py
# -*- coding: utf-8 -*-
"""流式路径补记账(docs/32 §2.4 问题 #6)。

铺 150 章蓝图实测 7.4 分钟,llm_usage 里一行都没有——最贵的调用恰好在成本表上
隐形。现在流式成功路径补一条**估算**记录(finish_reason="stream-est",诚实可查),
本测试守住它别被后续重构悄悄摘掉。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMAdapter
from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


class _StreamAdapter:
    """只实现流式路径的最小适配器(model_name + to_messages + stream)。"""

    model_name = "test-stream-model"

    # 记账要落到真适配器的那套实现上(生产路径里 _record_usage 来自 LLMAdapter)
    _record_usage = LLMAdapter._record_usage

    def __init__(self, parts: list[str]):
        self._parts = parts

    def to_messages(self, prompt):
        return [{"role": "user", "content": prompt}]

    async def stream(self, messages):
        for part in self._parts:
            yield part

    def record_stream_estimate(self, prompt, output, duration_ms=0):
        LLMAdapter.record_stream_estimate(self, prompt, output, duration_ms)


class _BareAdapter:
    """没有记账钩子的适配器(测试替身/自定义实现):不许因此炸掉生成。"""

    def to_messages(self, prompt):
        return [{"role": "user", "content": prompt}]

    async def stream(self, messages):
        yield "### 第1章 起\n"
        yield "节拍:开局\n"


def _chunk(adapter):
    from app.engines.pipeline.blueprint import _generate_chunk

    return asyncio.run(_generate_chunk(
        adapter, "请铺蓝图(这里是 prompt 正文)",
        expected=1, base_done=0, total=1, report=lambda s: None,
    ))


def test_stream_success_records_estimated_usage(client):
    from app.db.models import LlmUsage
    from app.db.session import SessionLocal

    out = _chunk(_StreamAdapter(["### 第1章 起\n", "节拍一\n节拍二\n"]))
    assert "第1章" in out

    with SessionLocal() as db:
        row = (
            db.query(LlmUsage)
            .filter_by(model="test-stream-model", finish_reason="stream-est")
            .order_by(LlmUsage.id.desc())
            .first()
        )
        assert row is not None, "流式成功路径没有留下用量记录"
        assert row.prompt_tokens > 0 and row.completion_tokens > 0
        assert row.truncated is False


def test_stream_estimate_is_character_based_and_marked():
    """估算口径:按字符数(保守 2 字/token),并用 finish_reason 标出这是估算。"""
    from app.db.models import LlmUsage
    from app.db.session import SessionLocal
    from app.llm.base import LLMAdapter

    class _Stub:
        model_name = "test-est-model"
        _record_usage = LLMAdapter._record_usage

    LLMAdapter.record_stream_estimate(_Stub(), "p" * 100, "o" * 400)
    with SessionLocal() as db:
        row = (
            db.query(LlmUsage).filter_by(model="test-est-model")
            .order_by(LlmUsage.id.desc()).first()
        )
        assert row.prompt_tokens == 50
        assert row.completion_tokens == 200
        assert row.finish_reason == "stream-est"


def test_adapter_without_hook_still_generates(client):
    """适配器没有记账钩子 → 生成照常,不抛错。"""
    assert "第1章" in _chunk(_BareAdapter())
