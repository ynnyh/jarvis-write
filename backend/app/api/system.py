# app/api/system.py
# -*- coding: utf-8 -*-
"""系统类接口:健康检查 + LLM 冒烟测试。

阶段 0 验收接口:
- GET  /api/health    查看服务与各 provider 配置状态
- POST /api/ping-llm  发一个 prompt，验证能否调通大模型
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.llm.embeddings import check_embedding
from app.llm.factory import (
    available_providers,
    create_llm_adapter,
    resolve_default_provider,
    resolve_provider_config,
)
from app.schemas.system import (
    HealthResponse,
    PingLLMRequest,
    PingLLMResponse,
)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """服务健康检查,并报告各 provider 是否已配置 key。"""
    return HealthResponse(status="ok", providers=available_providers())


@router.post(
    "/ping-llm",
    response_model=PingLLMResponse,
    # 端点级鉴权:/health 保持公开,ping-llm 要求登录
    # (登录后走当前用户自己配置的 key,不再白嫖服务端 .env 的 key)
    dependencies=[Depends(get_current_user)],
)
async def ping_llm(req: PingLLMRequest) -> PingLLMResponse:
    """给模型发一个 prompt，拿回复,并顺带探测 embedding 可用性。"""
    provider = (req.provider or resolve_default_provider()).lower()

    if not resolve_provider_config(provider)["api_key"]:
        raise HTTPException(
            status_code=400,
            detail=f"provider '{provider}' 尚未配置 api_key,请到设置页填写。",
        )

    adapter = create_llm_adapter(provider)
    try:
        resp = await adapter.complete(adapter.to_messages(req.prompt))
    except Exception as exc:  # noqa: BLE001 — 冒烟接口,直接把错误暴露给调用方
        raise HTTPException(status_code=502, detail=f"调用模型失败: {exc}") from exc

    # 顺带探测 embedding 可用性(最多 ~10s,失败不影响 ping 结果)
    emb_ok, emb_err = await check_embedding(provider)

    return PingLLMResponse(
        provider=provider,
        model=resp.model,
        reply=resp.content,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
        embedding_ok=emb_ok,
        embedding_error=emb_err,
    )
