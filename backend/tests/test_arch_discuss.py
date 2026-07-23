# tests/test_arch_discuss.py
# -*- coding: utf-8 -*-
"""架构研讨(对话式)测试:聊清不满意 → 蒸馏额外要求 → 注入重新生成。

验证点:
- POST .../architecture/discuss 返回 reply + directive(蒸馏结果)
- 蒸馏出"-"(无明确意见)归一化成空串
- 归属隔离:对他人项目研讨 → 404
- 空对话 → 400
- 引擎级:directive 高优先级注入雪花四步 prompt
"""
from __future__ import annotations

from unittest.mock import patch

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


def _create_project(client: TestClient, headers: dict, title: str = "研讨书") -> dict:
    r = client.post("/api/projects", headers=headers, json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


class _ChatAdapter:
    """假适配器:complete 返回续聊回复,ask 返回蒸馏结果(架构研讨两段式)。"""

    def __init__(self, reply: str, distilled: str):
        self._reply = reply
        self._distilled = distilled
        self.max_tokens = 8192

    def _record_usage(self, resp):  # noqa: ANN001
        pass

    async def complete(self, messages):
        return type("R", (), {
            "content": self._reply, "model": "fake",
            "prompt_tokens": 1, "completion_tokens": 1,
        })()

    async def ask(self, prompt, system=None):
        return self._distilled


def test_arch_discuss_returns_reply_and_directive(client):
    """研讨:续聊回复 + 蒸馏出的额外要求都返回。"""
    headers = _auth(client, "arch_disc_user")
    p = _create_project(client, headers)

    from app.engines.pipeline import architecture as arch_mod

    adapter = _ChatAdapter(
        reply="明白了,你是想让主角更黑化。那结局要收在开放式吗?",
        distilled="1. 主角改为反英雄气质,带道德瑕疵\n2. 结局收在开放式,不要大团圆",
    )
    with patch.object(arch_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/architecture/discuss",
            headers=headers,
            json={"messages": [{"role": "user", "content": "我觉得主角太正派了"}]},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "黑化" in body["reply"]
    assert "反英雄" in body["directive"]
    assert "开放式" in body["directive"]


def test_arch_discuss_empty_directive_when_dash(client):
    """蒸馏出短横线(尚无明确意见)→ directive 归一化成空串。"""
    headers = _auth(client, "arch_disc_dash")
    p = _create_project(client, headers)

    from app.engines.pipeline import architecture as arch_mod

    adapter = _ChatAdapter(reply="你具体是指哪方面不满意呢?", distilled="-")
    with patch.object(arch_mod, "get_adapter_for", return_value=adapter):
        r = client.post(
            f"/api/projects/{p['id']}/architecture/discuss",
            headers=headers,
            json={"messages": [{"role": "user", "content": "不太满意"}]},
        )
    assert r.status_code == 200, r.text
    assert r.json()["directive"] == ""


def test_arch_discuss_empty_messages_400(client):
    headers = _auth(client, "arch_disc_empty")
    p = _create_project(client, headers)
    r = client.post(
        f"/api/projects/{p['id']}/architecture/discuss",
        headers=headers,
        json={"messages": []},
    )
    assert r.status_code == 400


def test_arch_discuss_not_owner_404(client):
    """对他人项目研讨 → 404(不泄露存在性)。"""
    a = _auth(client, "arch_disc_a")
    b = _auth(client, "arch_disc_b")
    p = _create_project(client, a, "别人的研讨书")
    r = client.post(
        f"/api/projects/{p['id']}/architecture/discuss",
        headers=b,
        json={"messages": [{"role": "user", "content": "在?"}]},
    )
    assert r.status_code == 404


def test_directive_injected_into_snowflake_steps():
    """引擎级:directive 作为「额外要求」高优先级注入四步 prompt。"""
    import asyncio

    from app.engines.pipeline import architecture as arch_mod
    from tests.test_pipeline import MockAdapter, MOCK_ARCH_REPLIES

    adapter = MockAdapter(list(MOCK_ARCH_REPLIES))
    with patch.object(arch_mod, "get_adapter_for", return_value=adapter):
        asyncio.run(
            arch_mod.generate_architecture(
                topic="测试主题",
                genre="测试",
                number_of_chapters=10,
                word_number=3000,
                directive="主角改为反英雄气质,结局开放式",
            )
        )
    # 四步 prompt 都应带上额外要求块与具体指令
    assert len(adapter.calls) == 4
    for call in adapter.calls:
        assert "额外要求" in call
        assert "反英雄" in call


def test_directive_empty_keeps_old_behavior():
    """directive 为空:四步 prompt 不出现额外要求块(向后兼容)。"""
    import asyncio

    from app.engines.pipeline import architecture as arch_mod
    from tests.test_pipeline import MockAdapter, MOCK_ARCH_REPLIES

    adapter = MockAdapter(list(MOCK_ARCH_REPLIES))
    with patch.object(arch_mod, "get_adapter_for", return_value=adapter):
        asyncio.run(
            arch_mod.generate_architecture(
                topic="测试主题",
                genre="测试",
                number_of_chapters=10,
                word_number=3000,
            )
        )
    for call in adapter.calls:
        assert "额外要求" not in call
