# app/db/models/chapter.py
"""章节正文。is_stale 是大纲级联引擎的关键失配标记。"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Chapter(Base, TimestampMixin):
    """一章正文。

    outline_version_used:生成时基于的大纲版本号。
    is_stale:大纲改了但正文没重写 → True,前端红点提醒"是否重写"。
    """

    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    outline_id: Mapped[int | None] = mapped_column(
        ForeignKey("outlines.id", ondelete="SET NULL"), nullable=True
    )
    chapter_number: Mapped[int] = mapped_column(Integer, index=True)
    draft_content: Mapped[str] = mapped_column(Text, default="")
    final_content: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    outline_version_used: Mapped[int] = mapped_column(Integer, default=0)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    # empty / drafting / drafted / finalized / stale
    status: Mapped[str] = mapped_column(String(20), default="empty")
