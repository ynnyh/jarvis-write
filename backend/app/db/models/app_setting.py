# app/db/models/app_setting.py
"""站点级键值设置(阶段 9:后台管理)。

存需要在后台动态改、又不想重启改 .env 的配置,如注册邀请码。
读取方负责回落:DB 无记录时用 .env 的值。
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), default="")
