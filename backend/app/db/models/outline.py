# app/db/models/outline.py
"""章节大纲 + 大纲版本历史(大纲级联引擎依赖)。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class Outline(Base, TimestampMixin):
    """每章一行,可独立编辑。字段借鉴雪花写作法章节蓝图。

    content_hash 用于 diff 判断是否变更;current_version 配合 OutlineVersion。
    """

    __tablename__ = "outlines"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_number: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    chapter_role: Mapped[str] = mapped_column(String(100), default="")
    chapter_purpose: Mapped[str] = mapped_column(Text, default="")
    suspense_level: Mapped[str] = mapped_column(String(50), default="")
    foreshadowing: Mapped[str] = mapped_column(Text, default="")
    plot_twist_level: Mapped[str] = mapped_column(String(50), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    characters_involved: Mapped[list[Any]] = mapped_column(JSON, default=list)
    key_items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    scene_location: Mapped[str] = mapped_column(String(200), default="")
    # 内容指纹,级联引擎用它判断本章大纲是否真的变了
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    current_version: Mapped[int] = mapped_column(Integer, default=1)


class OutlineVersion(Base):
    """大纲版本快照,支撑改动 diff 与回溯。"""

    __tablename__ = "outline_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    outline_id: Mapped[int] = mapped_column(
        ForeignKey("outlines.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # minor(小改) / major(大改) —— 决定是否触发级联
    change_type: Mapped[str] = mapped_column(String(10), default="minor")
    change_summary: Mapped[str] = mapped_column(Text, default="")
