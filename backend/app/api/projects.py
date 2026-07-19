# app/api/projects.py
# -*- coding: utf-8 -*-
"""项目管理 + 生成流水线接口。

阶段 1 核心链路:
  POST /api/projects                          建项目(带全局倾向)
  POST /api/projects/{id}/architecture        雪花四步生成顶层架构
  POST /api/projects/{id}/blueprint           分块生成章节蓝图并落库
  GET  /api/projects/{id}/outlines            查看章节目录
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import assert_project_owner, current_user_id, get_current_user
from app.db.models import Outline, Project, User
from app.db.session import get_db
from app.engines.pipeline.architecture import generate_architecture, save_architecture
from app.engines.pipeline.blueprint import generate_blueprint, save_blueprint
from app.schemas.project import (
    ArchitectureOut,
    GenerateArchitectureRequest,
    GenerateBlueprintRequest,
    GenerateBlueprintResponse,
    OutlineOut,
    ProjectCreate,
    ProjectOut,
)

router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(get_current_user)],
)


def _get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    assert_project_owner(project)
    return project


@router.post("", response_model=ProjectOut)
async def create_project(req: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    project = Project(
        user_id=current_user_id.get(),
        title=req.title,
        topic=req.topic,
        genre=req.genre,
        target_chapters=req.target_chapters,
        target_words_per_chapter=req.target_words_per_chapter,
        global_tendency=req.global_tendency,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    uid = current_user_id.get()
    return list(
        db.query(Project)
        .filter(Project.user_id == uid)
        .order_by(Project.id.desc())
    )


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, db: Session = Depends(get_db)) -> Project:
    return _get_project_or_404(db, project_id)


class ProjectPatch(BaseModel):
    title: str | None = None
    topic: str | None = None
    genre: str | None = None
    target_chapters: int | None = None
    target_words_per_chapter: int | None = None
    global_tendency: dict | None = None


@router.patch("/{project_id}", response_model=ProjectOut)
async def patch_project(
    project_id: int, req: ProjectPatch, db: Session = Depends(get_db)
) -> Project:
    """修改项目信息(灵感区确定主题、调整全局倾向等)。"""
    project = _get_project_or_404(db, project_id)
    for field, value in req.model_dump(exclude_none=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


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
        tendency=req.tendency,
        global_tendency=project.global_tendency,
    )
    arch = save_architecture(db, project, result)
    db.commit()
    db.refresh(arch)
    return arch


@router.get("/{project_id}/architecture", response_model=ArchitectureOut)
async def get_project_architecture(
    project_id: int, db: Session = Depends(get_db)
):
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(status_code=404, detail="尚未生成架构")
    return project.architecture


@router.post("/{project_id}/blueprint", response_model=GenerateBlueprintResponse)
async def generate_project_blueprint(
    project_id: int,
    req: GenerateBlueprintRequest,
    db: Session = Depends(get_db),
):
    """基于已有架构,分块生成全书章节蓝图并落库。"""
    project = _get_project_or_404(db, project_id)
    if project.architecture is None:
        raise HTTPException(
            status_code=400, detail="请先生成顶层架构(POST .../architecture)"
        )

    from app.engines.pipeline.architecture import ArchitectureResult

    arch_text = ArchitectureResult(
        core_seed=project.architecture.core_seed,
        character_dynamics=project.architecture.character_dynamics,
        world_building=project.architecture.world_building,
        plot_architecture=project.architecture.plot_architecture,
    ).full_text

    chapters, warnings = await generate_blueprint(
        novel_architecture=arch_text,
        number_of_chapters=project.target_chapters,
        tendency=req.tendency,
        global_tendency=project.global_tendency,
    )
    outlines = save_blueprint(db, project, chapters)
    db.commit()
    return GenerateBlueprintResponse(
        outlines=[OutlineOut.model_validate(o) for o in outlines],
        warnings=warnings,
    )


@router.get("/{project_id}/outlines", response_model=list[OutlineOut])
async def list_outlines(
    project_id: int, db: Session = Depends(get_db)
) -> list[Outline]:
    _get_project_or_404(db, project_id)
    return list(
        db.query(Outline)
        .filter(Outline.project_id == project_id)
        .order_by(Outline.chapter_number)
    )
