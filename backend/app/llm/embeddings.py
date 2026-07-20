# app/llm/embeddings.py
# -*- coding: utf-8 -*-
"""Embedding 客户端:走 OpenAI 兼容 /embeddings 接口。

用默认 provider 的 base_url + api_key(设置页配置)。embedding 可用性取决于
provider:DeepSeek 官方没有 /embeddings 接口,部分中转站也未开放;不可用时
上层记忆模块会优雅降级(只用最近章节,不做语义检索)。
"""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.llm.factory import resolve_default_provider, resolve_provider_config

logger = logging.getLogger("jarvis-write.embeddings")


class EmbeddingClient:
    def __init__(
        self,
        model: str | None = None,
        provider: str | None = None,
        timeout: int = 60,
    ) -> None:
        settings = get_settings()
        self.provider = provider or resolve_default_provider()
        cfg = resolve_provider_config(self.provider)
        self.api_key = cfg["api_key"]
        self.base_url = (cfg["base_url"] or "").rstrip("/")
        self.model = model or settings.embedding_model
        self.timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。失败抛异常,由调用方决定降级策略。"""
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "input": texts},
            )
            resp.raise_for_status()
            data = resp.json()
        # OpenAI 格式:data[i].embedding,按 index 排序保证对齐
        items = sorted(data["data"], key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]


async def check_embedding(provider: str, timeout: int = 10) -> tuple[bool, str]:
    """探测某 provider 是否支持 /embeddings:对单个短文本发一次真实请求。

    返回 (是否可用, 失败原因)。永不抛异常,供 ping/测试接口安全调用;
    原因只含 HTTP 状态码或异常摘要,不携带 key。
    """
    try:
        client = EmbeddingClient(provider=provider, timeout=timeout)
        if not client.api_key:
            return False, "未配置 api_key"
        await client.embed(["测试"])
        return True, ""
    except httpx.HTTPStatusError as exc:
        return False, f"HTTP {exc.response.status_code}"
    except Exception as exc:  # noqa: BLE001 — 探测接口,任何失败都吞掉只报原因
        return False, f"{type(exc).__name__}: {exc}"[:200]
