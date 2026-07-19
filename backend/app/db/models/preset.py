# app/db/models/preset.py
"""倾向预设:用户存的标签模板(见 04-tag-system)。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class TendencyPreset(Base, TimestampMixin):
    __tablename__ = "tendency_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))  # 如"我的爽文模板"
    # outline / chapter / polish —— 预设作用的生成节点
    scope: Mapped[str] = mapped_column(String(10), index=True)
    # 标签组合(含自定义输入)
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
