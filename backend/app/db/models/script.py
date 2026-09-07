# app/db/models/script.py
# -*- coding: utf-8 -*-
"""剧本工坊:独立创作(从零写剧本)+ 小说改编(定稿章节 → 剧本)。

两张表:
- scripts:一部剧本(元信息 + 状态);
- script_episodes:集,每集一份剧本正文 + 大纲字段(集名/梗概/钩子)。

与漫剧工坊的分工:漫剧产「视觉制片手册」(提示词/分镜/出片),剧本工坊产
「文字剧本本身」——场景标题/动作行/对白,可直接导入专业编剧工具(Fountain 兼容)。
小说改编:source_project_id 指向源书,改编保留主线/人物弧/关键场景。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class Script(Base, TimestampMixin):
    """一部剧本。改编时 source_project_id 指向源小说,独立创作为空。"""

    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    genre: Mapped[str] = mapped_column(String(100), default="")
    # 一句话灵感/梗概
    logline: Mapped[str] = mapped_column(Text, default="")
    # 目标集数(改编时=源书章数换算的推荐集数,可改)
    target_episodes: Mapped[int] = mapped_column(Integer, default=12)
    # empty / outlining(分集大纲生成中)/ outlined / writing(逐集生成中)/ done
    status: Mapped[str] = mapped_column(String(20), default="empty")
    # 剧本风格备忘(随创作累积,注入后续集生成)
    style_memo: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ScriptEpisode(Base, TimestampMixin):
    """一集剧本。content 为剧本正文(场景标题/动作行/对白,Fountain 风格)。"""

    __tablename__ = "script_episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), index=True
    )
    episode_number: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200), default="")
    # 一句话梗概(本集讲什么)
    synopsis: Mapped[str] = mapped_column(Text, default="")
    # 开场钩子 / 结尾钩子(分集大纲生成时产出)
    opening_hook: Mapped[str] = mapped_column(Text, default="")
    ending_hook: Mapped[str] = mapped_column(Text, default="")
    # empty / drafting / drafted / approved
    status: Mapped[str] = mapped_column(String(20), default="empty")
    content: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
