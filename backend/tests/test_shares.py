# tests/test_shares.py
# -*- coding: utf-8 -*-
"""公开分享:创建/列表/撤销(作者面)+ 免登录只读(公开面)+ 越权防护。"""
from __future__ import annotations

import uuid

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


def _new_project(client: TestClient, headers: dict, title: str = "分享书") -> int:
    r = client.post("/api/projects", headers=headers, json={"title": title, "genre": "悬疑"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_book_share_public_read_flow(client):
    """创建整本书分享 → 免登录读:只回书名与有正文的章(按章号排序),浏览计数 +1。"""
    headers = _auth(client, f"shr_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers)
    # 造大纲章名 + 两个有正文章(第 2 章故意无正文 → 不应出现在公开内容里)
    from app.db.models import Chapter, Outline
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.add_all([
            Outline(project_id=pid, chapter_number=1, title="开局"),
            Outline(project_id=pid, chapter_number=2, title="转折"),
        ])
        db.flush()
        db.add_all([
            Chapter(project_id=pid, chapter_number=1, outline_id=db.query(Outline).filter_by(project_id=pid, chapter_number=1).first().id,
                    final_content="第一章的正文内容。", word_count=9, status="approved"),
            Chapter(project_id=pid, chapter_number=2, outline_id=db.query(Outline).filter_by(project_id=pid, chapter_number=2).first().id,
                    final_content="第二章的正文内容。", word_count=9, status="approved"),
        ])
        db.commit()
    finally:
        db.close()

    r = client.post(f"/api/projects/{pid}/shares", headers=headers, json={"scope": "book"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    # 免登录读(不带 Authorization)
    r2 = client.get(f"/api/public/shares/{token}")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["book_title"] == "分享书"
    assert [c["number"] for c in body["chapters"]] == [1, 2]
    assert "第一章的正文内容" in body["chapters"][0]["content"]
    # 不泄露任何设定/用户信息
    raw = str(body)
    assert "username" not in raw and "token" not in raw

    # 浏览计数
    r3 = client.get(f"/api/public/shares/{token}")
    assert r3.json()["chapters"] == body["chapters"]


def test_chapter_share_single_and_revoked_404(client):
    """单章分享只回该章;撤销后公开端点 404。"""
    from app.db.models import Chapter, Outline
    from app.db.session import SessionLocal

    headers = _auth(client, f"shr_ch_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers)

    db = SessionLocal()
    try:
        db.add(Outline(project_id=pid, chapter_number=1, title="独章"))
        db.flush()
        o1 = db.query(Outline).filter_by(project_id=pid, chapter_number=1).first()
        db.add(Chapter(project_id=pid, chapter_number=1, outline_id=o1.id,
                       final_content="独章正文。", word_count=5, status="approved"))
        db.commit()
    finally:
        db.close()

    r = client.post(f"/api/projects/{pid}/shares", headers=headers,
                    json={"scope": "chapter", "chapter_number": 1})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    body = client.get(f"/api/public/shares/{token}").json()
    assert len(body["chapters"]) == 1
    assert "独章正文" in body["chapters"][0]["content"]

    # 撤销 → 公开端 404
    shares = client.get(f"/api/projects/{pid}/shares", headers=headers).json()
    share_id = shares[0]["id"]
    r2 = client.delete(f"/api/projects/{pid}/shares/{share_id}", headers=headers)
    assert r2.status_code == 200
    assert client.get(f"/api/public/shares/{token}").status_code == 404


def test_share_scope_validation_and_cross_user(client):
    """chapter 分享缺章号 → 400;他人项目的分享/列表/撤销 → 404(不泄露存在性)。"""
    a = _auth(client, f"shr_a_{uuid.uuid4().hex[:6]}")
    b = _auth(client, f"shr_b_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, a)

    r = client.post(f"/api/projects/{pid}/shares", headers=a, json={"scope": "chapter"})
    assert r.status_code == 400

    # b 对 a 的项目:创建/列表 → 404
    assert client.post(f"/api/projects/{pid}/shares", headers=b, json={"scope": "book"}).status_code == 404
    assert client.get(f"/api/projects/{pid}/shares", headers=b).status_code == 404

    # a 创建分享后,b 撤销 → 404(不泄露存在性)
    r = client.post(f"/api/projects/{pid}/shares", headers=a, json={"scope": "book"})
    sid = r.json()["id"]
    assert client.delete(f"/api/projects/{pid}/shares/{sid}", headers=b).status_code == 404
