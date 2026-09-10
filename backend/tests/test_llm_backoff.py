# tests/test_llm_backoff.py
# -*- coding: utf-8 -*-
"""LLM 重试退避:按错误类型分流等待时长。

起因(2026-09-09):AI 编程工具在断流/限流时会显式提示「重试中 1/10」,
而我们是静默重试。补可见性之前,先把退避策略本身做对——原实现一律
2s→4s,对「限流」太急(继续被拒)、对「连接被掐断」太慢(白等)、
批量并发时还会同时醒来把刚恢复的渠道再打垮。

这里锁死分流规则:
- 429/529(限流/过载) → 递增且不低于 4s×(轮次),并优先服从上游 Retry-After;
- 网络超时/连接被重置 → 快速重试(≤1s):换条连接可能就好,久等没意义;
- 其它 5xx/瞬时态 → 标准指数退避;
- 一律带抖动(0~25%)且封顶 60s:批量任务并发重试要错开。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest

from app.llm.base import TRANSIENT_NET_ERRORS, UpstreamError, _backoff_delay, _parse_retry_after, with_retries


class _Resp:
    """够用的响应替身:只需要 headers。"""

    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = headers or {}


def test_retry_after_header_is_parsed():
    assert _parse_retry_after(_Resp({"retry-after": "12"})) == 12.0
    # RFC 7231 允许 HTTP-date 形态:不值得为此引日期解析,认不出就返回 None
    assert _parse_retry_after(_Resp({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
    assert _parse_retry_after(_Resp({})) is None


def test_retry_after_is_capped():
    """上游让等 10 分钟也不能照等:用户会以为卡死,封顶 60s。"""
    assert _parse_retry_after(_Resp({"retry-after": "600"})) == 60.0


def test_upstream_retry_after_wins():
    """上游明确给了等待时长就听它的,别自己瞎猜。"""
    exc = UpstreamError("限流", status=429, retryable=True, retry_after=30.0)
    assert _backoff_delay(exc, attempt=0, base_delay=2.0) >= 30.0


def test_rate_limit_waits_longer_than_plain_5xx():
    """429 必须比普通 5xx 等得久:2 秒后再去只会再吃一次拒绝。"""
    limited = UpstreamError("限流", status=429, retryable=True)
    server_err = UpstreamError("服务端错", status=500, retryable=True)
    for attempt in (0, 1, 2):
        assert _backoff_delay(limited, attempt, 2.0) > _backoff_delay(server_err, attempt, 2.0)


def test_network_error_retries_fast():
    """连接被掐断/超时:快速重试,久等没有意义。"""
    delay = _backoff_delay(httpx.ConnectError("连接被重置"), attempt=0, base_delay=2.0)
    assert delay <= 1.25  # 1s 基准 + 抖动上界


def test_plain_5xx_is_exponential():
    server_err = UpstreamError("服务端错", status=503, retryable=True)
    d0 = _backoff_delay(server_err, 0, 2.0)
    d1 = _backoff_delay(server_err, 1, 2.0)
    assert 2.0 <= d0 <= 2.5
    assert 4.0 <= d1 <= 5.0


def test_delay_never_exceeds_cap():
    limited = UpstreamError("限流", status=429, retryable=True)
    for attempt in range(10):
        assert _backoff_delay(limited, attempt, 2.0) <= 60.0


# ---------- with_retries 集成:真的按分类时长等待 ----------

def test_with_retries_uses_classified_delay():
    """连续 429 时,实际 sleep 时长必须服从分类策略而不是固定 2s→4s。"""
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    calls = {"n": 0}

    async def call(_attempt: int) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise UpstreamError("限流", status=429, retryable=True)
        return "ok"

    with patch("asyncio.sleep", _fake_sleep):
        result = asyncio.run(with_retries(call, attempts=3, base_delay=2.0))

    assert result == "ok"
    assert len(slept) == 2
    # 两轮都在 4s/8s 基准之上(+抖动),绝不是原来的 2s/4s
    assert slept[0] >= 4.0
    assert slept[1] >= 8.0


def test_with_retries_stops_on_non_retryable():
    """非 retryable 错误(鉴权/参数)一次都不重试。"""

    async def call(_attempt: int) -> str:
        raise UpstreamError("401 鉴权失败", status=401, retryable=False)

    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    with patch("asyncio.sleep", _fake_sleep):
        with pytest.raises(UpstreamError):
            asyncio.run(with_retries(call, attempts=3, base_delay=2.0))
    assert slept == []


def test_network_errors_are_retried():
    """网络层瞬时异常同样进重试圈(且走快速通道)。"""

    calls = {"n": 0}

    async def call(_attempt: int) -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise TRANSIENT_NET_ERRORS[0]("超时")
        return "ok"

    async def _fake_sleep(_seconds: float) -> None:
        return None

    with patch("asyncio.sleep", _fake_sleep):
        assert asyncio.run(with_retries(call, attempts=3, base_delay=2.0)) == "ok"
    assert calls["n"] == 2
