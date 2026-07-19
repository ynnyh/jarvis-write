# app/db/models/usage.py
"""LLM 用量记录:每次调用一行,成本统计用。"""
from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class LlmUsage(Base, TimestampMixin):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 记账归属:哪个用户烧的 token(NULL = 迁移前的历史记录)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    model: Mapped[str] = mapped_column(String(100), index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
