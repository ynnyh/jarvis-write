# app/llm/throttle.py
# -*- coding: utf-8 -*-
"""按「渠道 + 模型」维度的主动限速:并发上限 + 滑动窗口 RPM。

为什么挂在渠道维度而不是用户/配置维度:同一个中转站 + 同一个模型的
上游配额是共享的,不管哪个账号的命名配置发出去,挤的都是同一条管道——
按渠道限才能真正防 429/防封号,多用户站上也天然公平。

用法(async 上下文):
    async with throttle.slot(key, max_concurrency, rpm):
        ...发请求...

- max_concurrency = 0 → 不限并发;rpm = 0 → 不限速率(存量配置默认,行为不变)
- 配置改了并发/速率 → 新旧 gate 并存,旧持有者走旧 gate 释放,互不干扰
- 假定单一事件循环(uvicorn 默认);跨 loop 不共享窗口
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager

# RPM 滑动窗口宽度(秒):标准语义就是「每分钟」
WINDOW_SECONDS = 60.0


class _Gate:
    """一个 (渠道, 并发, rpm, 窗口宽) 组合的闸门。"""

    __slots__ = ("sem", "rpm", "window", "window_seconds")

    def __init__(self, concurrency: int, rpm: int, window_seconds: float) -> None:
        # asyncio.Semaphore 不能动态改容量:并发数写进 gate 键,配置一变自然换新闸门
        self.sem: asyncio.Semaphore | None = (
            asyncio.Semaphore(concurrency) if concurrency > 0 else None
        )
        self.rpm = rpm
        self.window_seconds = window_seconds
        self.window: deque[float] = deque()


_gates: dict[str, _Gate] = {}


async def _reserve_rpm(gate: _Gate) -> None:
    """在滑动窗口里占一个速率槽;满了就等到最早的请求滑出窗口。"""
    loop = asyncio.get_running_loop()
    width = gate.window_seconds
    while True:
        now = loop.time()
        while gate.window and now - gate.window[0] >= width:
            gate.window.popleft()
        if len(gate.window) < gate.rpm:
            gate.window.append(now)
            return
        wait = width - (now - gate.window[0]) + 0.05
        await asyncio.sleep(min(wait, 5.0))


@asynccontextmanager
async def slot(key: str, max_concurrency: int, rpm: int, window_seconds: float = WINDOW_SECONDS):
    """占一个 (并发, 速率) 槽位;两个上限都是 0 时零开销直通。"""
    if max_concurrency <= 0 and rpm <= 0:
        yield
        return
    gkey = f"{key}|c{max(0, int(max_concurrency))}|r{max(0, int(rpm))}|w{window_seconds}"
    gate = _gates.get(gkey)
    if gate is None:
        gate = _Gate(max(0, int(max_concurrency)), max(0, int(rpm)), window_seconds)
        _gates[gkey] = gate

    if gate.sem is not None:
        await gate.sem.acquire()
    try:
        if gate.rpm > 0:
            await _reserve_rpm(gate)
        yield
    finally:
        if gate.sem is not None:
            gate.sem.release()
