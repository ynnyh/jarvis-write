"""OpenCode Go 渠道适配器(opencode.ai 的 $10/月 订阅)。

一个 Key 通吃多家开源模型,但 Go 网关按模型族分了三套协议:
- GLM / Kimi / DeepSeek / MiMo / LongCat / Hy → /chat/completions(本类默认路径);
- Grok / GPT-luna → /v1/responses(Responses 协议,命中模型族自动改道);
- Qwen / MiniMax → /v1/messages(Anthropic 协议)——不在本卡覆盖,
  前端快捷预设把用户指到 anthropic 卡(Base URL 填不带 /v1 的网关根)。

对用户呈现为一张「opencode-go」卡,填 Key 选模型即用。官方建议每会话带
x-opencode-session 稳定会话头、自报 User-Agent(措辞是建议非强制,照做):
适配器按次创建,一次适配器生命周期视作一个会话。
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

from app.llm.base import LLMMessage
from app.llm.openai_compatible import OpenAICompatibleAdapter
from app.llm.openai_responses import USER_AGENT, OpenAIResponsesAdapter

# 命中即走 Responses 报文的模型族(Go 上 grok-4.6 / gpt-5.6-luna / muse-spark-*)
_RESPONSES_FAMILY = ("grok", "gpt-", "muse")


def uses_responses_api(model_name: str) -> bool:
    """模型名是否属于 Go 网关的 Responses 协议族。"""
    name = (model_name or "").lower()
    return name.startswith(_RESPONSES_FAMILY)


class _GoResponsesAdapter(OpenAIResponsesAdapter):
    """Go 网关的 Responses 路径:在通用头之上加会话头。"""

    def __init__(self, *args, session_id: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._session_id = session_id

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self._session_id:
            headers["x-opencode-session"] = self._session_id
        return headers


class OpenCodeGoAdapter(OpenAICompatibleAdapter):
    """OpenCode Go 卡:兼容族走 /chat/completions,Responses 族自动改道。"""

    interface_format = "opencode-go"
    default_base_url = "https://opencode.ai/zen/go/v1"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # 会话头:适配器按次创建,一个实例 = 一次生成会话,实例内稳定
        self._session_id = uuid.uuid4().hex

    def _headers(self) -> dict[str, str]:
        return {
            **super()._headers(),
            "User-Agent": USER_AGENT,
            "x-opencode-session": self._session_id,
        }

    def _responses_adapter(self) -> _GoResponsesAdapter:
        """按需现造 Responses 内层适配器(随本实例的最新字段同步,如撤参后的 thinking_mode)。"""
        return _GoResponsesAdapter(
            api_key=self.api_key,
            model_name=self.model_name,
            base_url=self.base_url,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            thinking_mode=self.thinking_mode,
            thinking_forced=self.thinking_forced,
            # 并发/速率闸门在外层基类的 complete() 统一执行,内层不再传
            session_id=self._session_id,
        )

    async def _complete_once(self, messages: list[LLMMessage]) -> LLMResponse:
        if uses_responses_api(self.model_name):
            return await self._responses_adapter()._complete_once(messages)
        return await super()._complete_once(messages)

    async def _iter_stream(
        self, messages: list[LLMMessage], sink: dict
    ) -> AsyncIterator[str]:
        inner = (
            self._responses_adapter()
            if uses_responses_api(self.model_name)
            else None
        )
        if inner is None:
            async for delta in super()._iter_stream(messages, sink):
                yield delta
        else:
            async for delta in inner._iter_stream(messages, sink):
                yield delta
