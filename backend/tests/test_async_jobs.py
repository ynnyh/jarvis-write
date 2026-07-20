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
