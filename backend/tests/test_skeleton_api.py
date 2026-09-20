# tests/test_skeleton_api.py
# -*- coding: utf-8 -*-
"""骨架拍板/锁定落库回归钉:JSON 列原地改不触发变更追踪的静默失败。

拍板接口曾把 macro_plan 的同一对象原地改完再赋回去,flush 比对新旧相等视为
未变更,commit 空转——响应 200、库没写,骨架墙的「拍板」成了摆设,
「全部确认,开始铺章」永远灰着(2026-09-20 全流程实测踩中,信任模式掩盖至今)。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.models import Project
from app.db.session import SessionLocal

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client, username: str) -> dict:
    r = client.post("/api/auth/register",
                    json={"username": username, "password": "pass123", "invite_code": INVITE})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _plan() -> list[dict]:
    return [
        {"start": 1, "end": 10, "title": "一段", "goal": "开局", "conflict": "",
         "start_state": "", "end_state": "", "confirmed": False, "locked": False},
        {"start": 11, "end": 20, "title": "二段", "goal": "中局", "conflict": "",
         "start_state": "", "end_state": "", "confirmed": False, "locked": False},
    ]


def test_confirm_and_lock_persist_to_db(client):
    headers = _auth(client, f"skel_{uuid.uuid4().hex[:6]}")
    pid = client.post("/api/projects", headers=headers,
                      json={"title": "骨架书"}).json()["id"]

    with SessionLocal() as db:
        db.get(Project, pid).macro_plan = _plan()
        db.commit()

    r = client.post(f"/api/projects/{pid}/skeleton/0/confirm?confirmed=true", headers=headers)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/{pid}/skeleton/1/lock?locked=true", headers=headers)
    assert r.status_code == 200, r.text

    # 全新会话直读库:拍板/锁定必须真的落库(响应 200 不算数)
    with SessionLocal() as db:
        segs = db.get(Project, pid).macro_plan
        assert segs[0]["confirmed"] is True
        assert segs[1]["locked"] is True and segs[1]["confirmed"] is True

    # 撤回拍板同样落库
    client.post(f"/api/projects/{pid}/skeleton/0/confirm?confirmed=false", headers=headers)
    with SessionLocal() as db:
        assert db.get(Project, pid).macro_plan[0]["confirmed"] is False


def test_edit_segment_persists(client):
    """分段编辑(段名/目标)同样必须真落库。"""
    headers = _auth(client, f"skel_edit_{uuid.uuid4().hex[:6]}")
    pid = client.post("/api/projects", headers=headers,
                      json={"title": "骨架编辑书"}).json()["id"]

    with SessionLocal() as db:
        db.get(Project, pid).macro_plan = _plan()
        db.commit()

    r = client.put(f"/api/projects/{pid}/skeleton/0", headers=headers,
                   json={"title": "新段名", "goal": "新目标"})
    assert r.status_code == 200, r.text

    with SessionLocal() as db:
        seg = db.get(Project, pid).macro_plan[0]
        assert seg["title"] == "新段名"
        assert seg["goal"] == "新目标"
