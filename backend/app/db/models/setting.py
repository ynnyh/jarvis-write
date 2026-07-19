# app/db/models/setting.py
"""运行时设置:LLM provider 配置存库,让用户在站点设置页配置,而非改 .env。

优先级:数据库里的配置 > .env / 环境变量(.env 仍可用,便于开发)。
"""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ProviderSetting(Base, TimestampMixin):
    """一个 LLM provider 的配置(deepseek / openai / gemini 各一行)。"""

    __tablename__ = "provider_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    api_key: Mapped[str] = mapped_column(String(300), default="")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    is_default: Mapped[bool] = mapped_column(default=False)
