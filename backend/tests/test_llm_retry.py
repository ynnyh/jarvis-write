# backend/tests/test_llm_retry.py
# -*- coding: utf-8 -*-
"""瞬时错误重试与 CDN 掐断(524)流式兜底。

不依赖真实 HTTP:子类化适配器,用脚本化的成功/异常序列驱动
_complete_once / _complete_via_stream,验证重试与降级路径。
"""
import asyncio

import httpx
import pytest

from app.llm.base import LLMResponse, UpstreamError, check_upstream
from app.llm.openai_compatible import OpenAICompatibleAdapter


def _err(status: int, retryable: bool = True) -> UpstreamError:
    return UpstreamError(f"HTTP {status}", status=status, retryable=retryable)


class _ScriptedAdapter(OpenAICompatibleAdapter):
    """按脚本出牌:once 序列失败/成功,记录每次走的是非流式还是流式。"""

    def __init__(
        self,
        script: list,
        stream_result: str = "流式内容",
        stream_script: list | None = None,
    ):
        super().__init__(api_key="sk-x", model_name="m")
        self.retry_base_delay = 0  # 测试不等退避
        self._script = list(script)
        self._stream_result = stream_result
        self._stream_script = list(stream_script) if stream_script else None
        self.calls: list[str] = []

    async def _complete_once(self, messages):
        self.calls.append("once")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=item, model="m")

    async def _complete_via_stream(self, messages):
        self.calls.append("stream")
        if self._stream_script:
            item = self._stream_script.pop(0)
            if isinstance(item, Exception):
                raise item
            return LLMResponse(content=item, model="m")
        return LLMResponse(content=self._stream_result, model="m")


def _run(adapter: _ScriptedAdapter) -> LLMResponse:
    return asyncio.run(adapter.complete(adapter.to_messages("hi")))


def test_retryable_error_then_success():
    """429 抖动 → 退避后重试成功(第二次起改走流式聚合)。"""
    a = _ScriptedAdapter([_err(429), "不会用到"])
    resp = _run(a)
    assert resp.content == "流式内容"
    assert a.calls == ["once", "stream"]


def test_524_falls_back_to_stream():
    """524(CDN 掐断)→ 下一次尝试走流式,长生成不再被 100 秒墙掐死。"""
    a = _ScriptedAdapter([_err(524)], stream_result="第22章正文...")
    resp = _run(a)
    assert resp.content == "第22章正文..."
    assert a.calls == ["once", "stream"]


def test_non_retryable_fails_fast():
    """401 鉴权错误重试无意义:立即抛出,不浪费尝试。"""
    a = _ScriptedAdapter([_err(401, retryable=False), "不会用到"])
    with pytest.raises(UpstreamError) as exc:
        _run(a)
    assert exc.value.status == 401
    assert a.calls == ["once"]


def test_network_timeout_is_retried():
    """网络层超时(httpx.TimeoutException)同样触发重试与流式降级。"""
    a = _ScriptedAdapter([httpx.ReadTimeout("boom")])
    resp = _run(a)
    assert resp.content == "流式内容"
    assert a.calls == ["once", "stream"]


def test_exhausted_attempts_raise_readable_error():
    """连续失败 → 抛出带最后错误的可读异常。"""
    a = _ScriptedAdapter(
        [_err(503)], stream_script=[_err(503), _err(503)]
    )
    with pytest.raises(UpstreamError, match="连续 3 次调用失败"):
        _run(a)
    # 后两次走流式
    assert a.calls == ["once", "stream", "stream"]


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
