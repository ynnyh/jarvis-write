# app/db/models/chapter.py
"""章节正文。is_stale 是大纲级联引擎的关键失配标记。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
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
    # 最近一次主审结果快照(JSON):四维分/短评/建议/达标/回炉轮数/来源/时间 +
    # 正文指纹(content_hash)。编辑部打开时指纹一致才回显,正文改动自动失效,
    # 避免用户对着已改过的正文重复点「请主编审读」。
    review_snapshot: Mapped[str] = mapped_column(Text, default="")


class ChapterVersion(Base):
    """章节正文的历史快照,支撑「重生成/润色/手改」前的新旧对比与回滚。

    与 OutlineVersion 对称:每次覆盖 chapters.final_content 前,先把当前正文
    存成一版快照。空章(无正文)不存。source 记录这一版是被什么操作顶替的:
      generated —— 被重新生成顶替(重写)
      polished  —— 被整章润色顶替
      edited    —— 被手动编辑顶替
      restored  —— 被回滚操作顶替(回滚前的当前版也留痕)
    """

    __tablename__ = "chapter_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    draft_content: Mapped[str] = mapped_column(Text, default="")
    final_content: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="generated")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
