# tests/test_cf_warning.py
# -*- coding: utf-8 -*-
"""「测试连接」对 CF 渠道的稳定性加测 + warnings 下发。

单次测试成功只代表那一刻通;CF 渠道国内直连常见分钟级间歇故障。这里用
脚本化假适配器(不发真实 HTTP)+ monkeypatch CF 判定,验证:
- CF 渠道测试通过后追加 2 次快测,抖动会进 warnings;
- 非 CF 渠道行为不变(单次调用、无 warnings)。
"""
import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMResponse
from app.main import app

INVITE = "test-invite"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class _FakeAdapter:
    """按脚本出牌:脚本项为异常则抛,否则返回该字符串为回复。"""

    def __init__(self, script: list):
        self._script = list(script)
        self.calls = 0

    @staticmethod
    def to_messages(prompt: str, system: str | None = None) -> list:
        return [{"role": "user", "content": prompt}]

    async def complete(self, messages) -> LLMResponse:
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=str(item), model="fake-model")


def _save_provider(client: TestClient, username: str) -> tuple[dict, int]:
    r = client.post(
        "/api/auth/register",
        json={"username": username, "password": "pass123", "invite_code": INVITE},
    )
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.post(
        "/api/settings/providers",
        headers=headers,
        json={
            "interface_format": "openai-compatible",
            "api_key": "sk-x",
            "base_url": "https://relay.example.com/v1",
            "model": "m",
        },
    )
    assert r.status_code == 200, r.text
    return headers, r.json()["id"]


def _test(client: TestClient, headers: dict, config_id: int):
    return client.post(
        f"/api/settings/providers/{config_id}/test", headers=headers
    )


def test_cf_channel_flaky_probe_warns(client, monkeypatch):
    """CF 渠道:主测试成功 + 快测一次失败 → warnings 带稳定性提示。"""
    headers, cid = _save_provider(client, "cf_flaky_user")
    adapter = _FakeAdapter(["连接成功", RuntimeError("断了"), "pong"])
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: True)

    r = _test(client, headers, cid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    # 主测试 + 2 次稳定性快测,共 3 次调用
    assert adapter.calls == 3
    assert any("Cloudflare" in w for w in body["warnings"])
    assert any("稳定性探测" in w and "2 次失败" in w for w in body["warnings"])


def test_cf_channel_stable_probe_still_flags_cdn(client, monkeypatch):
    """CF 渠道:3 次全过 → 仍带 CDN 风险提示,但无稳定性告警。"""
    headers, cid = _save_provider(client, "cf_stable_user")
    adapter = _FakeAdapter(["连接成功", "pong", "pong"])
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: True)

    body = _test(client, headers, cid).json()
    assert body["ok"] is True
    assert adapter.calls == 3
    assert any("Cloudflare" in w for w in body["warnings"])
    assert not any("稳定性" in w for w in body["warnings"])


def test_non_cf_channel_unchanged(client, monkeypatch):
    """非 CF 渠道:单次调用、无 warnings——旧行为完全不变。"""
    headers, cid = _save_provider(client, "non_cf_user")
    adapter = _FakeAdapter(["连接成功"])
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: False)

    body = _test(client, headers, cid).json()
    assert body["ok"] is True
    assert adapter.calls == 1
    assert body["warnings"] == []
