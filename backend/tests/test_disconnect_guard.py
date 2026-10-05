# tests/test_disconnect_guard.py
# -*- coding: utf-8 -*-
"""断开守卫中间件:客户端断开必须取消下游任务,正常请求必须原样通过。

用假 ASGI(app/receive/send 全是测试替身)直接驱动中间件,不经过 FastAPI——
要测的就是「http.disconnect → cancel 请求任务」这条传输层契约本身。
"""
import asyncio

from app.disconnect_guard import ClientDisconnectedGuardMiddleware


def _run(coro):
    return asyncio.run(coro)


def test_disconnect_cancels_downstream():
    """下游正「等 LLM」时收到 disconnect → 下游被取消,中间件安静收场。"""

    async def scenario():
        events = []

        async def downstream(scope, receive, send):
            try:
                await asyncio.sleep(3600)  # 模拟 await adapter.ask(...)
                events.append("completed")
            except asyncio.CancelledError:
                events.append("cancelled")
                raise

        msgs: asyncio.Queue = asyncio.Queue()
        await msgs.put({"type": "http.request", "body": b"", "more_body": False})
        sent: list[dict] = []

        mw = ClientDisconnectedGuardMiddleware(downstream)
        scope = {"type": "http", "method": "POST", "path": "/api/projects/1/three-questions"}
        task = asyncio.create_task(mw(scope, msgs.get, sent.append))
        await asyncio.sleep(0.05)  # 让下游进入「等待 LLM」
        await msgs.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 2)

        assert events == ["cancelled"], f"下游必须被取消,实际: {events}"
        # 499 占位响应已尝试发出(客户端收不到无所谓,给 uvicorn 一个交代)
        assert any(m.get("status") == 499 for m in sent)

    _run(scenario())


def test_normal_request_passes_through():
    """正常路径:body 消息按序转供、响应原样透传、不被误取消。"""

    async def scenario():
        async def downstream(scope, receive, send):
            seen = []
            while True:
                m = await receive()
                seen.append(m["type"])
                if m["type"] == "http.request" and not m.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
            return "handler-done"

        msgs: asyncio.Queue = asyncio.Queue()
        await msgs.put({"type": "http.request", "body": b"hello", "more_body": False})
        sent: list[dict] = []

        async def send(msg: dict) -> None:
            sent.append(msg)

        mw = ClientDisconnectedGuardMiddleware(downstream)
        scope = {"type": "http", "method": "POST", "path": "/x"}
        task = asyncio.create_task(mw(scope, msgs.get, send))
        assert await asyncio.wait_for(task, 2) == "handler-done"
        assert [m["type"] for m in sent[:2]] == ["http.response.start", "http.response.body"]
        # 事后才来的 disconnect 不再影响已完成的请求
        await msgs.put({"type": "http.disconnect"})
        await asyncio.sleep(0.02)

    _run(scenario())


def test_body_messages_replayed_in_order():
    """分片 body + 多条消息:下游收到的必须与真实顺序一致(不能被监听者偷走)。"""

    async def scenario():
        async def downstream(scope, receive, send):
            bodies = []
            while True:
                m = await receive()
                if m["type"] == "http.request":
                    bodies.append(m["body"])
                    if not m.get("more_body"):
                        break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"".join(bodies)})

        msgs: asyncio.Queue = asyncio.Queue()
        await msgs.put({"type": "http.request", "body": b"par", "more_body": True})
        await msgs.put({"type": "http.request", "body": b"tial", "more_body": False})
        sent: list[dict] = []

        async def send(msg: dict) -> None:
            sent.append(msg)

        mw = ClientDisconnectedGuardMiddleware(downstream)
        scope = {"type": "http", "method": "POST", "path": "/x"}
        task = asyncio.create_task(mw(scope, msgs.get, send))
        await asyncio.wait_for(task, 2)
        assert sent[-1]["body"] == b"partial"

    _run(scenario())


def test_non_http_scope_passes_through():
    """websocket/lifespan 等非 http scope:原样放行,不做任何取消。"""

    async def scenario():
        async def downstream(scope, receive, send):
            return "passed"

        mw = ClientDisconnectedGuardMiddleware(downstream)
        result = await mw({"type": "websocket", "path": "/ws"}, None, None)
        assert result == "passed"

    _run(scenario())


def test_cancellation_propagates_through_fastapi_stack():
    """集成:真 FastAPI + BaseHTTPMiddleware 夹层下,断开取消必须穿透到 handler。

    生产栈里守卫和 handler 之间隔着 RequestIdMiddleware / MaxBodySizeMiddleware
    等多个 BaseHTTPMiddleware(它把下游包进内部任务),取消必须能穿透这些层
    才算真掐断——这条例行测试钉住的就是「穿透」本身。
    """
    from fastapi import FastAPI
    from starlette.middleware.base import BaseHTTPMiddleware

    async def scenario():
        inner_cancelled = asyncio.Event()

        app = FastAPI()

        class _DummyBaseMW(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                return await call_next(request)

        @app.post("/slow")
        async def slow():
            try:
                await asyncio.sleep(3600)  # 模拟 await adapter.ask(...)
            except asyncio.CancelledError:
                inner_cancelled.set()
                raise
            return {"ok": True}

        app.add_middleware(_DummyBaseMW)                       # 模拟既有夹层
        app.add_middleware(ClientDisconnectedGuardMiddleware)  # 最外层守卫

        msgs: asyncio.Queue = asyncio.Queue()
        await msgs.put({"type": "http.request", "body": b"", "more_body": False})
        scope = {
            "type": "http", "method": "POST", "path": "/slow", "headers": [],
            "query_string": b"", "root_path": "", "scheme": "http",
            "http_version": "1.1", "server": ("testserver", 80),
            "client": ("127.0.0.1", 123),
            "asgi": {"version": "3.0", "spec_version": "2.3"},
        }
        sent: list[dict] = []

        async def send(msg: dict) -> None:
            sent.append(msg)

        task = asyncio.create_task(app(scope, msgs.get, send))
        await asyncio.sleep(0.1)  # 让 handler 进入等待
        await msgs.put({"type": "http.disconnect"})
        await asyncio.wait_for(task, 3)

        assert inner_cancelled.is_set(), "取消必须穿透 BaseHTTPMiddleware 夹层到达 handler"

    _run(scenario())
