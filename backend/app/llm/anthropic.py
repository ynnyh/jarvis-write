"""Anthropic (Claude) 适配器。

Claude 的 Messages API 与 OpenAI /chat/completions 不同:
- 端点 /v1/messages,认证走 x-api-key 头 + anthropic-version 头(不是 Bearer);
- system 提示走顶层 `system` 字段,不放进 messages;
- messages 只含 user/assistant,max_tokens 必填;
- 响应是 content blocks 数组(text / thinking / tool_use);流式是 SSE 事件。

用 httpx 直连,不引厂商 SDK,保持与其它适配器一致的轻量风格。
流式优先 / 重试 / 空正文归因由 LLMAdapter 统一实现。
"""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.llm.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    UpstreamError,
    as_text,
    check_upstream,
    strip_think,
)

# Messages API 版本头。锚定稳定版,升级时集中改这里。
ANTHROPIC_VERSION = "2023-06-01"

_HINT = "确认 Base URL 正确(默认 https://api.anthropic.com)且渠道支持 Anthropic 协议"


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

    # ---- 响应解析 ----
    def _parse_message(self, data: dict, *, status: int | None = None) -> LLMResponse:
        """content blocks → LLMResponse,并做空正文归因。

        text 块是正文;thinking 块是思考(不进正文,但空正文时是归因依据——
        开了 extended thinking 又给的 max_tokens 不够时,整个响应可能只有
        thinking 块,stop_reason=max_tokens)。
        """
        blocks = data.get("content") or []
        text = "".join(
            as_text(b.get("text")) for b in blocks if b.get("type") == "text"
        )
        thinking = "".join(
            as_text(b.get("thinking") or b.get("text"))
            for b in blocks
            if b.get("type") in ("thinking", "redacted_thinking")
        )
        usage = data.get("usage") or {}
        resp = LLMResponse(
            content=strip_think(text),
            model=data.get("model") or self.model_name,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            # stop_reason 归一到 finish_reason:max_tokens 即被截断
            finish_reason=data.get("stop_reason") or "",
            reasoning=thinking,
            raw=data,
        )
        if resp.content.strip():
            return resp
        salvaged = self._salvage_reasoning(resp)
        if salvaged:
            resp.content = salvaged
            return resp
        raise self._empty_content_error(resp, status=status)

    async def _complete_once(self, messages: list[LLMMessage]) -> LLMResponse:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self._endpoint(),
                headers=self._headers(),
                json=self._payload(messages, stream=False),
            )
            data = check_upstream(resp, hint=_HINT)
        return self._parse_message(data, status=resp.status_code)

    async def _iter_stream(
        self, messages: list[LLMMessage], sink: dict
    ) -> AsyncIterator[str]:
        """SSE 流式:产出 text_delta,把 thinking/stop_reason/用量塞进 sink。"""
        reasoning: list[str] = []
        try:
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
                        check_upstream(resp, hint=_HINT)
                    # 渠道无视 stream:true 直接回整包 JSON(中转站常见)→ 按非流式解析
                    if "event-stream" not in resp.headers.get("content-type", ""):
                        await resp.aread()
                        parsed = self._parse_message(
                            check_upstream(resp, hint=_HINT), status=resp.status_code
                        )
                        sink.update(
                            finish_reason=parsed.finish_reason,
                            prompt_tokens=parsed.prompt_tokens,
                            completion_tokens=parsed.completion_tokens,
                        )
                        reasoning.append(parsed.reasoning)
                        if parsed.content:
                            yield parsed.content
                        return
                    async for line in resp.aiter_lines():
                        # Anthropic SSE 交替 event:/data: 行,只关心 data: 行
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        kind = chunk.get("type")
                        if kind == "error":
                            err = chunk.get("error") or {}
                            raise UpstreamError(
                                f"上游在流式响应中报错: {err.get('message') or err}",
                                status=resp.status_code,
                                retryable=True,
                            )
                        if kind == "message_start":
                            usage = (chunk.get("message") or {}).get("usage") or {}
                            sink["prompt_tokens"] = usage.get("input_tokens", 0)
                            continue
                        if kind == "message_delta":
                            stop = (chunk.get("delta") or {}).get("stop_reason")
                            if stop:
                                sink["finish_reason"] = stop
                            usage = chunk.get("usage") or {}
                            if usage.get("output_tokens"):
                                sink["completion_tokens"] = usage["output_tokens"]
                            continue
                        if kind == "content_block_delta":
                            delta = chunk.get("delta") or {}
                            think = delta.get("thinking")
                            if think:
                                reasoning.append(as_text(think))
                                continue
                            text = delta.get("text")
                            if text:
                                yield text
        finally:
            sink["reasoning"] = "".join(reasoning)
