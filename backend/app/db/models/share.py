# app/db/models/share.py
# -*- coding: utf-8 -*-
"""公开分享链接:把整本书或单章以只读链接分享给任何人(免登录阅读)。

安全模型:
- token 是唯一凭证(secrets.token_urlsafe,不可枚举),持有即可读对应内容;
- 只读、无 AI、无设定/圣经/伏笔等任何其他数据——公开端点只回书名与正文;
- 可随时撤销(revoked),撤销后立即 404;项目删除随 CASCADE 消失。
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ShareLink(Base, TimestampMixin):
    __tablename__ = "share_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 不可枚举的访问凭证(URL 里只有它,不含任何自增 id)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # book = 整本书(全部有正文的章) / chapter = 单章
    scope: Mapped[str] = mapped_column(String(10), default="book")
    # scope=chapter 时的章号;book 恒为 NULL
    chapter_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 撤销标记:撤销后公开端点立即 404,行保留(链接可重建)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    # 公开页浏览次数(只增不精确,给作者一个「被看了多少次」的感知)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
