# app/jobs.py
# -*- coding: utf-8 -*-
"""轻量后台任务存储(进程内)。

长任务(章节生成)改为:发起 → 立刻拿 job_id → 前端轮询进度。
单机单进程场景够用;多进程部署时换 Redis(阶段 7 后话)。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("jarvis-write.jobs")

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_MAX_JOBS = 200  # 防泄漏:超出后清最旧的已完成任务


def create_job(kind: str) -> str:
    """建任务。owner_id 记当前登录用户,取不到(脚本/迁移上下文)则为 None。"""
    from app.auth import current_user_id

    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        if len(_JOBS) > _MAX_JOBS:
            for k in [k for k, v in _JOBS.items() if v["status"] != "running"][: len(_JOBS) - _MAX_JOBS]:
                _JOBS.pop(k, None)
        _JOBS[job_id] = {
            "kind": kind, "status": "running", "owner_id": current_user_id.get(),
            "stage": "排队中", "result": None, "error": None,
        }
    return job_id


def update_stage(job_id: str, stage: str) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id]["stage"] = stage


def finish_job(job_id: str, result: Any) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(status="done", stage="完成", result=result)


def fail_job(job_id: str, error: str) -> None:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(status="error", stage="失败", error=error)


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
    """通用异步任务封装:建 job → 后台跑 work(progress) → 结果/异常落 job。

    work 收到一个 progress(stage_text) 回调;返回值(可 JSON 化)作为 job result。
    幂等由调用方自行处理(需要防重的先查 list_running 再调这里)。
    """
    job_id = create_job(kind)

    async def runner() -> None:
        try:
            result = await work(lambda s: update_stage(job_id, s))
            finish_job(job_id, result)
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            logger.warning("任务 %s(%s) 失败: %s", job_id, kind, exc, exc_info=True)
            fail_job(job_id, str(exc)[:500])

    asyncio.create_task(runner())
    return job_id
