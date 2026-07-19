# app/db/models/setting.py
"""运行时设置:LLM provider 配置存库,让用户在站点设置页配置,而非改 .env。

阶段 8 起改为**每用户一份**:每个账号进来单独配自己的 key,互不共用。
优先级:数据库里当前用户的配置 > .env / 环境变量(.env 仅作开发兜底)。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ProviderSetting(Base, TimestampMixin):
    """某用户的一个 LLM provider 配置(每用户 × deepseek/openai/gemini 各一行)。"""

    __tablename__ = "provider_settings"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_provider_per_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20), index=True)
    api_key: Mapped[str] = mapped_column(String(300), default="")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    model: Mapped[str] = mapped_column(String(100), default="")
    is_default: Mapped[bool] = mapped_column(default=False)
