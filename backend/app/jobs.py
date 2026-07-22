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
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

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
    _persist_create(job_id, kind, owner)
    return job_id


def update_stage(job_id: str, stage: str) -> None:
    """高频进度更新:仅写内存(不写 DB,避免 SQLite 写锁竞争)。"""
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id]["stage"] = stage


def finish_job(job_id: str, result: Any) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(status="done", stage="完成", result=result)
    _persist_finish(job_id, result)


def fail_job(job_id: str, error: str) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(status="error", stage="失败", error=error)
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


def spawn_job(kind: str, work: Callable[[Callable[[str], None]], Awaitable[Any]]) -> str:
    """通用异步任务封装:建 job → 后台跑 work(progress) → 结果/异常落 job。"""
    job_id = create_job(kind)

    async def runner() -> None:
        try:
            result = await work(lambda s: update_stage(job_id, s))
            finish_job(job_id, result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("任务 %s(%s) 失败: %s", job_id, kind, exc, exc_info=True)
            fail_job(job_id, str(exc)[:500])

    asyncio.create_task(runner())
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
