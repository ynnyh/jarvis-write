# tests/test_scene_level_api.py
# -*- coding: utf-8 -*-
"""场景级生成开关的 API 契约:必须能读出来、能写进去、默认是关的。

钉住三件事:
1. GET /projects/{id} 带出 scene_level_enabled,新项目默认 False(不悄悄换掉老路径);
2. PATCH 能开关,且只动这一个字段(不碰同请求里的其他配置);
3. 开关状态可持久化,重新读取一致(否则前端勾了刷新就掉)。
"""
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


def _new_project(client: TestClient, headers: dict) -> int:
    r = client.post("/api/projects", headers=headers,
                    json={"title": "场景级", "target_chapters": 10})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_default_is_off(client):
    """新项目默认走老路径 —— 场景级是实验特性,不能偷偷改掉默认行为。"""
    headers = _auth(client, f"sc_def_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers)

    r = client.get(f"/api/projects/{pid}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["scene_level_enabled"] is False


def test_toggle_roundtrip(client):
    """开 → 读回来是 True;关 → 读回来是 False。"""
    headers = _auth(client, f"sc_tog_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers)

    r = client.patch(f"/api/projects/{pid}", headers=headers,
                     json={"scene_level_enabled": True})
    assert r.status_code == 200, r.text
    assert r.json()["scene_level_enabled"] is True

    r = client.get(f"/api/projects/{pid}", headers=headers)
    assert r.json()["scene_level_enabled"] is True

    r = client.patch(f"/api/projects/{pid}", headers=headers,
                     json={"scene_level_enabled": False})
    assert r.status_code == 200, r.text
    r = client.get(f"/api/projects/{pid}", headers=headers)
    assert r.json()["scene_level_enabled"] is False


def test_toggle_does_not_disturb_other_config(client):
    """只改场景级开关时,同卡片的审校配置不许被顺手重置。"""
    headers = _auth(client, f"sc_iso_{uuid.uuid4().hex[:6]}")
    pid = _new_project(client, headers)

    client.patch(f"/api/projects/{pid}", headers=headers,
                 json={"review_max_revisions": 1, "review_pass_threshold": 8})
    r = client.patch(f"/api/projects/{pid}", headers=headers,
                     json={"scene_level_enabled": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scene_level_enabled"] is True
    assert body["review_max_revisions"] == 1
    assert body["review_pass_threshold"] == 8
