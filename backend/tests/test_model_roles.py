# tests/test_model_roles.py
# -*- coding: utf-8 -*-
"""模型角色分配的可观测性(D7):创作走贵模型、校验走便宜模型,且这件事要看得见。

钉住:
1. 接口能返回三个角色(写手/审校/杂活)的配置;
2. 写手与审校同配置时,self_review 与 advice 要如实反映;
3. 配置读取异常时降级返回而非 500(设置页读不到配置不该整页报错)。
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


def test_model_roles_shape(client):
    headers = _auth(client, f"mr_{uuid.uuid4().hex[:6]}")
    r = client.get("/api/editorial/model-roles", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    for role in ("writer", "auditor", "worker"):
        assert role in body, role
        assert "tier" in body[role]
    assert "self_review" in body and "auditor_separated" in body


def test_advice_present_when_not_separated(client):
    """写手与审校没分开时,必须给出可操作的提示(而不是沉默)。"""
    headers = _auth(client, f"mr_sep_{uuid.uuid4().hex[:6]}")
    r = client.get("/api/editorial/model-roles", headers=headers)
    body = r.json()
    if body.get("auditor_separated") is False:
        assert body["advice"], "未分离却没给提示,用户不会知道分数不客观"
    else:
        assert body["advice"] == ""


def test_degrades_instead_of_500(client, monkeypatch):
    """配置读取抛异常时降级返回 available=False,不炸设置页。"""
    headers = _auth(client, f"mr_deg_{uuid.uuid4().hex[:6]}")

    import app.llm.router as router_mod

    def _boom(tier):
        raise RuntimeError("配置库读不到")

    monkeypatch.setattr(router_mod, "_tier_config", _boom)
    r = client.get("/api/editorial/model-roles", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert "配置库读不到" in body["reason"]
