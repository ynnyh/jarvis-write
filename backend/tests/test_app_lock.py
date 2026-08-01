# tests/test_app_lock.py
# -*- coding: utf-8 -*-
"""应用锁接口:设锁 → 解锁校验对错 → 改锁 → 移除 → server 模式拒绝。

local 模式的构造参照 test_local_mode_guard.py:patch settings.app_mode +
桌面入口环境变量(JARVIS_LAUNCHER/JARVIS_BIND_HOST),起独立 app 实例;
Host 守卫要求每请求带回环 Host 头。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

# local 模式的 Host 守卫只放行回环 Host(TestClient 默认 testserver 会被 403)
HOST = {"host": "127.0.0.1:8756"}


@pytest.fixture(scope="module")
def client():
    """server 模式的全局 app 单例(应用锁接口应全部 404)。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def local_client():
    """以 local 模式构建独立 app 实例;用例结束清掉锁记录,不污染共享临时库。"""
    from app.config import get_settings
    from app.main import create_app

    settings = get_settings()
    env = {"JARVIS_LAUNCHER": "desktop", "JARVIS_BIND_HOST": "127.0.0.1"}
    with patch.object(settings, "app_mode", "local"), patch.dict("os.environ", env):
        local_app = create_app()
        with TestClient(local_app) as c:
            yield c
    from app.db.models import AppSetting
    from app.db.session import session_scope

    with session_scope() as db:
        db.query(AppSetting).filter(
            AppSetting.key == "app_lock_password_hash"
        ).delete(synchronize_session=False)


def test_lock_full_flow(local_client):
    """设锁 → status/mode 反映 → 解锁对错 → 改锁 → 移除,全状态迁移。"""
    # 未设锁
    r = local_client.get("/api/app-lock/status", headers=HOST)
    assert r.status_code == 200
    assert r.json()["has_lock"] is False
    # 未设锁时解锁/移除都 400
    r = local_client.post(
        "/api/app-lock/unlock", headers=HOST, json={"password": "x"}
    )
    assert r.status_code == 400
    r = local_client.post(
        "/api/app-lock/remove", headers=HOST, json={"password": "x"}
    )
    assert r.status_code == 400

    # 首次设锁(无需旧密码)
    r = local_client.post(
        "/api/app-lock", headers=HOST, json={"new_password": "lock123"}
    )
    assert r.status_code == 200, r.text
    # /api/mode 带 has_lock,前端据此出锁屏
    r = local_client.get("/api/mode", headers=HOST)
    assert r.json()["is_local"] is True
    assert r.json()["has_lock"] is True

    # 解锁:错 401,对 200
    r = local_client.post(
        "/api/app-lock/unlock", headers=HOST, json={"password": "wrong"}
    )
    assert r.status_code == 401
    r = local_client.post(
        "/api/app-lock/unlock", headers=HOST, json={"password": "lock123"}
    )
    assert r.status_code == 200

    # 改锁:旧密码缺失/错误都 400
    r = local_client.post(
        "/api/app-lock", headers=HOST, json={"new_password": "lock456"}
    )
    assert r.status_code == 400
    r = local_client.post(
        "/api/app-lock",
        headers=HOST,
        json={"old_password": "nope", "new_password": "lock456"},
    )
    assert r.status_code == 400
    # 改锁成功:新密码可解锁,旧密码不行
    r = local_client.post(
        "/api/app-lock",
        headers=HOST,
        json={"old_password": "lock123", "new_password": "lock456"},
    )
    assert r.status_code == 200, r.text
    r = local_client.post(
        "/api/app-lock/unlock", headers=HOST, json={"password": "lock123"}
    )
    assert r.status_code == 401
    r = local_client.post(
        "/api/app-lock/unlock", headers=HOST, json={"password": "lock456"}
    )
    assert r.status_code == 200

    # 移除:密码错 401;成功后恢复无锁
    r = local_client.post(
        "/api/app-lock/remove", headers=HOST, json={"password": "wrong"}
    )
    assert r.status_code == 401
    r = local_client.post(
        "/api/app-lock/remove", headers=HOST, json={"password": "lock456"}
    )
    assert r.status_code == 200
    r = local_client.get("/api/app-lock/status", headers=HOST)
    assert r.json()["has_lock"] is False
    r = local_client.get("/api/mode", headers=HOST)
    assert r.json()["has_lock"] is False


def test_set_lock_same_as_old(local_client):
    local_client.post(
        "/api/app-lock", headers=HOST, json={"new_password": "lock123"}
    )
    r = local_client.post(
        "/api/app-lock",
        headers=HOST,
        json={"old_password": "lock123", "new_password": "lock123"},
    )
    assert r.status_code == 400
    assert "不能与旧密码相同" in r.json()["detail"]


def test_set_lock_too_short(local_client):
    """密码强度规则与注册一致(min_length=6),pydantic 拦 422。"""
    r = local_client.post(
        "/api/app-lock", headers=HOST, json={"new_password": "12345"}
    )
    assert r.status_code == 422


def test_set_lock_overlong(local_client):
    """bcrypt 只取前 72 字节,超长应 400 而非 500。"""
    r = local_client.post(
        "/api/app-lock", headers=HOST, json={"new_password": "a" * 73}
    )
    assert r.status_code == 400
    assert "密码过长" in r.json()["detail"]


def test_reset_lock_ok(local_client):
    """忘记密码重置:confirm=「重置」直接清除锁,恢复无锁状态。"""
    local_client.post(
        "/api/app-lock", headers=HOST, json={"new_password": "lock123"}
    )
    r = local_client.post(
        "/api/app-lock/reset", headers=HOST, json={"confirm": "重置"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    r = local_client.get("/api/app-lock/status", headers=HOST)
    assert r.json()["has_lock"] is False


def test_reset_lock_confirm_mismatch(local_client):
    """防误触:confirm 不是「重置」二字则 400,锁保留。"""
    local_client.post(
        "/api/app-lock", headers=HOST, json={"new_password": "lock123"}
    )
    r = local_client.post(
        "/api/app-lock/reset", headers=HOST, json={"confirm": "确定"}
    )
    assert r.status_code == 400
    assert "重置" in r.json()["detail"]
    r = local_client.get("/api/app-lock/status", headers=HOST)
    assert r.json()["has_lock"] is True


def test_reset_lock_server_404(client):
    r = client.post("/api/app-lock/reset", json={"confirm": "重置"})
    assert r.status_code == 404


def test_server_mode_rejects(client):
    """server 模式:应用锁接口全部 404;/api/mode 的 has_lock 恒 False。"""
    assert client.get("/api/app-lock/status").status_code == 404
    r = client.post("/api/app-lock", json={"new_password": "lock123"})
    assert r.status_code == 404
    r = client.post("/api/app-lock/unlock", json={"password": "x"})
    assert r.status_code == 404
    r = client.post("/api/app-lock/remove", json={"password": "x"})
    assert r.status_code == 404
    r = client.get("/api/mode")
    assert r.json()["is_local"] is False
    assert r.json()["has_lock"] is False
