# tests/test_cf_warning.py
# -*- coding: utf-8 -*-
"""「测试连接」对 CF 渠道的稳定性加测 + warnings 下发。

单次测试成功只代表那一刻通;CF 渠道国内直连常见分钟级间歇故障。这里用
脚本化假适配器(不发真实 HTTP)+ monkeypatch CF 判定,验证:
- CF 渠道测试通过后追加 2 次快测,抖动会进 warnings;
- 非 CF 渠道不做稳定性快测(但仍跑渠道体检的 2 次调用)。

调用序列(连通时):主测试 1 次 + 渠道体检 2 次(中文输出/JSON 结构)
+ CF 渠道额外 2 次稳定性快测。
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


def _probe_script() -> list:
    """连通后体检的两次回复:中文回答 + 合规 JSON(体检全过,不产生 warnings)。"""
    return ["连接成功", "一年有四个季节。", '{"season_count": 4, "example": "春天"}']


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
    adapter = _FakeAdapter(_probe_script() + [RuntimeError("断了"), "pong"])
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: True)

    r = _test(client, headers, cid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    # 主测试 + 体检 2 次 + 稳定性快测 2 次,共 5 次调用
    assert adapter.calls == 5
    assert any("Cloudflare" in w for w in body["warnings"])
    assert any("稳定性探测" in w and "2 次失败" in w for w in body["warnings"])


def test_cf_channel_stable_probe_still_flags_cdn(client, monkeypatch):
    """CF 渠道:3 次全过 → 仍带 CDN 风险提示,但无稳定性告警。"""
    headers, cid = _save_provider(client, "cf_stable_user")
    adapter = _FakeAdapter(_probe_script() + ["pong", "pong"])
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: True)

    body = _test(client, headers, cid).json()
    assert body["ok"] is True
    assert adapter.calls == 5
    assert any("Cloudflare" in w for w in body["warnings"])
    assert not any("稳定性" in w for w in body["warnings"])


def test_non_cf_channel_unchanged(client, monkeypatch):
    """非 CF 渠道:单次调用、无 warnings——旧行为完全不变。"""
    headers, cid = _save_provider(client, "non_cf_user")
    adapter = _FakeAdapter(_probe_script())
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: False)

    body = _test(client, headers, cid).json()
    assert body["ok"] is True
    assert adapter.calls == 3  # 主测试 + 体检 2 次
    assert body["warnings"] == []
    assert body["suitable"] is True


def test_empty_content_is_connected_with_warning(client, monkeypatch):
    """推理模型测试时只顾思考、不吐正文:链路是通的 → ok=True + 原因挂 warning。

    否则会出现"测试连接失败"的假警报——真正的问题是输出预算,不是连通性。
    """
    from app.llm.base import EmptyContentError

    headers, cid = _save_provider(client, "empty_content_user")
    adapter = _FakeAdapter([
        EmptyContentError("思考吃满预算", budget_bound=True, diagnosis="finish_reason=length")
    ])
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: False)

    body = _test(client, headers, cid).json()
    assert body["ok"] is True
    assert body["error"] == ""
    assert any("连接本身正常" in w and "思考吃满预算" in w for w in body["warnings"])


def test_empty_content_not_counted_as_flaky_link(client, monkeypatch):
    """CF 渠道的稳定性快测:空正文不算链路失败,不误报"链路不稳"。"""
    from app.llm.base import EmptyContentError

    headers, cid = _save_provider(client, "empty_flaky_user")
    adapter = _FakeAdapter(
        _probe_script() + [
            EmptyContentError("空", budget_bound=True),
            EmptyContentError("空", budget_bound=True),
        ]
    )
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: True)

    body = _test(client, headers, cid).json()
    assert body["ok"] is True
    assert not any("稳定性探测" in w for w in body["warnings"])
