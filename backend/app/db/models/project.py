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
    # 归属用户(阶段 8 多用户隔离);存量数据迁移时归到 admin
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(100), default="")
    target_chapters: Mapped[int] = mapped_column(Integer, default=30)
    target_words_per_chapter: Mapped[int] = mapped_column(Integer, default=3000)
    # 全局倾向:标签组合 JSON,如 {"pace": "快节奏", "tone": ["热血"], ...}
    global_tendency: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 结构化故事概念(灵感工坊产出):logline/hook/twist/protagonist/conflict/setting
    # 六字段 JSON,喂养架构生成的核心种子;可空(老项目只有 topic 一句话)。
    # 见 app/schemas/concept.py。topic 保留为 logline 的镜像,下游 title/简介仍读 topic。
    concept: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 书籍简介(网文风格 150-300 字,可 AI 生成也可手改);老库由迁移补列
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 起步流进度:创建即建草稿,记录停在哪一步(idea/tone/title/scale/launch);
    # 空/NULL = 起步完成(老项目天然视为完成)。列表页据此显示"继续创建"。
    setup_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 灵感对话记录([{role, content}, ...]):对话式捏概念的持久化,刷新不丢
    chat_log: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
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
