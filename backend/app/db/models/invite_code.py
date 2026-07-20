# app/db/models/invite_code.py
"""多邀请码(阶段 9 后台管理升级)。

取代 app_settings 里的单一邀请码:每个码可备注、限次、停用。
注册时的口径(见 api/auth.py):
- 本表有记录 → 只按本表校验,旧单码(app_settings / .env)立即失效;
- 本表为空 → 回落旧的单码逻辑,保证线上平滑过渡。
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class InviteCode(Base, TimestampMixin):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # 备注:这个码发给了谁/什么用途
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 次数上限,NULL = 不限次数长期有效
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
