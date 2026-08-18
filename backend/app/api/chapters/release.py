# app/api/chapters/release.py
# -*- coding: utf-8 -*-
"""人工审核通过与 quarantined 放行(补走被跳过的章后链路)。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.db.models import Chapter, ChapterIssue, Project
from app.db.session import SessionLocal, get_db
from app.engines.common import get_outline
from app.engines.pipeline.chapter import (
    apply_chapter_tail,
    rebuild_summaries_after,
    update_style_memo,
)
from app.jobs import create_job, fail_job, finish_job, list_running, update_stage

from ._common import ChapterDetail, _fill_handoff, _get_chapter_or_404

router = APIRouter()


@router.post("/{chapter_number}/approve", response_model=ChapterDetail)
async def approve_chapter(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """人工审核通过(docs/08 §5.5):pending_review → approved。

    幂等:已 approved 重复调用返回 200。quarantined 不可 approve——
    需先 gate-release 放行或重写通过门禁(返回 400)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    if ch.status == "approved":
        return ch  # 幂等
    if ch.status == "quarantined":
        raise HTTPException(
            status_code=400,
            detail="该章被一致性门禁隔离(quarantined),请先放行或重写通过后再审核",
        )
    if ch.status != "pending_review":
        raise HTTPException(
            status_code=400,
            detail=f"当前状态({ch.status})不能审核通过,仅待审核(pending_review)章节可 approve",
        )
    ch.status = "approved"
    db.commit()
    resp = ChapterDetail.model_validate(ch, from_attributes=True)
    _fill_handoff(db, ch, resp)
    return resp


@router.post("/{chapter_number}/gate-release")
async def gate_release(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """quarantined 放行:确认忽略全部 blocker,补走被跳过的章后链路。

    后台任务按序执行:open issues 标 ignored → 状态回 pending_review(待人工
    审核,docs/08 §5.5)→ 章后抽取(写圣经)→ 滚动摘要 → 章末契约 → 文风备忘 →
    重建下游摘要。
    任一步失败整体回滚,章节保持 quarantined(不会放出"半同步"状态)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    if ch.status != "quarantined":
        raise HTTPException(status_code=400, detail="该章不在隔离(quarantined)状态,无需放行")
    busy = (
        list_running(f"chapter-{project_id}-")
        + list_running(f"re-extract-{project_id}-")
        + list_running(f"gate-release-{project_id}-")
    )
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"已有章节任务在进行中({busy[0][1]['stage']}),等它完成再放行。",
        )
    job_id = create_job(f"gate-release-{project_id}-{chapter_number}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            ch2 = (
                session.query(Chapter)
                .filter(
                    Chapter.project_id == project_id,
                    Chapter.chapter_number == chapter_number,
                )
                .first()
            )
            if ch2 is None or ch2.status != "quarantined":
                raise ValueError("该章不在隔离(quarantined)状态,无需放行")
            update_stage(job_id, "1/3 标记放行(issues 转 ignored,状态回 pending_review)")
            session.query(ChapterIssue).filter(
                ChapterIssue.chapter_id == ch2.id,
                ChapterIssue.status == "open",
            ).update({"status": "ignored"}, synchronize_session=False)
            ch2.status = "pending_review"
            session.commit()
            # 补走 quarantined 时跳过的章后链路(抽取/摘要/契约,与生成主流程同一实现)
            outline = get_outline(session, project_id, chapter_number)
            stats = await apply_chapter_tail(
                session, project, ch2, chapter_number, ch2.final_content,
                outline.title if outline else "",
                report=lambda s: update_stage(job_id, f"2/3 {s}"),
            )
            update_stage(job_id, "3/3 文风备忘与下游摘要重建")
            await update_style_memo(session, project, chapter_number, ch2.final_content)
            rebuilt = await rebuild_summaries_after(
                session, project, chapter_number,
                progress=lambda s: update_stage(job_id, f"3/3 {s}"),
            )
            session.commit()
            finish_job(job_id, {
                "chapter_number": chapter_number,
                "status": "pending_review",
                "extraction_stats": stats,
                "summaries_rebuilt": rebuilt,
            })
        except Exception as exc:  # noqa: BLE001 — 失败整体回滚,保持 quarantined
            session.rollback()
            fail_job(job_id, str(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}
