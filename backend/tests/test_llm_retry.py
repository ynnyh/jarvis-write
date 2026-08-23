# backend/tests/test_llm_retry.py
# -*- coding: utf-8 -*-
"""瞬时错误重试、流式优先策略、以及流式被拒时的非流式回落。

不依赖真实 HTTP:子类化适配器,用脚本化的成功/异常序列驱动
_complete_once / _complete_via_stream,验证走的是哪条路。
"""
import asyncio

import httpx
import pytest

from app.llm.base import EmptyContentError, LLMResponse, UpstreamError, check_upstream
from app.llm.openai_compatible import OpenAICompatibleAdapter


def _err(status: int, retryable: bool = True) -> UpstreamError:
    return UpstreamError(f"HTTP {status}", status=status, retryable=retryable)


class _ScriptedAdapter(OpenAICompatibleAdapter):
    """按脚本出牌:两条路各一份脚本,记录每次实际走的是流式还是非流式。"""

    def __init__(
        self,
        once_script: list | None = None,
        stream_script: list | None = None,
        prefer_stream: bool = True,
    ):
        super().__init__(api_key="sk-x", model_name="m")
        self.retry_base_delay = 0  # 测试不等退避
        self.prefer_stream = prefer_stream
        self._once = list(once_script or [])
        self._stream = list(stream_script or [])
        self.calls: list[str] = []

    @staticmethod
    def _take(script: list, path: str) -> LLMResponse:
        item = script.pop(0) if script else RuntimeError(f"{path} 脚本用尽")
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=item, model="m")

    async def _complete_once(self, messages):
        self.calls.append("once")
        return self._take(self._once, "once")

    async def _complete_via_stream(self, messages):
        self.calls.append("stream")
        return self._take(self._stream, "stream")


def _run(adapter: _ScriptedAdapter) -> LLMResponse:
    return asyncio.run(adapter.complete(adapter.to_messages("hi")))


# ---------- 流式优先(与 cc-switch / Claude Code 同策略) ----------

def test_stream_is_the_default_path():
    """默认就走流式:长生成一直有字节流动,不给 CDN 掐断的机会。"""
    a = _ScriptedAdapter(stream_script=["第22章正文..."], once_script=["不该用到"])
    resp = _run(a)
    assert resp.content == "第22章正文..."
    assert a.calls == ["stream"]


def test_retryable_error_retries_on_stream():
    """429 抖动 → 退避后重试,仍走流式(流式不是降级手段,是主路)。"""
    a = _ScriptedAdapter(stream_script=[_err(429), "补上了"])
    resp = _run(a)
    assert resp.content == "补上了"
    assert a.calls == ["stream", "stream"]


def test_stream_rejected_falls_back_to_non_stream():
    """渠道不支持 stream:true(400 等非瞬时错)→ 同一次尝试内回落非流式。"""
    a = _ScriptedAdapter(
        stream_script=[_err(400, retryable=False)], once_script=["非流式救回"]
    )
    resp = _run(a)
    assert resp.content == "非流式救回"
    assert a.calls == ["stream", "once"]


def test_stream_rejected_then_sticks_to_non_stream():
    """流式被拒后不再来回试探:后续重试直接走非流式。"""
    a = _ScriptedAdapter(
        stream_script=[_err(400, retryable=False)],
        once_script=[_err(503), "第二次非流式成功"],
    )
    resp = _run(a)
    assert resp.content == "第二次非流式成功"
    assert a.calls == ["stream", "once", "once"]


def test_auth_error_fails_fast_without_fallback():
    """401 鉴权错误换成非流式也一样错:立即抛出,不浪费尝试。"""
    a = _ScriptedAdapter(
        stream_script=[_err(401, retryable=False)], once_script=["不该用到"]
    )
    with pytest.raises(UpstreamError) as exc:
        _run(a)
    assert exc.value.status == 401
    assert a.calls == ["stream"]


def test_empty_content_is_not_retried_here():
    """空正文交给 ask() 放大预算重试,这层重试同参数只是白等一次长生成。"""
    a = _ScriptedAdapter(
        stream_script=[EmptyContentError("空", budget_bound=True)],
        once_script=["不该用到"],
    )
    with pytest.raises(EmptyContentError):
        _run(a)
    assert a.calls == ["stream"]


# ---------- 非流式起步(prefer_stream=False)时仍会升级到流式 ----------

def test_524_from_non_stream_upgrades_to_stream():
    """524(CDN 掐断)→ 下一次尝试走流式,长生成不再被 100 秒墙掐死。"""
    a = _ScriptedAdapter(
        once_script=[_err(524)], stream_script=["第22章正文..."], prefer_stream=False
    )
    resp = _run(a)
    assert resp.content == "第22章正文..."
    assert a.calls == ["once", "stream"]


def test_network_timeout_is_retried():
    """网络层超时(httpx.TimeoutException)同样触发重试与流式升级。"""
    a = _ScriptedAdapter(
        once_script=[httpx.ReadTimeout("boom")],
        stream_script=["流式内容"],
        prefer_stream=False,
    )
    resp = _run(a)
    assert resp.content == "流式内容"
    assert a.calls == ["once", "stream"]


def test_exhausted_attempts_raise_readable_error():
    """连续失败 → 抛出带最后错误的可读异常。"""
    a = _ScriptedAdapter(stream_script=[_err(503), _err(503), _err(503)])
    with pytest.raises(UpstreamError, match="连续 3 次调用失败"):
        _run(a)
    assert a.calls == ["stream", "stream", "stream"]


def test_exhausted_with_silent_net_error_still_readable():
    """空消息网络异常(httpx 在 Windows/anyio 下 str 为 '',实测 DNS 失败即此形态)
    也要在"最后错误"里给出可读翻译,不能留一片空白。"""
    a = _ScriptedAdapter(stream_script=[httpx.ConnectError("")] * 3)
    with pytest.raises(UpstreamError) as exc:
        _run(a)
    msg = str(exc.value)
    assert "网络连接失败" in msg
    # 不能以空白的"最后错误: "结尾
    assert not msg.endswith("最后错误: ")


def test_exhausted_with_silent_timeout_translated():
    a = _ScriptedAdapter(stream_script=[httpx.ReadTimeout("")] * 3)
    with pytest.raises(UpstreamError, match="网络超时"):
        _run(a)


# ---------- check_upstream 的 52x 专属文案 ----------

def test_524_message_explains_cdn_timeout_not_base_url():
    """52x 错误要解释'生成耗时被 CDN 掐断',不再误导用户查 Base URL。"""
    resp = httpx.Response(
        524, text="<!DOCTYPE html><html><body>524</body></html>"
    )
    with pytest.raises(UpstreamError) as exc:
        check_upstream(resp, hint="确认 Base URL 含 /v1 且渠道支持 OpenAI 协议")
    msg = str(exc.value)
    assert "HTTP 524" in msg
    assert "CDN" in msg
    assert exc.value.retryable is True
    # 52x 场景不拼 Base URL 误导提示
    assert "Base URL" not in msg
