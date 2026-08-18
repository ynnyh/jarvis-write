# app/api/chapters/versions.py
# -*- coding: utf-8 -*-
"""正文版本历史:新旧对比与回滚。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_project_or_404
from app.chapter_versions import snapshot_chapter
from app.db.models import ChapterVersion
from app.db.session import get_db

from ._common import ChapterDetail, _get_chapter_or_404

router = APIRouter()


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
    # 回滚后的正文未经审核,回到待审核(docs/08 §5.5)
    ch.status = "pending_review"
    db.commit()
    return ch
