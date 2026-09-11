# app/api/chapters/feedback.py
# -*- coding: utf-8 -*-
"""章节反馈(docs/17 M2):用户对生成结果表态,差评带四桶分类。

POST /{chapter_number}/feedback   upsert(一人一章一条,可改判)
GET  /{chapter_number}/feedback   回显当前用户在该章的反馈

反馈是观测数据不是门禁:不参与任何卡口,只进 admin 聚合与交叉归因。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db.models import ChapterFeedback, User
from app.db.session import get_db
from app.engines.editorial import content_hash

from ._common import _get_chapter_or_404

router = APIRouter()

# 差评四桶(docs/17):埋点前先想清楚要回答什么问题——四桶对应已知四类风险
FEEDBACK_CATEGORIES = {"style_flavor", "fact_error", "pacing", "format_trunc"}


class FeedbackIn(BaseModel):
    """改判即覆盖:rating 必填;差评时 categories 至少选一桶建议(不强卡)。"""

    rating: str = Field(pattern="^(good|bad)$")
    categories: list[str] = Field(default_factory=list, max_length=8)
    comment: str = Field(default="", max_length=2000)


class FeedbackOut(BaseModel):
    rating: str
    categories: list[str]
    comment: str
    # 反馈时的正文指纹是否仍与当前正文一致:改稿后旧反馈自动判过期
    stale: bool
    created_at: str
    updated_at: str


def _validate_categories(raw: list[str]) -> list[str]:
    cleaned = [c.strip() for c in raw if c and c.strip()]
    unknown = [c for c in cleaned if c not in FEEDBACK_CATEGORIES]
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"未知的差评分类: {', '.join(unknown)}"
        )
    # 去重保序
    return list(dict.fromkeys(cleaned))


@router.post("/{chapter_number}/feedback", response_model=FeedbackOut)
async def upsert_feedback(
    project_id: int,
    chapter_number: int,
    body: FeedbackIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FeedbackOut:
    chapter = _get_chapter_or_404(db, project_id, chapter_number)
    if body.rating == "bad" and not body.comment.strip() and not body.categories:
        raise HTTPException(
            status_code=422, detail="差评请至少选一个分类或写一句备注"
        )
    categories = _validate_categories(body.categories) if body.rating == "bad" else []

    row = (
        db.query(ChapterFeedback)
        .filter(
            ChapterFeedback.chapter_id == chapter.id,
            ChapterFeedback.user_id == user.id,
        )
        .first()
    )
    if row is None:
        row = ChapterFeedback(chapter_id=chapter.id, user_id=user.id)
        db.add(row)
    row.rating = body.rating
    row.categories = categories
    row.comment = body.comment.strip()
    row.content_hash = content_hash(chapter.final_content or "")
    db.commit()
    db.refresh(row)
    return _to_out(row, chapter)


@router.get("/{chapter_number}/feedback", response_model=FeedbackOut | None)
async def get_my_feedback(
    project_id: int,
    chapter_number: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    chapter = _get_chapter_or_404(db, project_id, chapter_number)
    row = (
        db.query(ChapterFeedback)
        .filter(
            ChapterFeedback.chapter_id == chapter.id,
            ChapterFeedback.user_id == user.id,
        )
        .first()
    )
    if row is None:
        return None
    return _to_out(row, chapter)


def _to_out(row: ChapterFeedback, chapter) -> FeedbackOut:
    current = content_hash(chapter.final_content or "")
    return FeedbackOut(
        rating=row.rating,
        categories=list(row.categories or []),
        comment=row.comment,
        stale=bool(row.content_hash) and row.content_hash != current,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )
