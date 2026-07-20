# app/jobs.py
# -*- coding: utf-8 -*-
"""轻量后台任务存储(进程内)。

长任务(章节生成)改为:发起 → 立刻拿 job_id → 前端轮询进度。
单机单进程场景够用;多进程部署时换 Redis(阶段 7 后话)。
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

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
