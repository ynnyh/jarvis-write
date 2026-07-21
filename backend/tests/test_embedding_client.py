# tests/test_embedding_client.py
# -*- coding: utf-8 -*-
"""EmbeddingClient 传输层:分批发送、瞬时失败重试、4xx 直接抛。

背景:部分中转站的 /embeddings 极慢(实测单条 ~20s)且整章几十段打包更慢,
过去默认 60s 超时会被误杀成"降级为空"。修复后:超时可配、大批量分块串行、
超时/5xx 轻量重试、4xx(鉴权/参数)不空转直接抛。这里用 MockTransport
验证这几条,不打真实网络。用 asyncio.run 驱动(与仓库其余异步测试一致)。
"""
import asyncio
import json

import httpx
import pytest

from app.llm.embeddings import EmbeddingClient


def _emb_response(n: int, dim: int = 4) -> httpx.Response:
    """造一个 OpenAI 格式的 /embeddings 成功响应,含 n 条向量。"""
    return httpx.Response(
        200,
        json={
            "data": [
                {"index": i, "embedding": [float(i)] * dim} for i in range(n)
            ]
        },
    )


def _patch_transport(monkeypatch, handler) -> None:
    """让 embed() 内部自建的 AsyncClient 挂上 MockTransport。"""
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)


def _client() -> EmbeddingClient:
    """造一个配置固定的 client,不依赖 DB/env(直接写字段,绕过 __init__ 解析)。"""
    c = EmbeddingClient.__new__(EmbeddingClient)
    c.provider = "openai"
    c.api_key = "sk-test"
    c.base_url = "https://fake/v1"
    c.model = "fake-embed"
    c.source = "user"
    c.timeout = 5
    c.batch_size = 16
    c.max_retries = 2
    return c


def test_batches_split_by_size(monkeypatch):
    """40 段、batch_size=16 → 发 3 个请求(16+16+8),向量按序拼回。"""
    seen_sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        n = len(json.loads(request.content)["input"])
        seen_sizes.append(n)
        return _emb_response(n)

    _patch_transport(monkeypatch, handler)
    c = _client()
    c.batch_size = 16
    out = asyncio.run(c.embed([f"seg{i}" for i in range(40)]))

    assert seen_sizes == [16, 16, 8]
    assert len(out) == 40


def test_retry_on_5xx_then_success(monkeypatch):
    """首次 500、重试成功 → 不抛,拿到向量。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(500, text="upstream boom")
        return _emb_response(2)

    _patch_transport(monkeypatch, handler)
    c = _client()
    c.max_retries = 2
    out = asyncio.run(c.embed(["a", "b"]))

    assert state["n"] == 2  # 重试了一次
    assert len(out) == 2


def test_4xx_raises_without_retry(monkeypatch):
    """401 鉴权错 → 立即抛,不重试(避免无意义空转)。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(401, text="unauthorized")

    _patch_transport(monkeypatch, handler)
    c = _client()
    c.max_retries = 3
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(c.embed(["x"]))
    assert state["n"] == 1  # 只发一次,没重试


def test_retry_exhausted_raises(monkeypatch):
    """一直 503 → 重试用尽后抛出(由上层降级)。"""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(503, text="unavailable")

    _patch_transport(monkeypatch, handler)
    c = _client()
    c.max_retries = 2
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(c.embed(["x"]))
    assert state["n"] == 2  # 试满 max_retries


def test_empty_input_short_circuits(monkeypatch):
    """空输入不发请求,直接返回空。"""
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return _emb_response(0)

    _patch_transport(monkeypatch, handler)
    c = _client()
    out = asyncio.run(c.embed([]))
    assert out == []
    assert called["n"] == 0
