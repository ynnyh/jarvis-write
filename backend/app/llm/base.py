"""LLM 适配器抽象基类。

所有厂商适配器实现同一套接口:
- `complete()`      一次性返回完整回复
- `stream()`        异步逐块产出(供 SSE 流式生成用)

上层引擎只依赖本抽象,不关心底层是 DeepSeek 还是 OpenAI。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

Role = Literal["system", "user", "assistant"]


@dataclass
class LLMMessage:
    """一条对话消息。"""

    role: Role
    content: str


@dataclass
class LLMResponse:
    """一次完整调用的返回。"""

    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # 原始返回,调试用;不参与业务逻辑
    raw: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMAdapter(abc.ABC):
    """厂商适配器抽象基类。

    子类通过构造函数拿到 api_key / base_url / model_name 等,
    实现 `complete` 与 `stream` 两个方法即可接入。
    """

    interface_format: str = "base"

    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 600,
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    # ---- 便捷构造:把一个纯文本 prompt 包成 messages ----
    @staticmethod
    def to_messages(
        prompt: str, system: str | None = None
    ) -> list[LLMMessage]:
        msgs: list[LLMMessage] = []
        if system:
            msgs.append(LLMMessage(role="system", content=system))
        msgs.append(LLMMessage(role="user", content=prompt))
        return msgs

    @abc.abstractmethod
    async def complete(self, messages: list[LLMMessage]) -> LLMResponse:
        """一次性返回完整回复。"""
        raise NotImplementedError

    @abc.abstractmethod
    def stream(self, messages: list[LLMMessage]) -> AsyncIterator[str]:
        """异步逐块产出文本增量(用于 SSE)。

        注意:实现应为 async generator,调用方 `async for chunk in ...`。
        """
        raise NotImplementedError

    @staticmethod
    def _record_usage(resp: "LLMResponse") -> None:
        """用量记账(静默失败,绝不影响生成)。"""
        try:
            from app.auth import current_user_id
            from app.db.models import LlmUsage
            from app.db.session import session_scope

            with session_scope() as db:
                db.add(
                    LlmUsage(
                        user_id=current_user_id.get(),
                        model=resp.model,
                        prompt_tokens=resp.prompt_tokens,
                        completion_tokens=resp.completion_tokens,
                    )
                )
        except Exception:  # noqa: BLE001
            pass

    # ---- 便捷入口:直接传字符串 ----
    async def ask(self, prompt: str, system: str | None = None) -> str:
        """带重试的问答:空回复自动重试并放大 max_tokens。

        推理类模型(DeepSeek-R 系/中转站)思考内容可能吃掉 token 上限,
        导致正文为空——空正文绝不能当结果返回污染下游,这里兜底。
        每次调用自动记录 token 用量。
        """
        messages = self.to_messages(prompt, system)
        original_max = self.max_tokens
        try:
            for attempt in range(3):
                resp = await self.complete(messages)
                self._record_usage(resp)
                content = (resp.content or "").strip()
                if content:
                    return content
                # 空正文:翻倍 max_tokens 再试,给推理模型留足思考+输出空间
                self.max_tokens = min(self.max_tokens * 2, 32768)
            raise RuntimeError(
                f"模型连续 3 次返回空正文(model={self.model_name})。"
                "可能是推理模型思考耗尽 token,请调大 max_tokens 或更换模型。"
            )
        finally:
            self.max_tokens = original_max
