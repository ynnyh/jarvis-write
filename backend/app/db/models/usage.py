# app/db/models/usage.py
"""用量记录:llm_usage 记 token 成本账,feature_usage 记功能使用账。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
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


class FeatureUsage(Base):
    """功能使用计数:按「功能线 × 用户」聚合的动作级最小埋点。

    只回答一个问题——「哪个工坊/哪条线真的有人在用」,给功能取舍提供数据回路。
    隐私边界:不记内容、不记具体路径参数,只有 功能名/次数/最后时间;数据存在
    用户自己的库里(桌面版不出本机,网页版随库备份)。写入走 app/usage.py 的
    内存缓冲批量 upsert,不在请求路径上直接碰库。
    """

    __tablename__ = "feature_usage"

    feature: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    uses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
