# tests/test_local_mode_guard.py
# -*- coding: utf-8 -*-
"""local(桌面单机)模式的两道防线:

1. Host 校验中间件(防 DNS rebinding):免鉴权只靠绑 127.0.0.1,恶意网页把域名
   重绑定到 127.0.0.1 后,浏览器发出的请求 Host 头是攻击者域名——必须 403;
   本机回环 Host 正常放行。
2. _assert_local_safe 的绑定地址强制校验:设了 JARVIS_LAUNCHER=desktop 却绑
   非回环地址(如 0.0.0.0)必须拒启动,杜绝公网裸奔组合。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def local_client():
    """以 local 模式构建独立 app 实例(不动全局单例)。

    lifespan 会跑 _assert_local_safe:需要桌面入口标记 + 回环绑定地址,
    否则 local 模式直接拒启动(这正是被测的防线之一)。
    """
    from app.config import get_settings
    from app.main import create_app

    settings = get_settings()
    env = {"JARVIS_LAUNCHER": "desktop", "JARVIS_BIND_HOST": "127.0.0.1"}
    with patch.object(settings, "app_mode", "local"), patch.dict("os.environ", env):
        app = create_app()
        with TestClient(app) as c:
            yield c


def test_local_mode_allows_loopback_host(local_client):
    r = local_client.get("/api/health", headers={"host": "127.0.0.1:8756"})
    assert r.status_code == 200


def test_local_mode_allows_localhost_host(local_client):
    r = local_client.get("/api/health", headers={"host": "localhost:8756"})
    assert r.status_code == 200


def test_local_mode_rejects_foreign_host(local_client):
    """DNS rebinding:Host 是攻击者域名,必须拒。"""
    r = local_client.get("/api/health", headers={"host": "evil.attacker.com:8756"})
    assert r.status_code == 403


def test_local_mode_rejects_missing_host(local_client):
    r = local_client.get("/api/health", headers={"host": ""})
    assert r.status_code == 403


def test_assert_local_safe_rejects_non_loopback_bind():
    from app.config import get_settings
    from app.main import _assert_local_safe

    settings = get_settings()
    with patch.object(settings, "app_mode", "local"), \
         patch.dict("os.environ", {"JARVIS_LAUNCHER": "desktop", "JARVIS_BIND_HOST": "0.0.0.0"}):
        with pytest.raises(RuntimeError, match="回环"):
            _assert_local_safe()


def test_assert_local_safe_accepts_loopback_bind():
    from app.config import get_settings
    from app.main import _assert_local_safe

    settings = get_settings()
    with patch.object(settings, "app_mode", "local"), \
         patch.dict("os.environ", {"JARVIS_LAUNCHER": "desktop", "JARVIS_BIND_HOST": "127.0.0.1"}):
        _assert_local_safe()  # 不抛


def test_assert_local_safe_rejects_missing_launcher_flag():
    from app.config import get_settings
    from app.main import _assert_local_safe

    settings = get_settings()
    with patch.object(settings, "app_mode", "local"), \
         patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("JARVIS_LAUNCHER", None)
        with pytest.raises(RuntimeError, match="desktop"):
            _assert_local_safe()
