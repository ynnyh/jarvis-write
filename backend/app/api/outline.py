# app/api/outline.py
# -*- coding: utf-8 -*-
"""大纲编辑与级联接口(核心差异化能力)。

PUT  /api/projects/{id}/outlines/{n}          编辑大纲 → diff 分级 → 版本快照
POST /api/projects/{id}/outlines/{n}/impact   下游影响分析(只分析不执行)
POST /api/projects/{id}/outlines/cascade      用户确认后级联重生成勾选的章节
GET  /api/projects/{id}/outlines/{n}/versions 版本历史
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Outline, OutlineVersion, Project
from app.db.session import get_db
from app.engines.cascade import (
    analyze_impact,
    apply_outline_edit,
    cascade_regenerate,
)
from app.schemas.project import OutlineOut
from app.schemas.tendency import Tendency

router = APIRouter(prefix="/api/projects/{project_id}/outlines", tags=["outline"])


class OutlineUpdate(BaseModel):
    """所有字段可选,只传要改的。"""

    title: str | None = None
    chapter_role: str | None = None
    chapter_purpose: str | None = None
    suspense_level: str | None = None
    foreshadowing: str | None = None
    plot_twist_level: str | None = None
    summary: str | None = None
    characters_involved: list[Any] | None = None
    key_items: list[Any] | None = None
    scene_location: str | None = None


class EditResult(BaseModel):
    status: str
    change_type: str | None
    change_summary: str
    changed_fields: list[str]
    own_chapter_stale: bool
    needs_impact_analysis: bool
    outline: OutlineOut


class ImpactItem(BaseModel):
    chapter_number: int
    reason: str
    action: str = "regenerate"


class ImpactReport(BaseModel):
    source_chapter: int
    affected: list[ImpactItem]
    overall: str


class CascadeRequest(BaseModel):
    source_chapter: int
    chapter_numbers: list[int] = Field(description="用户勾选要重生成的章节")
    reasons: dict[int, str] = Field(default_factory=dict)
    tendency: Tendency = Field(default_factory=dict)


class CascadeResult(BaseModel):
    updated: list[int]
    stale_chapters: list[int]
    warnings: list[str]
    outlines: list[OutlineOut]


class VersionOut(BaseModel):
    version: int
    change_type: str
    change_summary: str
    snapshot: dict

    model_config = {"from_attributes": True}


def _project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    return p


def _outline(db: Session, project_id: int, n: int) -> Outline:
    o = (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )
    if o is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章大纲不存在")
    return o


@router.put("/{chapter_number}", response_model=EditResult)
async def edit_outline(
    project_id: int,
    chapter_number: int,
    req: OutlineUpdate,
    db: Session = Depends(get_db),
):
    """编辑大纲。major 改动会提示做影响分析(needs_impact_analysis=true)。"""
    _project(db, project_id)
    outline = _outline(db, project_id, chapter_number)
    result = await apply_outline_edit(
        db, outline, req.model_dump(exclude_none=True)
    )
    db.commit()
    return EditResult(
        **result, outline=OutlineOut.model_validate(outline, from_attributes=True)
    )


@router.post("/{chapter_number}/impact", response_model=ImpactReport)
async def impact(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """分析最新一次改动对下游的影响。只分析,不改任何数据。"""
    project = _project(db, project_id)
    outline = _outline(db, project_id, chapter_number)
    result = await analyze_impact(db, project, outline)
    return ImpactReport(
        source_chapter=result["source_chapter"],
        overall=result["overall"],
        affected=[ImpactItem(**a) for a in result["affected"]],
    )


@router.post("/cascade", response_model=CascadeResult)
async def cascade(
    project_id: int, req: CascadeRequest, db: Session = Depends(get_db)
):
    """级联重生成用户勾选的章节大纲(用户拍板后才调用)。"""
    project = _project(db, project_id)
    try:
        result = await cascade_regenerate(
            db,
            project,
            req.source_chapter,
            req.chapter_numbers,
            reasons=req.reasons,
            tendency=req.tendency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    outlines = (
        db.query(Outline)
        .filter(
            Outline.project_id == project_id,
            Outline.chapter_number.in_(result["updated"]),
        )
        .order_by(Outline.chapter_number)
        .all()
    )
    return CascadeResult(
        **result,
        outlines=[OutlineOut.model_validate(o, from_attributes=True) for o in outlines],
    )


@router.get("/{chapter_number}/versions", response_model=list[VersionOut])
async def versions(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    _project(db, project_id)
    outline = _outline(db, project_id, chapter_number)
    return list(
        db.query(OutlineVersion)
        .filter(OutlineVersion.outline_id == outline.id)
        .order_by(OutlineVersion.version)
    )
