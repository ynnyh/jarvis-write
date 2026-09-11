# tests/test_quality_overview.py
# -*- coding: utf-8 -*-
"""生成质量聚合端点(GET /api/admin/quality-overview)。

纪律:
- 隔离内存库(create_engine("sqlite://")) + dependency_overrides,不碰共享文件库;
- 覆盖四路信号(llm 截断 / rework 画像 / issues 分布 / volume)与边界(空库 / 脏快照);
- 反向验证:改坏 trigger 归类或截断计数必须有断言变红。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.admin import get_current_admin
from app.auth import get_current_user, hash_password
from app.db.base import Base
import app.db.models  # noqa: F401 — 注册全部模型
from app.db.models import Chapter, ChapterFeedback, ChapterIssue, LlmUsage, Project, User
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def env():
    """隔离内存库 + 直接注入管理员,不经过真实鉴权链。返回 (client, db)。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        # StaticPool:全部连接共享同一内存库。端点在 anyio 线程池里跑,
        # 不加它每个线程拿到的是各自为政的空库(no such table)。
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    admin = User(username=f"qa_admin_{uuid.uuid4().hex[:8]}",
                 password_hash=hash_password("pass123"), is_admin=True)
    db.add(admin)
    db.commit()

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_admin] = lambda: admin
    with TestClient(app) as c:
        yield c, db
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_admin, None)
    db.close()


def _snapshot(passed=True, rounds=1, triggers=("gate",), hints=None):
    """按 chapter_rework._finish 的落库形状构造 review_snapshot。"""
    return json.dumps({
        "passed": passed,
        "revision_rounds": rounds,
        "rework_log": [
            {"round": rounds, "trigger": t, "blockers": [], "note": ""} for t in triggers
        ],
        "hints": hints or [],
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False)


def _mk_chapter(db, snapshot="", words=3000, number=1):
    project = Project(title=f"测试书{uuid.uuid4().hex[:6]}", target_chapters=10)
    db.add(project)
    db.flush()
    # Chapter 没有 title 列(标题在 outline 上),只落数值与快照
    ch = Chapter(project_id=project.id, chapter_number=number,
                 word_count=words, review_snapshot=snapshot)
    db.add(ch)
    db.flush()
    return ch


def _get(client):
    r = client.get("/api/admin/quality-overview")
    assert r.status_code == 200, r.text
    return r.json()


# =============== 空库边界 ===============

def test_empty_db_all_zero(env):
    client, _db = env
    out = _get(client)
    assert out["llm"]["total_calls"] == 0
    assert out["llm"]["truncated_ratio"] == 0.0
    assert out["rework"]["chapters_reviewed"] == 0
    assert out["rework"]["avg_revision_rounds"] == 0.0
    assert out["issues"]["open_count"] == 0
    assert out["volume"]["chapters"] == 0


# =============== llm 截断路 ===============

def test_llm_truncation_and_finish_length(env):
    client, db = env
    now = datetime.now(timezone.utc)
    db.add_all([
        # 正常一次
        LlmUsage(model="m1", prompt_tokens=100, completion_tokens=200,
                 finish_reason="stop", truncated=False, created_at=now),
        # 截断一次(truncated=True)
        LlmUsage(model="m1", prompt_tokens=100, completion_tokens=50,
                 finish_reason="", truncated=True, created_at=now),
        # 输出预算用尽(finish_reason=length)
        LlmUsage(model="m2", prompt_tokens=100, completion_tokens=400,
                 finish_reason="length", truncated=False, created_at=now),
        # 窗口外的旧记录:不得计入
        LlmUsage(model="m1", prompt_tokens=9, completion_tokens=9,
                 finish_reason="stop", truncated=True,
                 created_at=now - timedelta(days=40)),
    ])
    db.commit()

    out = _get(client)
    llm = out["llm"]
    assert llm["total_calls"] == 3
    assert llm["truncated_calls"] == 1
    assert llm["truncated_ratio"] == round(1 / 3, 4)
    assert llm["finish_length_calls"] == 1
    assert llm["prompt_tokens"] == 300 and llm["completion_tokens"] == 650
    by_model = {m["model"]: m for m in llm["by_model"]}
    assert by_model["m1"]["calls"] == 2 and by_model["m1"]["truncated"] == 1
    assert by_model["m2"]["truncated_ratio"] == 0.0


# =============== 回炉画像路 ===============

def test_rework_triggers_and_degraded(env):
    client, db = env
    _mk_chapter(db, snapshot=_snapshot(passed=True, rounds=2,
                                       triggers=("gate", "review")))
    _mk_chapter(db, snapshot=_snapshot(passed=False, rounds=3,
                                       triggers=("gate_degraded",),
                                       hints=["「节奏」维连续多轮回炉无改善"]))
    # 脏快照:不许拖垮整体,也不计入
    _mk_chapter(db, snapshot="{not-json")
    # 无快照章节:不计入回炉,但计入体量
    _mk_chapter(db, snapshot="", words=5000, number=2)
    db.commit()

    out = _get(client)
    rework = out["rework"]
    assert rework["chapters_reviewed"] == 2
    assert rework["passed"] == 1 and rework["pass_ratio"] == 0.5
    assert rework["avg_revision_rounds"] == 2.5
    assert rework["trigger_counts"] == {"gate": 1, "review": 1, "gate_degraded": 1}
    assert rework["gate_degraded_count"] == 1
    assert rework["stalled_hint_chapters"] == 1
    # 体量:脏快照章 + 无快照章都算章节;(3000×3 + 5000) / 4
    assert out["volume"]["chapters"] == 4
    assert out["volume"]["avg_word_count"] == 3500.0


# =============== 问题分布路 ===============

def test_issues_by_type_and_open(env):
    client, db = env
    ch = _mk_chapter(db)
    db.add_all([
        ChapterIssue(chapter_id=ch.id, source="gate", severity="blocker",
                     issue_type="state", description="主角伤情与上章矛盾",
                     status="open"),
        ChapterIssue(chapter_id=ch.id, source="review", severity="minor",
                     issue_type="timeline", description="时序倒置", status="open"),
        ChapterIssue(chapter_id=ch.id, source="gate", severity="minor",
                     issue_type="state", description="已修复", status="resolved"),
    ])
    db.commit()

    out = _get(client)
    issues = out["issues"]
    assert issues["open_count"] == 2
    assert issues["by_type"]["state"] == 2
    assert issues["by_type"]["timeline"] == 1
    assert issues["by_severity"]["blocker"] == 1
    assert issues["by_severity"]["minor"] == 2


# =============== 端点防护 ===============

def test_requires_admin(env):
    """未覆盖管理员依赖时(还原 override),普通用户不可见。"""
    client, db = env
    app.dependency_overrides.pop(get_current_admin, None)
    r = client.get("/api/admin/quality-overview")
    assert r.status_code in (401, 403)


def test_days_param_validated(env):
    client, _db = env
    assert client.get("/api/admin/quality-overview?days=0").status_code == 422
    assert client.get("/api/admin/quality-overview?days=366").status_code == 422
    assert client.get("/api/admin/quality-overview?days=7").status_code == 200


# =============== 反馈闭环(docs/17 M2) ===============

def _as_user(client: TestClient, user: User) -> None:
    """把鉴权依赖指到隔离库用户:不走真实注册(注册流程有它自己的测试)。"""
    app.dependency_overrides[get_current_user] = lambda: user


def _mk_user(db) -> User:
    u = User(username=f"fb_{uuid.uuid4().hex[:8]}", password_hash=hash_password("pass12345"))
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_feedback_upsert_get_and_validation(env):
    client, db = env
    ch = _mk_chapter(db)
    db.commit()
    base = f"/api/projects/{ch.project_id}/chapters/{ch.chapter_number}"
    user = _mk_user(db)
    _as_user(client, user)
    # 未登录(清掉 user override)→ 401
    app.dependency_overrides.pop(get_current_user, None)
    assert client.post(f"{base}/feedback", json={"rating": "good"}).status_code == 401

    _as_user(client, user)

    # 差评零分类零备注 → 422(必须给一个归因线索)
    r = client.post(f"{base}/feedback", json={"rating": "bad", "categories": [], "comment": ""})
    assert r.status_code == 422

    # 非法分类 → 422
    r = client.post(f"{base}/feedback", json={"rating": "bad", "categories": ["made_up_bucket"]})
    assert r.status_code == 422

    # 好评:categories 被清空(好评分桶无意义)
    r = client.post(f"{base}/feedback", json={"rating": "good", "categories": ["pacing"]})
    assert r.status_code == 200 and r.json()["categories"] == []

    # 改判为差评:upsert 同一条,四桶去重保序
    r = client.post(f"{base}/feedback",
                    json={"rating": "bad",
                          "categories": ["pacing", "style_flavor", "pacing"],
                          "comment": "中段拖沓"})
    out = r.json()
    assert out["rating"] == "bad"
    assert out["categories"] == ["pacing", "style_flavor"]
    assert out["stale"] is False  # 正文没动过,指纹一致

    # GET 回显自己的反馈
    r = client.get(f"{base}/feedback")
    assert r.status_code == 200 and r.json()["rating"] == "bad"

    # 一人一条:另一用户独立计数
    user2 = _mk_user(db)
    _as_user(client, user2)
    r = client.post(f"{base}/feedback", json={"rating": "good"})
    assert r.status_code == 200
    assert db.query(ChapterFeedback).filter(ChapterFeedback.chapter_id == ch.id).count() == 2

    app.dependency_overrides.pop(get_current_user, None)


def test_feedback_cross_attribution(env):
    """差评章的降级隔离率 vs 全体基线:归因半环的最小验证。"""
    client, db = env
    # 章 A:快照带 gate_degraded,差评
    a = _mk_chapter(db, snapshot=_snapshot(passed=False, rounds=1,
                                           triggers=("gate_degraded",)))
    # 章 B:干净通过,无人反馈
    _mk_chapter(db, snapshot=_snapshot(passed=True, rounds=0, triggers=()), number=2)
    db.commit()

    user = _mk_user(db)
    _as_user(client, user)
    base = f"/api/projects/{a.project_id}/chapters/{a.chapter_number}"
    r = client.post(f"{base}/feedback",
                    json={"rating": "bad", "categories": ["fact_error"]})
    assert r.status_code == 200

    out = _get(client)
    fb = out["feedback"]
    assert fb["total"] == 1 and fb["bad"] == 1 and fb["good"] == 0
    assert fb["by_category"] == {"fact_error": 1}
    # 差评章(1 章,带降级)降级率 1.0;全体有快照基线 1/2 = 0.5
    assert fb["cross"]["bad_chapters"]["count"] == 1
    assert fb["cross"]["bad_chapters"]["degraded_ratio"] == 1.0
    assert fb["cross"]["baseline"]["degraded_ratio"] == 0.5
    app.dependency_overrides.pop(get_current_user, None)
