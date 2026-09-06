# tests/test_projects_finished_guard.py
# -*- coding: utf-8 -*-
"""完本门槛:写完章数(有正文)达到目标章数才允许标完本;未达标 409 报差额。"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import Chapter, Project
from app.db.session import SessionLocal
from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(client):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _auth(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _new_project(client: TestClient, headers: dict, target: int) -> int:
    r = client.post("/api/projects", headers=headers,
                    json={"title": "完本书", "target_chapters": target})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _add_chapter(db: Session, project_id: int, n: int) -> None:
    db.add(Chapter(
        project_id=project_id, chapter_number=n,
        final_content=f"第{n}章正文。", word_count=10, status="approved",
    ))
    db.commit()


def test_finish_blocked_then_allowed(client, db):
    """0 章/差 1 章 → 409 报差额;写满目标章数 → 允许完本。"""
    headers = _auth(client, f"fin_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers, target=3)

    # 0 章:禁止完本
    r = client.patch(f"/api/projects/{pid}", headers=headers, json={"finished": True})
    assert r.status_code == 409
    assert "3 章没写完" in r.json()["detail"]

    # 写 2 章(差 1 章):仍禁止,detail 报差额
    db.add_all([
        Chapter(project_id=pid, chapter_number=1,
                final_content="一。", word_count=2, status="approved"),
        Chapter(project_id=pid, chapter_number=2,
                final_content="二。", word_count=2, status="approved"),
    ])
    db.commit()
    r = client.patch(f"/api/projects/{pid}", headers=headers, json={"finished": True})
    assert r.status_code == 409
    assert "1 章没写完" in r.json()["detail"]

    # 写满第 3 章:允许完本
    _add_chapter(db, pid, 3)
    r = client.patch(f"/api/projects/{pid}", headers=headers, json={"finished": True})
    assert r.status_code == 200, r.text
    assert r.json()["finished"] is True


def test_chapters_beyond_target_also_pass(client, db):
    """写超目标(如目标 2 写了 3 章)同样允许完本——门槛是下限不是上限。"""
    headers = _auth(client, f"fin_over_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers, target=2)
    _add_chapter(db, pid, 1)
    _add_chapter(db, pid, 2)
    _add_chapter(db, pid, 3)
    r = client.patch(f"/api/projects/{pid}", headers=headers, json={"finished": True})
    assert r.status_code == 200, r.text
    assert r.json()["finished"] is True
