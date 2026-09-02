# tests/test_usage.py
# -*- coding: utf-8 -*-
"""功能使用计数:路径归线、GET 不计、缓冲落库、admin 汇总端点。

埋点是全局内存态(模块级缓冲),用例间用 usage.flush() / 手工清空隔离,
不依赖测试顺序。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import usage
from app.auth import hash_password
from app.db.models import User
from app.db.session import SessionLocal
from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, name: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": name, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def admin_headers(client: TestClient) -> dict:
    """直接建管理员(测试库整个会话共用,首个注册用户未必是本模块的)。"""
    with SessionLocal() as db:
        user = User(
            username=f"usage_admin_{uuid.uuid4().hex[:8]}",
            password_hash=hash_password("pass123"),
            is_admin=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    r = client.post("/api/auth/login", json={"username": user.username, "password": "pass123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# =============== 纯函数:路径归线 ===============

def test_feature_of_mapping():
    assert usage.feature_of("/api/series/characters") == "series"
    assert usage.feature_of("/api/clips") == "clips"
    assert usage.feature_of("/api/inspire/engines") == "inspire"
    assert usage.feature_of("/api/birthday") == "birthday"
    assert usage.feature_of("/api/promos") == "promo"
    assert usage.feature_of("/api/projects") == "novel"
    assert usage.feature_of("/api/projects/3/chapters") == "novel"
    assert usage.feature_of("/api/projects/3/drama/episodes") == "drama"
    # 不计:基础设施数
    assert usage.feature_of("/api/auth/login") is None
    assert usage.feature_of("/api/admin/usage") is None
    assert usage.feature_of("/api/settings") is None
    assert usage.feature_of("/app/index.html") is None


def test_record_skips_reads_and_unknown():
    usage._buf.clear()
    usage.record("GET", "/api/series/characters", 1)      # 读,不计
    usage.record("HEAD", "/api/series/characters", 1)     # 读,不计
    usage.record("POST", "/api/auth/login", 1)            # 基础设施,不计
    assert usage._buf == {}
    usage.record("POST", "/api/series/characters", 1)
    usage.record("PUT", "/api/series/episodes/9", 1)
    usage.record("POST", "/api/series/characters", 1)
    assert usage._buf == {("series", 1): 3}
    usage._buf.clear()


# =============== HTTP 链路:动作计数 → admin 汇总 ===============

def test_usage_roundtrip(client: TestClient, admin_headers: dict):
    usage._buf.clear()
    headers = _register(client, f"usage_u_{uuid.uuid4().hex[:8]}")

    # 一条 series 动作(建角色;look 必填)
    r = client.post(
        "/api/series/characters",
        json={"name": "用量浣熊", "look": "测试定妆", "direction": "render3d",
              "default_duration_s": 10},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    # 一条 novel 动作(建项目)
    r = client.post(
        "/api/projects",
        json={"title": "用量书", "topic": "测试", "genre": "玄幻", "target_chapters": 3},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    # 浏览(GET)不该计数
    r = client.get("/api/series/characters", headers=headers)
    assert r.status_code == 200

    usage.flush()  # 强制落库(正常由 30s 周期任务做)

    r = client.get("/api/admin/usage", headers=admin_headers)
    assert r.status_code == 200, r.text
    by_feature = {row["feature"]: row for row in r.json()["usage"]}
    assert by_feature["series"]["uses"] >= 1
    assert by_feature["series"]["users"] >= 1
    assert by_feature["novel"]["uses"] >= 1
    assert by_feature["series"]["last_used_at"] is not None


def test_usage_requires_admin(client: TestClient, admin_headers: dict):
    headers = _register(client, f"usage_p_{uuid.uuid4().hex[:8]}")
    r = client.get("/api/admin/usage", headers=headers)
    assert r.status_code == 403
