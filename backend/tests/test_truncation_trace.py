# backend/tests/test_truncation_trace.py
# -*- coding: utf-8 -*-
"""被掐断 vs 预算用尽:两种"半截输出"必须分开留痕。

起因(2026-09-08 50 章压测):魔芋中转把响应尾巴随机截断,连 16 字符的 JSON
都截在半途。表象只有「JSON 解析失败」,但根因有两种且对策完全不同:
- finish_reason=length      → 输出预算用尽,加 max_tokens 就能解决;
- 无 [DONE] 也无 finish_reason → 网关/CDN 静默掐断,加预算毫无用处,
                                 要续写或换渠道(据实测,魔芋属于这一类)。

此前 finish_reason 只活在内存里做空正文归因,没落库,事后无法回溯某渠道
到底是哪种——于是「要不要换渠道」只能靠猜。这里把两者钉死到 LLMResponse
上,并确认能一路记进用量表。
"""
from __future__ import annotations

import asyncio

import httpx

from app.llm.openai_compatible import OpenAICompatibleAdapter

_BASE = "https://moyu.example/v1"


class _FakeStreamCM:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aenter__(self) -> httpx.Response:
        return self._response

    async def __aexit__(self, *_exc) -> bool:
        return False


def _patch_stream(monkeypatch, response: httpx.Response) -> None:
    monkeypatch.setattr(
        httpx.AsyncClient, "stream", lambda self, *a, **kw: _FakeStreamCM(response)
    )


def _adapter() -> OpenAICompatibleAdapter:
    a = OpenAICompatibleAdapter(
        api_key="sk-x", model_name="deepseek-v4-flash-0731", base_url=_BASE
    )
    a.prefer_stream = True
    return a


def _sse(body: bytes) -> httpx.Response:
    return httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"}
    )


def test_silent_cut_is_marked_truncated(monkeypatch):
    """没等到 [DONE] 也没 finish_reason → 标记 truncated(被网关掐断)。"""
    body = b'data: {"choices":[{"delta":{"content":"{\\"issues\\": ["}}]}\n\n'
    _patch_stream(monkeypatch, _sse(body))

    a = _adapter()
    resp = asyncio.run(a._complete_via_stream(a.to_messages("检查本章")))

    assert resp.truncated is True, "静默掐断必须留痕,否则事后分不清是哪种半截"
    assert resp.finish_reason == ""
    assert resp.content == '{"issues": ['


def test_clean_stream_is_not_truncated(monkeypatch):
    """正常收尾([DONE] + stop)→ truncated 必须为假,不能冤枉健康渠道。"""
    body = (
        b'data: {"choices":[{"delta":{"content":"{\\"issues\\": []}"}}]}\n\n'
        b'data: {"choices":[{"finish_reason":"stop","delta":{}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    _patch_stream(monkeypatch, _sse(body))

    a = _adapter()
    resp = asyncio.run(a._complete_via_stream(a.to_messages("检查本章")))

    assert resp.truncated is False
    assert resp.finish_reason == "stop"
    assert resp.content == '{"issues": []}'


def test_length_truncation_is_different_from_cut(monkeypatch):
    """finish_reason=length 是预算用尽(有明确收尾),不算被掐断。"""
    body = (
        b'data: {"choices":[{"delta":{"content":"{\\"issues\\": ["}}]}\n\n'
        b'data: {"choices":[{"finish_reason":"length","delta":{}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    _patch_stream(monkeypatch, _sse(body))

    a = _adapter()
    resp = asyncio.run(a._complete_via_stream(a.to_messages("检查本章")))

    assert resp.finish_reason == "length"
    assert resp.truncated is False, "length 有明确收尾信号,与静默掐断不能混为一谈"


def test_usage_row_carries_truncation_trace(monkeypatch):
    """truncated / finish_reason 要真的落进用量记录(不然还是只能靠猜)。"""
    captured: dict = {}

    class _FakeLlmUsage:
        def __init__(self, **kw):
            captured.update(kw)

    class _FakeScope:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def add(self, _row):
            return None

    from app.auth import current_user_id

    token = current_user_id.set(1)
    monkeypatch.setattr("app.db.session.session_scope", lambda: _FakeScope())
    monkeypatch.setattr("app.db.models.LlmUsage", _FakeLlmUsage)

    from app.llm.base import LLMAdapter, LLMResponse

    resp = LLMResponse(
        content="半截",
        model="deepseek-v4-flash-0731",
        prompt_tokens=10,
        completion_tokens=2,
        finish_reason="length",
        truncated=True,
    )
    LLMAdapter._record_usage(resp)
    current_user_id.reset(token)

    assert captured["finish_reason"] == "length"
    assert captured["truncated"] is True
    assert captured["model"] == "deepseek-v4-flash-0731"
