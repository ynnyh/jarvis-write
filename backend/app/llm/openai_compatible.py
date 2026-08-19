"""OpenAI 兼容适配器基类。

DeepSeek、OpenAI、以及任何 OpenAI-compatible 接口(含本地 Ollama)
都走 `/chat/completions`,请求/返回格式一致,故抽出公共实现。
用 httpx 直连,不引厂商 SDK,保持轻量可控。
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
    check_upstream,
    with_retries,
)


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
        """一次性返回完整回复,带瞬时错误重试与 CDN 掐断兜底。

        长生成(章节草稿/定稿)的非流式请求可能几分钟不出一个字节,
        套了 Cloudflare 的中转站会在 ~100 秒处掐断(HTTP 524)。因此:
        - 首次走非流式;一旦遭遇 52x/超时等瞬时错误,后续重试改走流式聚合
          (流式一出响应头就持续吐 chunk,CDN 不会再掐);
        - 429/5xx/网络超时按指数退避重试。
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
        """经典非流式调用:POST 后等完整响应体。"""
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

        # 200 + 合法 JSON 但 choices 为空:中转站渠道抽风/额度耗尽/被内容过滤时高发
        # (无 error 字段,check_upstream 会放行)。裸 data["choices"][0] 会抛
        # IndexError「list index out of range」污染上层,这里转成可读、可重试的上游错误。
        choices = data.get("choices") or []
        if not choices:
            raise UpstreamError(
                "上游返回了空的 choices(渠道无输出,可能是该中转渠道抽风、额度耗尽"
                "或被内容过滤)。系统会自动重试,多次失败请到「设置」更换渠道",
                status=resp.status_code,
                retryable=True,
            )
        message = choices[0].get("message") or {}
        # 兼容极少数只回 text(legacy completion 形态)的渠道;content 为空交给 ask() 重试
        choice = message.get("content") or choices[0].get("text") or ""
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice,
            model=data.get("model", self.model_name),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            raw=data,
        )

    async def _complete_via_stream(self, messages: list[LLMMessage]) -> LLMResponse:
        """流式聚合:边收边拼,规避 CDN 的长请求掐断(524)。

        代价是拿不到准确 token 用量(多数渠道只在流尾给 usage 且常被省略),
        兜底路径可接受——用量记账允许为 0。
        """
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
                    # 流式响应的错误体也要读出来,给用户可读文案(而非裸状态码)
                    await resp.aread()
                    check_upstream(
                        resp, hint="确认 Base URL 含 /v1 且渠道支持 OpenAI 协议"
                    )
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
                    # 尾包/心跳包可能带空 choices(仅 usage);裸 [0] 会抛 IndexError,跳过即可
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
