# tests/test_change_password.py
# -*- coding: utf-8 -*-
"""修改密码接口:成功 / 旧密码错误 / 新密码不合规 / 新旧相同 / 未登录。"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

INVITE = "test-invite"
OLD_PW = "oldpass123"


@pytest.fixture(scope="module")
def client():
    # 进入上下文才会跑 lifespan(建表 + 幂等迁移),全程在临时库上
    with TestClient(app) as c:
        yield c


def _register(client: TestClient, username: str, password: str = OLD_PW) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_change_password_ok(client):
    """改密成功:旧密码登录失败,新密码可登录。"""
    user = _register(client, "cp_ok")
    headers = _auth(user["token"])

    r = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": OLD_PW, "new_password": "newpass456"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    r = client.post(
        "/api/auth/login", json={"username": "cp_ok", "password": OLD_PW}
    )
    assert r.status_code == 401
    r = client.post(
        "/api/auth/login", json={"username": "cp_ok", "password": "newpass456"}
    )
    assert r.status_code == 200, r.text


def test_change_password_wrong_old(client):
    headers = _auth(_register(client, "cp_wrong_old")["token"])
    r = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": "not-the-password", "new_password": "newpass456"},
    )
    assert r.status_code == 400
    assert "旧密码不正确" in r.json()["detail"]


def test_change_password_same_as_old(client):
    headers = _auth(_register(client, "cp_same")["token"])
    r = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": OLD_PW, "new_password": OLD_PW},
    )
    assert r.status_code == 400
    assert "不能与旧密码相同" in r.json()["detail"]


def test_change_password_too_short(client):
    """新密码强度规则与注册一致(min_length=6),pydantic 拦 422。"""
    headers = _auth(_register(client, "cp_short")["token"])
    r = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": OLD_PW, "new_password": "12345"},
    )
    assert r.status_code == 422


def test_change_password_overlong(client):
    """bcrypt 只取前 72 字节,超长新密码应返回 400 而非 500。"""
    headers = _auth(_register(client, "cp_long")["token"])
    r = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": OLD_PW, "new_password": "a" * 73},
    )
    assert r.status_code == 400
    assert "密码过长" in r.json()["detail"]


def test_change_password_unauthenticated(client):
    r = client.post(
        "/api/auth/change-password",
        json={"old_password": OLD_PW, "new_password": "newpass456"},
    )
    assert r.status_code == 401
