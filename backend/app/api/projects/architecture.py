# app/api/projects/architecture.py
# -*- coding: utf-8 -*-
"""顶层架构:雪花四步生成(同步/异步)、手动编辑、多轮研讨。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Project
from app.db.session import SessionLocal, get_db
from app.engines.consistency.persona import extract_cast_profiles
from app.engines.pipeline.architecture import (
    LAYER_KEYS,
    LAYER_LABEL,
    confirm_architecture_layer,
    discuss_architecture,
    generate_architecture,
    generate_architecture_layer,
    layer_state,
    mark_layers_edited,
    save_architecture,
    save_architecture_layer,
    validate_layer,
)
from app.jobs import create_job, fail_job, finish_job, fire_and_track, list_running, normalize_job_error, update_stage
from app.schemas.project import (
    ArchitectureConfirmRequest,
    ArchitectureLayerRequest,
    ArchitectureOut,
    GenerateArchitectureRequest,
)

from ._common import _get_project_or_404

router = APIRouter()

logger = logging.getLogger("jarvis-write.persona")


async def _extract_personas_safe(db: Session, project: Project) -> None:
    """架构落库后提炼核心人物画像卡;失败只告警,绝不拖垮架构链路(降级对齐 clock/canon)。"""
    try:
        await extract_cast_profiles(db, project)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — 画像提炼是增值步骤,不阻塞主流程
        db.rollback()
        logger.warning("核心人物画像提炼失败(已跳过): %s", exc)


class ArchitecturePatch(BaseModel):
    core_seed: str | None = None
    character_dynamics: str | None = None
    world_building: str | None = None
    plot_architecture: str | None = None


@router.patch("/{project_id}/architecture", response_model=ArchitectureOut)
async def patch_architecture(
    project_id: int, req: ArchitecturePatch, db: Session = Depends(get_db)
):
    """手动编辑架构(工作台直接改,版本+1)。手改层与下游回未拍板。"""
    project = _get_project_or_404(db, project_id)
    arch = project.architecture
    if arch is None:
        raise HTTPException(status_code=404, detail="尚未生成架构")
    updates = req.model_dump(exclude_none=True)
    if updates:
        for field, value in updates.items():
            setattr(arch, field, value)
        arch.version += 1
        # 确认链:手改的层不再是被拍板的那版,该层与下游回未拍板
        mark_layers_edited(arch, list(updates.keys()))
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
        dna=project.dna,
        directive=req.directive,
        open_ended=project.open_ended,
    )
    arch = save_architecture(db, project, result)
    db.commit()
    db.refresh(arch)
    await _extract_personas_safe(db, project)
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
        dna=project.dna,
                directive=req.directive,
                open_ended=project.open_ended,
                progress=lambda s: update_stage(job_id, s),
            )
            update_stage(job_id, "落库中")
            arch = save_architecture(session, project, result)
            session.commit()
            session.refresh(arch)
            update_stage(job_id, "提炼核心人物画像")
            await _extract_personas_safe(session, project)
            session.refresh(arch)
            finish_job(job_id, ArchitectureOut.model_validate(arch).model_dump())
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return {"job_id": job_id}


@router.get("/{project_id}/architecture", response_model=ArchitectureOut)
async def get_project_architecture(
    project_id: int, db: Session = Depends(get_db)
):
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(status_code=404, detail="尚未生成架构")
    return project.architecture


# =============== 架构闸门(docs/确认链 L2):逐层生成/拍板 ===============

async def _extract_personas_bg(project_id: int) -> None:
    """后台提炼核心人物画像(角色动力学层拍板后);自带会话,失败只告警。"""
    session = SessionLocal()
    try:
        project = session.get(Project, project_id)
        if project is not None:
            await _extract_personas_safe(session, project)
    finally:
        session.close()


def _require_upstream_confirmed(project: Project, layer: str) -> None:
    """闸门链式约束:生成/拍板第 N 层前,1..N-1 层必须全部已拍板。"""
    validate_layer(layer)
    state = layer_state(project.architecture)
    idx = LAYER_KEYS.index(layer)
    missing = [LAYER_LABEL[k] for k in LAYER_KEYS[:idx] if not state.get(k)]
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"上游 {'、'.join(missing)} 尚未拍板,先拍板上游再动这层",
        )


@router.post("/{project_id}/architecture/layer-async")
async def generate_architecture_layer_async(
    project_id: int,
    req: ArchitectureLayerRequest,
    db: Session = Depends(get_db),
):
    """逐层生成架构(架构闸门):只生成 req.layer 一层,吃已拍板的上游。

    directive = 带话重出(「节奏再慢一点」这类对该层的修改要求),与整本
    architecture-async 的研讨 directive 同一注入通道。产物落库后该层与
    下游回未拍板;角色动力学层完成后顺带后台提炼人物画像。
    """
    project = _get_project_or_404(db, project_id)
    try:
        validate_layer(req.layer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _require_upstream_confirmed(project, req.layer)
    # 防重复提交:同项目同层任务已在跑 → 复用
    for jid, _job in list_running(f"arch-layer-{project_id}"):
        return {"job_id": jid}
    job_id = create_job(f"arch-layer-{project_id}")
    layer = req.layer
    directive = req.directive
    tendency = req.tendency

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            arch = project.architecture
            text = await generate_architecture_layer(
                layer=layer,
                topic=project.topic,
                genre=project.genre,
                number_of_chapters=project.target_chapters,
                word_number=project.target_words_per_chapter,
                concept=project.concept,
                tendency=tendency,
                global_tendency=project.global_tendency,
                directive=directive,
                dna=project.dna,
                open_ended=project.open_ended,
                core_seed=arch.core_seed if arch else "",
                character_dynamics=arch.character_dynamics if arch else "",
                world_building=arch.world_building if arch else "",
            )
            update_stage(job_id, "落库中")
            arch = save_architecture_layer(session, project, layer, text)
            session.commit()
            session.refresh(arch)
            if layer == "character_dynamics":
                update_stage(job_id, "提炼核心人物画像")
                await _extract_personas_safe(session, project)
            finish_job(job_id, ArchitectureOut.model_validate(arch).model_dump())
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    fire_and_track(runner())
    return {"job_id": job_id}


@router.post("/{project_id}/architecture/confirm", response_model=ArchitectureOut)
async def confirm_project_architecture_layer(
    project_id: int,
    req: ArchitectureConfirmRequest,
    db: Session = Depends(get_db),
):
    """拍板/撤回架构的指定一层(同步,无 LLM 调用)。

    拍板要求上游全已拍板;撤回级联作废下游。角色动力学层拍板后后台
    提炼人物画像(不阻塞确认响应)。
    """
    project = _get_project_or_404(db, project_id)
    try:
        arch = confirm_architecture_layer(db, project, req.layer, req.confirmed)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(arch)
    if req.layer == "character_dynamics" and req.confirmed:
        fire_and_track(_extract_personas_bg(project_id))
    return arch


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
