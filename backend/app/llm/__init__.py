"""LLM 适配层。

对上层暴露一致的 `LLMAdapter` 协议,底层支持三类 wire 协议:
- openai-compatible:OpenAI / DeepSeek / Kimi / 通义 / 中转站 / 本地 Ollama…(主力通用卡);
- anthropic:Claude 原生 Messages API;
- gemini:Google 原生 generateContent。
不用 LangChain,自己封更可控(见 docs/01-architecture.md)。
"""
from app.llm.base import LLMAdapter, LLMMessage, LLMResponse
from app.llm.factory import create_llm_adapter

__all__ = ["LLMAdapter", "LLMMessage", "LLMResponse", "create_llm_adapter"]
