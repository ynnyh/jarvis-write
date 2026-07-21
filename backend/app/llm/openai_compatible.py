"""OpenAI 兼容适配器基类。

DeepSeek、OpenAI、以及任何 OpenAI-compatible 接口(含本地 Ollama)
都走 `/chat/completions`,请求/返回格式一致,故抽出公共实现。
用 httpx 直连,不引厂商 SDK,保持轻量可控。
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.llm.base import LLMAdapter, LLMMessage, LLMResponse, check_upstream


class OpenAICompatibleAdapter(LLMAdapter):
    """走 OpenAI /chat/completions 协议的通用适配器。"""

    interface_format = "openai-compatible"
    default_base_url = "https://api.openai.com/v1"

    def _endpoint(self) -> str:
        base = (self.base_url or self.default_base_url).rstrip("/")
        return f"{base}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages: list[LLMMessage], stream: bool) -> dict:
        return {
            "model": self.model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }

    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self._endpoint(),
                headers=self._headers(),
                json=self._payload(messages, stream=False),
            )
            data = check_upstream(
                resp,
                hint="确认 Base URL 含 /v1 且渠道支持 OpenAI 协议",
            )

        choice = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice,
            model=data.get("model", self.model_name),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            raw=data,
        )

    async def stream(self, messages: list[LLMMessage]) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                self._endpoint(),
                headers=self._headers(),
                json=self._payload(messages, stream=True),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    delta = (
                        chunk.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if delta:
                        yield delta
