# app/api/sse.py
# -*- coding: utf-8 -*-
"""Server-Sent Events 小工具:把 (event, data) 拼成一帧 text/event-stream 文本。

用于 AI 对话的真流式打字机(前端 fetch + ReadableStream 逐帧解析):
  event: token\\ndata: {"text": "…"}\\n\\n   逐字增量
  event: done\\ndata: {...}\\n\\n              结构化收尾(reply/suggestion/directive)
  event: error\\ndata: {"detail": "…"}\\n\\n   出错(HTTP 已 200,错误走帧内)
"""
from __future__ import annotations

import json
from typing import Any

# 流式响应头:关掉反代/CDN 的响应缓冲,否则逐字增量会被攒着一次性下发(线上走 nginx 反代必须)。
STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_event(event: str, data: Any) -> str:
    """拼一帧 SSE 文本。data 用 JSON 序列化(ensure_ascii=False 保中文可读)。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
