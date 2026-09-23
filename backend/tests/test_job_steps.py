# tests/test_job_steps.py
# -*- coding: utf-8 -*-
"""任务步骤检查点 + token 任务级聚合(Phase 4.2)。

验证点:
- record_step 落库/覆盖(同 job+step 重试留最新);
- clips 批量生成后,job 查询带步骤清单(每张卡一条 take:N);
- llm_usage 按 job_id 聚合出 tokens(调用量/上行/下行);
- 步骤/聚合失败静默,不拖垮任务查询。
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

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


def _wait_job(client: TestClient, headers: dict, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/api/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] != "running":
            return job
        assert time.monotonic() < deadline, f"job {job_id} 超时: {job}"
        time.sleep(0.02)


def test_record_step_upsert_and_read(client):
    """record_step 同 (job, step) 覆盖写;get_job_steps 按时间序读出。

    client 夹具保证应用已启动(表已建)——记录器依赖库表。
    """
    from app.jobs import get_job_steps, record_step

    record_step("job_step_ut", "scene:1", "done", output={"words": 612})
    record_step("job_step_ut", "scene:2", "failed", error="超时")
    record_step("job_step_ut", "scene:1", "done", output={"words": 700})  # 重试覆盖

    steps = get_job_steps("job_step_ut")
    assert [s["step_key"] for s in steps] == ["scene:1", "scene:2"]
    by_key = {s["step_key"]: s for s in steps}
    assert by_key["scene:1"]["output"]["words"] == 700  # 最新一次
    assert by_key["scene:2"]["status"] == "failed"
    assert "超时" in by_key["scene:2"]["error"]


def test_clips_batch_records_take_steps(client):
    """clips 两段式批产:每张卡的展开落一条 take:N 步骤,job 查询可见。"""
    from tests.test_clips import _EXPAND, _HEAD, _PhaseAdapter  # 复用两段式桩

    headers = _auth(client, "steps_clips_user")
    r = client.post("/api/clips", headers=headers, json={
        "theme": "regret", "duration_s": 15, "direction": "live",
    })
    assert r.status_code == 200, r.text
    cid = r.json()["clip_row"]["id"]

    with patch("app.engines.clips.batch.get_adapter_for", return_value=_PhaseAdapter(_HEAD, _EXPAND)):
        r = client.post(f"/api/clips/{cid}/generate", headers=headers)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        job = _wait_job(client, headers, job_id)

    assert job["status"] == "done", job
    steps = job.get("steps") or []
    take_steps = [s for s in steps if s["step_key"].startswith("take:")]
    assert len(take_steps) == 3  # 三条切入各一步
    assert all(s["status"] == "done" for s in take_steps)
    assert all("shots" in (s["output"] or {}) for s in take_steps)


def test_job_tokens_aggregation(client):
    """llm_usage 按 job_id 聚合:任务查询带 tokens 总账。"""
    from app.db.models import LlmUsage
    from app.db.session import SessionLocal

    headers = _auth(client, "steps_tokens_user")
    # 造一个归属本人的已完成任务:用 cancel 的前置——先建任务再查
    # (更直接:任意任务即可,这里用 architecture-async 会引 LLM mock 链,太重;
    #  改为手插 usage 行 + 用 list 端点确认不了——需要真实 job 行。用 create_job。)
    from app.jobs import create_job, fail_job
    job_id = create_job("tokens-ut-0")
    fail_job(job_id, "收尾便于查询历史")

    with SessionLocal() as db:
        db.add(LlmUsage(user_id=1, model="mock-1", prompt_tokens=100,
                        completion_tokens=50, job_id=job_id))
        db.add(LlmUsage(user_id=1, model="mock-1", prompt_tokens=30,
                        completion_tokens=20, job_id=job_id))
        db.commit()

    # 归属:jobs 表 owner_id 是 None(直接 create_job)→ 查询端点按"不存在"拦。
    # 这里直接调聚合函数验证账目口径,端点归属在其余用例覆盖。
    from app.api.misc import _job_tokens
    tokens = _job_tokens(job_id)
    assert tokens == {"prompt": 130, "completion": 70, "calls": 2}
    assert _job_tokens("no-such-job") is None
