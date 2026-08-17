"""Anthropic (Claude) 适配器。

Claude 的 Messages API 与 OpenAI /chat/completions 不同:
- 端点 /v1/messages,认证走 x-api-key 头 + anthropic-version 头(不是 Bearer);
- system 提示走顶层 `system` 字段,不放进 messages;
- messages 只含 user/assistant,max_tokens 必填;
- 响应是 content blocks 数组;流式是 SSE 事件(content_block_delta.delta.text)。

用 httpx 直连,不引厂商 SDK,保持与其它适配器一致的轻量风格。
complete 的重试 + CDN 掐断改流式兜底逻辑与 OpenAICompatibleAdapter 对齐。
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.llm.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    check_upstream,
    with_retries,
)

# Messages API 版本头。锚定稳定版,升级时集中改这里。
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicAdapter(LLMAdapter):
    """走 Anthropic /v1/messages 协议的适配器(Claude 原生)。"""

    interface_format = "anthropic"
    default_base_url = "https://api.anthropic.com"

    def _endpoint(self) -> str:
        base = (self.base_url or self.default_base_url).rstrip("/")
        return f"{base}/v1/messages"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _split_messages(
        self, messages: list[LLMMessage]
    ) -> tuple[str | None, list[dict]]:
        """拆成 (system, turns)。system 走顶层字段;turns 只留 user/assistant。

        多条 system 合并成一段。本项目只产出 system/user/assistant,且恒以
        user 开头(见 to_messages / ask),满足 Anthropic 对交替与首条的要求。
        """
        system_text: str | None = None
        turns: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_text = (
                    m.content if system_text is None else f"{system_text}\n{m.content}"
                )
                continue
            turns.append({"role": m.role, "content": m.content})
        return system_text, turns

    def _payload(self, messages: list[LLMMessage], stream: bool) -> dict:
        system_text, turns = self._split_messages(messages)
        payload: dict = {
            "model": self.model_name,
            "max_tokens": self.max_tokens,  # Anthropic 必填
            "messages": turns,
            "temperature": min(self.temperature, 1.0),  # Anthropic 上限 1.0
            "stream": stream,
        }
        if system_text:
            payload["system"] = system_text
        return payload

    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        """一次性返回完整回复,带瞬时错误重试与 CDN 掐断兜底。

        与 OpenAI 兼容卡同策略:首次走非流式,遭遇 52x/超时等瞬时错误后
        改走流式聚合(流式一出响应头就持续吐 chunk,CDN 不会再掐)。
        """
        use_stream = False

        async def call(_attempt: int) -> LLMResponse:
            if use_stream:
                return await self._complete_via_stream(messages)
            return await self._complete_once(messages)

        def on_retry(_exc: Exception) -> None:
            nonlocal use_stream
            use_stream = True

        return await with_retries(
            call,
            attempts=self.retry_attempts,
            base_delay=self.retry_base_delay,
            on_retry=on_retry,
        )

    async def _complete_once(self, messages: list[LLMMessage]) -> LLMResponse:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self._endpoint(),
                headers=self._headers(),
                json=self._payload(messages, stream=False),
            )
            data = check_upstream(
                resp,
                hint="确认 Base URL 正确(默认 https://api.anthropic.com)且渠道支持 Anthropic 协议",
            )

        # content 是 block 数组,拼接所有 text 块(忽略 thinking/tool_use 等)
        blocks = data.get("content") or []
        text = "".join(
            b.get("text", "") for b in blocks if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        return LLMResponse(
            content=text,
            model=data.get("model", self.model_name),
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            raw=data,
        )

    async def _complete_via_stream(self, messages: list[LLMMessage]) -> LLMResponse:
        """流式聚合:规避 CDN 的长请求掐断(token 用量可能拿不到,记账允许为 0)。"""
        chunks: list[str] = []
        async for delta in self.stream(messages):
            chunks.append(delta)
        return LLMResponse(content="".join(chunks), model=self.model_name)

    async def stream(self, messages: list[LLMMessage]) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                self._endpoint(),
                headers=self._headers(),
                json=self._payload(messages, stream=True),
            ) as resp:
                if resp.status_code >= 400:
                    # 流式错误体也读出来,给用户可读文案(而非裸状态码)
                    await resp.aread()
                    check_upstream(
                        resp,
                        hint="确认 Base URL 正确且渠道支持 Anthropic 协议",
                    )
                async for line in resp.aiter_lines():
                    # Anthropic SSE 交替 event:/data: 行,只关心 data: 行的增量事件
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    # 文本增量事件:content_block_delta.delta.text(text_delta)
                    if chunk.get("type") == "content_block_delta":
                        text = chunk.get("delta", {}).get("text")
                        if text:
                            yield text
