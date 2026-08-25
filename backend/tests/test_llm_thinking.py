# backend/tests/test_llm_thinking.py
# -*- coding: utf-8 -*-
"""思考模式控制:payload 注入(含模型名启发式与渠道拒收缓存)与 400 自动撤销重发。

背景:V4 系模型(deepseek-v4-flash 等)思考默认开且 effort=high,结构化长契约会
思考数万 token 吃光 max_tokens → 空正文 + 翻倍重试分钟级白跑(线上实测)。
修复 = 默认下发 thinking.type=disabled,渠道不认参数时撤掉重发。
"""
import asyncio

from app.llm.base import (
    LLMResponse,
    UpstreamError,
    remember_thinking_rejected,
    _THINKING_REJECTED,
)
from app.llm.openai_compatible import OpenAICompatibleAdapter


def _adapter(model: str = "deepseek-v4-flash", **kw) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        api_key="sk-x", model_name=model, base_url="https://api.example.com/v1", **kw
    )


def _payload(adapter: OpenAICompatibleAdapter) -> dict:
    return adapter._payload(adapter.to_messages("hi"), stream=False)


# ---------- payload 注入 ----------

def test_disabled_mode_injects_thinking_param():
    """disabled → thinking.type=disabled(对推理系模型)。"""
    p = _payload(_adapter(thinking_mode="disabled"))
    assert p["thinking"] == {"type": "disabled"}


def test_effort_mode_maps_to_reasoning_effort():
    """low/high/max → reasoning_effort(思考保持开,只调深度)。"""
    for mode in ("low", "high", "max"):
        p = _payload(_adapter(thinking_mode=mode))
        assert p["reasoning_effort"] == mode
        assert "thinking" not in p


def test_empty_mode_sends_nothing():
    a = _adapter(thinking_mode="")
    assert "thinking" not in _payload(a)
    assert "reasoning_effort" not in _payload(a)


def test_non_reasoning_model_skips_param_unless_forced():
    """模型名不像推理系(gpt-4o)且只是跟随默认 → 不下发,免得白挨 400;
    用户显式指定(thinking_forced)则照发——照顾被中转站改名的模型。"""
    a = _adapter(model="gpt-4o", thinking_mode="disabled")
    assert "thinking" not in _payload(a)
    forced = _adapter(model="gpt-4o", thinking_mode="disabled", thinking_forced=True)
    assert _payload(forced)["thinking"] == {"type": "disabled"}


def test_rejected_channel_is_remembered():
    """渠道拒收过一次参数 → 同 (base_url, model) 不再下发。"""
    a = _adapter(thinking_mode="disabled")
    remember_thinking_rejected(a.base_url, a.model_name)
    try:
        assert "thinking" not in _payload(a)
    finally:
        _THINKING_REJECTED.discard((a.base_url, a.model_name))


# ---------- 400 自动撤销重发 ----------

class _RejectThenOK(OpenAICompatibleAdapter):
    """第一次调用 400(参数错),撤参后第二次成功。记录每次 payload。"""

    def __init__(self):
        super().__init__(
            api_key="sk-x",
            model_name="deepseek-v4-flash",
            base_url="https://api.example.com/v1",
            thinking_mode="disabled",
        )
        self.retry_base_delay = 0
        self.sent: list[dict] = []

    async def _complete_via_stream(self, messages):
        self.sent.append(self._payload(messages, stream=True))
        if len(self.sent) == 1:
            raise UpstreamError(
                "上游返回 HTTP 400: Unknown parameter: 'thinking'.",
                status=400,
                retryable=False,
            )
        return LLMResponse(content="正文", model=self.model_name)


def test_param_rejected_400_strips_and_resends():
    a = _RejectThenOK()
    resp = asyncio.run(a.complete(a.to_messages("hi")))
    assert resp.content == "正文"
    # 第一次带了 thinking,第二次撤掉;渠道被记入拒收名单
    assert a.sent[0].get("thinking") == {"type": "disabled"}
    assert "thinking" not in a.sent[1]
    assert a.thinking_mode == ""
    assert (a.base_url, a.model_name) in _THINKING_REJECTED
    _THINKING_REJECTED.discard((a.base_url, a.model_name))


def test_genuine_400_is_not_swallowed():
    """与思考参数无关的 400(如 Model Not Exist)不吃掉、不重试,原样抛出。"""

    class _Model404(OpenAICompatibleAdapter):
        def __init__(self):
            super().__init__(
                api_key="sk-x",
                model_name="deepseek-v4-flash",
                base_url="https://api.example.com/v1",
                thinking_mode="disabled",
            )
            self.retry_base_delay = 0
            self.calls = 0

        def _raise(self):
            self.calls += 1
            return UpstreamError(
                "上游返回 HTTP 400: Model Not Exist", status=400, retryable=False
            )

        async def _complete_via_stream(self, messages):
            raise self._raise()

        async def _complete_once(self, messages):
            raise self._raise()

    a = _Model404()
    try:
        asyncio.run(a.complete(a.to_messages("hi")))
        raise AssertionError("应当抛出")
    except UpstreamError as exc:
        assert "Model Not Exist" in str(exc)
    # 流式 1 次 + 非流式回落 1 次,不再进重试圈;参数未被撤销
    assert a.calls == 2
    assert a.thinking_mode == "disabled"
    _THINKING_REJECTED.discard((a.base_url, a.model_name))


# ---------- factory 的解析优先级 ----------

def test_factory_resolves_thinking_priority(monkeypatch):
    """显式参数 > 配置列(空=未指定)> 全局默认;只有显式来源才 forced。"""
    import app.llm.factory as factory

    fake = {
        "id": None,
        "name": "t",
        "interface_format": "openai-compatible",
        "api_key": "sk-x",
        "base_url": "",
        "model": "deepseek-v4-flash",
        "timeout": 0,
        "max_tokens": 0,
        "thinking_mode": "",
        "is_default": True,
        "is_default_fast": False,
    }
    monkeypatch.setattr(factory, "_db_configs", lambda: [dict(fake)])

    # 跟随默认:disabled,但不算 forced(受模型名启发式管)
    a = factory.create_llm_adapter()
    assert a.thinking_mode == "disabled"
    assert a.thinking_forced is False

    # 配置列显式指定:forced,启发式不拦
    fake["thinking_mode"] = "high"
    b = factory.create_llm_adapter()
    assert b.thinking_mode == "high"
    assert b.thinking_forced is True

    # 显式参数压过配置列
    c = factory.create_llm_adapter(thinking_mode="low")
    assert c.thinking_mode == "low"
    assert c.thinking_forced is True
