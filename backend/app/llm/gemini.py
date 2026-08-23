"""Google Gemini 适配器。

Gemini 的 generateContent 接口与 OpenAI 协议不同:
- 认证走 URL 上的 ?key=,不用 Bearer
- 消息结构是 contents/parts,role 用 user/model(没有 assistant)
- system 指令走单独的 system_instruction 字段
- 2.5 系是思考模型:思考也吃 maxOutputTokens,吃满时 candidate 连 parts
  都不给,只留 finishReason=MAX_TOKENS(归一到 finish_reason 供基类归因)
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

_HINT = "Gemini 卡仅支持 Google 原生协议;中转站(含卖 Gemini 模型的)请改用 OpenAI 卡"


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

    # ---- 响应解析 ----
    def _parse_candidates(
        self, data: dict, *, status: int | None = None
    ) -> LLMResponse:
        """candidates → LLMResponse,并做空正文归因。

        Gemini 不回传思考文本(只给 thoughtsTokenCount),所以思考吃满预算的
        情形靠 finishReason=MAX_TOKENS 判定;思考 token 计入 completion 以免
        用量记账少算(Google 按输出计费)。
        """
        # 空 candidates:Gemini 触发内容安全过滤(promptFeedback.blockReason)或
        # 渠道异常时会返回 200 + 空 candidates。裸 data["candidates"][0] 会抛
        # IndexError「list index out of range」,这里转成可读的上游错误。
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            reason = feedback.get("blockReason") or "无输出"
            raise UpstreamError(
                f"Gemini 未返回内容(原因: {reason}),可能触发内容安全过滤或渠道异常,"
                "请重试或到「设置」更换模型",
                status=status,
                retryable=True,
            )
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        # thought=True 的 part 是思考摘要(开了 includeThoughts 才有),不进正文
        text = "".join(as_text(p.get("text")) for p in parts if not p.get("thought"))
        thoughts = "".join(as_text(p.get("text")) for p in parts if p.get("thought"))
        usage = data.get("usageMetadata") or {}
        resp = LLMResponse(
            content=strip_think(text),
            model=data.get("modelVersion") or self.model_name,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=(
                usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
            ),
            finish_reason=candidate.get("finishReason") or "",
            reasoning=thoughts,
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
        url = f"{self._base()}/models/{self.model_name}:generateContent?key={self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=self._payload(messages))
            data = check_upstream(resp, hint=_HINT)
        return self._parse_candidates(data, status=resp.status_code)

    async def _iter_stream(
        self, messages: list[LLMMessage], sink: dict
    ) -> AsyncIterator[str]:
        url = (
            f"{self._base()}/models/{self.model_name}:streamGenerateContent"
            f"?alt=sse&key={self.api_key}"
        )
        reasoning: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=self._payload(messages)) as resp:
                    if resp.status_code >= 400:
                        # 错误体读出来给可读文案,而不是 raise_for_status 的裸状态码
                        await resp.aread()
                        check_upstream(resp, hint=_HINT)
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        usage = chunk.get("usageMetadata") or {}
                        if usage:
                            sink["prompt_tokens"] = usage.get("promptTokenCount", 0)
                            sink["completion_tokens"] = usage.get(
                                "candidatesTokenCount", 0
                            ) + usage.get("thoughtsTokenCount", 0)
                        for cand in chunk.get("candidates", []):
                            if cand.get("finishReason"):
                                sink["finish_reason"] = cand["finishReason"]
                            for part in (cand.get("content") or {}).get("parts", []):
                                text = as_text(part.get("text"))
                                if not text:
                                    continue
                                if part.get("thought"):
                                    reasoning.append(text)
                                else:
                                    yield text
        finally:
            sink["reasoning"] = "".join(reasoning)
