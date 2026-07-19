# app/db/base.py
"""SQLAlchemy 2.x 声明式基类。

所有模型继承 Base。为了让 Alembic 的 autogenerate 能发现全部表,
在 app/db/models/__init__.py 中集中导入所有模型。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """项目内所有 ORM 模型的基类。"""


class TimestampMixin:
    """给需要的表统一加 created_at / updated_at。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
