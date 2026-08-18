# app/api/projects/architecture.py
# -*- coding: utf-8 -*-
"""顶层架构:雪花四步生成(同步/异步)、手动编辑、多轮研讨。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Project
from app.db.session import SessionLocal, get_db
from app.engines.pipeline.architecture import (
    discuss_architecture,
    generate_architecture,
    save_architecture,
)
from app.jobs import create_job, fail_job, finish_job, list_running, normalize_job_error, update_stage
from app.schemas.project import ArchitectureOut, GenerateArchitectureRequest

from ._common import _get_project_or_404

router = APIRouter()


class ArchitecturePatch(BaseModel):
    core_seed: str | None = None
    character_dynamics: str | None = None
    world_building: str | None = None
    plot_architecture: str | None = None


@router.patch("/{project_id}/architecture", response_model=ArchitectureOut)
async def patch_architecture(
    project_id: int, req: ArchitecturePatch, db: Session = Depends(get_db)
):
    """手动编辑架构(工作台直接改,版本+1)。"""
    project = _get_project_or_404(db, project_id)
    arch = project.architecture
    if arch is None:
        raise HTTPException(status_code=404, detail="尚未生成架构")
    updates = req.model_dump(exclude_none=True)
    if updates:
        for field, value in updates.items():
            setattr(arch, field, value)
        arch.version += 1
        db.commit()
        db.refresh(arch)
    return arch


@router.post("/{project_id}/architecture", response_model=ArchitectureOut)
async def generate_project_architecture(
    project_id: int,
    req: GenerateArchitectureRequest,
    db: Session = Depends(get_db),
):
    """雪花四步生成顶层架构(串行 4 次 LLM 调用,耗时较长)。"""
    project = _get_project_or_404(db, project_id)

    result = await generate_architecture(
        topic=project.topic,
        genre=project.genre,
        number_of_chapters=project.target_chapters,
        word_number=project.target_words_per_chapter,
        concept=project.concept,
        tendency=req.tendency,
        global_tendency=project.global_tendency,
        directive=req.directive,
    )
    arch = save_architecture(db, project, result)
    db.commit()
    db.refresh(arch)
    return arch


@router.post("/{project_id}/architecture-async")
async def generate_project_architecture_async(
    project_id: int,
    req: GenerateArchitectureRequest,
    db: Session = Depends(get_db),
):
    """异步生成架构:立即返回 job_id,前端轮询 /api/jobs/{job_id} 看 1/4-4/4 进度。"""
    _get_project_or_404(db, project_id)  # 先校验存在与归属
    # 防重复提交:同项目架构任务已在跑 → 复用(前端接上轮询即可)
    for jid, _job in list_running(f"architecture-{project_id}"):
        if _job["kind"] == f"architecture-{project_id}":
            return {"job_id": jid}
    job_id = create_job(f"architecture-{project_id}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            result = await generate_architecture(
                topic=project.topic,
                genre=project.genre,
                number_of_chapters=project.target_chapters,
                word_number=project.target_words_per_chapter,
                concept=project.concept,
                tendency=req.tendency,
                global_tendency=project.global_tendency,
                directive=req.directive,
                progress=lambda s: update_stage(job_id, s),
            )
            update_stage(job_id, "落库中")
            arch = save_architecture(session, project, result)
            session.commit()
            session.refresh(arch)
            finish_job(job_id, ArchitectureOut.model_validate(arch).model_dump())
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


@router.get("/{project_id}/architecture", response_model=ArchitectureOut)
async def get_project_architecture(
    project_id: int, db: Session = Depends(get_db)
):
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(status_code=404, detail="尚未生成架构")
    return project.architecture


class ArchDiscussRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)


class ArchDiscussResponse(BaseModel):
    reply: str
    directive: str = ""


@router.post("/{project_id}/architecture/discuss", response_model=ArchDiscussResponse)
async def discuss_project_architecture(
    project_id: int,
    req: ArchDiscussRequest,
    db: Session = Depends(get_db),
):
    """就当前架构与作者多轮研讨:聊清不满意在哪 → 蒸馏出「额外要求」。

    前端拿返回的 directive 去调 architecture-async(directive 字段)重新生成。
    """
    project = _get_project_or_404(db, project_id)
    try:
        result = await discuss_architecture(
            req.messages,
            topic=project.topic,
            concept=project.concept,
            arch=project.architecture,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ArchDiscussResponse(**result)
