# app/db/models/user.py
"""用户账号(阶段 8:多用户)。

- 密码用 bcrypt 哈希存储,不存明文。
- 每个用户的 LLM key 独立(见 ProviderSetting.user_id),互不共用。
- 项目数据按 user_id 隔离(见 Project.user_id)。
"""
from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
