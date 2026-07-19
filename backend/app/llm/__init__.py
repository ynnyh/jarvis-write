"""LLM 适配层。

统一封装 DeepSeek / OpenAI / Gemini 三家接口,对上层暴露一致的
`LLMAdapter` 协议。不用 LangChain,自己封更可控(见 docs/01-architecture.md)。
"""
from app.llm.base import LLMAdapter, LLMMessage, LLMResponse
from app.llm.factory import create_llm_adapter

__all__ = ["LLMAdapter", "LLMMessage", "LLMResponse", "create_llm_adapter"]
