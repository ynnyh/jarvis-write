# backend/tests/test_opencode_go.py
# -*- coding: utf-8 -*-
"""OpenCode Go 渠道卡:请求头(会话/UA)、模型族路由(兼容族 vs Responses 族)、
Responses 协议报文序列化与 SSE 解析、注册表与预设的 API 端到端。

Go 网关按模型族分协议:GLM/Kimi/DeepSeek 等 → /chat/completions,
Grok/GPT-luna → /v1/responses。卡内自动路由,用户只填一个 Key。
"""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.llm.factory import create_llm_adapter
from app.llm.opencode_go import OpenCodeGoAdapter, uses_responses_api
from app.llm.openai_responses import OpenAIResponsesAdapter

INVITE = "test-invite"
_GO = "https://opencode.ai/zen/go/v1"


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


def _with_uid(client: TestClient, headers: dict, fn):
    from app.auth import current_user_id

    me = client.get("/api/auth/me", headers=headers).json()
    tok = current_user_id.set(me["id"])
    try:
        return fn()
    finally:
        current_user_id.reset(tok)


def _go_adapter(**kw) -> OpenCodeGoAdapter:
    kw.setdefault("base_url", _GO)
    kw.setdefault("model_name", "glm-5.3-flash")
    a = OpenCodeGoAdapter(api_key="sk-go", **kw)
    a.retry_base_delay = 0  # 测试不等退避
    return a


# ---------- 模型族路由判断 ----------

def test_uses_responses_api():
    for m in ("glm-5.3-flash", "kimi-k3", "deepseek-v4-pro", "longcat-2.0",
              "mimo-v2.5-pro", "hy4-preview"):
        assert not uses_responses_api(m), m
    for m in ("grok-4.6", "gpt-5.6-luna", "muse-spark-1.3-contributor"):
        assert uses_responses_api(m), m


# ---------- 端点与会话头 ----------

def test_default_endpoint_and_go_headers():
    a = OpenCodeGoAdapter(api_key="sk-go", model_name="glm-5.3-flash")
    assert a._endpoint() == f"{_GO}/chat/completions"
    h = a._headers()
    assert h["Authorization"] == "Bearer sk-go"
    assert h["User-Agent"] == "jarvis-write"
    sid = h["x-opencode-session"]
    assert sid
    # 适配器按次创建,一个实例视作一个会话:实例内会话头稳定
    assert a._headers()["x-opencode-session"] == sid


def test_custom_base_url():
    a = OpenCodeGoAdapter(
        api_key="k", model_name="kimi-k3", base_url="https://mirror.example.com/v1/"
    )
    assert a._endpoint() == "https://mirror.example.com/v1/chat/completions"


def test_responses_adapter_endpoint():
    a = OpenAIResponsesAdapter(api_key="k", model_name="grok-4.6")
    assert a._endpoint() == "https://api.openai.com/v1/responses"


# ---------- 路由:兼容族 vs Responses 族 ----------

class _FakeStreamCM:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aenter__(self) -> httpx.Response:
        return self._response

    async def __aexit__(self, *_exc) -> bool:
        return False


def _patch_post(monkeypatch, respond):
    """桩掉非流式 POST:respond(url, headers, payload) → httpx.Response。"""
    seen = {}

    async def fake_post(self, url, *, headers=None, json=None, **kw):
        seen.update({"url": url, "headers": headers or {}, "payload": json})
        return respond(seen)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return seen


def _patch_stream(monkeypatch, response: httpx.Response):
    seen = {}

    def fake_stream(self, method, url, *, headers=None, json=None, **kw):
        seen.update({"url": url, "headers": headers or {}, "payload": json})
        return _FakeStreamCM(response)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
    return seen


def test_chat_family_posts_chat_completions(monkeypatch):
    def respond(seen):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "雪落了一夜。"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 6},
        })

    seen = _patch_post(monkeypatch, respond)
    a = _go_adapter()
    a.prefer_stream = False
    resp = asyncio.run(a.complete(a.to_messages("写一句")))
    assert seen["url"] == f"{_GO}/chat/completions"
    assert "messages" in seen["payload"] and "input" not in seen["payload"]
    assert seen["headers"]["x-opencode-session"]
    assert resp.content == "雪落了一夜。"


def test_responses_family_routed_with_go_headers(monkeypatch):
    def respond(seen):
        return httpx.Response(200, json={
            "id": "resp_1", "status": "completed", "model": "grok-4.6",
            "output": [
                {"type": "reasoning",
                 "summary": [{"type": "summary_text", "text": "推演了一下"}]},
                {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "刀出鞘了。"}]},
            ],
            "usage": {"input_tokens": 12, "output_tokens": 34,
                      "output_tokens_details": {"reasoning_tokens": 5}},
        })

    seen = _patch_post(monkeypatch, respond)
    a = _go_adapter(model_name="grok-4.6")
    a.prefer_stream = False
    resp = asyncio.run(a.complete(a.to_messages("写一句")))
    assert seen["url"] == f"{_GO}/responses"
    payload = seen["payload"]
    assert "input" in payload and "messages" not in payload
    assert payload["max_output_tokens"] == a.max_tokens
    # to_messages 不带 system → 无 instructions 字段
    assert "instructions" not in payload
    # Go 会话头与 UA 要带到 Responses 路径
    assert seen["headers"]["x-opencode-session"] == a._headers()["x-opencode-session"]
    assert seen["headers"]["User-Agent"] == "jarvis-write"
    assert resp.content == "刀出鞘了。"
    assert resp.finish_reason == "stop"
    assert resp.prompt_tokens == 12
    assert resp.completion_tokens == 34
    assert resp.reasoning == "推演了一下"


def test_responses_stream_sse(monkeypatch):
    body = "".join([
        'data: {"type":"response.output_text.delta","delta":"刀"}\n\n',
        'data: {"type":"response.output_text.delta","delta":"出鞘了。"}\n\n',
        'data: {"type":"response.completed","response":{"status":"completed",'
        '"usage":{"input_tokens":12,"output_tokens":34,'
        '"output_tokens_details":{"reasoning_tokens":5}}}}\n\n',
    ])
    seen = _patch_stream(monkeypatch, httpx.Response(
        200, text=body, headers={"content-type": "text/event-stream"}
    ))
    a = _go_adapter(model_name="grok-4.6")
    resp = asyncio.run(a.complete(a.to_messages("写一句")))
    assert seen["url"] == f"{_GO}/responses"
    assert seen["payload"]["stream"] is True
    assert resp.content == "刀出鞘了。"
    assert resp.finish_reason == "stop"
    assert resp.completion_tokens == 34


# ---------- 注册表与预设:API 端到端 ----------

def test_registry_and_preset_roundtrip(client):
    """经设置 API 建 opencode-go 配置:预设回显正确,factory 造出 Go 适配器。"""
    headers = _auth(client, "ocgo_api")
    r = client.post(
        "/api/settings/providers",
        headers=headers,
        json={"interface_format": "opencode-go", "api_key": "sk-go",
              "model": "glm-5.3-flash"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default_base_url"] == _GO
    assert body["default_model"] == "glm-5.3-flash"

    adapter = _with_uid(
        client, headers, lambda: create_llm_adapter(config_id=body["id"])
    )
    assert isinstance(adapter, OpenCodeGoAdapter)
    assert adapter.model_name == "glm-5.3-flash"
    # 配置没填 base_url → 回落到 Go 官方端点
    assert adapter._endpoint() == f"{_GO}/chat/completions"
