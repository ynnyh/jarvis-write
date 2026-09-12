# app/db/models/premise_ledger.py
"""梗兑现账本(docs/19 M3 交稿对账):每章对核心梗的兑现记账。

章后抽取顺带判断「本章是否兑现了核心梗的哪一拍」写入这里;
交稿对账区展示并允许作者改判;体检「梗健康度」按它画兑现间隔曲线。
同章唯一(unique),重抽取覆盖。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class PremiseLedger(Base, TimestampMixin):
    __tablename__ = "premise_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_number: Mapped[int] = mapped_column(Integer, index=True)
    # 本章是否兑现了核心梗(纯过渡章=False;连续 False 是文扑前兆,体检标红)
    fulfilled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 兑现的节拍名(照抄梗卡节拍表;未兑现填"")
    beat: Mapped[str] = mapped_column(String(100), default="")
    # 一句话:本章哪个情节兑现了这一拍 / 为什么算未兑现
    note: Mapped[str] = mapped_column(Text, default="")
    # 强度 1-5(这场兑现的力度,与场景张力档同口径;抽不出来默认 3)
    strength: Mapped[int] = mapped_column(Integer, default=3)
    # 抽取附带的佐证原文(可选)
    evidence: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (UniqueConstraint("project_id", "chapter_number"),)
