# app/db/models/foreshadowing.py
"""伏笔调度(借鉴 NovelClaw 四态 + KazKozDev 揭示调度)。

调度规则:status in (planted, reinforced) 且
expected_payoff_chapter <= 当前章+2 → 进入"该回收"提醒列表,
生成该章时注入 prompt:"以下伏笔应在近期回收:…"。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class Foreshadowing(Base, TimestampMixin):
    __tablename__ = "foreshadowings"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(Text)
    chapter_planted: Mapped[int] = mapped_column(Integer)
    expected_payoff_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 最早不能早于(KazKozDev minimumChapter)
    earliest_payoff_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # planted / reinforced / paid_off / abandoned
    status: Mapped[str] = mapped_column(String(12), default="planted", index=True)
    payoff_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reinforcement_chapters: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # critical / major / minor
    importance: Mapped[str] = mapped_column(String(10), default="major")
    required_hints: Mapped[list[Any]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
