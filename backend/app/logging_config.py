# app/logging_config.py
# -*- coding: utf-8 -*-
"""日志配置与请求追踪。

轻量级实现,不引入额外依赖:
- RequestIdFilter:给每条日志加上 request_id / user_id 上下文
- 请求 ID 中间件:为每个 HTTP 请求生成唯一 ID,便于追踪整条请求链路
- 长任务 ID 上下文:后台异步任务也能带上 job_id

使用方式:
- 在 main.py 调用 setup_logging() 初始化
- 日志里自动包含 request_id(如果在请求上下文中)
- 后台任务用 contextvars 注入 job_id
"""
from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# 请求 ID 上下文变量:中间件设置,日志过滤器读取
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

# 用户 ID 上下文变量:鉴权后设置(可选,便于追踪哪个用户的请求)
user_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "user_id", default=None
)

# 任务 ID 上下文变量:后台异步任务设置
job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "job_id", default=None
)


class ContextFilter(logging.Filter):
    """日志过滤器:给每条日志加上 request_id / user_id / job_id 上下文。

    没有上下文时显示 "-",避免日志格式错乱。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        record.user_id = user_id_var.get() or "-"
        record.job_id = job_id_var.get() or "-"
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """初始化日志配置。

    在 main.py 的模块顶层调用,替换默认的 basicConfig。
    日志格式包含时间、日志器名、级别、request_id、user_id、job_id、消息。
    """
    # 移除已有的 handler(避免重复输出)
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.addFilter(ContextFilter())

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)-30s | req=%(request_id)s | user=%(user_id)s | job=%(job_id)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root.addHandler(handler)
    root.setLevel(level)

    # 降低第三方库的日志级别,避免噪音
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """请求 ID 中间件:为每个 HTTP 请求生成唯一 ID,注入到上下文变量。

    请求 ID 优先从客户端传入的 X-Request-ID 头读取(便于跨服务追踪),
    没有则生成一个新的 UUID。响应头也会带上 X-Request-ID,便于客户端排查。
    """

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        # 优先用客户端传入的 request_id,没有则生成新的
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]

        # 注入上下文变量(整个请求处理过程中有效,包括异步任务)
        token = request_id_var.set(request_id)

        try:
            response = await call_next(request)
            # 响应头带上 request_id,便于客户端排查
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            # 恢复上下文变量(重要:避免请求间串扰)
            request_id_var.reset(token)


def set_job_id(job_id: str) -> contextvars.Token:
    """设置当前任务的 job_id(后台异步任务调用)。

    返回 token,任务结束后用 reset_job_id(token) 恢复。
    用法:
        token = set_job_id(job_id)
        try:
            ... 任务逻辑 ...
        finally:
            reset_job_id(token)
    """
    return job_id_var.set(job_id)


def reset_job_id(token: contextvars.Token) -> None:
    """恢复 job_id 上下文变量。"""
    job_id_var.reset(token)


def set_user_id(user_id: int) -> contextvars.Token:
    """设置当前请求的 user_id(鉴权后调用)。"""
    return user_id_var.set(user_id)


def reset_user_id(token: contextvars.Token) -> None:
    """恢复 user_id 上下文变量。"""
    user_id_var.reset(token)
