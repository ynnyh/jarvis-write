# tests/test_llm_throttle.py
# -*- coding: utf-8 -*-
"""主动限速器:并发上限串行化、RPM 滑动窗口排队、0=不限直通、工厂接线。"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.llm import throttle
from app.llm.base import LLMMessage
from app.llm.openai_compatible import OpenAICompatibleAdapter
from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_gates():
    throttle._gates.clear()
    yield
    throttle._gates.clear()


@pytest.fixture
def db_session(client):
    """依赖 client fixture 以触发 lifespan 建表;用完即关。"""
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_zero_limits_pass_through_immediately():
    """两个上限都是 0:直通,不建闸门。"""

    async def run():
        started = time.monotonic()
        async with throttle.slot("k", 0, 0):
            pass
        return time.monotonic() - started

    elapsed = asyncio.run(run())
    assert elapsed < 0.2
    assert not throttle._gates  # 零开销直通:不该留闸门


def test_concurrency_cap_serializes():
    """并发上限 1:两个任务必须排队,重叠执行数峰值 = 1。"""

    async def run():
        active = 0
        peak = 0

        async def worker():
            nonlocal active, peak
            async with throttle.slot("k", 1, 0):
                nonlocal_checked = True
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.08)
                active -= 1

        await asyncio.gather(worker(), worker())
        return peak

    assert asyncio.run(run()) == 1


def test_unlimited_concurrency_runs_parallel():
    """不限并发(0):同键任务并行跑,重叠峰值 = 2。"""

    async def run():
        active = 0
        peak = 0

        async def worker():
            nonlocal active, peak
            async with throttle.slot("k", 0, 0):
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.08)
                active -= 1

        await asyncio.gather(worker(), worker())
        return peak

    assert asyncio.run(run()) == 2


def test_rpm_window_queues_excess():
    """RPM=2,窗口 0.5 秒:第 3 个请求必须等到窗口滑出才放行。"""

    async def run():
        admitted: list[float] = []

        async def worker(i: int):
            async with throttle.slot("k", 0, 2, window_seconds=0.5):
                admitted.append(time.monotonic())
                await asyncio.sleep(0.01)

        start = time.monotonic()
        await asyncio.gather(*(worker(i) for i in range(4)))
        total = time.monotonic() - start
        return admitted, total

    admitted, total = asyncio.run(run())
    # 前 2 个立即放行,后 2 个要等窗口滑出 → 总时长至少跨过一次窗口
    assert total >= 0.45
    assert len(admitted) == 4


def test_slot_release_on_exception():
    """槽位持有者抛异常也要释放,不能把闸门卡死。"""

    async def run():
        with pytest.raises(RuntimeError):
            async with throttle.slot("k", 1, 0):
                raise RuntimeError("boom")
        # 再进一次能立刻拿到(说明上一个持有者已释放)
        async with throttle.slot("k", 1, 0):
            pass

    asyncio.run(run())


def test_adapter_throttle_key_uses_channel_and_model():
    """闸门维度 = 渠道 + 模型:同中转同模型共享,不同渠道互不影响。"""
    a = OpenAICompatibleAdapter(
        api_key="k", model_name="deepseek-chat", base_url="https://relay.example.com/v1"
    )
    b = OpenAICompatibleAdapter(
        api_key="k", model_name="deepseek-chat", base_url="https://relay.example.com/v1"
    )
    c = OpenAICompatibleAdapter(
        api_key="k", model_name="deepseek-chat", base_url="https://other.example.com/v1"
    )
    assert a.throttle_key() == b.throttle_key()
    assert a.throttle_key() != c.throttle_key()


def test_factory_passes_rate_limits(db_session, monkeypatch):
    """create_llm_adapter 把配置里的并发/RPM 传给适配器。"""
    from app.auth import hash_password
    from app.db.models import ProviderConfig, User
    from app.llm.factory import create_llm_adapter

    user = User(username="th_user", password_hash=hash_password("pass123"))
    db_session.add(user)
    db_session.commit()
    db_session.add(
        ProviderConfig(
            user_id=user.id,
            name="t",
            interface_format="openai-compatible",
            api_key="enc",
            base_url="https://relay.example.com/v1",
            model="deepseek-chat",
            max_concurrency=3,
            rpm=25,
        )
    )
    db_session.commit()

    from app.auth import current_user_id

    token = current_user_id.set(user.id)
    try:
        adapter = create_llm_adapter(config_id=None, provider="openai-compatible")
    finally:
        current_user_id.reset(token)
    assert adapter.max_concurrency == 3
    assert adapter.rpm == 25
    assert "relay.example.com" in adapter.throttle_key()
