"""OpenAI 兼容适配器基类。

DeepSeek、OpenAI、以及任何 OpenAI-compatible 接口(含本地 Ollama)
都走 `/chat/completions`,请求/返回格式一致,故抽出公共实现。
用 httpx 直连,不引厂商 SDK,保持轻量可控。

流式优先 / 退避重试 / 空正文归因都在 `LLMAdapter` 里,本类只负责
「怎么发这个协议的请求」和「怎么把这个协议的响应解析成 LLMResponse」。
"""
from __future__ import annotations

import json
import logging
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
    thinking_param_rejected,
)

logger = logging.getLogger("jarvis-write.llm")

_HINT = "确认 Base URL 含 /v1 且渠道支持 OpenAI 协议"

# 模型名里出现这些片段 → 视为思考/推理系模型,思考控制参数才有下发的意义
# (对非推理模型发 thinking 只会白挨 400)。用户在配置里显式指定时不走这道
# 启发式(thinking_forced),照顾被中转站改名的模型。
_REASONING_NAME_HINTS = (
    "deepseek", "v4", "v3", "reasoner", "r1", "think", "qwq", "qwen3",
    "glm", "kimi", "grok", "gpt-5", "o1", "o3", "o4",
)


def _looks_reasoning(model_name: str) -> bool:
    name = (model_name or "").lower()
    return any(h in name for h in _REASONING_NAME_HINTS)


def _reasoning_of(message: dict) -> str:
    """取思考内容:DeepSeek 系用 reasoning_content,部分中转站用 reasoning。"""
    return as_text(message.get("reasoning_content") or message.get("reasoning") or "")


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

    def _thinking_control(self) -> dict:
        """思考控制参数(disabled → thinking.type;low/high/max → reasoning_effort)。

        为什么默认关:V4 系(deepseek-v4-flash 等)思考默认开且 effort=high,我们的
        结构化长契约会触发数万 token 思考、吃光 max_tokens → 空正文 + 翻倍重试,
        分钟级白跑(实测同一提示词:思考开 1 分钟后空正文,关闭后 6.8 秒出全文)。
        - 渠道已拒收过该参数(complete() 里的 400 自动撤销)→ 不再下发;
        - 跟随默认值时只对模型名像推理系的下发,显式指定(thinking_forced)不受限。
        """
        mode = self.thinking_mode
        if not mode:
            return {}
        base = self.base_url or getattr(self, "default_base_url", "")
        if thinking_param_rejected(base, self.model_name):
            return {}
        if not (self.thinking_forced or _looks_reasoning(self.model_name)):
            return {}
        if mode == "disabled":
            return {"thinking": {"type": "disabled"}}
        if mode in ("low", "high", "max"):
            return {"reasoning_effort": mode}
        return {}

    def _payload(self, messages: list[LLMMessage], stream: bool) -> dict:
        payload = {
            "model": self.model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        payload.update(self._thinking_control())
        if stream:
            # 要一份流尾 usage,否则走流式就没法记 token 账。渠道不认这个参数时
            # 会回 400,complete() 会自动回落非流式(那条路照样有 usage)。
            payload["stream_options"] = {"include_usage": True}
        return payload

    # ---- 响应解析 ----
    def _parse_completion(
        self, data: dict, *, status: int | None = None
    ) -> LLMResponse:
        """非流式响应 → LLMResponse,并做空正文归因。

        推理模型的思考走 reasoning_content(或混在 content 的 `<think>` 里),
        不能当正文;但正文为空时它是判断"思考吃满预算"还是"渠道空转"的唯一
        线索,所以一并解析出来交给基类归因。
        """
        # 200 + 合法 JSON 但 choices 为空:中转站渠道抽风/额度耗尽/被内容过滤时高发
        # (无 error 字段,check_upstream 会放行)。裸 data["choices"][0] 会抛
        # IndexError「list index out of range」污染上层,这里转成可读、可重试的上游错误。
        choices = data.get("choices") or []
        if not choices:
            raise UpstreamError(
                "上游返回了空的 choices(渠道无输出,可能是该中转渠道抽风、额度耗尽"
                "或被内容过滤)。系统会自动重试,多次失败请到「设置」更换渠道",
                status=status,
                retryable=True,
            )
        choice = choices[0]
        message = choice.get("message") or {}
        # 兼容极少数只回 text(legacy completion 形态)的渠道
        raw = message.get("content") or choice.get("text") or ""
        usage = data.get("usage") or {}
        resp = LLMResponse(
            content=strip_think(as_text(raw)),
            model=data.get("model") or self.model_name,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason") or "",
            reasoning=_reasoning_of(message),
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
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self._endpoint(),
                headers=self._headers(),
                json=self._payload(messages, stream=False),
            )
            data = check_upstream(resp, hint=_HINT)
        return self._parse_completion(data, status=resp.status_code)

    async def _iter_stream(
        self, messages: list[LLMMessage], sink: dict
    ) -> AsyncIterator[str]:
        """SSE 流式:产出正文增量,把思考/收尾原因/用量塞进 sink。"""
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
                        # 流式响应的错误体也要读出来,给用户可读文案(而非裸状态码)
                        await resp.aread()
                        check_upstream(resp, hint=_HINT)
                    # 部分中转站无视 stream:true,直接回整包 JSON。按非流式解析,
                    # 否则下面找不到 data: 行会误判成"一个字节都没吐"。
                    if "event-stream" not in resp.headers.get("content-type", ""):
                        await resp.aread()
                        parsed = self._parse_completion(
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
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        # 流中夹的软错误(中转站"无可用渠道"常这么回)
                        if isinstance(chunk, dict) and chunk.get("error"):
                            err = chunk["error"]
                            detail = (
                                err.get("message") if isinstance(err, dict) else str(err)
                            )
                            raise UpstreamError(
                                f"上游在流式响应中报错: {detail or err}",
                                status=resp.status_code,
                                retryable=True,
                            )
                        # 尾包可能只带 usage(choices 为空);裸 [0] 会抛 IndexError
                        usage = chunk.get("usage") or {}
                        if usage:
                            sink["prompt_tokens"] = usage.get("prompt_tokens", 0)
                            sink["completion_tokens"] = usage.get("completion_tokens", 0)
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        if choices[0].get("finish_reason"):
                            sink["finish_reason"] = choices[0]["finish_reason"]
                        delta = choices[0].get("delta") or {}
                        think = _reasoning_of(delta)
                        if think:
                            reasoning.append(think)
                        text = as_text(delta.get("content"))
                        if text:
                            yield text
                    else:
                        # 没等到 [DONE] 就把 SSE 读完了:多为中转站/CDN 静默掐断,
                        # 拿到的可能是半截正文。不改判成功失败(改判会误伤那些
                        # 既不发 [DONE] 也不给 finish_reason 的渠道),但要留痕。
                        if not sink.get("finish_reason"):
                            logger.warning(
                                "流式结束但既无 [DONE] 也无 finish_reason"
                                "(model=%s),正文可能被中途掐断",
                                self.model_name,
                            )
        finally:
            # 中途异常/被调用方提前关闭也要留下已收到的思考,供空正文归因
            sink["reasoning"] = "".join(reasoning)
