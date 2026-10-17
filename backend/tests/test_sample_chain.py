# tests/test_sample_chain.py
# -*- coding: utf-8 -*-
"""样稿链/全链:先小后大(docs/32 §2.2)。

守的是「先出能读的东西」这条纪律:样稿只铺前三章(不惊动长书的卷纲),
步骤幂等(已有产物就跳过),任务归属与去重正确,失败收敛进 job 而不是抛给请求。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _project(client: TestClient, headers: dict, title: str = "链书") -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------- 纯逻辑 ----------


def test_sample_target_bounds():
    """样稿章数:最多 3 章,短故事 1 章,目标缺失也不为 0。"""
    import app.api.projects.chain as chain

    class _P:
        def __init__(self, n):
            self.target_chapters = n

    assert chain._sample_target(_P(150)) == 3
    assert chain._sample_target(_P(3)) == 3
    assert chain._sample_target(_P(1)) == 1
    assert chain._sample_target(_P(0)) == 1


def test_spawn_sample_chain_dedupes_running_job(monkeypatch):
    """同项目样稿链已在跑 → 复用那个 job,不新建(防重入)。"""
    import app.api.projects.chain as chain

    monkeypatch.setattr(chain, "list_running", lambda prefix: [("existing123", {"kind": prefix + "9"})])
    created: list[str] = []
    monkeypatch.setattr(chain, "create_job", lambda kind: created.append(kind) or "new")
    assert chain.spawn_sample_chain(9) == "existing123"
    assert created == []


# ---------- 端点契约 ----------


def test_sample_chain_requires_confirmed_order(client):
    headers = _auth(_register(client, "chain_guard")["token"])
    pid = _project(client, headers)
    r = client.post(f"/api/projects/{pid}/sample-chain", headers=headers, json={})
    assert r.status_code == 409
    assert "理解卡" in r.json()["detail"]


def test_full_chain_requires_confirmed_order(client):
    headers = _auth(_register(client, "chain_guard2")["token"])
    pid = _project(client, headers)
    r = client.post(f"/api/projects/{pid}/full-chain", headers=headers)
    assert r.status_code == 409


def test_sample_chain_endpoint_spawns_and_records(client):
    """确认过的项目调样稿链 → 建 job(归属 pid)+ 记一条输入留痕。"""
    from app.db.models import GenInput, Project
    from app.db.session import SessionLocal

    headers = _auth(_register(client, "chain_spawn")["token"])
    pid = _project(client, headers)
    with SessionLocal() as db:
        row = db.get(Project, pid)
        row.brief = "书名(暂定):《玉玺》\n【故事内核】物语体自述"
        row.brief_confirmed = True
        db.commit()

    r = client.post(f"/api/projects/{pid}/sample-chain", headers=headers,
                    json={"restart": "blueprint", "directive": "开场再冷一点"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    from app.jobs import get_job

    job = get_job(job_id)
    assert job is not None
    assert job["kind"] == f"sample-chain-{pid}"   # 归属项目,复盘能按 pid 捞到
    assert job["project_id"] == pid

    with SessionLocal() as db:
        inputs = db.query(GenInput).filter_by(project_id=pid, kind="sample-chain").all()
        assert len(inputs) == 1
        assert inputs[0].payload["directive"] == "开场再冷一点"
        assert inputs[0].payload["restart"] == "blueprint"


# ---------- 编排:五步按序、幂等、失败收敛 ----------


def _run_captured(captured: list) -> None:
    """fire_and_track 被替换成「收集协程」,这里手动把它跑完(确定性,不靠轮询)。"""
    assert captured, "没有捕获到任务协程"
    asyncio.run(captured[0])


def test_sample_chain_runs_five_steps_in_order(client, monkeypatch):
    import app.api.projects.chain as chain
    from app.db.models import GenInput, Project
    from app.db.session import SessionLocal
    from app.jobs import get_job

    headers = _auth(_register(client, "chain_order")["token"])
    pid = _project(client, headers)
    with SessionLocal() as db:
        row = db.get(Project, pid)
        row.brief = "订单"
        row.brief_confirmed = True
        db.commit()

    calls: list[str] = []

    def make(name: str):
        async def _step(session, p, report, **kwargs):
            calls.append(name)
        return _step

    monkeypatch.setattr(chain, "_step_concept", make("concept"))
    monkeypatch.setattr(chain, "_step_premise", make("premise"))
    monkeypatch.setattr(chain, "_step_core_seed", make("core_seed"))
    monkeypatch.setattr(chain, "_step_sample_blueprint", make("blueprint"))
    monkeypatch.setattr(chain, "_step_sample_chapters", make("chapters"))
    captured: list = []
    monkeypatch.setattr(chain, "fire_and_track", lambda coro: captured.append(coro))

    job_id = chain.spawn_sample_chain(pid)
    _run_captured(captured)

    assert calls == ["concept", "premise", "core_seed", "blueprint", "chapters"]
    assert get_job(job_id)["status"] == "done"
    with SessionLocal() as db:
        assert db.query(GenInput).filter_by(project_id=pid, kind="sample-chain").count() == 1


def test_sample_chain_skips_existing_steps(client, monkeypatch):
    """幂等:概念/梗卡/种子/大纲都在且本次不带 directive → 只跑正文步。"""
    import app.api.projects.chain as chain
    from app.db.models import Outline, Premise, Project
    from app.db.session import SessionLocal

    headers = _auth(_register(client, "chain_idem")["token"])
    pid = _project(client, headers)
    with SessionLocal() as db:
        row = db.get(Project, pid)
        row.brief = "订单"
        row.brief_confirmed = True
        row.concept = {"logline": "玉玺自述两千年", "setting": "真实历史"}
        db.add(Premise(project_id=pid, kind="main", high_concept="玉玺见证", payoff="每朝一劫"))
        db.add(Outline(project_id=pid, chapter_number=1, title="受命"))
        db.commit()

    calls: list[str] = []

    def make(name: str):
        async def _step(session, p, report, **kwargs):
            calls.append(name)
        return _step

    monkeypatch.setattr(chain, "_step_concept", make("concept"))
    monkeypatch.setattr(chain, "_step_premise", make("premise"))
    monkeypatch.setattr(chain, "_step_core_seed", make("core_seed"))
    monkeypatch.setattr(chain, "_step_sample_blueprint", make("blueprint"))
    monkeypatch.setattr(chain, "_step_sample_chapters", make("chapters"))
    captured: list = []
    monkeypatch.setattr(chain, "fire_and_track", lambda coro: captured.append(coro))

    chain.spawn_sample_chain(pid)
    _run_captured(captured)
    # 概念/梗卡/大纲已存在 → 跳过;架构为空 → 种子仍要跑;正文步永远跑(内部按章幂等)
    assert calls == ["core_seed", "chapters"]


def test_sample_chain_failure_lands_in_job(client, monkeypatch):
    """任一步抛错 → job 记 error(用户看得到原因),不冒泡到请求。"""
    import app.api.projects.chain as chain
    from app.jobs import get_job

    headers = _auth(_register(client, "chain_fail")["token"])
    pid = _project(client, headers)
    from app.db.models import Project
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        row = db.get(Project, pid)
        row.brief = "订单"
        row.brief_confirmed = True
        db.commit()

    async def _boom(session, p, report, **kwargs):
        raise RuntimeError("上游 502")

    monkeypatch.setattr(chain, "_step_concept", _boom)
    captured: list = []
    monkeypatch.setattr(chain, "fire_and_track", lambda coro: captured.append(coro))
    job_id = chain.spawn_sample_chain(pid)
    _run_captured(captured)
    job = get_job(job_id)
    assert job["status"] == "error"
    assert "502" in (job["error"] or "")


def test_full_chain_dedupes_and_uses_skeleton_pipeline(client, monkeypatch):
    """全链:去重 + 三步走(其余三层 → 骨架 → 全书蓝图)。"""
    import app.api.projects.chain as chain
    from app.db.models import Project
    from app.db.session import SessionLocal
    from app.jobs import get_job

    headers = _auth(_register(client, "full_chain")["token"])
    pid = _project(client, headers)
    with SessionLocal() as db:
        row = db.get(Project, pid)
        row.brief = "订单"
        row.brief_confirmed = True
        db.commit()

    seen: list[str] = []

    async def _fake_layer(*, layer, **kwargs):
        seen.append(f"layer:{layer}")
        return f"{layer} 文本"

    async def _fake_skeleton(session, p, **kwargs):
        seen.append("skeleton")
        return [{"start": 1, "end": 60, "title": "第一卷", "goal": "起势",
                 "conflict": "", "start_state": "", "end_state": "",
                 "confirmed": False, "locked": False}]

    async def _fake_blueprint(session, p, **kwargs):
        seen.append(f"blueprint:start={kwargs.get('start_chapter')}")

    monkeypatch.setattr(chain, "generate_architecture_layer", _fake_layer)
    monkeypatch.setattr(chain, "save_architecture_layer", lambda *a, **k: None)
    monkeypatch.setattr(chain, "confirm_architecture_layer", lambda *a, **k: None)
    monkeypatch.setattr(chain, "build_skeleton_segments", _fake_skeleton)
    monkeypatch.setattr(chain, "run_blueprint_pipeline", _fake_blueprint)
    monkeypatch.setattr(chain, "_extract_personas_safe", lambda *a, **k: asyncio.sleep(0))
    captured: list = []
    monkeypatch.setattr(chain, "fire_and_track", lambda coro: captured.append(coro))

    job_id = chain.spawn_full_chain(pid)
    assert chain.spawn_full_chain(pid) != ""  # 幂等入口存在
    _run_captured(captured)
    assert seen == [
        "layer:character_dynamics", "layer:world_building", "layer:plot_architecture",
        "skeleton", "blueprint:start=4",
    ]
    assert get_job(job_id)["status"] == "done"
    # 骨架落库 + 失配标记复位
    with SessionLocal() as db:
        row = db.get(Project, pid)
        assert row.macro_plan and row.macro_plan[0]["title"] == "第一卷"
        assert row.skeleton_stale is False
