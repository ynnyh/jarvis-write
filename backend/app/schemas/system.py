# app/schemas/system.py
# -*- coding: utf-8 -*-
"""系统类接口的请求/响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str = "ok"
    providers: dict[str, bool] = Field(
        default_factory=dict, description="各 LLM provider 是否已配置好 key"
    )


class PingLLMRequest(BaseModel):
    """冒烟测试:给一个 prompt，看模型能否正常回复。"""

    prompt: str = Field(default="用一句话介绍你自己。", description="发给模型的内容")
    provider: str | None = Field(
        default=None, description="指定 provider；缺省用默认 provider"
    )


class PingLLMResponse(BaseModel):
    """冒烟测试响应。"""

    provider: str
    model: str
    reply: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    embedding_ok: bool = False
    embedding_error: str = ""
