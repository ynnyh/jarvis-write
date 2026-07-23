# tests/test_net_guard.py
# -*- coding: utf-8 -*-
"""base_url SSRF 防线:拒绝内网/本机地址,放行公网,空串放行。

单元测试用字面 IP(getaddrinfo 对字面 IP 不发 DNS,不依赖网络);
接口测试验证保存配置这一入口确实挡住内网 base_url。
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.net_guard import assert_public_base_url

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
    r = client.put(
        "/api/settings/providers/deepseek",
        headers=headers,
        json={"api_key": "sk-x", "base_url": "http://127.0.0.1:11434", "model": ""},
    )
    assert r.status_code == 400
    assert "内网" in r.json()["detail"]


def test_save_embedding_rejects_internal_base_url(client):
    headers = _auth_headers(client, "ssrf_emb_user")
    r = client.put(
        "/api/settings/providers/embedding",
        headers=headers,
        json={"api_key": "sk-x", "base_url": "http://192.168.0.10", "model": ""},
    )
    assert r.status_code == 400
