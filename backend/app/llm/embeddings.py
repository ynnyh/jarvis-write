# app/llm/embeddings.py
# -*- coding: utf-8 -*-
"""Embedding 客户端:走 OpenAI 兼容 /embeddings 接口。

配置解析优先级(resolve_embedding_config):
① 当前用户在设置页保存的专用 embedding 配置(provider_settings 里
  provider="embedding" 的伪 provider 行,有 key 才算);
② 环境变量 EMBEDDING_BASE_URL / EMBEDDING_API_KEY(+ EMBEDDING_MODEL);
③ 默认 provider 的 base_url + key,模型取 settings.embedding_model。

embedding 可用性取决于所配渠道:DeepSeek 官方没有 /embeddings 接口,部分
中转站也未开放;不可用时上层记忆模块会优雅降级(只用最近章节,不做语义检索)。
"""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.llm.factory import (
    _db_settings,
    resolve_default_provider,
    resolve_provider_config,
)

logger = logging.getLogger("jarvis-write.embeddings")

# provider_settings 表里专用 embedding 配置行的伪 provider 名
EMBEDDING_PROVIDER = "embedding"


def resolve_embedding_config() -> dict:
    """解析当前生效的 embedding 配置,返回 {api_key, base_url, model, source}。

    source ∈ {"user", "env", "default"}:分别表示专用配置(设置页)、
    环境变量、默认 provider 兜底。default 兜底时可能没有 key(未配置),
    调用方据此提示;memory 层调用失败会自行降级。
    """
    settings = get_settings()

    # ① 设置页保存的专用 embedding 配置(有 key 才算)
    db_cfg = _db_settings().get(EMBEDDING_PROVIDER, {})
    if db_cfg.get("api_key"):
        return {
            "api_key": db_cfg["api_key"],
            "base_url": (db_cfg.get("base_url") or "").rstrip("/"),
            "model": db_cfg.get("model") or settings.embedding_model,
            "source": "user",
        }

    # ② 环境变量专用配置
    if settings.embedding_api_key:
        return {
            "api_key": settings.embedding_api_key,
            "base_url": (settings.embedding_base_url or "").rstrip("/"),
            "model": settings.embedding_model,
            "source": "env",
        }

    # ③ 默认 provider 兜底(维持原行为)
    cfg = resolve_provider_config(resolve_default_provider())
    return {
        "api_key": cfg["api_key"],
        "base_url": (cfg["base_url"] or "").rstrip("/"),
        "model": settings.embedding_model,
        "source": "default",
    }


class EmbeddingClient:
    def __init__(
        self,
        model: str | None = None,
        provider: str | None = None,
        timeout: int = 60,
    ) -> None:
        settings = get_settings()
        if provider is not None:
            # 显式指定 provider:探测该 provider 自身的 /embeddings(设置页
            # 聊天卡测试用),语义与专用 embedding 配置无关
            self.provider = provider
            cfg = resolve_provider_config(provider)
            self.api_key = cfg["api_key"]
            self.base_url = (cfg["base_url"] or "").rstrip("/")
            self.model = model or settings.embedding_model
            self.source = "default"
        else:
            self.provider = resolve_default_provider()
            cfg = resolve_embedding_config()
            self.api_key = cfg["api_key"]
            self.base_url = cfg["base_url"]
            self.model = model or cfg["model"]
            self.source = cfg["source"]
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
