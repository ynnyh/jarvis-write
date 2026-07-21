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

from app.api.deps import get_project_or_404
from app.auth import get_current_user
from app.db.models import Chapter, Project
from app.db.session import SessionLocal, get_db
from app.engines.pipeline.chapter import generate_chapter
from app.engines.polish import ai_flavor_report
from app.jobs import create_job, fail_job, finish_job, update_stage
from app.schemas.tendency import Tendency

router = APIRouter(
    prefix="/api/projects/{project_id}/chapters",
    tags=["chapters"],
    dependencies=[Depends(get_current_user)],
)


class GenerateChapterRequest(BaseModel):
    tendency: Tendency = Field(default_factory=dict)
    # 重写时的修改意见(可选,最长 500 字;首次生成传了也会被引擎忽略)
    revision: str = Field(default="", max_length=500)


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
    """生成结果:正文 + 一致性检查问题 + 圣经抽取统计 + AI 味指数。"""

    consistency_issues: list[dict] = []
    extraction_stats: dict = {}
    # AI 味指数:纯规则统计(不调 LLM,零额外耗时),生成完成即给出
    ai_flavor: dict = {}


def _flavor_dict(text: str) -> dict:
    report = ai_flavor_report(text)
    return {"score": report.score, "summary": report.summary()}


@router.post("/{chapter_number}/generate", response_model=GenerateChapterResponse)
async def generate(
    project_id: int,
    chapter_number: int,
    req: GenerateChapterRequest,
    db: Session = Depends(get_db),
):
    """生成一章(草稿/定稿/检查/抽取/摘要,多次 LLM 调用,耗时较长)。"""
    project = get_project_or_404(db, project_id)
    try:
        chapter, issues, stats = await generate_chapter(
            db, project, chapter_number, req.tendency,
            revision=req.revision.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    resp = GenerateChapterResponse.model_validate(chapter, from_attributes=True)
    resp.consistency_issues = issues
    resp.extraction_stats = stats
    resp.ai_flavor = _flavor_dict(chapter.final_content)
    return resp


@router.post("/{chapter_number}/generate-async")
async def generate_async(
    project_id: int,
    chapter_number: int,
    req: GenerateChapterRequest,
    db: Session = Depends(get_db),
):
    """异步生成:立即返回 job_id,前端轮询 /api/jobs/{job_id} 看五段进度。"""
    get_project_or_404(db, project_id)  # 先校验存在
    job_id = create_job(f"chapter-{project_id}-{chapter_number}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            chapter, issues, stats = await generate_chapter(
                session, project, chapter_number, req.tendency,
                progress=lambda s: update_stage(job_id, s),
                revision=req.revision.strip(),
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
                "ai_flavor": _flavor_dict(chapter.final_content),
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


class EditContentRequest(BaseModel):
    final_content: str = Field(min_length=1)


@router.put("/{chapter_number}/content", response_model=ChapterDetail)
async def edit_content(
    project_id: int,
    chapter_number: int,
    req: EditContentRequest,
    db: Session = Depends(get_db),
):
    """手动编辑正文:立即保存。保存后请调 re-extract-async 同步圣经/摘要。"""
    get_project_or_404(db, project_id)
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
    ch.final_content = req.final_content.strip()
    ch.word_count = len(ch.final_content)
    ch.status = "finalized"
    db.commit()
    return ch


@router.post("/{chapter_number}/re-extract-async")
async def re_extract_async(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """手改正文后:重抽取(幂等,先清旧账)→ 重建下游摘要 → 更新向量库。"""
    get_project_or_404(db, project_id)
    job_id = create_job(f"re-extract-{project_id}-{chapter_number}")

    async def runner() -> None:
        from app.engines.consistency.extractor import extract_and_apply
        from app.engines.memory import ChapterMemory
        from app.engines.pipeline.chapter import rebuild_summaries_after

        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            ch = (
                session.query(Chapter)
                .filter(
                    Chapter.project_id == project_id,
                    Chapter.chapter_number == chapter_number,
                )
                .first()
            )
            update_stage(job_id, "1/3 重新抽取状态(清旧账)")
            stats = await extract_and_apply(
                session, project_id, chapter_number, ch.final_content
            )
            update_stage(job_id, "2/3 重建下游前情摘要")
            rebuilt = await rebuild_summaries_after(
                session, project, chapter_number,
                progress=lambda s: update_stage(job_id, f"2/3 {s}"),
            )
            update_stage(job_id, "3/3 更新向量库")
            await ChapterMemory(project_id).add_chapter(
                chapter_number, ch.final_content
            )
            session.commit()
            finish_job(job_id, {"extraction_stats": stats, "summaries_rebuilt": rebuilt})
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}


@router.get("", response_model=list[ChapterBrief])
async def list_chapters(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    return list(
        db.query(Chapter)
        .filter(Chapter.project_id == project_id)
        .order_by(Chapter.chapter_number)
    )


@router.get("/{chapter_number}", response_model=ChapterDetail)
async def get_chapter(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    get_project_or_404(db, project_id)
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
