# tests/test_book_health_api.py
# -*- coding: utf-8 -*-
"""成书体检报告的 API 面(docs/15 §7.3)。

验证:
- JSON 形态:各块字段齐全,前端画曲线要的字段都在
- markdown 形态:直接回可下载的纯文本
- 归属隔离:别人的 project 拿不到
- 空书不谎报:曲线为空、notes 有口径说明

端点级测试用 `with TestClient(app)`(裸 TestClient 不走 lifespan/迁移)。
"""
from __future__ import annotations

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
        json={"username": username, "password": "pw123456", "invite_code": INVITE},
    )
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _project(client: TestClient, headers: dict, title: str) -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _seed_chapters(pid: int, count: int) -> None:
    from app.db.session import SessionLocal
    from app.db.models import Chapter

    with SessionLocal() as s:
        for n in range(1, count + 1):
            s.add(Chapter(project_id=pid, chapter_number=n,
                          final_content="他推开门,雪落进来,风把烛火压得极低。" * 20,
                          word_count=400, status="approved"))
        s.commit()


def test_health_report_json_shape(client):
    headers = _auth(client, "health_ok")
    pid = _project(client, headers, "体检书")
    _seed_chapters(pid, 3)

    r = client.get(f"/api/projects/{pid}/health-report", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_id"] == pid
    assert body["chapters_written"] == 3
    assert body["total_words"] == 1200
    assert body["mean_flavor"] is not None
    assert len(body["flavor_curve"]) == 3
    # 前端画曲线需要的字段
    assert {"chapter", "score", "chars"} <= set(body["flavor_curve"][0])
    assert "markdown" in body


def test_health_report_markdown_format(client):
    headers = _auth(client, "health_md")
    pid = _project(client, headers, "体检书md")
    _seed_chapters(pid, 2)

    r = client.get(
        f"/api/projects/{pid}/health-report?format=markdown", headers=headers
    )
    assert r.status_code == 200, r.text
    assert "text/markdown" in r.headers["content-type"]
    assert r.text.startswith("# 《体检书md》成书体检报告")
    assert "## 体量" in r.text


def test_health_report_empty_book_does_not_fake_zero(client):
    headers = _auth(client, "health_empty")
    pid = _project(client, headers, "空体检书")

    body = client.get(
        f"/api/projects/{pid}/health-report", headers=headers
    ).json()
    assert body["chapters_written"] == 0
    assert body["flavor_curve"] == []
    assert body["tension_curve"] == []
    assert body["notes"]          # 必须说明「为什么空」


def test_health_report_ownership_isolation(client):
    headers = _auth(client, "health_owner")
    pid = _project(client, headers, "归属体检书")
    other = _auth(client, "health_other")
    r = client.get(f"/api/projects/{pid}/health-report", headers=other)
    assert r.status_code == 404
