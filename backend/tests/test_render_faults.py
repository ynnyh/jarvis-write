# tests/test_render_faults.py
# -*- coding: utf-8 -*-
"""出片链路故障路径测试:让 client.py 的真 httpx 代码跑起来。

既有 test_render.py 全部 patch 在 service/api 层,client 本身零执行——
这里用 httpx.MockTransport 替换传输层,超时/非200/畸形JSON/余额不足逐一打穿。
"""
import asyncio

import httpx
import pytest

from app.engines.render import client
from app.engines.render.client import RenderError

BASE = "https://platform.example"


def _patch_transport(monkeypatch, handler):
    """把 client 内部 new 出来的 AsyncClient 换成 MockTransport。"""
    real = httpx.AsyncClient

    def factory(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(**kw)

    monkeypatch.setattr(client.httpx, "AsyncClient", factory)


# ---------------------------------------------------------------------------
# submit 四态
# ---------------------------------------------------------------------------

def test_submit_timeout(monkeypatch):
    def handler(req):
        raise httpx.ConnectTimeout("slow")

    _patch_transport(monkeypatch, handler)
    with pytest.raises(RenderError, match="超时"):
        asyncio.run(client.submit(BASE, "tok", "wf", {}))


def test_submit_non200(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(RenderError, match="HTTP 500"):
        asyncio.run(client.submit(BASE, "tok", "wf", {}))


def test_submit_bad_json(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, text="<html>"))
    with pytest.raises(RenderError, match="无法解析"):
        asyncio.run(client.submit(BASE, "tok", "wf", {}))


def test_submit_rejected_by_platform(monkeypatch):
    """200 但业务拒绝(如余额不足):平台 msg 要原样上屏,不能被吞。"""
    _patch_transport(monkeypatch, lambda req: httpx.Response(
        200, json={"code": "balance_not_enough", "msg": "余额不足"}))
    with pytest.raises(RenderError, match="余额不足"):
        asyncio.run(client.submit(BASE, "tok", "wf", {}))


def test_submit_missing_token():
    """没配令牌不发出请求,直接人话提示。"""
    with pytest.raises(RenderError, match="令牌"):
        asyncio.run(client.submit(BASE, "", "wf", {}))


def test_submit_ok_returns_task_id(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(
        200, json={"code": "success", "data": {"task_id": "pt-123"}}))
    assert asyncio.run(client.submit(BASE, "tok", "wf", {})) == "pt-123"


# ---------------------------------------------------------------------------
# poll 单次行为:瞬时故障可重试,钱/令牌问题快失败
# ---------------------------------------------------------------------------

def test_poll_network_error_is_transient(monkeypatch):
    """网络异常不再被吞成 running:抛瞬时错误,交给上层带计数地重试。"""
    from app.engines.render.client import PollTransientError

    _patch_transport(monkeypatch, lambda req: (_ for _ in ()).throw(httpx.ConnectError("flaky")))
    with pytest.raises(PollTransientError):
        asyncio.run(client.poll(BASE, "tok", "t1"))


def test_poll_5xx_is_transient(monkeypatch):
    """平台 5xx 单次不当失败:长轮询里网关抖一下是常态。"""
    from app.engines.render.client import PollTransientError

    _patch_transport(monkeypatch, lambda req: httpx.Response(502, text="gateway"))
    with pytest.raises(PollTransientError):
        asyncio.run(client.poll(BASE, "tok", "t1"))


def test_poll_bad_json_is_transient(monkeypatch):
    from app.engines.render.client import PollTransientError

    _patch_transport(monkeypatch, lambda req: httpx.Response(200, text="<html>"))
    with pytest.raises(PollTransientError):
        asyncio.run(client.poll(BASE, "tok", "t1"))


@pytest.mark.parametrize("code", [401, 402, 403])
def test_poll_auth_balance_fast_fail(monkeypatch, code):
    """401/402/403 是令牌/钱的问题:快失败并说人话,不当抖动重试烧时间。"""
    _patch_transport(monkeypatch, lambda req: httpx.Response(code, text="no"))
    with pytest.raises(RenderError, match="令牌|余额"):
        asyncio.run(client.poll(BASE, "tok", "t1"))


# ---------------------------------------------------------------------------
# poll_with_retry:弹性窗口
# ---------------------------------------------------------------------------

def test_poll_with_retry_transient_then_success(monkeypatch):
    """连续抖动后恢复:弹性窗口内不放弃。"""
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise httpx.ConnectError("flaky")
        return httpx.Response(200, json={
            "data": {"status": "SUCCESS", "results": [{"url": "https://x/v.mp4"}]}})

    _patch_transport(monkeypatch, handler)
    status, urls = asyncio.run(client.poll_with_retry(BASE, "tok", "t1"))
    assert status == "success" and urls == ["https://x/v.mp4"]


def test_poll_with_retry_gives_up_after_streak(monkeypatch):
    """连续失败超上限:明确放弃,不无限吊着。"""
    _patch_transport(monkeypatch, lambda req: (_ for _ in ()).throw(httpx.ConnectError("down")))
    with pytest.raises(RenderError, match="多次查询"):
        asyncio.run(client.poll_with_retry(BASE, "tok", "t1"))


def test_poll_with_retry_success_resets_streak(monkeypatch):
    """失败-成功交替不累积:只要偶尔通,就一直等下去。"""
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            return httpx.Response(200, json={"data": {"status": "RUNNING"}})
        raise httpx.ConnectError("flaky")

    _patch_transport(monkeypatch, handler)

    async def drive():
        for _ in range(10):  # 10 轮:每轮两败一成,计数次次被清零
            status, _ = await client.poll_with_retry(BASE, "tok", "t1")
            if status != "running":
                return status
        return "running"

    assert asyncio.run(drive()) == "running"
    assert calls["n"] >= 27  # 确实扛过了远超 5 次的累计失败


# ---------------------------------------------------------------------------
# job 体系兜底:取消不永挂、启动清理出片任务
# ---------------------------------------------------------------------------

def test_spawn_job_cancelled_marks_failed():
    """worker 被 asyncio 取消时,job 不能永远停在 running(CancelledError 是
    BaseException,裸 except Exception 兜不住)。"""
    from contextlib import suppress

    from app import jobs

    async def driver():
        started = asyncio.Event()

        async def work(progress):
            started.set()
            await asyncio.sleep(60)

        job_id = jobs.spawn_job("render-cancel-test", work)
        await started.wait()
        victim = next((t for t in jobs._bg_tasks if not t.done()), None)
        assert victim is not None
        victim.cancel()
        with suppress(asyncio.CancelledError):
            await victim
        return job_id

    job_id = asyncio.run(driver())
    job = jobs.get_job(job_id)
    assert job is not None and job["status"] == "error"
    assert "取消" in job["error"]


def test_cleanup_stuck_render_tasks():
    """启动清理:queued/running 的 RenderTask 随进程死了,一并标 failed;
    已成功的任务不动。"""
    from app.db import models  # noqa: F401 — 注册全部模型
    from app.db.base import Base
    from app.db.models import RenderTask, User
    from app.db.session import SessionLocal, engine
    from app.jobs import cleanup_stuck_jobs

    Base.metadata.create_all(engine)  # 幂等:不起 app 单跑时自建表

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "render_cleanup_user").first()
        if user is None:
            user = User(username="render_cleanup_user")
            db.add(user)
            db.commit()
        r1 = RenderTask(user_id=user.id, line="drama", kind="i2v",
                        status="running", provider_task_id="pt-x")
        r2 = RenderTask(user_id=user.id, line="drama", kind="i2v", status="queued")
        r3 = RenderTask(user_id=user.id, line="drama", kind="i2v",
                        status="success", result_path="render/r1.mp4")
        db.add_all([r1, r2, r3])
        db.commit()
        id1, id2, id3 = r1.id, r2.id, r3.id

    cleanup_stuck_jobs()

    with SessionLocal() as db:
        assert db.get(RenderTask, id1).status == "failed"
        assert "重启" in db.get(RenderTask, id1).error
        assert db.get(RenderTask, id2).status == "failed"
        assert db.get(RenderTask, id3).status == "success"


def test_fetch_bytes_non200(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(404))
    with pytest.raises(RenderError, match="HTTP 404"):
        asyncio.run(client.fetch_bytes("https://cdn.example/v.mp4"))


def test_fetch_bytes_ok(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=b"vid"))
    assert asyncio.run(client.fetch_bytes("https://cdn.example/v.mp4")) == b"vid"
