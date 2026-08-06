"""LLM 适配器抽象基类。

所有厂商适配器实现同一套接口:
- `complete()`      一次性返回完整回复
- `stream()`        异步逐块产出(供 SSE 流式生成用)

上层引擎只依赖本抽象,不关心底层是 DeepSeek 还是 OpenAI。
"""
from __future__ import annotations

import abc
import asyncio
import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

import httpx

# 瞬时错误状态码:限流/服务端抖动/网关与 CDN 异常,值得退避重试。
# 520-529 是 Cloudflare 系错误(524=CDN 等源站超时掐断,中转站高发)。
RETRYABLE_STATUSES = frozenset(
    {408, 409, 425, 429, 500, 502, 503, 504, 529} | set(range(520, 530))
)

# 网络层瞬时异常:超时/连接失败,同样值得重试
TRANSIENT_NET_ERRORS = (httpx.TimeoutException, httpx.ConnectError)


class UpstreamError(RuntimeError):
    """上游调用失败(继承 RuntimeError,老代码的 except 不受影响)。

    附带 status / retryable,供重试层判断要不要再来一次;
    retryable=False 的错(401/403/404/参数错误)重试无意义,直接抛给用户。
    """

    def __init__(
        self, message: str, *, status: int | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def check_upstream(resp: httpx.Response, *, hint: str = "") -> dict:
    """校验上游响应并返回 JSON;异常时抛出用户可读的错误。

    - HTTP 错误(>=400):带上状态码和上游错误消息(若有);
    - 52x(Cloudflare 网关/CDN 错误):单独说明"生成耗时过长被掐断/渠道拥挤",
      不误导用户去查 Base URL;
    - HTTP 200 但 body 夹 error 字段(中转站软错误,如"无可用渠道"):也抛出,
      不再被当成正常响应放行、退化成下游"空回复";
    - 非 JSON(Base URL 填错/协议不匹配,上游回了 HTML 错误页):说明原因并附 hint。
    """
    if resp.status_code >= 400:
        msg = f"上游返回 HTTP {resp.status_code}"
        try:
            err = resp.json()
            detail = (err.get("error") or {}).get("message") or err.get("message")
            if detail:
                msg += f": {detail}"
        except Exception:  # noqa: BLE001
            snippet = resp.text[:80].strip()
            if snippet:
                msg += f"(响应非 JSON,开头: {snippet})"
        if 520 <= resp.status_code <= 529:
            msg += (
                "。这是中转站网关/CDN 错误,通常是生成耗时过长被 CDN 掐断"
                "或渠道拥挤,稍后重试一般可恢复(系统会自动重试并改走流式)"
            )
        elif hint:
            msg += f"。{hint}"
        raise UpstreamError(
            msg,
            status=resp.status_code,
            retryable=resp.status_code in RETRYABLE_STATUSES,
        )
    try:
        data = resp.json()
    except json.JSONDecodeError:
        snippet = resp.text[:80].strip()
        msg = (
            f"上游返回了非 JSON 内容(HTTP {resp.status_code}),通常是 Base URL 填错"
            f"或该渠道不支持此协议(响应开头: {snippet})"
        )
        if hint:
            msg += f"。{hint}"
        raise UpstreamError(msg, status=resp.status_code) from None
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        detail = err.get("message") if isinstance(err, dict) else str(err)
        raise UpstreamError(
            f"上游返回错误(HTTP {resp.status_code}): {detail or err}",
            status=resp.status_code,
        )
    return data


async def with_retries(call, *, attempts: int = 3, base_delay: float = 2.0, on_retry=None):
    """瞬时错误退避重试:retryable 的 UpstreamError / 网络超时连接错误。

    - call(attempt): 第几次尝试(0 起),返回 awaitable;
    - on_retry(exc): 每次重试前回调,调用方可借此调整下一次尝试的方式
      (如被 CDN 掐断后改走流式聚合);
    - 非 retryable 的错误(鉴权/参数/Base URL 错)立即抛出,不浪费重试。
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await call(attempt)
        except UpstreamError as exc:
            if not exc.retryable:
                raise
            last = exc
        except TRANSIENT_NET_ERRORS as exc:
            last = exc
        if attempt < attempts - 1:
            if on_retry is not None:
                on_retry(last)
            await asyncio.sleep(base_delay * (2**attempt))
    raise UpstreamError(
        f"上游连续 {attempts} 次调用失败,最后错误: {last}", retryable=True
    ) from last
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
        # 瞬时错误重试:次数与退避基数(秒)。置 1 即关闭重试。
        self.retry_attempts = 3
        self.retry_base_delay = 2.0

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
