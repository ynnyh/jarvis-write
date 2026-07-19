# app/llm/embeddings.py
# -*- coding: utf-8 -*-
"""Embedding 客户端:走 OpenAI 兼容 /embeddings 接口。

用默认 provider 的 base_url + api_key(设置页配置)。中转站/官方一般都提供
embedding 模型;不可用时上层记忆模块会优雅降级(只用最近章节,不做语义检索)。
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
