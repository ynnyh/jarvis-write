"""DeepSeek 适配器。

DeepSeek 完全兼容 OpenAI /chat/completions 协议,只是默认 base_url 不同。
阶段 0 我们优先跑通这一家。
"""
from __future__ import annotations

from app.llm.openai_compatible import OpenAICompatibleAdapter


class DeepSeekAdapter(OpenAICompatibleAdapter):
    interface_format = "deepseek"
    default_base_url = "https://api.deepseek.com"
