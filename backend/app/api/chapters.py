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
from app.chapter_versions import snapshot_chapter
from app.db.models import Chapter, ChapterVersion, Project
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
    """生成结果卡的 AI 味字段:score/summary + 分类得分明细(hover 展示用)。"""
    report = ai_flavor_report(text)
    return {
        "score": report.score,
        "summary": report.summary(),
        "categories": report.categories,
    }


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
    # 覆盖前留一版:手改后悔可回退到编辑前
    snapshot_chapter(db, ch, source="edited")
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


# ---------- 正文版本历史:新旧对比与回滚 ----------


class ChapterVersionBrief(BaseModel):
    """版本列表项(不含全文,轻量)。"""

    id: int
    version: int
    source: str
    word_count: int
    created_at: str

    model_config = {"from_attributes": True}


class ChapterVersionDetail(ChapterVersionBrief):
    final_content: str
    draft_content: str


def _get_chapter_or_404(db: Session, project_id: int, n: int) -> Chapter:
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
        .first()
    )
    if ch is None:
        raise HTTPException(status_code=404, detail=f"第 {n} 章尚未生成")
    return ch


def _version_brief(v: ChapterVersion) -> ChapterVersionBrief:
    return ChapterVersionBrief(
        id=v.id, version=v.version, source=v.source,
        word_count=v.word_count, created_at=v.created_at.isoformat(),
    )


@router.get("/{chapter_number}/versions", response_model=list[ChapterVersionBrief])
async def list_versions(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """本章的历史正文版本(最新在前,不含全文)。每条是一次被覆盖前的快照。"""
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    rows = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.chapter_id == ch.id)
        .order_by(ChapterVersion.version.desc())
        .all()
    )
    return [_version_brief(v) for v in rows]


@router.get(
    "/{chapter_number}/versions/{version_id}", response_model=ChapterVersionDetail
)
async def get_version(
    project_id: int, chapter_number: int, version_id: int,
    db: Session = Depends(get_db),
):
    """取某个历史版本的全文(用于新旧对比)。"""
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    v = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.id == version_id, ChapterVersion.chapter_id == ch.id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    return ChapterVersionDetail(
        id=v.id, version=v.version, source=v.source, word_count=v.word_count,
        created_at=v.created_at.isoformat(),
        final_content=v.final_content, draft_content=v.draft_content,
    )


@router.post("/{chapter_number}/versions/{version_id}/restore",
             response_model=ChapterDetail)
async def restore_version(
    project_id: int, chapter_number: int, version_id: int,
    db: Session = Depends(get_db),
):
    """回滚到某历史版本:先把当前正文留一版(source=restored),再换回该版正文。

    回滚只改正文;圣经/摘要仍停留在被弃版本,前端须随后调 re-extract-async 同步
    (与手动编辑保存一致)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    v = (
        db.query(ChapterVersion)
        .filter(ChapterVersion.id == version_id, ChapterVersion.chapter_id == ch.id)
        .first()
    )
    if v is None:
        raise HTTPException(status_code=404, detail="版本不存在")
    if not v.final_content:
        raise HTTPException(status_code=400, detail="该版本无正文,无法回滚")
    snapshot_chapter(db, ch, source="restored")
    ch.final_content = v.final_content
    ch.draft_content = v.draft_content or ch.draft_content
    ch.word_count = len(ch.final_content)
    ch.status = "finalized"
    db.commit()
    return ch


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
