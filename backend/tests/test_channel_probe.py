# tests/test_channel_probe.py
# -*- coding: utf-8 -*-
"""渠道体检:连通 ≠ 能干活。

真实案例(2026-09-08):某中转渠道(ooioo / glm-5.3-flash,Claude 中继)
「测试连接」秒过、小请求也正常,但真跑中文长篇管线时把英文思维链直接吐进
正文(`Let me carefully analyze this task…`),JSON 环节全线解析失败。
ping 式测试看不见这类问题,体检(中文输出 / JSON 结构)必须看得见——
这几条测试就是那次事故的回归锁。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMResponse
from app.llm.probe import cjk_ratio, probe_channel
from app.main import app

INVITE = "test-invite"

_CN_OK = "一年有四个季节。"
_JSON_OK = '{"season_count": 4, "example": "春天"}'
# 事故原样:英文思维链被当成正文吐回来
_COT_LEAK = (
    "Let me carefully analyze this task. I'm reviewing the blueprint against "
    "the timeline and the world rules. Chapter 38 en…"
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class _FakeAdapter:
    """按脚本出牌;可指定 token 数以触发速度检查。"""

    def __init__(self, script: list, tokens: int = 0):
        self._script = list(script)
        self.tokens = tokens
        self.calls = 0
        self.prompts: list[str] = []

    @staticmethod
    def to_messages(prompt: str, system: str | None = None) -> list:
        return [{"role": "user", "content": prompt}]

    async def complete(self, messages) -> LLMResponse:
        self.calls += 1
        self.prompts.append(messages[-1]["content"])
        item = self._script.pop(0) if self._script else ""
        if isinstance(item, Exception):
            raise item
        return LLMResponse(content=str(item), model="fake-model", completion_tokens=self.tokens)


def test_cjk_ratio_basics():
    assert cjk_ratio("一年有四个季节") == 1.0
    assert cjk_ratio("") == 0.0
    assert cjk_ratio(_COT_LEAK) == 0.0
    assert 0.5 < cjk_ratio("一年有 4 个季节。") < 1.0


def test_probe_passes_on_chinese_and_json():
    adapter = _FakeAdapter([_CN_OK, _JSON_OK])
    probe = asyncio.run(probe_channel(adapter))

    assert probe.ok is True
    assert probe.suitable is True
    assert adapter.calls == 2
    assert [c.name for c in probe.checks if not c.warning] == ["中文输出", "JSON 结构"]


def test_english_cot_leak_is_not_suitable():
    """英文思维链泄漏(ooioo 事故):连通但判不适配,且失败项是「中文输出」。"""
    adapter = _FakeAdapter([_COT_LEAK, _JSON_OK])
    probe = asyncio.run(probe_channel(adapter))

    assert probe.ok is True  # 链路是通的
    assert probe.suitable is False
    assert probe.failed_names() == ["中文输出"]
    assert "思维链" in next(c.detail for c in probe.checks if c.name == "中文输出")


def test_json_structure_failure_blocks():
    """JSON 解析不出 = 一致性/主审/抽取全线降级,必须判不适配。"""
    adapter = _FakeAdapter([_CN_OK, "好的,我的回答如下:一年有四个季节。"])
    probe = asyncio.run(probe_channel(adapter))

    assert probe.ok is True
    assert probe.suitable is False
    assert "JSON 结构" in probe.failed_names()


def test_call_failure_stops_probe():
    adapter = _FakeAdapter([RuntimeError("上游 500")])
    probe = asyncio.run(probe_channel(adapter))

    assert probe.ok is False
    assert probe.suitable is False
    assert adapter.calls == 1  # 首次调用就炸,不必再浪费第二次


def test_slow_channel_only_warns():
    """慢只是提醒:不参与「是否适配」判定,但要给出 tok/s。"""
    adapter = _FakeAdapter([_CN_OK, _JSON_OK], tokens=1)
    probe = asyncio.run(probe_channel(adapter))

    assert probe.suitable is True
    assert probe.tokens_per_second > 0
    speed = next(c for c in probe.checks if c.name == "响应速度")
    assert speed.warning is True


def test_endpoint_reports_unsuitable_channel(client, monkeypatch):
    """设置页「测试连接」:通但不适配 → suitable=False + 可行动的红字提示。"""
    headers, cid = _save_provider(client, "probe_bad_user")
    adapter = _FakeAdapter([_COT_LEAK, _JSON_OK])
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: False)

    body = client.post(
        f"/api/settings/providers/{cid}/test", headers=headers
    ).json()

    assert body["ok"] is True
    assert body["suitable"] is False
    assert len(body["checks"]) >= 2
    assert any(c["name"] == "中文输出" and not c["passed"] for c in body["checks"])
    assert any("不适合中文长篇写作" in w for w in body["warnings"])
    assert "思维链" in "".join(c["detail"] for c in body["checks"])


def test_endpoint_reports_healthy_channel(client, monkeypatch):
    headers, cid = _save_provider(client, "probe_good_user")
    # 连通主测试 1 次 + 体检 2 次
    adapter = _FakeAdapter(["连接成功", _CN_OK, _JSON_OK])
    monkeypatch.setattr("app.api.settings.create_llm_adapter", lambda **kw: adapter)
    monkeypatch.setattr("app.api.settings.is_cloudflare_hosted", lambda url: False)

    body = client.post(
        f"/api/settings/providers/{cid}/test", headers=headers
    ).json()

    assert body["ok"] is True
    assert body["suitable"] is True
    assert body["warnings"] == []
    # 体检用的是探针 prompt,不是「请回复:连接成功」
    assert any("季节" in p for p in adapter.prompts)


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
