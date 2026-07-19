# app/db/models/project.py
"""小说项目 + 顶层架构(雪花写作法产出)。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class Project(Base, TimestampMixin):
    """一部小说。global_tendency 存全局倾向标签组合(见 04-tag-system)。"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(100), default="")
    target_chapters: Mapped[int] = mapped_column(Integer, default=30)
    target_words_per_chapter: Mapped[int] = mapped_column(Integer, default=3000)
    # 全局倾向:标签组合 JSON,如 {"pace": "快节奏", "tone": ["热血"], ...}
    global_tendency: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # draft / outlining / writing / done
    status: Mapped[str] = mapped_column(String(20), default="draft")

    architecture: Mapped["Architecture | None"] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )


class Architecture(Base):
    """顶层架构:雪花写作法四步产出。可改,故带 version。"""

    __tablename__ = "architecture"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    core_seed: Mapped[str] = mapped_column(Text, default="")
    character_dynamics: Mapped[str] = mapped_column(Text, default="")
    world_building: Mapped[str] = mapped_column(Text, default="")
    plot_architecture: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)

    project: Mapped[Project] = relationship(back_populates="architecture")
