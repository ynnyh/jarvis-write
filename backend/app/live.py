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
_THINK_TAIL_CHARS = 2000  # 思考流尾部(思考只是"看见在干活",留太多了没意义)
_MAX_STREAMS = 128      # 内存上限:超出先清已结束的流
_POLL_S = 0.12          # 订阅端查新间隔
_HEARTBEAT_S = 15.0     # 心跳间隔(穿透 nginx/CDN 的空闲超时)
_FOLLOW_MAX_S = 3600.0  # 单条订阅最长存活,防僵尸连接

_LOCK = threading.Lock()


class _Stream:
    """一个任务的直播缓冲:尾部若干字 + 单调游标 + 当前步骤。"""

    __slots__ = (
        "chunks", "chars", "seq", "step", "epoch", "closed", "touched", "calls",
        "retries", "last_err",
        "think_chunks", "think_chars", "think_seq", "think_warn", "think_burned",
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
        # 渠道异常累计:本任务至今的重试次数 + 最近一次上游错误摘要。成功不消零——
        # 「渠道在背后反复抽风」要持续可见,用户才不会把卡住当成正常等待。
        self.retries = 0
        self.last_err = ""
        # 思考流(模型 reasoning):直播"正在思考"的观感 + 强制思考预警账。
        # 思考不写入正文,单独一条缓冲,与正文各自持游标。
        self.think_chunks: deque[str] = deque()
        self.think_chars = 0
        self.think_seq = 0
        # 强制思考预警:渠道被判定「思考关不掉」后置位(一次任务只提示一条),
        # think_burned 累计本任务白烧的思考 token(上游 usage 报数,跨调用累加)。
        self.think_warn = False
        self.think_burned = 0
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

    def think_tail(self) -> str:
        return "".join(self.think_chunks)

    def trim_think(self) -> None:
        """思考流裁到 _THINK_TAIL_CHARS(算法同 trim)。"""
        while self.think_chars > _THINK_TAIL_CHARS and len(self.think_chunks) > 1:
            self.think_chars -= len(self.think_chunks.popleft())
        if self.think_chars > _THINK_TAIL_CHARS:
            kept = self.think_chunks[0][-_THINK_TAIL_CHARS:]
            self.think_chunks[0] = kept
            self.think_chars = len(kept)


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


def publish_reasoning(delta: str, *, job_id: str | None = None) -> None:
    """把一段思考增量挂到当前任务的思考流上(与正文分流,前端单独渲染)。

    为什么单独一条流:思考不写入正文,混进正文会把书稿内容搅乱;但"模型正在
    思考"必须可见——强制思考的渠道上一动不动几十秒,用户只会以为卡死了。
    无 job 上下文时直接丢弃,与 publish 同口径。
    """
    if not delta:
        return
    jid = job_id or current_job_id.get()
    if not jid:
        return
    with _LOCK:
        stream = _ensure_locked(jid)
        if stream.closed:
            return
        stream.think_chunks.append(delta)
        stream.think_chars += len(delta)
        stream.think_seq += len(delta)
        stream.touched = time.monotonic()
        stream.trim_think()


def note_thinking_unstoppable(job_id: str, burned_tokens: int = 0) -> None:
    """记一次强制思考:任务横幅置位(一次任务只提示一条)+ 累计白烧的思考 token。

    与 note_retry 同设计:横幅跨调用持续可见(渠道特性不变,提示不能闪一下就没了),
    消耗跨调用累加(用户得知道这条渠道一顿饭烧了多少)。重复置位不重复提示。
    """
    with _LOCK:
        stream = _ensure_locked(job_id)
        if not stream.think_warn:
            stream.think_warn = True
        if burned_tokens > 0:
            stream.think_burned += burned_tokens
        stream.touched = time.monotonic()


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
        # 思考流同屏清:一步一屏对思考一样成立,上一步的思考不该糊到这一步
        stream.think_chunks.clear()
        stream.think_chars = 0


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
            "retries": stream.retries,
            "lastErr": stream.last_err,
            "think": stream.think_tail(),
            "thinkSeq": stream.think_seq,
            "thinkWarn": stream.think_warn,
            "thinkBurned": stream.think_burned,
        }


def peek_step(job_id: str) -> str:
    """读当前步骤文案(不改变任何状态)。

    给重试提示拼前缀用:「一致性检查」→「一致性检查 · 重试中 2/3(上游限流)」,
    用户才知道是哪一步在重试。
    """
    if not job_id:
        return ""
    with _LOCK:
        stream = _STREAMS.get(job_id)
        return stream.step if stream is not None else ""


def label_step(job_id: str, step: str) -> None:
    """只换步骤文案,**不清屏**(对应 SSE 的 label 帧)。

    与 set_step 的差别:set_step 表示「进入新步骤」,会 epoch+1 并清掉上一屏;
    这里只是给同一步换个标题(如挂上「重试中 2/3」再摘掉),已经吐给用户看的
    正文必须原封不动。渐进计数(蓝图「已生成 N/M 章」)也走这条。
    """
    if not job_id:
        return
    with _LOCK:
        stream = _STREAMS.get(job_id)
        if stream is None or stream.step == step:
            return
        stream.step = step
        stream.touched = time.monotonic()


def note_retry(job_id: str, err: str) -> None:
    """记一次上游重试:累计次数 + 最近错误摘要,随 snapshot/label 帧下发。

    为什么不只靠 label_step 的「重试中 x/y」:那是瞬时文案,重试成功就摘掉,
    而渠道抽风往往是**整个任务过程中反复发生**——累计数让前端能持续显示
    「渠道不稳」的警示(卡住的用户才知道该去换渠道,而不是干等)。
    """
    if not job_id:
        return
    # 压成一行、截断:错误原文可能带换行/request id,警示条上只留可读摘要
    brief = " ".join(str(err).split())[:100]
    with _LOCK:
        stream = _ensure_locked(job_id)
        stream.retries += 1
        stream.last_err = brief
        stream.touched = time.monotonic()


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
      think 思考增量(与正文分流,前端单独渲染;带 reset 语义同 reset)
      reset 订阅端落后太多、缓冲区已滚过 → 整屏重置(带丢弃字数,不伪造连续)
      ping  心跳(穿透反代空闲超时)
      done  任务结束(带 status/stage/error),之后本流不再有内容

    每个内容帧都带 seq(服务端已发到第几个字),订阅端存下来,断线重连传回
    cursor 即可续看,不重复也不糊账;思考流有独立游标(前端单独维护)。
    强制思考预警(thinkWarn/thinkBurned)随 step/label 帧下发,变化即推。
    """
    from app.jobs import get_job  # 延迟导入:jobs 依赖本模块,避免循环

    epoch = -1
    step_shown = None
    retries_shown = 0
    think_shown = False
    burned_shown = 0
    think_cursor = 0
    started = last_sent = time.monotonic()
    while True:
        snap = snapshot(job_id)
        job = get_job(job_id)
        if snap is not None:
            if snap["epoch"] != epoch:
                # 换屏(含首帧):整屏下发,游标对齐到当前
                epoch = snap["epoch"]
                step_shown = snap["step"]
                retries_shown = snap["retries"]
                think_shown = snap["thinkWarn"]
                burned_shown = snap["thinkBurned"]
                cursor = snap["seq"]
                think_cursor = snap["thinkSeq"]
                yield ("step", {
                    "step": snap["step"], "epoch": epoch,
                    "text": snap["text"], "seq": cursor,
                    "retries": snap["retries"], "lastErr": snap["lastErr"],
                    "think": snap["think"], "thinkSeq": snap["thinkSeq"],
                    "thinkWarn": snap["thinkWarn"], "thinkBurned": snap["thinkBurned"],
                })
                last_sent = time.monotonic()
            else:
                if snap["step"] != step_shown:
                    step_shown = snap["step"]
                    yield ("label", {
                        "step": snap["step"], "seq": cursor,
                        "retries": snap["retries"], "lastErr": snap["lastErr"],
                        "thinkWarn": snap["thinkWarn"], "thinkBurned": snap["thinkBurned"],
                    })
                    last_sent = time.monotonic()
                elif (
                    snap["retries"] != retries_shown
                    or snap["thinkWarn"] != think_shown
                    or snap["thinkBurned"] != burned_shown
                ):
                    # 重试/强制思考预警变了但步骤没变:单独推一帧 label,警示别等到下次换屏才可见
                    retries_shown = snap["retries"]
                    think_shown = snap["thinkWarn"]
                    burned_shown = snap["thinkBurned"]
                    yield ("label", {
                        "step": snap["step"], "seq": cursor,
                        "retries": snap["retries"], "lastErr": snap["lastErr"],
                        "thinkWarn": snap["thinkWarn"], "thinkBurned": snap["thinkBurned"],
                    })
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
                if snap["thinkSeq"] > think_cursor:
                    ttext, tstart = snap["think"], snap["thinkSeq"] - len(snap["think"])
                    if think_cursor < tstart:
                        yield ("think", {
                            "reset": True, "text": ttext,
                            "dropped": tstart - think_cursor, "seq": snap["thinkSeq"],
                        })
                    else:
                        yield ("think", {
                            "text": ttext[len(ttext) - (snap["thinkSeq"] - think_cursor):],
                            "seq": snap["thinkSeq"],
                        })
                    think_cursor = snap["thinkSeq"]
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
