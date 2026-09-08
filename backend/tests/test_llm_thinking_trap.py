# backend/tests/test_llm_thinking_trap.py
# -*- coding: utf-8 -*-
"""思考参数陷阱:渠道不认/反转「关闭思考」参数的自动识别与兼容。

真实现场(2026-09-08, ooioo.work / glm-5.3-flash):部分中转对
thinking={"type":"disabled"} 反向执行——思考全开、吃满输出预算、正文为空。
裸 HTTP 探针实锤:传参数时 reasoning_tokens 吃满 max_tokens、content 空;
不传参数只轻思考、输出正常。而生产路径 factory 会把全局默认 disabled
下发给所有推理系名字的模型(glm 在启发式名单里),正好踩中。

兼容策略:发了 disabled 却仍见思考(文本或 reasoning_tokens>0)→ 记住该
(渠道,模型)不再下发 + 本次撤参自动重试。本文件把三条路都钉住:
非流式、流式、正文非空也要记账。
"""
import asyncio

import httpx
import pytest

from app.llm.base import (
    EmptyContentError,
    UpstreamError,
    _THINKING_REJECTED,
    thinking_param_rejected,
)
from app.llm.openai_compatible import OpenAICompatibleAdapter

_BASE = "https://ooioo.work/v1"


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前后清空渠道登记,避免跨用例污染。"""
    _THINKING_REJECTED.clear()
    yield
    _THINKING_REJECTED.discard((_BASE, "glm-5.3-flash"))


def _patch_post_seq(monkeypatch, responses: list[httpx.Response]) -> None:
    """依次返回 responses(用尽后重复最后一个)。"""
    calls = {"n": 0}

    async def fake_post(self, *args, **kwargs):
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


class _FakeStreamCM:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aenter__(self) -> httpx.Response:
        return self._response

    async def __aexit__(self, *_exc) -> bool:
        return False


def _patch_stream_seq(monkeypatch, responses: list[httpx.Response]) -> None:
    calls = {"n": 0}

    def fake_stream(self, *args, **kwargs):
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return _FakeStreamCM(r)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)


def _sse(*lines: str) -> httpx.Response:
    body = "".join(f"{line}\n\n" for line in lines)
    return httpx.Response(
        200, text=body, headers={"content-type": "text/event-stream"}
    )


def _patch_both(monkeypatch, post_resps: list[httpx.Response],
                stream_resps: list[httpx.Response]) -> None:
    """非流式与流式都桩掉:重试路径会把下一次尝试切回流式(on_retry),
    只桩 post 的话第二次尝试会真连网络。"""
    _patch_post_seq(monkeypatch, post_resps)
    _patch_stream_seq(monkeypatch, stream_resps)


def _ok_sse() -> httpx.Response:
    return _sse(
        'data: {"choices":[{"delta":{"content":"雪落了一夜。"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    )


def _adapter(**kw) -> OpenAICompatibleAdapter:
    prefer_stream = kw.pop("prefer_stream", True)
    a = OpenAICompatibleAdapter(
        api_key="sk-x", model_name="glm-5.3-flash", base_url=_BASE, **kw
    )
    a.prefer_stream = prefer_stream
    a.retry_base_delay = 0  # 测试不等退避
    return a


def _trap_resp() -> httpx.Response:
    """陷阱现场:发了 disabled,渠道反转执行——思考吃满预算,正文空。"""
    return httpx.Response(200, json={
        "choices": [{
            "message": {"content": ""},
            "finish_reason": "length",
        }],
        "usage": {
            "completion_tokens": 16384,
            "completion_tokens_details": {"reasoning_tokens": 16200},
        },
    })


# ---------- 非流式:识别 + 撤参重试 ----------

def test_inverted_param_detected_and_retried_without_param(monkeypatch):
    """发了 disabled 却思考吃满预算 → 记住渠道、撤参、自动重发拿真正文。"""
    ok = httpx.Response(200, json={
        "choices": [{"message": {"content": "雪落了一夜。"}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 8},
    })
    # 重试会把下一次尝试切回流式(on_retry),所以流式桩里也要给成功响应
    _patch_both(monkeypatch, [_trap_resp(), ok], [_ok_sse()])
    a = _adapter(thinking_mode="disabled", prefer_stream=False)

    out = asyncio.run(a.complete(a.to_messages("写第一章")))

    assert out.content == "雪落了一夜。"
    assert thinking_param_rejected(_BASE, "glm-5.3-flash")  # 渠道已被记忆
    assert a.thinking_mode == ""  # 本实例也已撤参


def test_reasoning_tokens_alone_trigger_detection(monkeypatch):
    """部分中转不给思考文本只报 token 数 → 靠 usage 细节也能识别。"""
    ok = httpx.Response(200, json={
        "choices": [{"message": {"content": "正文"}, "finish_reason": "stop"}],
        "usage": {"completion_tokens": 3},
    })
    ok_sse = _sse(
        'data: {"choices":[{"delta":{"content":"正文"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    )
    _patch_both(monkeypatch, [_trap_resp(), ok], [ok_sse])
    a = _adapter(thinking_mode="disabled", prefer_stream=False)
    out = asyncio.run(a.complete(a.to_messages("hi")))
    assert out.content == "正文"
    assert thinking_param_rejected(_BASE, "glm-5.3-flash")


def test_no_trap_when_param_not_sent(monkeypatch):
    """没发思考参数时同样的空正文 → 走原「放大预算」路径,不记渠道。"""
    _patch_post_seq(monkeypatch, [_trap_resp()])
    a = _adapter(thinking_mode="", prefer_stream=False)
    with pytest.raises(EmptyContentError) as exc:
        asyncio.run(a.complete(a.to_messages("hi")))
    assert exc.value.budget_bound is True
    assert not thinking_param_rejected(_BASE, "glm-5.3-flash")


def test_content_ok_still_records_trap(monkeypatch):
    """正文拿到了但渠道白烧思考(浪费钱)→ 照样记账,后续调用撤参。"""
    wasteful = httpx.Response(200, json={
        "choices": [{
            "message": {"content": "正文"},
            "finish_reason": "stop",
        }],
        "usage": {
            "completion_tokens": 5000,
            "completion_tokens_details": {"reasoning_tokens": 4900},
        },
    })
    _patch_post_seq(monkeypatch, [wasteful])
    a = _adapter(thinking_mode="disabled", prefer_stream=False)
    out = asyncio.run(a.complete(a.to_messages("hi")))
    assert out.content == "正文"
    assert thinking_param_rejected(_BASE, "glm-5.3-flash")
    assert a.thinking_mode == ""


# ---------- 流式:生产默认路径同样覆盖 ----------

def test_stream_path_trap_detected_and_retried(monkeypatch):
    """流式优先路径:空正文 + 尾包 usage 带思考 token → 撤参重发成功。"""
    trap_sse = _sse(
        'data: {"choices":[{"delta":{"reasoning_content":"想想…"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"length"}],'
        '"usage":{"completion_tokens":16384,'
        '"completion_tokens_details":{"reasoning_tokens":16200}}}',
        "data: [DONE]",
    )
    ok_sse = _sse(
        'data: {"choices":[{"delta":{"content":"雪落了一夜。"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    )
    _patch_stream_seq(monkeypatch, [trap_sse, ok_sse])
    a = _adapter(thinking_mode="disabled")  # prefer_stream 默认 True

    out = asyncio.run(a.complete(a.to_messages("写第一章")))

    assert out.content == "雪落了一夜。"
    assert thinking_param_rejected(_BASE, "glm-5.3-flash")


def test_trap_error_is_retryable_not_empty_content(monkeypatch):
    """陷阱错误的类型契约:retryable(会重发)而非 EmptyContentError
    (放大预算同参数重试只是白等)。直接打非流式单发,验证首次抛出的错误类型。

    注:不能构造「重试后仍陷阱」的场景——第一次陷阱即永久撤参,
    后续尝试根本不会再带参数,那是另一条(合法的)空正文路径。
    """
    _patch_post_seq(monkeypatch, [_trap_resp()])
    a = _adapter(thinking_mode="disabled", prefer_stream=False)
    with pytest.raises(UpstreamError) as exc:
        asyncio.run(a._complete_once(a.to_messages("hi")))
    assert exc.value.retryable is True
    assert not isinstance(exc.value, EmptyContentError)
    assert "撤掉参数" in str(exc.value)
    assert thinking_param_rejected(_BASE, "glm-5.3-flash")
