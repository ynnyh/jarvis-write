"""Google Gemini 适配器。

Gemini 的 generateContent 接口与 OpenAI 协议不同:
- 认证走 URL 上的 ?key=,不用 Bearer
- 消息结构是 contents/parts,role 用 user/model(没有 assistant)
- system 指令走单独的 system_instruction 字段
留接口,阶段 0 不重点验证,后续阶段补齐测试。
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.llm.base import LLMAdapter, LLMMessage, LLMResponse


class GeminiAdapter(LLMAdapter):
    interface_format = "gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _base(self) -> str:
        return (self.base_url or self.default_base_url).rstrip("/")

    def _split_messages(self, messages: list[LLMMessage]) -> tuple[str | None, list[dict]]:
        """把统一消息拆成 (system_instruction, contents)。"""
        system_text: str | None = None
        contents: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_text = m.content if system_text is None else f"{system_text}\n{m.content}"
                continue
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})
        return system_text, contents

    def _payload(self, messages: list[LLMMessage]) -> dict:
        system_text, contents = self._split_messages(messages)
        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        if system_text:
            payload["system_instruction"] = {"parts": [{"text": system_text}]}
        return payload

    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        url = f"{self._base()}/models/{self.model_name}:generateContent?key={self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=self._payload(messages))
            resp.raise_for_status()
            data = resp.json()

        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata", {})
        return LLMResponse(
            content=text,
            model=self.model_name,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            raw=data,
        )

    async def stream(self, messages: list[LLMMessage]) -> AsyncIterator[str]:
        url = (
            f"{self._base()}/models/{self.model_name}:streamGenerateContent"
            f"?alt=sse&key={self.api_key}"
        )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, json=self._payload(messages)) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for cand in chunk.get("candidates", []):
                        for part in cand.get("content", {}).get("parts", []):
                            text = part.get("text")
                            if text:
                                yield text
