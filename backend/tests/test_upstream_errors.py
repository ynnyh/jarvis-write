# backend/tests/test_upstream_errors.py
# -*- coding: utf-8 -*-
"""上游错误可读化:非 JSON / HTTP 错误要抛出用户看得懂的提示,而不是 JSONDecodeError。"""
import httpx
import pytest

from app.llm.base import check_upstream


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
