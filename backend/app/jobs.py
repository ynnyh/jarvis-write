# app/jobs.py
# -*- coding: utf-8 -*-
"""后台任务存储:内存热路径 + SQLite 持久化。

状态转换(create/finish/fail)同步写 DB,高频 stage 更新仅写内存。
服务重启后:running 超 30 分钟的标记为 failed(进程死了任务不可能还活着)。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app import live
from app.logging_config import set_job_id

logger = logging.getLogger("jarvis-write.jobs")

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_MAX_JOBS = 200  # 内存上限:超出后清最旧的已完成任务
_STUCK_MINUTES = 30  # 启动时:running 超过此时间视为 stuck


# ---------------------------------------------------------------------------
# DB 辅助(延迟导入避免循环依赖)
# ---------------------------------------------------------------------------

def _db_session():
    from app.db.session import SessionLocal
    return SessionLocal()


def _persist_create(job_id: str, kind: str, owner_id: Any) -> None:
    """状态转换:创建 → 写 DB。"""
    try:
        from app.db.models import Job
        session = _db_session()
        session.add(Job(id=job_id, kind=kind, status="running", owner_id=owner_id, stage="排队中"))
        session.commit()
        session.close()
    except Exception:  # noqa: BLE001 — 持久化失败不阻塞任务
        logger.debug("job %s 持久化(create)失败", job_id, exc_info=True)


def _persist_finish(job_id: str, result: Any) -> None:
    """状态转换:完成 → 写 DB。"""
    try:
        from app.db.models import Job
        session = _db_session()
        row = session.get(Job, job_id)
        if row:
            row.status = "done"
            row.stage = "完成"
            row.result = result
        session.commit()
        session.close()
    except Exception:  # noqa: BLE001
        logger.debug("job %s 持久化(finish)失败", job_id, exc_info=True)


def _persist_fail(job_id: str, error: str) -> None:
    """状态转换:失败 → 写 DB。"""
    try:
        from app.db.models import Job
        session = _db_session()
        row = session.get(Job, job_id)
        if row:
            row.status = "error"
            row.stage = "失败"
            row.error = error
        session.commit()
        session.close()
    except Exception:  # noqa: BLE001
        logger.debug("job %s 持久化(fail)失败", job_id, exc_info=True)


# ---------------------------------------------------------------------------
# 公开 API(与旧版签名完全兼容)
# ---------------------------------------------------------------------------

def create_job(kind: str) -> str:
    """建任务。owner_id 记当前登录用户,取不到(脚本/迁移上下文)则为 None。"""
    from app.auth import current_user_id

    job_id = uuid.uuid4().hex[:12]
    owner = current_user_id.get()
    with _LOCK:
        if len(_JOBS) > _MAX_JOBS:
            for k in [k for k, v in _JOBS.items() if v["status"] != "running"][: len(_JOBS) - _MAX_JOBS]:
                _JOBS.pop(k, None)
        _JOBS[job_id] = {
            "kind": kind, "status": "running", "owner_id": owner,
            "stage": "排队中", "result": None, "error": None,
        }
    # 实时正文归属:在这里(而非各 runner 里)设 ContextVar——asyncio.create_task
    # 复制创建时的上下文,所以随后 fire_and_track/spawn_job 起的后台任务及其嵌套
    # 的每一次 LLM 调用都自动认领这个 job_id,不必逐个接口改(共 20+ 处建任务点)。
    live.current_job_id.set(job_id)
    # 日志上下文:任务期间的所有日志行带 job=<id>,排查"哪次生成在刷屏"直接按号过滤
    set_job_id(job_id)
    _persist_create(job_id, kind, owner)
    return job_id


def update_stage(job_id: str, stage: str) -> None:
    """高频进度更新:仅写内存(不写 DB,避免 SQLite 写锁竞争)。"""
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id]["stage"] = stage
    live.set_step(job_id, stage)  # 直播换一屏:一步一屏,不把几段正文糊在一起


def finish_job(job_id: str, result: Any) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(status="done", stage="完成", result=result)
    live.close(job_id)
    _persist_finish(job_id, result)


def fail_job(job_id: str, error: str) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(status="error", stage="失败", error=error)
    live.close(job_id)
    _persist_fail(job_id, error)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_running(kind_prefix: str) -> list[tuple[str, dict[str, Any]]]:
    """按 kind 前缀列出运行中的任务(去重复提交/断线重连用)。"""
    with _LOCK:
        return [
            (jid, dict(job))
            for jid, job in _JOBS.items()
            if job["status"] == "running" and job["kind"].startswith(kind_prefix)
        ]


def list_for_user(owner_id: Any, running_only: bool = True) -> list[tuple[str, dict[str, Any]]]:
    """某用户的任务(全局任务中心用)。running_only=False 时含近期已完成的。"""
    with _LOCK:
        return [
            (jid, dict(job))
            for jid, job in _JOBS.items()
            if job.get("owner_id") == owner_id
            and (not running_only or job["status"] == "running")
        ]


def normalize_job_error(exc: Exception) -> str:
    """把常见 LLM/网络异常归一化成用户可读的中文,存进 job.error 直接上屏。

    原始英文异常不丢:spawn_job 的 logger.warning 带 exc_info 完整进日志。
    归一化只覆盖高频形态(连接失败/超时、401 key 无效或欠费、404 模型不存在、
    429 限流),其余原样返回(后端业务错误本身已是中文)。
    """
    msg = str(exc)
    # 出片链路的错误文案是面向用户的最终版(含「出片平台…(HTTP 401/402)」形态),
    # 若掉进下面的 HTTP 状态分支会被翻译成「模型 API Key 无效」,指错地方——原样放行。
    from app.engines.render.client import RenderError
    if isinstance(exc, RenderError):
        return msg
    if "HTTP 401" in msg:
        return "模型 API Key 无效或已欠费(HTTP 401),请到「设置」检查 key 与账户余额"
    if "HTTP 402" in msg:
        return "模型账户欠费(HTTP 402),请充值后重试"
    if "HTTP 404" in msg:
        return "模型不存在或接口地址错误(HTTP 404),请到「设置」检查模型名与 Base URL"
    if "HTTP 429" in msg:
        return "模型限流(HTTP 429),请稍后重试,或到「设置」更换模型"
    if isinstance(exc, httpx.TimeoutException):
        return "调用模型超时(网络慢或模型负载高),请稍后重试"
    if isinstance(exc, httpx.ConnectError):
        return "无法连接模型服务,请检查网络/代理,或「设置」里的 Base URL"
    if isinstance(exc, httpx.HTTPError):
        return f"模型服务网络错误:{msg}"
    return msg


# 后台任务强引用保持:事件循环对 create_task 的返回值只持弱引用,任务对象被 GC
# 回收会导致运行中的后台任务被静默取消(见官方 asyncio.create_task 警告)。存进
# 模块级 set、完成时用回调移除,确保任务活到跑完。
_bg_tasks: set[asyncio.Task[Any]] = set()


def fire_and_track(coro: Coroutine[Any, Any, Any]) -> None:
    """起后台任务并保留强引用(防 create_task 的任务被 GC 中途回收)。"""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


def spawn_job(kind: str, work: Callable[[Callable[[str], None]], Awaitable[Any]]) -> str:
    """通用异步任务封装:建 job → 后台跑 work(progress) → 结果/异常落 job。"""
    job_id = create_job(kind)

    async def runner() -> None:
        # 兜底再设一次(create_job 已设):万一 work 被从别的上下文调度,
        # 直播归属也不会串到别的任务上
        live.current_job_id.set(job_id)
        try:
            result = await work(lambda s: update_stage(job_id, s))
            finish_job(job_id, result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("任务 %s(%s) 失败: %s", job_id, kind, exc, exc_info=True)
            fail_job(job_id, normalize_job_error(exc)[:500])

    fire_and_track(runner())
    return job_id


# ---------------------------------------------------------------------------
# 启动清理:标记 stuck 任务
# ---------------------------------------------------------------------------

def cleanup_stuck_jobs() -> None:
    """服务启动时调用:把 DB 中 running 超时的任务标记为 failed。

    进程重启后,之前 running 的任务不可能还活着(asyncio task 随进程消亡)。
    """
    try:
        from app.db.models import Job
        session = _db_session()
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STUCK_MINUTES)
        stuck = (
            session.query(Job)
            .filter(Job.status == "running", Job.created_at < cutoff)
            .all()
        )
        for job in stuck:
            job.status = "error"
            job.stage = "失败"
            job.error = "服务重启,任务中断(超时自动标记)"
        if stuck:
            session.commit()
            logger.info("启动清理:%d 个 stuck 任务标记为 failed", len(stuck))
        session.close()
    except Exception:  # noqa: BLE001 — 清理失败不阻塞启动
        logger.debug("启动清理 stuck jobs 失败", exc_info=True)
