# tests/test_proofread_echo.py
# -*- coding: utf-8 -*-
"""校对结果回显测试:快照存/取纯函数 + GET /proofread 接口(含指纹失效)。

两层:
1. 引擎纯函数单测:store_proofread_snapshot 打标(issues/fixed/source/时间/指纹)/
   load_proofread_snapshot 指纹一致才回显、正文改动或缺列时返回 None。
2. GET /api/projects/{id}/chapters/{n}/proofread 接口集成测:存了快照且正文没动
   → 回显;正文被改 → null;无快照 → null;未登录 → 401;章节不存在 → 404。
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
    load_proofread_snapshot,
    store_proofread_snapshot,
)
from app.main import app


# ---------- 纯函数单测(duck-type,不碰数据库) ----------
def test_store_and_load_roundtrip():
    ch = SimpleNamespace(final_content="第一章正文。", proofread_snapshot="")
    issues = [
        {"type": "typo", "original": "的地得", "suggestion": "地", "reason": "用错"},
        {"type": "punct", "original": "。，", "suggestion": "。", "reason": "重复标点"},
    ]
    store_proofread_snapshot(ch, issues, source="generation", content=ch.final_content)

    loaded = load_proofread_snapshot(ch)
    assert loaded is not None
    assert loaded["source"] == "generation"
    assert loaded["fixed"] == 2                 # 缺省取 issues 长度
    assert len(loaded["issues"]) == 2
    assert loaded["issues"][0]["original"] == "的地得"
    assert loaded["proofread_at"]               # 打了时间戳
    assert loaded["content_hash"] == content_hash("第一章正文。")


def test_store_manual_uses_explicit_fixed_zero():
    ch = SimpleNamespace(final_content="正文。", proofread_snapshot="")
    issues = [{"type": "dup", "original": "很很", "suggestion": "很", "reason": "重复"}]
    # 手动校对:待修清单,fixed 显式传 0
    store_proofread_snapshot(ch, issues, source="manual", content=ch.final_content, fixed=0)
    loaded = load_proofread_snapshot(ch)
    assert loaded is not None
    assert loaded["source"] == "manual"
    assert loaded["fixed"] == 0
    assert len(loaded["issues"]) == 1


def test_load_returns_none_when_content_changed():
    ch = SimpleNamespace(final_content="原正文。", proofread_snapshot="")
    store_proofread_snapshot(ch, [{"type": "typo", "original": "x", "suggestion": "y"}],
                             source="generation", content=ch.final_content)
    # 正文被编辑/润色/重写 → 指纹对不上 → 不回显过期清单
    ch.final_content = "改过的正文。"
    assert load_proofread_snapshot(ch) is None


def test_load_returns_none_when_empty_or_missing_column():
    assert load_proofread_snapshot(SimpleNamespace(final_content="x", proofread_snapshot="")) is None
    # 老数据没有 proofread_snapshot 列(duck-type getattr 兜底)
    assert load_proofread_snapshot(SimpleNamespace(final_content="x")) is None
    # 脏 JSON 不抛异常
    assert load_proofread_snapshot(
        SimpleNamespace(final_content="x", proofread_snapshot="not-json")) is None


# ---------- GET /proofread 接口集成测 ----------
@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def seeded():
    """建一个用户 + 项目 + 第 1 章(带正文),返回 (token, project_id, chapter_id)。"""
    db = SessionLocal()
    try:
        user = User(username=f"proof-{uuid.uuid4().hex[:8]}", password_hash="x", is_active=True)
        db.add(user)
        db.flush()
        project = Project(title="校对回显测试书", user_id=user.id, target_chapters=1)
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
    store_proofread_snapshot(ch, [
        {"type": "typo", "original": "在再", "suggestion": "再", "reason": "用错字"},
    ], source=source, content=content)
    db.commit()


def test_get_proofread_echoes_stored_snapshot(client, seeded):
    token, pid, cid = seeded
    db = SessionLocal()
    content = db.get(Chapter, cid).final_content
    _store(db, cid, content)
    db.close()

    r = client.get(f"/api/projects/{pid}/chapters/1/proofread",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    proof = r.json()["proofread"]
    assert proof is not None
    assert proof["source"] == "generation"
    assert proof["fixed"] == 1
    assert proof["issues"][0]["original"] == "在再"


def test_get_proofread_null_after_content_edited(client, seeded):
    token, pid, cid = seeded
    db = SessionLocal()
    content = db.get(Chapter, cid).final_content
    _store(db, cid, content)
    ch = db.get(Chapter, cid)
    ch.final_content = content + "新增的一句。"
    db.commit()
    db.close()

    r = client.get(f"/api/projects/{pid}/chapters/1/proofread",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["proofread"] is None


def test_get_proofread_null_when_never_proofread(client, seeded):
    token, pid, _ = seeded
    r = client.get(f"/api/projects/{pid}/chapters/1/proofread",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["proofread"] is None


def test_get_proofread_requires_auth(client, seeded):
    _, pid, _ = seeded
    r = client.get(f"/api/projects/{pid}/chapters/1/proofread")
    assert r.status_code == 401


def test_get_proofread_404_for_missing_chapter(client, seeded):
    token, pid, _ = seeded
    r = client.get(f"/api/projects/{pid}/chapters/99/proofread",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404
