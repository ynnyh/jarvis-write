# tests/test_cors_android.py
# -*- coding: utf-8 -*-
"""安卓壳(Capacitor)跨域:server 模式放行 https://localhost 源(远程客户端)。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_capacitor_origin_allowed(client):
    """安卓壳源(https://localhost)的预检与实际请求都带 CORS 头。"""
    r = client.options(
        "/api/auth/login",
        headers={
            "Origin": "https://localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://localhost"

    r2 = client.get("/api/health", headers={"Origin": "https://localhost"})
    assert r2.headers.get("access-control-allow-origin") == "https://localhost"


def test_other_origins_still_rejected(client):
    """非白名单源(如 https://evil.example)不放行——白名单语义没有被放宽成全放。"""
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert r.headers.get("access-control-allow-origin") != "https://evil.example"
