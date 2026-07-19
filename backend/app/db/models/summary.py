# app/db/models/summary.py
"""章节滚动摘要:每章定稿后,把剧情压缩合并进"前情摘要"。

第 N 章的行存的是「截至第 N 章的完整前情摘要」,
生成第 N+1 章时取 chapter_number=N 的行注入。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ChapterSummary(Base, TimestampMixin):
    __tablename__ = "chapter_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_number: Mapped[int] = mapped_column(Integer, index=True)
    rolling_summary: Mapped[str] = mapped_column(Text, default="")
