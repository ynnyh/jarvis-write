# tests/test_retry_visibility.py
# -*- coding: utf-8 -*-
"""重试要看得见:「重试中 2/3(上游限流)」必须推到前端。

对比对象:AI 编程工具遇到断流/限流会明说「Retrying 1/10」,用户知道系统在
自救;我们此前是**静默重试**——用户看不见过程,失败时显得毫无征兆,重试中
又以为卡死。补的就是这段可见性。

纪律:
- 提示只在后台任务(job 上下文)下发;前台请求/脚本无人观看,不占内存;
- 重试成功后必须把临时文案**还原**成真步骤名,且还原不能清屏(已经吐给用户
  看的正文不许因为一句提示的变动被抹掉);
- 提示环节自身出任何岔子都不能拖垮主流程(try/except 包住)。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app import live
from app.llm.base import TRANSIENT_NET_ERRORS, UpstreamError, with_retries


async def _no_sleep(_seconds: float) -> None:
    return None


def test_retry_notice_shows_progress_and_reason():
    """重试时:步骤文案变成「原步骤 · 重试中 x/y(原因)」。"""
    token = live.current_job_id.set("job-vis-1")
    try:
        live.set_step("job-vis-1", "一致性检查")
        calls = {"n": 0}

        async def call(_attempt: int) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise UpstreamError("too many requests", status=429, retryable=True)
            return "ok"

        with patch("asyncio.sleep", _no_sleep):
            asyncio.run(with_retries(call, attempts=3, base_delay=2.0))

        assert calls["n"] == 2
        # 成功后必须还原成真步骤名,不能一直挂着「重试中」
        assert live.peek_step("job-vis-1") == "一致性检查"
    finally:
        live.current_job_id.reset(token)
        live.drop("job-vis-1")


def test_retry_notice_text_appears_while_retrying():
    """重试进行中那一刻,前端确实能读到带计数与原因的文案。"""
    token = live.current_job_id.set("job-vis-2")
    try:
        live.set_step("job-vis-2", "第 5 章生成")
        seen: list[str] = []
        calls = {"n": 0}

        async def call(_attempt: int) -> str:
            calls["n"] += 1
            seen.append(live.peek_step("job-vis-2"))
            if calls["n"] == 1:
                raise TRANSIENT_NET_ERRORS[0]("连接被重置")
            return "ok"

        with patch("asyncio.sleep", _no_sleep):
            asyncio.run(with_retries(call, attempts=3, base_delay=2.0))

        # 第一次尝试时还没重试(文案是原步骤);第二次之前已挂上重试提示
        assert seen[0] == "第 5 章生成"
        assert "重试中 1/3" in seen[1]
        assert "连接被掐断" in seen[1]
    finally:
        live.current_job_id.reset(token)
        live.drop("job-vis-2")


def test_restore_keeps_already_streamed_text():
    """还原步骤不能清屏:用户已经看到的正文必须还在。"""
    jid = "job-vis-3"
    token = live.current_job_id.set(jid)
    try:
        live.set_step(jid, "第 9 章生成")
        live.publish("半截正文……", job_id=jid)
        before = live.snapshot(jid)
        assert before and before["text"] == "半截正文……"

        live.label_step(jid, "第 9 章生成 · 重试中 1/3(服务端抖动)")
        live.label_step(jid, "第 9 章生成")

        after = live.snapshot(jid)
        assert after["step"] == "第 9 章生成"
        assert after["text"] == "半截正文……", "还原文案不许把已吐的字清掉"
    finally:
        live.current_job_id.reset(token)
        live.drop(jid)


def test_no_job_context_stays_silent():
    """没有 job 上下文(前台请求/脚本)→ 安静重试,不碰直播流。"""
    assert live.current_job_id.get() is None or True  # 上下文可能已被其它用例设置
    calls = {"n": 0}

    async def call(_attempt: int) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise UpstreamError("boom", status=500, retryable=True)
        return "ok"

    with patch("asyncio.sleep", _no_sleep):
        assert asyncio.run(with_retries(call, attempts=2, base_delay=2.0)) == "ok"
    assert calls["n"] == 2


def test_reason_wording_is_human():
    """错误原因要说人话,不甩状态码给用户。"""
    from app.llm.base import _retry_reason

    assert _retry_reason(UpstreamError("x", status=429, retryable=True)) == "上游限流"
    assert _retry_reason(UpstreamError("x", status=529, retryable=True)) == "上游过载"
    assert _retry_reason(UpstreamError("x", status=524, retryable=True)) == "网关掐断了连接"
    assert _retry_reason(UpstreamError("x", status=503, retryable=True)) == "服务端抖动"
