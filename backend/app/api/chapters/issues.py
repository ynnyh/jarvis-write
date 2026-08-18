# app/api/chapters/issues.py
# -*- coding: utf-8 -*-
"""一致性门禁:问题清单、状态流转、按问题修订。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.db.models import ChapterIssue, Project
from app.db.session import SessionLocal, get_db
from app.engines.pipeline.chapter import generate_chapter
from app.engines.pipeline.handoff import handoff_payload
from app.jobs import create_job, fail_job, finish_job, list_running, normalize_job_error, update_stage

from ._common import _flavor_dict, _gate_payload, _get_chapter_or_404

router = APIRouter()


class ChapterIssueOut(BaseModel):
    """chapter_issues 记录(docs/08 §5.7):门禁/预审/诊断产出的一致性问题。"""

    id: int
    source: str
    severity: str
    issue_type: str
    description: str
    evidence: str
    suggestion: str
    status: str
    created_at: str


class IssuePatchRequest(BaseModel):
    """问题状态流转(docs/08 §5.5):open → resolved / ignored(单向)。"""

    status: str = Field(min_length=1)


def _issue_out(r: ChapterIssue) -> ChapterIssueOut:
    return ChapterIssueOut(
        id=r.id, source=r.source, severity=r.severity, issue_type=r.issue_type,
        description=r.description, evidence=r.evidence, suggestion=r.suggestion,
        status=r.status, created_at=r.created_at.isoformat(),
    )


def _get_issue_or_404(db: Session, chapter_id: int, issue_id: int) -> ChapterIssue:
    issue = (
        db.query(ChapterIssue)
        .filter(ChapterIssue.id == issue_id, ChapterIssue.chapter_id == chapter_id)
        .first()
    )
    if issue is None:
        raise HTTPException(status_code=404, detail="问题记录不存在")
    return issue


@router.get("/{chapter_number}/issues", response_model=list[ChapterIssueOut])
async def list_issues(
    project_id: int, chapter_number: int, db: Session = Depends(get_db)
):
    """本章的一致性问题清单(最新在前,含 open/resolved/ignored 各状态)。"""
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    rows = (
        db.query(ChapterIssue)
        .filter(ChapterIssue.chapter_id == ch.id)
        .order_by(ChapterIssue.id.desc())
        .all()
    )
    return [
        ChapterIssueOut(
            id=r.id, source=r.source, severity=r.severity, issue_type=r.issue_type,
            description=r.description, evidence=r.evidence, suggestion=r.suggestion,
            status=r.status, created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.patch("/{chapter_number}/issues/{issue_id}", response_model=ChapterIssueOut)
async def patch_issue(
    project_id: int, chapter_number: int, issue_id: int,
    req: IssuePatchRequest, db: Session = Depends(get_db),
):
    """单条问题状态流转:open → resolved(已人工改完)/ ignored(确认忽略)。

    单向流转:已是 resolved/ignored 的不可再改(ignored 的失效语义照旧——
    正文指纹变化后门禁重建会清除旧 ignored,同一矛盾重新报警)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    issue = _get_issue_or_404(db, ch.id, issue_id)
    if req.status not in ("resolved", "ignored"):
        raise HTTPException(status_code=400, detail="status 只能是 resolved 或 ignored")
    if issue.status != "open":
        raise HTTPException(
            status_code=400, detail=f"该问题已是 {issue.status} 状态,不可再流转"
        )
    issue.status = req.status
    db.commit()
    return _issue_out(issue)


@router.post("/{chapter_number}/issues/{issue_id}/apply-revision")
async def apply_issue_revision(
    project_id: int, chapter_number: int, issue_id: int,
    db: Session = Depends(get_db),
):
    """采纳单条问题的修正建议:拼成修订指令走重写链路(异步 job)。

    简化取舍:修订发起(本端点受理)即把该 issue 标 resolved;重写后门禁会重跑
    并重建 open 集,同类问题若未消除会以新的 open 记录回来(不会漏报,但
    resolved 标记不代表"已验证消除");且该 resolved 记录的正文指纹随重写失效,
    门禁重建时按既有指纹语义清除(与 ignored 的失效语义一致)。
    """
    get_project_or_404(db, project_id)
    ch = _get_chapter_or_404(db, project_id, chapter_number)
    issue = _get_issue_or_404(db, ch.id, issue_id)
    if issue.status != "open":
        raise HTTPException(
            status_code=400, detail=f"该问题已是 {issue.status} 状态,无需再修订"
        )
    if not (issue.suggestion or issue.description).strip():
        raise HTTPException(status_code=400, detail="该问题没有可用的修正建议")
    if not ch.final_content.strip():
        raise HTTPException(status_code=400, detail="本章尚无定稿正文,无法按问题修订")
    busy = (
        list_running(f"chapter-{project_id}-")
        + list_running(f"re-extract-{project_id}-")
        + list_running(f"gate-release-{project_id}-")
    )
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"已有章节任务在进行中({busy[0][1]['stage']}),等它完成再修订。",
        )
    # 修订指令:问题点 + 证据 + 修正建议(走 generate_chapter 的 revision 通道)
    revision = (
        f"针对一致性问题的修订:\n问题:{issue.description}"
        + (f"\n证据:{issue.evidence}" if issue.evidence else "")
        + (f"\n修正建议:{issue.suggestion}" if issue.suggestion else "")
    )[:500]
    # 简化语义:发起即标 resolved(见 docstring 的取舍说明)
    issue.status = "resolved"
    db.commit()
    job_id = create_job(f"chapter-{project_id}-{chapter_number}")

    async def runner() -> None:
        session = SessionLocal()
        try:
            project = session.get(Project, project_id)
            chapter, issues, stats, guard_result, review_result, preflight = (
                await generate_chapter(
                    session, project, chapter_number, None,
                    progress=lambda s: update_stage(job_id, s),
                    revision=revision,
                )
            )
            session.commit()
            handoff = handoff_payload(session, chapter)
            finish_job(job_id, {
                "chapter_number": chapter.chapter_number,
                "applied_issue_id": issue_id,
                "word_count": chapter.word_count,
                "status": chapter.status,
                "final_content": chapter.final_content,
                "draft_content": chapter.draft_content,
                "is_stale": chapter.is_stale,
                "outline_version_used": chapter.outline_version_used,
                "consistency_issues": issues,
                "extraction_stats": stats,
                "ai_flavor": _flavor_dict(chapter.final_content),
                "word_guard_action": guard_result.action,
                "split_info": guard_result.split_info,
                "review": review_result,
                "gate": _gate_payload(chapter, issues),
                "preflight": {"warnings": preflight},
                "handoff_contract": handoff["contract"],
                "handoff_extract_status": handoff["status"],
                "handoff_extract_error": handoff["error"],
            })
        except Exception as exc:  # noqa: BLE001 — 任务失败进 job 状态
            session.rollback()
            fail_job(job_id, normalize_job_error(exc)[:500])
        finally:
            session.close()

    asyncio.create_task(runner())
    return {"job_id": job_id}
