# tests/test_ratelimit.py
# -*- coding: utf-8 -*-
"""限流中间件:超阈值返回 429,不同来源 / 非命中路径互不影响。

用独立小 app 验证中间件本身,不碰全局 app(其限流在测试里已关闭,见 conftest)。

默认口径(trust_proxy_headers=False)按 request.client.host 分桶:X-Forwarded-For
第一段是客户端自己写的,认它等于限流形同虚设,所以默认**完全不看**这个头。
"""
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ratelimit import RateLimitMiddleware, Rule


def _client(rules=(Rule("POST", "/hit", 3, 60),)) -> TestClient:
    app = FastAPI()
    # POST /hit 每 60s 最多 3 次;GET /open 不限
    app.add_middleware(RateLimitMiddleware, rules=rules)

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


def test_spoofed_xff_does_not_switch_bucket():
    """默认口径:换 X-Forwarded-For 换不了桶(否则轮换 XFF 即可绕过 IP 限流)。

    TestClient 全部请求的对端都是同一个 testclient,所以只有"XFF 不参与分桶"
    才能让下面这第 4 次仍然被挡。
    """
    c = _client()
    for xff in ("2.2.2.2", "3.3.3.3", "4.4.4.4"):
        assert c.post("/hit", headers=_ip(xff)).status_code == 200
    assert c.post("/hit", headers=_ip("5.5.5.5")).status_code == 429


def test_xff_used_only_when_proxy_trusted():
    """开了 trust_proxy_headers 才读 XFF,且取最右一跳(反代自己记下的对端)。"""
    from app.config import get_settings

    settings = get_settings()
    c = _client()
    with patch.object(settings, "trust_proxy_headers", True):
        # 最右一跳才是生效的桶键:左边伪造再多也挤不进同一个桶
        for xff in ("9.9.9.9, 6.6.6.6", "8.8.8.8, 6.6.6.6", "7.7.7.7, 6.6.6.6"):
            assert c.post("/hit", headers=_ip(xff)).status_code == 200
        assert c.post("/hit", headers=_ip("1.1.1.1, 6.6.6.6")).status_code == 429


def test_trusted_proxy_different_hops_independent():
    """可信反代模式下,不同客户端(最右一跳不同)仍然各走各的桶。"""
    from app.config import get_settings

    settings = get_settings()
    c = _client()
    with patch.object(settings, "trust_proxy_headers", True):
        for _ in range(3):
            c.post("/hit", headers=_ip("6.6.6.6"))
        assert c.post("/hit", headers=_ip("6.7.6.6")).status_code == 200


def test_unmatched_path_not_limited():
    c = _client()
    for _ in range(10):
        assert c.get("/open", headers=_ip("4.4.4.4")).status_code == 200


def _login_client(max_hits: int = 3) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        rules=(Rule("POST", "/api/auth/login", max_hits, 300, by_username=True),),
    )
    seen: list[str] = []

    @app.post("/api/auth/login")
    def login(payload: dict):
        # 回显读到的 body:钉死"限流读了 body 不影响下游解析"这条前提
        seen.append(payload.get("username", ""))
        return {"ok": True}

    c = TestClient(app)
    c.seen = seen  # type: ignore[attr-defined]
    return c


def test_login_username_bucket_blocks_rotating_ips():
    """登录按用户名分桶:同一账号换着 IP 打,一样在阈值后被拒。

    开 trust_proxy_headers 让 XFF 的最右一跳真的分出不同 IP 桶,否则 TestClient
    所有请求同源,被限的其实是 IP 桶,这条断言就名不副实了。
    """
    from app.config import get_settings

    settings = get_settings()
    c = _login_client()
    with patch.object(settings, "trust_proxy_headers", True):
        for i in range(3):
            r = c.post(
                "/api/auth/login",
                json={"username": "victim", "password": "x"},
                headers=_ip(f"10.0.0.{i}"),
            )
            assert r.status_code == 200, r.text
        r = c.post(
            "/api/auth/login",
            json={"username": "victim", "password": "x"},
            headers=_ip("10.0.0.9"),
        )
    assert r.status_code == 429
    assert "过于频繁" in r.json()["detail"]


def test_login_username_bucket_case_insensitive():
    """用户名桶键归一化大小写:Victim 与 victim 是同一个桶。"""
    from app.config import get_settings

    settings = get_settings()
    c = _login_client()
    with patch.object(settings, "trust_proxy_headers", True):
        for i, name in enumerate(("Victim", "victim", "VICTIM")):
            r = c.post(
                "/api/auth/login",
                json={"username": name, "password": "x"},
                headers=_ip(f"10.1.0.{i}"),
            )
            assert r.status_code == 200, r.text
        r = c.post(
            "/api/auth/login",
            json={"username": " victim ", "password": "x"},
            headers=_ip("10.1.0.9"),
        )
    assert r.status_code == 429


def test_login_other_username_not_affected():
    """用户名桶互不影响:被限的账号不牵连另一个账号(各自独立 IP)。"""
    from app.config import get_settings

    settings = get_settings()
    c = _login_client()
    with patch.object(settings, "trust_proxy_headers", True):
        for i in range(3):
            r = c.post(
                "/api/auth/login",
                json={"username": "victim", "password": "x"},
                headers=_ip(f"10.2.0.{i}"),
            )
            assert r.status_code == 200, r.text
        # 第 4 次(换了个 IP)才触发用户名桶
        assert c.post(
            "/api/auth/login",
            json={"username": "victim", "password": "x"},
            headers=_ip("10.2.0.3"),
        ).status_code == 429
        r = c.post(
            "/api/auth/login",
            json={"username": "bystander", "password": "x"},
            headers=_ip("10.2.0.9"),
        )
    assert r.status_code == 200, r.text


def test_login_ip_bucket_still_applies():
    """双限的另一头:IP 桶照旧生效(同 IP 换着用户名打也被挡)。"""
    c = _login_client()
    for name in ("u1", "u2", "u3"):
        assert c.post("/api/auth/login", json={"username": name, "password": "x"}).status_code == 200
    assert c.post("/api/auth/login", json={"username": "u4", "password": "x"}).status_code == 429


def test_login_body_still_reaches_route():
    """限流读过的 body 必须原样喂给下游路由(否则登录直接收不到参数)。"""
    c = _login_client()
    r = c.post("/api/auth/login", json={"username": "alice", "password": "pw"})
    assert r.status_code == 200
    assert c.seen == ["alice"]  # type: ignore[attr-defined]


def test_login_unusable_username_falls_back_to_ip_only():
    """取不到可用用户名(缺字段/超长)时仍走 IP 桶,不会因解析失败而漏放行。"""
    c = _login_client()
    for payload in ({"password": "x"}, {"username": "u" * 200, "password": "x"}, {"password": "x"}):
        r = c.post("/api/auth/login", json=payload)
        assert r.status_code == 200, r.text
    assert c.post("/api/auth/login", json={"password": "x"}).status_code == 429
