# tests/test_render_faults.py
# -*- coding: utf-8 -*-
"""出片链路故障路径测试:让 client.py 的真 httpx 代码跑起来。

既有 test_render.py 全部 patch 在 service/api 层,client 本身零执行——
这里用 httpx.MockTransport 替换传输层,超时/非200/畸形JSON/余额不足逐一打穿。
"""
import asyncio

import httpx
import pytest

from app.engines.render import client
from app.engines.render.client import RenderError

BASE = "https://platform.example"


def _patch_transport(monkeypatch, handler):
    """把 client 内部 new 出来的 AsyncClient 换成 MockTransport。"""
    real = httpx.AsyncClient

    def factory(**kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(**kw)

    monkeypatch.setattr(client.httpx, "AsyncClient", factory)


# ---------------------------------------------------------------------------
# submit 四态
# ---------------------------------------------------------------------------

def test_submit_timeout(monkeypatch):
    def handler(req):
        raise httpx.ConnectTimeout("slow")

    _patch_transport(monkeypatch, handler)
    with pytest.raises(RenderError, match="超时"):
        asyncio.run(client.submit(BASE, "tok", "wf", {}))


def test_submit_non200(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(RenderError, match="HTTP 500"):
        asyncio.run(client.submit(BASE, "tok", "wf", {}))


def test_submit_bad_json(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, text="<html>"))
    with pytest.raises(RenderError, match="无法解析"):
        asyncio.run(client.submit(BASE, "tok", "wf", {}))


def test_submit_rejected_by_platform(monkeypatch):
    """200 但业务拒绝(如余额不足):平台 msg 要原样上屏,不能被吞。"""
    _patch_transport(monkeypatch, lambda req: httpx.Response(
        200, json={"code": "balance_not_enough", "msg": "余额不足"}))
    with pytest.raises(RenderError, match="余额不足"):
        asyncio.run(client.submit(BASE, "tok", "wf", {}))


def test_submit_missing_token():
    """没配令牌不发出请求,直接人话提示。"""
    with pytest.raises(RenderError, match="令牌"):
        asyncio.run(client.submit(BASE, "", "wf", {}))


def test_submit_ok_returns_task_id(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(
        200, json={"code": "success", "data": {"task_id": "pt-123"}}))
    assert asyncio.run(client.submit(BASE, "tok", "wf", {})) == "pt-123"


# ---------------------------------------------------------------------------
# fetch_bytes
# ---------------------------------------------------------------------------

def test_fetch_bytes_non200(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(404))
    with pytest.raises(RenderError, match="HTTP 404"):
        asyncio.run(client.fetch_bytes("https://cdn.example/v.mp4"))


def test_fetch_bytes_ok(monkeypatch):
    _patch_transport(monkeypatch, lambda req: httpx.Response(200, content=b"vid"))
    assert asyncio.run(client.fetch_bytes("https://cdn.example/v.mp4")) == b"vid"
