# tests/test_review_echo.py
# -*- coding: utf-8 -*-
"""主审结果回显测试:快照存/取纯函数 + GET /review 接口(含指纹失效)。

两层:
1. 引擎纯函数单测:content_hash 稳定性 / store_review_snapshot 打标 /
   load_review_snapshot 指纹一致才回显、正文改动或缺列时返回 None。
2. GET /api/projects/{id}/chapters/{n}/review 接口集成测:存了快照且正文没动
   → 回显;正文被改 → null(不显示过期评分);无快照 → null;未登录 → 401。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth import build_token
from app.db.models import Chapter, Project, User
from app.db.session import SessionLocal
from app.engines.editorial import (
    content_hash,
    load_review_snapshot,
    store_review_snapshot,
)
from app.main import app


# ---------- 纯函数单测(duck-type,不碰数据库) ----------
def test_content_hash_stable_and_sensitive():
    assert content_hash("同一段正文") == content_hash("同一段正文")
    assert content_hash("正文A") != content_hash("正文B")
    assert len(content_hash("x")) == 16  # 取 sha256 前 16 位


def test_store_and_load_roundtrip():
    ch = SimpleNamespace(final_content="第一章正文。", review_snapshot="")
    review = {"scores": {"plot": 8, "prose": 7, "pacing": 8, "character": 7},
              "comment": "不错", "suggestions": [], "passed": True}
    store_review_snapshot(ch, review, source="generation", content=ch.final_content)

    loaded = load_review_snapshot(ch)
    assert loaded is not None
    assert loaded["scores"]["plot"] == 8
    assert loaded["source"] == "generation"
    assert loaded["reviewed_at"]            # 打了时间戳
    assert loaded["content_hash"] == content_hash("第一章正文。")


def test_load_returns_none_when_content_changed():
    ch = SimpleNamespace(final_content="原正文。", review_snapshot="")
    store_review_snapshot(ch, {"scores": {}, "comment": "", "suggestions": []},
                          source="manual", content=ch.final_content)
    # 正文被编辑/润色/重写 → 指纹对不上 → 不回显过期评分
    ch.final_content = "改过的正文。"
    assert load_review_snapshot(ch) is None


def test_load_returns_none_when_empty_or_missing_column():
    # 空快照
    assert load_review_snapshot(SimpleNamespace(final_content="x", review_snapshot="")) is None
    # 老数据没有 review_snapshot 列(duck-type getattr 兜底)
    assert load_review_snapshot(SimpleNamespace(final_content="x")) is None
    # 脏 JSON 不抛异常
    assert load_review_snapshot(
        SimpleNamespace(final_content="x", review_snapshot="not-json")) is None


# ---------- GET /review 接口集成测 ----------
@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def seeded():
    """建一个用户 + 项目 + 第 1 章(带正文),返回 (token, project_id, chapter)。"""
    db = SessionLocal()
    try:
        user = User(username=f"echo-{uuid.uuid4().hex[:8]}", password_hash="x", is_active=True)
        db.add(user)
        db.flush()
        project = Project(title="回显测试书", user_id=user.id, target_chapters=1)
        db.add(project)
        db.flush()
        chapter = Chapter(
            project_id=project.id, chapter_number=1,
            final_content="这是第一章的正文。" * 10, status="finalized",
        )
        db.add(chapter)
        db.commit()
        token = build_token(user.id)
        yield token, project.id, chapter.id
    finally:
        db.close()


def _store(db, chapter_id, content, source="generation"):
    ch = db.get(Chapter, chapter_id)
    store_review_snapshot(ch, {
        "scores": {"plot": 8, "prose": 8, "pacing": 8, "character": 8},
        "comment": "稳", "suggestions": [], "passed": True,
    }, source=source, content=content)
    db.commit()


def test_get_review_echoes_stored_snapshot(client, seeded):
    token, pid, cid = seeded
    db = SessionLocal()
    content = db.get(Chapter, cid).final_content
    _store(db, cid, content)
    db.close()

    r = client.get(f"/api/projects/{pid}/chapters/1/review",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    review = r.json()["review"]
    assert review is not None
    assert review["scores"]["plot"] == 8
    assert review["source"] == "generation"


def test_get_review_null_after_content_edited(client, seeded):
    token, pid, cid = seeded
    db = SessionLocal()
    content = db.get(Chapter, cid).final_content
    _store(db, cid, content)
    # 用户手改了正文 → 快照指纹失配
    ch = db.get(Chapter, cid)
    ch.final_content = content + "新增的一句。"
    db.commit()
    db.close()

    r = client.get(f"/api/projects/{pid}/chapters/1/review",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["review"] is None


def test_get_review_null_when_never_reviewed(client, seeded):
    token, pid, _ = seeded
    r = client.get(f"/api/projects/{pid}/chapters/1/review",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["review"] is None


def test_get_review_requires_auth(client, seeded):
    _, pid, _ = seeded
    r = client.get(f"/api/projects/{pid}/chapters/1/review")
    assert r.status_code == 401


def test_get_review_404_for_missing_chapter(client, seeded):
    token, pid, _ = seeded
    r = client.get(f"/api/projects/{pid}/chapters/99/review",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
