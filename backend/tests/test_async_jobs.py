# tests/test_async_jobs.py
# -*- coding: utf-8 -*-
"""架构/蓝图异步生成接口测试(TestClient + mock LLM)。

验证点:
- POST .../architecture-async / blueprint-async 立即返回 job_id
- job 归属隔离:他人查 job / 对他人项目发起 → 404
- mock LLM 下任务跑完:结果可读、数据落库
- 蓝图前置校验:无架构 → 400
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_pipeline import MOCK_ARCH_REPLIES, MOCK_BLUEPRINT_REPLY, MockAdapter

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _create_project(client: TestClient, headers: dict, title: str = "异步测试书") -> dict:
    r = client.post(
        "/api/projects",
        headers=headers,
        json={"title": title, "target_chapters": 3},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    """轮询 job 直到 done/error。后台 task 跑在 TestClient 的事件循环上,请求即驱动。"""
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] != "running":
            return job
        assert time.monotonic() < deadline, f"job {job_id} 超时未完成: {job}"
        time.sleep(0.02)


def test_architecture_async_full_flow(client):
    """架构异步生成:返回 job_id → 轮询完成 → 结果与落库一致。"""
    from app.engines.pipeline import architecture as arch_mod

    headers = _auth(client, "async_arch_user")
    other = _auth(client, "async_arch_other")
    p = _create_project(client, headers)

    adapter = MockAdapter(MOCK_ARCH_REPLIES)
    with patch.object(arch_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/architecture-async",
            headers=headers,
            json={"tendency": {}},
        )
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]

        # 归属隔离:他人查 job → 404;对他人项目发起 → 404
        assert client.get(f"/api/jobs/{job_id}", headers=other).status_code == 404
        assert client.post(
            f"/api/projects/{p['id']}/architecture-async",
            headers=other,
            json={"tendency": {}},
        ).status_code == 404

        job = _wait_job(client, headers, job_id)

    assert job["status"] == "done", job
    assert job["kind"] == f"architecture-{p['id']}"
    assert "林晚" in job["result"]["core_seed"]
    assert len(adapter.calls) == 4  # 雪花四步都走了

    # 落库可读:与同步端点的产出一致
    r = client.get(f"/api/projects/{p['id']}/architecture", headers=headers)
    assert r.status_code == 200
    assert r.json()["core_seed"] == job["result"]["core_seed"]


def test_blueprint_async_full_flow(client):
    """蓝图异步生成:先异步架构,再异步蓝图,警告与大纲落库可读。"""
    from app.engines.pipeline import architecture as arch_mod
    from app.engines.pipeline import blueprint as bp_mod

    headers = _auth(client, "async_bp_user")
    p = _create_project(client, headers, "异步蓝图书")

    with patch.object(
        arch_mod, "get_adapter_for", return_value=MockAdapter(MOCK_ARCH_REPLIES)
    ):
        job_id = client.post(
            f"/api/projects/{p['id']}/architecture-async",
            headers=headers,
            json={"tendency": {}},
        ).json()["job_id"]
        assert _wait_job(client, headers, job_id)["status"] == "done"

    with patch.object(
        bp_mod, "get_adapter_for", return_value=MockAdapter([MOCK_BLUEPRINT_REPLY])
    ):
        r = client.post(
            f"/api/projects/{p['id']}/blueprint-async",
            headers=headers,
            json={"tendency": {}},
        )
        assert r.status_code == 200, r.text
        job = _wait_job(client, headers, r.json()["job_id"])

    assert job["status"] == "done", job
    assert job["kind"] == f"blueprint-{p['id']}"
    assert job["result"]["warnings"] == []
    assert len(job["result"]["outlines"]) == 3
    assert job["result"]["outlines"][2]["title"] == "交易与背叛"

    # 落库可读
    r = client.get(f"/api/projects/{p['id']}/outlines", headers=headers)
    assert r.status_code == 200
    assert [o["chapter_number"] for o in r.json()] == [1, 2, 3]


def test_blueprint_async_requires_architecture(client):
    """没有架构直接发起异步蓝图 → 400(同步端点同一校验)。"""
    headers = _auth(client, "async_bp_noarch")
    p = _create_project(client, headers, "无架构书")
    r = client.post(
        f"/api/projects/{p['id']}/blueprint-async",
        headers=headers,
        json={"tendency": {}},
    )
    assert r.status_code == 400
    assert "架构" in r.json()["detail"]


def test_architecture_async_llm_failure_marks_job_error(client):
    """LLM 抛错 → job 进 error 态并带原因,不会卡死在 running。"""

    class _BoomAdapter:
        async def ask(self, prompt, system=None):
            raise RuntimeError("connection refused")

    from app.engines.pipeline import architecture as arch_mod

    headers = _auth(client, "async_arch_fail")
    p = _create_project(client, headers, "失败书")

    with patch.object(arch_mod, "get_adapter_for", return_value=_BoomAdapter()):
        job_id = client.post(
            f"/api/projects/{p['id']}/architecture-async",
            headers=headers,
            json={"tendency": {}},
        ).json()["job_id"]
        job = _wait_job(client, headers, job_id)

    assert job["status"] == "error"
    assert "connection refused" in job["error"]
    # 失败不落库
    assert client.get(
        f"/api/projects/{p['id']}/architecture", headers=headers
    ).status_code == 404


# ---------- 重启韧性:任务丢失的快速反馈(P2-13) ----------

def _mk_job_row(owner_id: int, status: str, result=None) -> str:
    """直接往 DB 插一条 job(绕过内存注册表,模拟重启后的状态)。"""
    import uuid

    from app.db.models import Job
    from app.db.session import SessionLocal

    jid = uuid.uuid4().hex[:12]
    with SessionLocal() as db:
        db.add(Job(id=jid, kind="test", status=status, owner_id=owner_id,
                   stage="排队中", result=result))
        db.commit()
    return jid


def test_job_status_db_fallback_after_restart(client):
    """内存 miss → DB 兜底:已结束任务结果仍可查;挂着 running 的按
    「服务重启,任务已中断」报错,不让前端拿到永远跑不完的假进度。"""
    import uuid

    headers = _auth(client, "restart_fallback")
    # 拿 owner_id:Job.owner_id 记的是创建任务时的用户 id
    from app.db.models import User
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        uid = db.query(User).filter(
            User.username == "restart_fallback").one().id

    done_jid = _mk_job_row(uid, "done", result={"ok": 1})
    run_jid = _mk_job_row(uid, "running")

    # 已结束:重启后结果照常可读
    r = client.get(f"/api/jobs/{done_jid}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    assert r.json()["result"] == {"ok": 1}

    # 还挂 running(重启瞬间的窗口):立刻报「服务重启」错误
    r = client.get(f"/api/jobs/{run_jid}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "error"
    assert "服务重启" in r.json()["error"]

    # 归属隔离不因兜底而松动:他人的任务仍按不存在处理
    other = _mk_job_row(uid + 999999 if uid < 999999 else uid - 1, "done")
    r = client.get(f"/api/jobs/{other}", headers=headers)
    assert r.status_code == 404
    assert uuid  # 避免 import 挪到顶部时的未用告警


def test_cleanup_marks_fresh_running_jobs_failed(client):
    """启动清理不再等 30 分钟:进程死了任务必死,全部 running 立即标失败。"""
    from app.jobs import cleanup_stuck_jobs

    headers = _auth(client, "restart_cleanup")
    from app.db.models import User
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        uid = db.query(User).filter(
            User.username == "restart_cleanup").one().id
    jid = _mk_job_row(uid, "running")  # 刚建的,按旧语义不会被清理

    cleanup_stuck_jobs()

    from app.db.session import SessionLocal as S
    with S() as db:
        from app.db.models import Job
        row = db.get(Job, jid)
        assert row.status == "error"
        assert "重启" in row.error
