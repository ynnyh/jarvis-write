"""OpenAI (GPT) 适配器。

标准 OpenAI /chat/completions 协议。留接口,阶段 0 不重点验证。
"""
from __future__ import annotations

from app.llm.openai_compatible import OpenAICompatibleAdapter


class OpenAIAdapter(OpenAICompatibleAdapter):
    interface_format = "openai"
    default_base_url = "https://api.openai.com/v1"
