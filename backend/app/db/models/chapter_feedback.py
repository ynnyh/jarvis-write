# app/db/models/chapter_feedback.py
"""章节反馈:用户对生成结果的最小归因闭环(docs/17 M2)。

一行 = 一个用户对一章的表态(可改判,upsert)。差评带四桶分类,
与 llm_usage 截断率 / review_snapshot 降级信号交叉,回答
「差评是模型能力问题,还是截断/降级/注入问题」——没这层就只能猜。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ChapterFeedback(Base, TimestampMixin):
    __tablename__ = "chapter_feedback"
    __table_args__ = (
        UniqueConstraint("chapter_id", "user_id", name="uq_feedback_chapter_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # good / bad
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    # 差评四桶(docs/17):style_flavor / fact_error / pacing / format_trunc;好评为 []
    categories: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    # 选填备注:自由文本,只给作者/管理员看
    comment: Mapped[str] = mapped_column(Text, default="")
    # 反馈时的正文指纹:正文改了旧反馈自动判失效(对齐 chapter_issues 口径)
    content_hash: Mapped[str] = mapped_column(String(16), default="")
