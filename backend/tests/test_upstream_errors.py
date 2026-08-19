# backend/tests/test_upstream_errors.py
# -*- coding: utf-8 -*-
"""上游错误可读化:非 JSON / HTTP 错误 / 空 choices 要抛出用户看得懂的提示,
而不是 JSONDecodeError 或裸 IndexError「list index out of range」。"""
import asyncio

import httpx
import pytest

from app.llm.base import UpstreamError, check_upstream
from app.llm.gemini import GeminiAdapter
from app.llm.openai_compatible import OpenAICompatibleAdapter


def _resp(status: int, text: str, json_body: dict | None = None) -> httpx.Response:
    if json_body is not None:
        return httpx.Response(status, json=json_body)
    return httpx.Response(status, text=text)


def test_html_error_page_becomes_readable_message():
    """中转站不支持该协议时返回 HTML 错误页 → 可读提示,含 hint。"""
    resp = _resp(404, "<html><body>404 Not Found</body></html>")
    with pytest.raises(RuntimeError) as exc:
        check_upstream(resp, hint="中转站请改用 OpenAI 卡")
    msg = str(exc.value)
    assert "HTTP 404" in msg
    assert "非 JSON" in msg
    assert "中转站请改用 OpenAI 卡" in msg
    # 不再出现原始的 JSONDecodeError 文案
    assert "Expecting value" not in msg


def test_200_with_html_body_reports_base_url_problem():
    """200 但返回 HTML(如网关页)→ 指出 Base URL/协议问题。"""
    resp = _resp(200, "<!DOCTYPE html><html></html>")
    with pytest.raises(RuntimeError) as exc:
        check_upstream(resp, hint="确认 Base URL 含 /v1")
    msg = str(exc.value)
    assert "非 JSON" in msg
    assert "Base URL" in msg
    assert "/v1" in msg


def test_http_error_extracts_upstream_json_message():
    """上游 JSON 错误体 → 提取 error.message。"""
    resp = _resp(401, "", {"error": {"message": "Invalid API key"}})
    with pytest.raises(RuntimeError) as exc:
        check_upstream(resp)
    assert "HTTP 401" in str(exc.value)
    assert "Invalid API key" in str(exc.value)


def test_valid_json_passes_through():
    resp = _resp(200, "", {"choices": []})
    assert check_upstream(resp) == {"choices": []}


# ---------- adapter 层:200 + 空 choices/candidates 兜底 ----------
# check_upstream 只校验信封(见上一条),空 choices 由 check_upstream 放行,
# 必须在适配器解析层拦住,否则裸 data["choices"][0] 会抛 IndexError
# 「list index out of range」——正是章节润色/生成偶发的那个报错。

def _patch_post(monkeypatch, response: httpx.Response) -> None:
    async def fake_post(self, *args, **kwargs):
        return response

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def test_openai_empty_choices_is_readable_not_indexerror(monkeypatch):
    """中转站抽风/额度耗尽/内容过滤 → 200 + {"choices": []}:
    抛可读、可重试的 UpstreamError,而不是裸 IndexError。"""
    _patch_post(monkeypatch, httpx.Response(200, json={"choices": [], "usage": {}}))
    adapter = OpenAICompatibleAdapter(api_key="sk-x", model_name="m")
    with pytest.raises(UpstreamError) as exc:
        asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    msg = str(exc.value)
    assert "choices" in msg
    assert exc.value.retryable is True  # 渠道抖动多为瞬时,交给 with_retries 再试


def test_openai_normal_choices_still_parsed(monkeypatch):
    """回归护栏:正常响应解析不受空值兜底影响。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "choices": [{"message": {"content": "润色稿"}}],
        "model": "m", "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }))
    adapter = OpenAICompatibleAdapter(api_key="sk-x", model_name="m")
    resp = asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert resp.content == "润色稿"
    assert resp.completion_tokens == 2


def test_gemini_empty_candidates_is_readable_not_indexerror(monkeypatch):
    """Gemini 安全过滤 → 200 + 空 candidates:抛可读 UpstreamError,不是 IndexError。"""
    _patch_post(monkeypatch, httpx.Response(200, json={
        "promptFeedback": {"blockReason": "SAFETY"},
    }))
    adapter = GeminiAdapter(api_key="k", model_name="gemini-x")
    with pytest.raises(UpstreamError) as exc:
        asyncio.run(adapter._complete_once(adapter.to_messages("hi")))
    assert "SAFETY" in str(exc.value)
