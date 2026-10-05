# app/disconnect_guard.py
# -*- coding: utf-8 -*-
"""客户端断开守卫:用户「终止等待」后真正掐断后端的 LLM 调用。

背景(2026-10-05 v0.56.3 复盘):前端方案流加了「终止等待」,但那只是前端
单方面放弃——uvicorn 不会主动通知 handler「客户端走了」,handler 会继续把
`await adapter.ask(...)` 等完,token 照烧,请求位也白占。

本中间件在收到 `http.disconnect` 时取消整个请求任务:CancelledError 会穿透
handler 里所有 `except Exception`(BaseException 体系),沿 await 链一路掐进
httpx 的 await,连接被关闭——上游通常也随之停止生成。这是真掐断。

实现要点(纯 ASGI,不能用 BaseHTTPMiddleware):
- `receive` 是单消费者通道。中间件里另起 pump 任务**唯一持有**真 receive,
  把消息复制进队列转供给下游——否则「监听断开」会和下游读 body 抢消息,
  请求体可能被偷走。
- 断开消息入队后 cancel 请求任务;生成器型依赖(get_db 的 finally)照常执行,
  SQLAlchemy 会话照常关闭。
- 取消后补发一个 499(nginx 惯例:client closed request)占位,避免 uvicorn
  记「ASGI 未完成响应」的噪声日志。客户端已经走了,发什么都发不出去,
  纯粹是给服务端一个交代,所以 send 全程吞异常。
- 若取消来自服务器 shutdown 而非断开,同样安静收场:请求任务本就该结束。
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("jarvis-write.disconnect")


class ClientDisconnectedGuardMiddleware:
    """http.disconnect → cancel 请求任务(见模块 docstring)。"""

    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001, ANN201
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        queue: asyncio.Queue = asyncio.Queue()
        main_task = asyncio.current_task()

        async def pump() -> None:
            # 唯一的 receive 消费者:转发所有消息;见到 disconnect 就 cancel 请求任务。
            try:
                while True:
                    msg = await receive()
                    await queue.put(msg)
                    if msg["type"] == "http.disconnect":
                        if main_task is not None and not main_task.done():
                            main_task.cancel()
                        return
            except asyncio.CancelledError:
                pass  # 主流程收尾时的正常清理
            except Exception:  # noqa: BLE001  连接层异常(如服务器关闭)——等同断开
                if main_task is not None and not main_task.done():
                    main_task.cancel()

        pump_task = asyncio.create_task(pump())

        async def recv():
            return await queue.get()

        try:
            return await self.app(scope, recv, send)
        except asyncio.CancelledError:
            logger.info("客户端断开,请求已取消: %s %s", scope.get("method"), scope.get("path"))
            try:
                await send({"type": "http.response.start", "status": 499, "headers": []})
                await send({"type": "http.response.body", "body": b""})
            except Exception:  # noqa: BLE001  客户端已走/响应已发——发不出去就发不出去
                pass
        finally:
            pump_task.cancel()
