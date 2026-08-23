# app/live.py
# -*- coding: utf-8 -*-
"""实时正文总线:把后台任务里「模型正在吐的字」原样转给浏览器。

为什么要有这一层:全站 60+ 处 LLM 调用几乎都跑在后台 job 里,前端只能轮询到
一句"正在写草稿",几十分钟看不到一个字;而逐个接口加 SSE 端点(60+ 个)必漏。
所以只在唯一的出水口——适配器的流式增量(见 `llm/base.py::_iter_live`)——装一个
钩子,按 job_id 分流进内存缓冲,再由 `GET /api/jobs/{id}/live` 一条 SSE 下发。

设计取舍:
- 纯内存、进程内:多 worker 部署时订阅必须落到同一进程(本项目单 worker);
  这条流丢了只影响"看直播",不影响任务本身,故一切失败都静默降级。
- 只留尾部 `_TAIL_CHARS` 字:直播是"看现在写到哪",不是传全文(全文有正式接口)。
- 步骤(job.stage)一变就清屏并 epoch+1:让前端一步一屏,而不是几段正文糊一起。
- 订阅端用轮询(_POLL_S)而不是 asyncio.Event:Event 绑事件循环,而 publish
  可能来自任何上下文;120ms 一跳对打字机观感无差,却省掉一类跨循环 bug。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from contextvars import ContextVar
from typing import Any, AsyncIterator

logger = logging.getLogger("jarvis-write.live")

# 当前上下文所属的后台任务:create_job 时设置。asyncio.create_task 会复制当前
# 上下文,所以 spawn_job / 手写 runner 起的后台任务都自动继承,嵌套多深都在。
current_job_id: ContextVar[str | None] = ContextVar("live_job_id", default=None)

_TAIL_CHARS = 4000      # 每个任务只保留尾部这么多字
_MAX_STREAMS = 128      # 内存上限:超出先清已结束的流
_POLL_S = 0.12          # 订阅端查新间隔
_HEARTBEAT_S = 15.0     # 心跳间隔(穿透 nginx/CDN 的空闲超时)
_FOLLOW_MAX_S = 3600.0  # 单条订阅最长存活,防僵尸连接

_LOCK = threading.Lock()


class _Stream:
    """一个任务的直播缓冲:尾部若干字 + 单调游标 + 当前步骤。"""

    __slots__ = (
        "chunks", "chars", "seq", "step", "epoch", "closed", "touched", "calls",
    )

    def __init__(self) -> None:
        self.chunks: deque[str] = deque()
        self.chars = 0      # 缓冲区现有字数(= "".join(chunks) 的长度)
        self.seq = 0        # 累计发布字数(单调递增,做订阅游标)
        self.step = ""      # 当前步骤文案(跟 job.stage 同步)
        self.epoch = 0      # 换屏计数:真正换步骤时 +1
        self.closed = False
        # 正在吐字的 LLM 调用数(决定换步骤要不要清屏)。用计数而非布尔:少数环节
        # 并发跑多路调用(如 inspire 的并发精筛),先结束的那路不能把窗口关掉。
        self.calls = 0
        self.touched = time.monotonic()

    def tail(self) -> str:
        return "".join(self.chunks)

    def trim(self) -> None:
        """裁到 _TAIL_CHARS 以内(整块丢;只剩一块还超长就截这块的尾巴)。"""
        while self.chars > _TAIL_CHARS and len(self.chunks) > 1:
            self.chars -= len(self.chunks.popleft())
        if self.chars > _TAIL_CHARS:
            kept = self.chunks[0][-_TAIL_CHARS:]
            self.chunks[0] = kept
            self.chars = len(kept)


_STREAMS: dict[str, _Stream] = {}


def _evict_locked() -> None:
    """内存兜底:超上限先清已结束的流(最旧优先),不够再清最旧的活跃流。"""
    if len(_STREAMS) <= _MAX_STREAMS:
        return
    over = len(_STREAMS) - _MAX_STREAMS
    for jid in [j for j, s in _STREAMS.items() if s.closed][:over]:
        _STREAMS.pop(jid, None)
    while len(_STREAMS) > _MAX_STREAMS:
        _STREAMS.pop(next(iter(_STREAMS)), None)


def _ensure_locked(job_id: str) -> _Stream:
    stream = _STREAMS.get(job_id)
    if stream is None:
        stream = _STREAMS[job_id] = _Stream()
        _evict_locked()
    return stream


def publish(delta: str, *, job_id: str | None = None) -> None:
    """把一段增量正文挂到当前任务的直播流上。

    无 job 上下文(前台请求、脚本、测试)时直接丢弃——那些路径要么自己有 SSE,
    要么没人看,不必占内存。
    """
    if not delta:
        return
    jid = job_id or current_job_id.get()
    if not jid:
        return
    with _LOCK:
        stream = _ensure_locked(jid)
        if stream.closed:
            return  # 任务已收尾,迟到的增量不再入流
        stream.chunks.append(delta)
        stream.chars += len(delta)
        stream.seq += len(delta)
        stream.touched = time.monotonic()
        stream.trim()


def set_step(job_id: str, step: str) -> None:
    """步骤变了 → 换屏(清缓冲 + epoch+1),让前端一步一屏。

    但只在"上一次调用已经吐完"时才清:有些环节会在同一次流式调用进行中反复更新
    进度文案(蓝图边写边报「已生成 N/M 章」),那不是新步骤,清屏会让用户眼前的字
    每隔几百字消失一次。这种情况只换标签,正文照旧往下滚。
    """
    if not job_id:
        return
    with _LOCK:
        stream = _ensure_locked(job_id)
        if stream.step == step:
            return
        stream.step = step
        stream.touched = time.monotonic()
        if stream.calls > 0:
            return  # 同一次调用中的进度计数:只换标签,不清屏、不换 epoch
        stream.epoch += 1
        stream.chunks.clear()
        stream.chars = 0     # 注意 seq 不回退:订阅游标必须保持单调


def begin_call(*, job_id: str | None = None) -> None:
    """一次 LLM 流式调用开始吐字(由适配器钩子调用,见 llm/base.py::_iter_live)。"""
    jid = job_id or current_job_id.get()
    if not jid:
        return
    with _LOCK:
        stream = _ensure_locked(jid)
        stream.calls += 1
        stream.touched = time.monotonic()


def end_call(*, job_id: str | None = None) -> None:
    """一次 LLM 调用结束(正常吐完/报错/被取消都走这)——最后一路收工才允许清屏。"""
    jid = job_id or current_job_id.get()
    if not jid:
        return
    with _LOCK:
        stream = _STREAMS.get(jid)
        if stream is not None:
            stream.calls = max(0, stream.calls - 1)
            stream.touched = time.monotonic()


def close(job_id: str) -> None:
    """任务收尾:标记流结束(尾巴留着,晚一步进来的订阅者还能看到最后一屏)。"""
    if not job_id:
        return
    with _LOCK:
        stream = _STREAMS.get(job_id)
        if stream is not None:
            stream.closed = True
            stream.touched = time.monotonic()


def snapshot(job_id: str) -> dict[str, Any] | None:
    """取当前一屏(无该任务的流则 None)。"""
    with _LOCK:
        stream = _STREAMS.get(job_id)
        if stream is None:
            return None
        return {
            "text": stream.tail(),
            "seq": stream.seq,
            "step": stream.step,
            "epoch": stream.epoch,
            "closed": stream.closed,
        }


def drop(job_id: str) -> None:
    """彻底丢掉某任务的流(测试/主动清理用)。"""
    with _LOCK:
        _STREAMS.pop(job_id, None)


def reset() -> None:
    """清空所有流(仅测试用)。"""
    with _LOCK:
        _STREAMS.clear()


async def follow(
    job_id: str, *, cursor: int = 0
) -> AsyncIterator[tuple[str, Any]]:
    """订阅一个任务的直播,产出 (event, data) 交给 SSE 层下发。

    事件:
      step  换屏(带这一屏已有的文字)——首帧必发,充当初始快照
      label 只换步骤文案(同一次调用中的进度计数,如蓝图「已生成 N/M 章」),正文不动
      token 正文增量
      reset 订阅端落后太多、缓冲区已滚过 → 整屏重置(带丢弃字数,不伪造连续)
      ping  心跳(穿透反代空闲超时)
      done  任务结束(带 status/stage/error),之后本流不再有内容

    每个内容帧都带 seq(服务端已发到第几个字),订阅端存下来,断线重连传回
    cursor 即可续看,不重复也不糊账。
    """
    from app.jobs import get_job  # 延迟导入:jobs 依赖本模块,避免循环

    epoch = -1
    step_shown = None
    started = last_sent = time.monotonic()
    while True:
        snap = snapshot(job_id)
        job = get_job(job_id)
        if snap is not None:
            if snap["epoch"] != epoch:
                # 换屏(含首帧):整屏下发,游标对齐到当前
                epoch = snap["epoch"]
                step_shown = snap["step"]
                cursor = snap["seq"]
                yield ("step", {
                    "step": snap["step"], "epoch": epoch,
                    "text": snap["text"], "seq": cursor,
                })
                last_sent = time.monotonic()
            else:
                if snap["step"] != step_shown:
                    step_shown = snap["step"]
                    yield ("label", {"step": snap["step"], "seq": cursor})
                    last_sent = time.monotonic()
                if snap["seq"] > cursor:
                    text, start = snap["text"], snap["seq"] - len(snap["text"])
                    if cursor < start:
                        yield ("reset", {
                            "text": text, "dropped": start - cursor, "seq": snap["seq"],
                        })
                    else:
                        yield ("token", {
                            "text": text[len(text) - (snap["seq"] - cursor):],
                            "seq": snap["seq"],
                        })
                    cursor = snap["seq"]
                    last_sent = time.monotonic()
        # 收尾:流已 close、或任务已不在运行(压根没产生过流的失败任务也走这)
        if job is None or job.get("status") != "running" or (snap and snap["closed"]):
            yield ("done", {
                "status": (job or {}).get("status", "gone"),
                "stage": (job or {}).get("stage", ""),
                "error": (job or {}).get("error"),
            })
            return
        now = time.monotonic()
        if now - started > _FOLLOW_MAX_S:
            # 单条连接不长驻,但**不发 done**:任务可能还在跑(连写能跑几小时),
            # 发 done 前端就当"结束了"永久停更。这里直接断,客户端按"流没 done
            # 就断了"的常规路径带 cursor 重订,直播继续。
            logger.debug("直播订阅到期,断开等客户端重订: job=%s", job_id)
            return
        if now - last_sent >= _HEARTBEAT_S:
            last_sent = now
            yield ("ping", {"seq": cursor})
        await asyncio.sleep(_POLL_S)
