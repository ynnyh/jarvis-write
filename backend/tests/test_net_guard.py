# tests/test_net_guard.py
# -*- coding: utf-8 -*-
"""base_url SSRF 防线:拒绝内网/本机地址,放行公网,空串放行。

单元测试用字面 IP(getaddrinfo 对字面 IP 不发 DNS,不依赖网络);
接口测试验证保存配置这一入口确实挡住内网 base_url。
另:Cloudflare CDN 检测(提示用,不拦截)——同样只用字面 IP,不发 DNS。
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.net_guard import assert_public_base_url, is_cloudflare_hosted, is_cloudflare_ip

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://10.0.0.5",
        "http://192.168.1.1/v1",
        "http://169.254.169.254/latest/meta-data",  # 云元数据
        "http://[::1]:8000",
    ],
)
def test_rejects_internal(url):
    with pytest.raises(HTTPException) as exc:
        assert_public_base_url(url)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("url", ["", "   ", "https://api.deepseek.com".replace("api.deepseek.com", "8.8.8.8")])
def test_allows_public_and_empty(url):
    # 字面公网 IP 与空串放行(不发 DNS)
    assert_public_base_url(url) is None


def test_allows_literal_public_ip():
    assert assert_public_base_url("http://1.1.1.1/v1") is None


def _auth_headers(client: TestClient, username: str) -> dict:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_save_provider_rejects_internal_base_url(client):
    headers = _auth_headers(client, "ssrf_user")
    r = client.post(
        "/api/settings/providers",
        headers=headers,
        json={
            "interface_format": "deepseek",
            "api_key": "sk-x",
            "base_url": "http://127.0.0.1:11434",
            "model": "",
        },
    )
    assert r.status_code == 400
    assert "内网" in r.json()["detail"]


# ---------- check_public_url:引擎链路用的出站校验(返回理由,不抛) ----------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x.mp4",
        "http://10.0.0.5/img.png",
        "http://192.168.1.1/v.mp4",
        "http://169.254.169.254/latest/meta-data/",  # 云元数据
        "http://[::1]/x",
    ],
)
def test_check_public_url_blocks_internal(url):
    from app.net_guard import check_public_url

    reason = check_public_url(url)
    assert reason is not None and "内网" in reason


def test_check_public_url_allows_public_literal_ip():
    from app.net_guard import check_public_url

    assert check_public_url("https://8.8.8.8/v.mp4") is None


def test_check_public_url_allows_unresolvable_host():
    # 解析不了的域名连不上,放行(真正外呼会自然失败),不误伤
    from app.net_guard import check_public_url

    assert check_public_url("https://no-such-host-jarvis.invalid/v.mp4") is None


@pytest.mark.parametrize("url", ["", "   ", "not a url"])
def test_check_public_url_rejects_hostless(url):
    from app.net_guard import check_public_url

    reason = check_public_url(url)
    assert reason is not None and "格式" in reason


# ---------- Cloudflare CDN 检测(提示用,不拦截) ----------


@pytest.mark.parametrize(
    "ip",
    [
        "104.21.23.114",   # CF 常用边缘段
        "172.67.210.209",  # CF 常用边缘段
        "188.114.97.1",    # 188.114.96.0/20
        "2606:4700:3030::ac43:d2d1",  # CF IPv6
    ],
)
def test_cf_ip_detected(ip):
    assert is_cloudflare_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "113.31.106.41", "1.2.3.4", "not-an-ip"])
def test_non_cf_ip_not_detected(ip):
    assert is_cloudflare_ip(ip) is False


def test_cf_hosted_with_literal_ip_url():
    # 字面 IP 不发 DNS,稳定可测
    assert is_cloudflare_hosted("https://104.21.23.114/v1") is True
    assert is_cloudflare_hosted("https://8.8.8.8/v1") is False


@pytest.mark.parametrize("url", ["", "   ", "not a url"])
def test_cf_hosted_empty_or_invalid_url_is_false(url):
    # 检测是尽力而为的提示,解析不了绝不报错
    assert is_cloudflare_hosted(url) is False


def test_provider_list_marks_cloudflare(client):
    """接口层:套 CF 的配置在列表/保存响应里带 cloudflare=True(存量配置提醒)。"""
    headers = _auth_headers(client, "cf_user")
    r = client.post(
        "/api/settings/providers",
        headers=headers,
        json={
            "interface_format": "openai-compatible",
            "api_key": "sk-x",
            "base_url": "https://104.21.23.114/v1",  # 字面 CF IP,不发 DNS
            "model": "m",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["cloudflare"] is True

    lst = client.get("/api/settings/providers", headers=headers).json()
    assert [c["cloudflare"] for c in lst] == [True]
