# tests/test_ratelimit.py
# -*- coding: utf-8 -*-
"""限流中间件:超阈值返回 429,不同 IP / 非命中路径互不影响。

用独立小 app 验证中间件本身,不碰全局 app(其限流在测试里已关闭,见 conftest)。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ratelimit import RateLimitMiddleware, Rule


def _client() -> TestClient:
    app = FastAPI()
    # POST /hit 每 60s 最多 3 次;GET /open 不限
    app.add_middleware(RateLimitMiddleware, rules=(Rule("POST", "/hit", 3, 60),))

    @app.post("/hit")
    def hit():
        return {"ok": True}

    @app.get("/open")
    def open_():
        return {"ok": True}

    return TestClient(app)


def _ip(addr: str) -> dict:
    return {"X-Forwarded-For": addr}


def test_blocks_over_limit():
    c = _client()
    for _ in range(3):
        assert c.post("/hit", headers=_ip("1.1.1.1")).status_code == 200
    r = c.post("/hit", headers=_ip("1.1.1.1"))
    assert r.status_code == 429
    assert "过于频繁" in r.json()["detail"]
    assert int(r.headers["Retry-After"]) >= 1


def test_other_ip_independent():
    c = _client()
    for _ in range(3):
        c.post("/hit", headers=_ip("2.2.2.2"))
    # 换个 IP:自己的窗口,照常放行
    assert c.post("/hit", headers=_ip("3.3.3.3")).status_code == 200


def test_unmatched_path_not_limited():
    c = _client()
    for _ in range(10):
        assert c.get("/open", headers=_ip("4.4.4.4")).status_code == 200
