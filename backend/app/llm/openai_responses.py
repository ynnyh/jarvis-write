"""OpenAI Responses 协议适配器(/v1/responses)。

OpenAI 新一代接口,也是 OpenCode Go 网关上 Grok / GPT-luna 系模型唯一提供的
协议(该网关把 GLM/Kimi/DeepSeek 等放在 /chat/completions、Qwen/MiniMax 放在
/v1/messages,见 opencode_go.py)。请求(instructions+input+max_output_tokens)、
响应(output[] 数组)与 SSE 事件(response.output_text.delta 等)都与
/chat/completions 不同构,故独立成类而不复用 OpenAICompatibleAdapter;
流式优先/退避重试/空正文归因/思考撤参记忆仍由基类承担。

注:报文按 OpenAI Responses 公开规范实现,暂无真实 Key 对过线上;
接入后若网关有偏差,以 check_upstream 的可读报错为线索修正。
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from app.llm.base import (
    _http_timeout,
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    UpstreamError,
    as_text,
    check_upstream,
    strip_think,
    thinking_param_rejected,
)

logger = logging.getLogger("jarvis-write.llm")

# Go 官方建议自报身份(非强制):"Identify itself with its own user agent,
# such as my-coding-agent/1.0, rather than a generic SDK or HTTP-library name."
USER_AGENT = "jarvis-write"

_HINT = "确认 Base URL 含 /v1 且渠道支持 OpenAI Responses 协议"


class OpenAIResponsesAdapter(LLMAdapter):
    """走 OpenAI /responses 协议的适配器。"""

    interface_format = "openai-responses"
    default_base_url = "https://api.openai.com/v1"

    def _endpoint(self) -> str:
        base = (self.base_url or self.default_base_url).rstrip("/")
        return f"{base}/responses"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _split_messages(
        self, messages: list[LLMMessage]
    ) -> tuple[str | None, list[dict]]:
        """拆成 (instructions, turns)。system 走顶层 instructions,turns 只留 user/assistant。"""
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

    def _reasoning_effort(self) -> str:
        """思考档位 → Responses 的 reasoning.effort(low/medium/high)。

        与 openai_compatible 不同:这里没有「思考关闭」的线上的值(disabled
        一律不下发参数,跟随模型默认),max 档映射到 high。参数被渠道拒收过
        就不再下发(共用基类的 (渠道,模型) 记忆)。
        """
        if thinking_param_rejected(self.base_url or "", self.model_name):
            return ""
        if self.thinking_mode in ("low", "high"):
            return self.thinking_mode
        if self.thinking_mode == "max":
            return "high"
        return ""

    def _payload(self, messages: list[LLMMessage], stream: bool) -> dict:
        system_text, turns = self._split_messages(messages)
        payload: dict = {
            "model": self.model_name,
            "input": turns,
            "max_output_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }
        if system_text:
            payload["instructions"] = system_text
        effort = self._reasoning_effort()
        if effort:
            payload["reasoning"] = {"effort": effort}
        return payload

    # ---- 响应解析 ----
    def _parse_response(self, data: dict, *, status: int | None = None) -> LLMResponse:
        """output[] 数组 → LLMResponse,并做空正文归因。

        message 项的 output_text 是正文;reasoning 项的 summary 是思考
        (不进正文,空正文时是归因依据)。status=incomplete 即被截断。
        """
        # 200 + 合法 JSON 但带 error(Responses 规范允许 failed 状态走 200):
        # check_upstream 只看 HTTP 状态码,这里补一刀
        if data.get("error"):
            err = data["error"]
            raise UpstreamError(
                f"上游返回错误: {err.get('message') if isinstance(err, dict) else err}",
                status=status,
                retryable=True,
            )
        items = data.get("output") or []
        text = "".join(
            as_text(part.get("text"))
            for item in items
            if item.get("type") == "message"
            for part in (item.get("content") or [])
            if part.get("type") == "output_text"
        )
        reasoning = "".join(
            as_text(part.get("text"))
            for item in items
            if item.get("type") == "reasoning"
            for part in (item.get("summary") or [])
            if part.get("type") == "summary_text"
        )
        usage = data.get("usage") or {}
        details = usage.get("output_tokens_details") or {}
        resp_status = data.get("status") or ""
        resp = LLMResponse(
            content=strip_think(text),
            model=data.get("model") or self.model_name,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            # incomplete 即被截断(max_output_tokens 用尽),归一到 length
            finish_reason=(
                "length" if resp_status == "incomplete"
                else ("stop" if resp_status == "completed" else resp_status)
            ),
            reasoning=reasoning,
            reasoning_tokens=details.get("reasoning_tokens", 0),
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
        """经典非流式调用:POST 后等完整响应体(流式被拒时的兜底路径)。"""
        async with httpx.AsyncClient(timeout=_http_timeout(self.timeout)) as client:
            resp = await client.post(
                self._endpoint(),
                headers=self._headers(),
                json=self._payload(messages, stream=False),
            )
            data = check_upstream(resp, hint=_HINT)
        return self._parse_response(data, status=resp.status_code)

    async def _iter_stream(
        self, messages: list[LLMMessage], sink: dict
    ) -> AsyncIterator[str]:
        """SSE 流式:产出正文增量,把思考/收尾原因/用量塞进 sink。

        Responses 的事件名都在 data 报文的 type 字段里,不必解析 event: 行。
        """
        reasoning: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=_http_timeout(self.timeout)) as client:
                async with client.stream(
                    "POST",
                    self._endpoint(),
                    headers=self._headers(),
                    json=self._payload(messages, stream=True),
                ) as resp:
                    if resp.status_code >= 400:
                        # 流式响应的错误体也要读出来,给用户可读文案(而非裸状态码)
                        await resp.aread()
                        check_upstream(resp, hint=_HINT)
                    # 渠道无视 stream:true 直接回整包 JSON → 按非流式解析
                    if "event-stream" not in resp.headers.get("content-type", ""):
                        await resp.aread()
                        parsed = self._parse_response(
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
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[len("data:"):].strip()
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:  # 流中非 JSON 片段(注释/心跳):跳过
                            continue
                        kind = chunk.get("type")
                        if kind == "response.output_text.delta":
                            text = as_text(chunk.get("delta"))
                            if text:
                                yield text
                        elif kind == "response.reasoning_summary_text.delta":
                            think = as_text(chunk.get("delta"))
                            if think:
                                reasoning.append(think)
                        elif kind in ("response.completed", "response.incomplete"):
                            done = chunk.get("response") or {}
                            usage = done.get("usage") or {}
                            sink["prompt_tokens"] = usage.get("input_tokens", 0)
                            sink["completion_tokens"] = usage.get("output_tokens", 0)
                            details = usage.get("output_tokens_details") or {}
                            if details.get("reasoning_tokens"):
                                sink["reasoning_tokens"] = details["reasoning_tokens"]
                            sink["finish_reason"] = (
                                "length" if kind == "response.incomplete" else "stop"
                            )
                        elif kind == "response.failed":
                            err = (chunk.get("response") or {}).get("error") or {}
                            raise UpstreamError(
                                "上游在流式响应中报错: "
                                f"{err.get('message') if isinstance(err, dict) else err}",
                                status=resp.status_code,
                                retryable=True,
                            )
                        elif kind == "error":
                            raise UpstreamError(
                                "上游在流式响应中报错: "
                                f"{chunk.get('message') or chunk}",
                                status=resp.status_code,
                                retryable=True,
                            )
        finally:
            # 中途异常/被调用方提前关闭也要留下已收到的思考,供空正文归因
            sink["reasoning"] = "".join(reasoning)
