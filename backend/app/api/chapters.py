# app/api/chapters.py
# -*- coding: utf-8 -*-
"""章节接口:逐章生成与查看。

POST /api/projects/{id}/chapters/{n}/generate   生成第 n 章(草稿→定稿→摘要→入库)
GET  /api/projects/{id}/chapters                章节列表(不含正文,轻量)
GET  /api/projects/{id}/chapters/{n}            单章详情(含正文)
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.models import Chapter, Project
from app.db.session import SessionLocal, get_db
from app.engines.pipeline.chapter import generate_chapter
from app.jobs import create_job, fail_job, finish_job, update_stage
from app.schemas.tendency import Tendency

router = APIRouter(prefix="/api/projects/{project_id}/chapters", tags=["chapters"])


class GenerateChapterRequest(BaseModel):
    tendency: Tendency = Field(default_factory=dict)


class ChapterBrief(BaseModel):
    chapter_number: int
    status: str
    word_count: int
    is_stale: bool

    model_config = {"from_attributes": True}


class ChapterDetail(ChapterBrief):
    draft_content: str
    final_content: str
    outline_version_used: int


class GenerateChapterResponse(ChapterDetail):
    """生成结果:正文 + 一致性检查问题 + 圣经抽取统计。"""

    consistency_issues: list[dict] = []
    extraction_stats: dict = {}


def _project(db: Session, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")
    return p


@router.post("/{chapter_number}/generate", response_model=GenerateChapterResponse)
async def generate(
    project_id: int,
    chapter_number: int,
    req: GenerateChapterRequest,
    db: Session = Depends(get_db),
):
    """生成一章(草稿/定稿/检查/抽取/摘要,多次 LLM 调用,耗时较长)。"""
    project = _project(db, project_id)
    try:
        chapter, issues, stats = await generate_chapter(
            db, project, chapter_number, req.tendency
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    resp = GenerateChapterResponse.model_validate(chapter, from_attributes=True)
    resp.consistency_issues = issues
    resp.extraction_stats = stats
    return resp


@router.post("/{chapter_number}/generate-async")
async def generate_async(
    project_id: int,
    chapter_number: int,
    req: GenerateChapterRequest,
    db: Session = Depends(get_db),
):
    """异步生成:立即返回 job_id,前端轮询 /api/jobs/{job_id} 看五段进度。"""
    _project(db, project_id)  # 先校验存在
    job_id = create_job(f"chapter-{project_id}-{chapter_number}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            chapter, issues, stats = await generate_chapter(
                session, project, chapter_number, req.tendency,
                progress=lambda s: update_stage(job_id, s),
            )
            session.commit()
            finish_job(job_id, {
                "chapter_number": chapter.chapter_number,
                "word_count": chapter.word_count,
                "status": chapter.status,
                "final_content": chapter.final_content,
                "draft_content": chapter.draft_content,
                "is_stale": chapter.is_stale,
                "outline_version_used": chapter.outline_version_used,
                "consistency_issues": issues,
                "extraction_stats": stats,
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


@router.get("", response_model=list[ChapterBrief])
async def list_chapters(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return list(
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )


@router.get("/{chapter_number}", response_model=ChapterDetail)
async def get_chapter(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    _project(db, project_id)
    ch = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project_id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {chapter_number} 章尚未生成")
    return ch
