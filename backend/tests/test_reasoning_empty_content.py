# backend/tests/test_reasoning_empty_content.py
# -*- coding: utf-8 -*-
"""推理模型的空正文:归因、兜底、放大预算重试。

真实症状:cc-switch 里同一个模型好用,接到这里却频繁"模型连续 3 次返回空正文"。
根因是思考内容(reasoning_content / thinking / `<think>` 标签)此前被整段丢掉,
既拿不回正文,也说不出为什么空。本文件把这几条路都钉住。
"""
import asyncio

import httpx
import pytest

from app.llm.anthropic import AnthropicAdapter
from app.llm.base import EmptyContentError, LLMResponse, UpstreamError, strip_think
from app.llm.gemini import GeminiAdapter
from app.llm.openai_compatible import OpenAICompatibleAdapter


def _patch_post(monkeypatch, response: httpx.Response) -> None:
    async def fake_post(self, *args, **kwargs):
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


class _FakeStreamCM:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aenter__(self) -> httpx.Response:
        return self._response

    async def __aexit__(self, *_exc) -> bool:
        return False


def _patch_stream(monkeypatch, response: httpx.Response) -> None:
    def fake_stream(self, *args, **kwargs):
        return _FakeStreamCM(response)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)


def _sse(*lines: str) -> httpx.Response:
    body = "".join(f"{line}\n\n" for line in lines)
    return httpx.Response(
        200, text=body, headers={"content-type": "text/event-stream"}
    )


def _openai(**kw) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(api_key="sk-x", model_name="reasoner", **kw)


# ---------- 非流式解析:思考不当正文,但要能归因 ----------

def test_thinking_eats_budget_gives_actionable_error(monkeypatch):
    """思考吃满 max_tokens(finish_reason=length,content 空)→ 说清原因,
    并标记为"放大预算有救"(budget_bound),而不是一句无从下手的"空正文"。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "choices": [{
            "message": {"content": "", "reasoning_content": "先想想主角动机" * 20},
            "finish_reason": "length",
        }],
        "usage": {"completion_tokens": 4096},
    }))
    adapter = _openai()
    with pytest.raises(EmptyContentError) as exc:
        asyncio.run(adapter._complete_once(adapter.to_messages("写第一章")))
    assert exc.value.budget_bound is True
    assert exc.value.retryable is False  # 同参数重试只是白等一次长生成
    assert "思考" in str(exc.value)
    assert "finish_reason=length" in exc.value.diagnosis
    assert "4096" in exc.value.diagnosis


def test_reasoning_field_holding_the_answer_is_salvaged(monkeypatch):
    """渠道把答案整段塞进 reasoning_content、content 留空(正常收尾)→
    取思考内容兜底,免得一整章白跑。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "choices": [{
            "message": {"content": "", "reasoning": "他推开门,风雪扑面。"},
            "finish_reason": "stop",
        }],
        "usage": {"completion_tokens": 12},
    }))
    adapter = _openai()
    resp = asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert resp.content == "他推开门,风雪扑面。"
    assert resp.finish_reason == "stop"


def test_truncated_thinking_is_never_salvaged_as_text(monkeypatch):
    """思考被截断时绝不能拿半截思考当正文写进书里。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "choices": [{
            "message": {"content": "", "reasoning_content": "我应该先写环境,然后"},
            "finish_reason": "length",
        }],
    }))
    adapter = _openai()
    with pytest.raises(EmptyContentError):
        asyncio.run(adapter._complete_once(adapter.to_messages("hi")))


def test_content_filter_does_not_waste_retries(monkeypatch):
    """被安全策略拦下 → budget_bound=False,放大预算没用,别浪费三轮长生成。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}],
    }))
    adapter = _openai()
    with pytest.raises(EmptyContentError) as exc:
        asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert exc.value.budget_bound is False
    assert "安全策略" in str(exc.value)


def test_think_tags_in_content_are_stripped(monkeypatch):
    """把思考混在 content 里的模型:`<think>…</think>` 剥掉,只留正文。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "choices": [{
            "message": {"content": "<think>先铺环境</think>雪落了一夜。"},
            "finish_reason": "stop",
        }],
    }))
    adapter = _openai()
    resp = asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert resp.content == "雪落了一夜。"


def test_unclosed_think_tag_counts_as_empty(monkeypatch):
    """只吐了半截 `<think>`(思考被截断)→ 算空正文,不能污染下游解析。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "choices": [{
            "message": {"content": "<think>我应该先写环境,然后"},
            "finish_reason": "length",
        }],
    }))
    adapter = _openai()
    with pytest.raises(EmptyContentError) as exc:
        asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert exc.value.budget_bound is True


def test_content_blocks_array_is_flattened(monkeypatch):
    """少数渠道把 content 回成 blocks 数组,不能变成空正文。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "choices": [{
            "message": {"content": [{"type": "text", "text": "第一句。"}]},
            "finish_reason": "stop",
        }],
    }))
    adapter = _openai()
    resp = asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert resp.content == "第一句。"


# ---------- 流式路径:同样要收思考与收尾原因 ----------

def test_stream_reports_thinking_only_response(monkeypatch):
    """流式里只来了 reasoning_content 增量 → 归因到"思考吃满预算"。"""
    _patch_stream(monkeypatch, _sse(
        'data: {"choices":[{"delta":{"reasoning_content":"先想想"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"再想想"},'
        '"finish_reason":"length"}]}',
        "data: [DONE]",
    ))
    adapter = _openai()
    with pytest.raises(EmptyContentError) as exc:
        asyncio.run(adapter._complete_via_stream(adapter.to_messages("hi")))
    assert exc.value.budget_bound is True
    assert "finish_reason=length" in exc.value.diagnosis
    assert "思考 6 字" in exc.value.diagnosis


def test_stream_with_zero_bytes_says_so(monkeypatch):
    """流连上了但一个字节都没吐(中转渠道空转)→ 报出这个特征,别让人去查 key。"""
    _patch_stream(monkeypatch, _sse("data: [DONE]"))
    adapter = _openai()
    with pytest.raises(EmptyContentError) as exc:
        asyncio.run(adapter._complete_via_stream(adapter.to_messages("hi")))
    assert "一个字节都没吐" in str(exc.value)


def test_stream_keeps_usage_for_accounting(monkeypatch):
    """流式尾包带 usage 时要记下来,token 记账不因走流式而丢。"""
    _patch_stream(monkeypatch, _sse(
        'data: {"choices":[{"delta":{"content":"雪落了。"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":100,"completion_tokens":8}}',
        "data: [DONE]",
    ))
    adapter = _openai()
    resp = asyncio.run(adapter._complete_via_stream(adapter.to_messages("hi")))
    assert resp.content == "雪落了。"
    assert (resp.prompt_tokens, resp.completion_tokens) == (100, 8)
    assert resp.finish_reason == "stop"


def test_relay_ignoring_stream_flag_still_works(monkeypatch):
    """中转站无视 stream:true 回整包 JSON → 按非流式解析,而不是判成空正文。"""
    _patch_stream(monkeypatch, httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": "整包回来的正文"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5},
        },
        headers={"content-type": "application/json"},
    ))
    adapter = _openai()
    resp = asyncio.run(adapter._complete_via_stream(adapter.to_messages("hi")))
    assert resp.content == "整包回来的正文"
    assert resp.completion_tokens == 5


def test_stream_soft_error_is_retryable(monkeypatch):
    """流中夹的 error 事件("无可用渠道"等)要抛成可重试的上游错误。"""
    _patch_stream(monkeypatch, _sse(
        'data: {"error":{"message":"当前分组下无可用渠道"}}',
    ))
    adapter = _openai()
    with pytest.raises(UpstreamError) as exc:
        asyncio.run(adapter._complete_via_stream(adapter.to_messages("hi")))
    assert "无可用渠道" in str(exc.value)
    assert exc.value.retryable is True


# ---------- ask():放大预算重试与最终报错 ----------

class _AlwaysEmpty(OpenAICompatibleAdapter):
    """每次都空正文,记录每轮用的 max_tokens。"""

    def __init__(self, budget_bound: bool = True):
        super().__init__(api_key="sk-x", model_name="reasoner", max_tokens=4096)
        self.retry_base_delay = 0
        self._budget_bound = budget_bound
        self.budgets: list[int] = []

    async def _complete_via_stream(self, messages):
        self.budgets.append(self.max_tokens)
        raise EmptyContentError(
            "空正文",
            budget_bound=self._budget_bound,
            diagnosis="finish_reason=length, 思考 999 字",
        )


def test_ask_doubles_budget_then_reports_diagnosis():
    """三轮逐次翻倍输出预算;仍空则报错带上诊断与可操作建议,事后还原预算。"""
    a = _AlwaysEmpty()
    with pytest.raises(UpstreamError) as exc:
        asyncio.run(a.ask("写第一章"))
    assert a.budgets == [4096, 8192, 16384]
    msg = str(exc.value)
    assert "连续 3 次返回空正文" in msg
    assert "finish_reason=length" in msg
    assert "max_tokens" in msg
    assert a.max_tokens == 4096  # 放大只在本次调用内生效


def test_ask_fails_fast_when_budget_cannot_help():
    """内容被过滤这类空正文放大预算无用 → 一次就抛,不拖三轮。"""
    a = _AlwaysEmpty(budget_bound=False)
    with pytest.raises(EmptyContentError):
        asyncio.run(a.ask("写第一章"))
    assert a.budgets == [4096]


def test_ask_returns_salvaged_reasoning():
    """兜底拿到的正文照常返回给上层。"""

    class _Salvaged(OpenAICompatibleAdapter):
        async def _complete_via_stream(self, messages):
            return LLMResponse(content="兜底正文", model="m", finish_reason="stop")

    a = _Salvaged(api_key="sk-x", model_name="m")
    assert asyncio.run(a.ask("hi")) == "兜底正文"


# ---------- 截断:正文不空但被 max_tokens 砍断,也要放大预算重试 ----------

def test_ask_retries_when_content_is_truncated():
    """半截正文不能当成功:放大预算重来,拿到完整的那次才返回。

    只治"空正文"治不到这种——推理模型常常是吐了个开头就被砍断,半截 JSON
    一路流到下游解析失败,还会被错怪成"模型返回的角色名对不上"。
    """
    budgets: list[int] = []

    class _TruncThenOk(OpenAICompatibleAdapter):
        async def _complete_via_stream(self, messages):
            budgets.append(self.max_tokens)
            if len(budgets) == 1:
                return LLMResponse(
                    content='{"sheets": [{"name": "沈砚", "ref_prompt_cn": "单人正',
                    model="m", finish_reason="length", reasoning="想了很久",
                )
            return LLMResponse(content='{"sheets": []}', model="m", finish_reason="stop")

    a = _TruncThenOk(api_key="sk-x", model_name="reasoner", max_tokens=4096)
    a.retry_base_delay = 0
    assert asyncio.run(a.ask("出定妆照")) == '{"sheets": []}'
    assert budgets == [4096, 8192]
    assert a.max_tokens == 4096  # 放大只在本次调用内生效


def test_ask_returns_longest_truncated_as_last_resort():
    """三轮全被截断:返回最长的那一次,由调用方去抢救,而不是整次调用白跑。"""
    budgets: list[int] = []

    class _AlwaysTrunc(OpenAICompatibleAdapter):
        async def _complete_via_stream(self, messages):
            budgets.append(self.max_tokens)
            return LLMResponse(
                content="半截" * len(budgets), model="m", finish_reason="max_tokens"
            )

    a = _AlwaysTrunc(api_key="sk-x", model_name="reasoner", max_tokens=4096)
    a.retry_base_delay = 0
    assert asyncio.run(a.ask("出定妆照")) == "半截半截半截"
    assert budgets == [4096, 8192, 16384]


# ---------- 讨论类入口(自己拼 messages)也必须吃到放大预算 ----------

def test_ask_messages_escalates_budget_for_multi_turn_callers():
    """改稿/架构/润色讨论与灵感对话走的是 ask_messages,不能在第一次空正文就摔。

    这条是回归护栏:complete() 改成"遇空正文抛 EmptyContentError"之后,那四处
    薄封装里"等空串再翻倍"的老循环就永远等不到了——一次空正文直接摔给用户。
    """
    calls: list[int] = []

    class _EmptyThenOk(OpenAICompatibleAdapter):
        async def _complete_via_stream(self, messages):
            calls.append(self.max_tokens)
            if len(calls) == 1:
                raise EmptyContentError(
                    "思考吃满预算", budget_bound=True, diagnosis="finish_reason=length"
                )
            return LLMResponse(content="第二次给了正文", model="m", finish_reason="stop")

    a = _EmptyThenOk(api_key="sk-x", model_name="reasoner", max_tokens=4096)
    a.retry_base_delay = 0
    assert asyncio.run(a.ask_messages(a.to_messages("接着上一轮聊"))) == "第二次给了正文"
    assert calls == [4096, 8192]        # 第二次是放大后的预算
    assert a.max_tokens == 4096         # 放大只在本次调用内生效


def test_discussion_wrappers_delegate_to_shared_budget_helper():
    """四处讨论入口都必须走共享的放大预算逻辑,而不是各写一份会失效的循环。

    用鸭子类型假适配器(只有 complete/max_tokens)调用它们——线上那些入口也常被
    这样替身测试,共享逻辑必须只依赖 complete()。
    """
    from app.api.inspire import _complete_text
    from app.engines.pipeline.architecture import _arch_complete
    from app.engines.pipeline.chapter import _revise_complete
    from app.engines.polish.polisher import _discuss_complete

    class _EmptyThenOk:
        """第一次空正文(抛),第二次给正文——只有走共享逻辑才能拿到第二次。"""

        def __init__(self):
            self.max_tokens = 4096
            self.budgets: list[int] = []

        async def complete(self, messages):
            self.budgets.append(self.max_tokens)
            if len(self.budgets) == 1:
                raise EmptyContentError("思考吃满预算", budget_bound=True)
            return LLMResponse(content="第二次给了正文", model="m", finish_reason="stop")

    for fn in (_complete_text, _arch_complete, _revise_complete, _discuss_complete):
        spy = _EmptyThenOk()
        assert asyncio.run(fn(spy, [])) == "第二次给了正文", fn.__name__
        assert spy.budgets == [4096, 8192], fn.__name__   # 放大后重试
        assert spy.max_tokens == 4096, fn.__name__        # 事后还原


# ---------- 另两个协议:同一类坑 ----------

def test_anthropic_thinking_only_response(monkeypatch):
    """Claude 开思考但 max_tokens 不够:只回 thinking 块 + stop_reason=max_tokens。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "content": [{"type": "thinking", "thinking": "让我先梳理时间线"}],
        "stop_reason": "max_tokens",
        "usage": {"input_tokens": 10, "output_tokens": 2048},
    }))
    adapter = AnthropicAdapter(api_key="sk-ant", model_name="claude-x")
    with pytest.raises(EmptyContentError) as exc:
        asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert exc.value.budget_bound is True
    assert "finish_reason=max_tokens" in exc.value.diagnosis


def test_anthropic_text_blocks_still_parsed(monkeypatch):
    """回归护栏:thinking + text 混排时只取 text 当正文。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "content": [
            {"type": "thinking", "thinking": "梳理一下"},
            {"type": "text", "text": "他推开门。"},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }))
    adapter = AnthropicAdapter(api_key="sk-ant", model_name="claude-x")
    resp = asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert resp.content == "他推开门。"
    assert resp.reasoning == "梳理一下"
    assert resp.completion_tokens == 20


def test_gemini_thinking_exhausts_output_budget(monkeypatch):
    """Gemini 2.5 思考吃满 maxOutputTokens:candidate 连 parts 都不给。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "candidates": [{"content": {"role": "model"}, "finishReason": "MAX_TOKENS"}],
        "usageMetadata": {"promptTokenCount": 20, "thoughtsTokenCount": 4000},
    }))
    adapter = GeminiAdapter(api_key="k", model_name="gemini-2.5-pro")
    with pytest.raises(EmptyContentError) as exc:
        asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert exc.value.budget_bound is True
    # 思考 token 计入输出用量,记账不少算
    assert "4000 tokens" in exc.value.diagnosis


def test_strip_think_leaves_normal_text_untouched():
    assert strip_think("正常正文,不含标签") == "正常正文,不含标签"
    assert strip_think("") == ""


def test_stream_payload_asks_for_usage():
    """走流式也要能记 token 账:流式请求带 stream_options.include_usage,
    非流式请求不带(那条路的 usage 在响应体里)。"""
    adapter = _openai()
    msgs = adapter.to_messages("hi")
    assert adapter._payload(msgs, stream=True)["stream_options"] == {
        "include_usage": True
    }
    assert "stream_options" not in adapter._payload(msgs, stream=False)
