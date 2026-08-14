# app/db/models/writing_card.py
"""写作手法卡:作者为某本书启用的可复用风格/节奏手法(见 docs/skills 相关设计)。

一张卡 = 「手法名 + 具体写法指令」。用户在书内勾选启用、可调序,sort 越大排越后。
启用的卡会被拼成【写作手法卡】块注入所有生成节点(润色 / 正文草稿与定稿 / 重写),
与「创作偏好档案」并存于 style_block,但语义不同:档案是结构化整书主张,卡片是
可勾选、可排序的手法清单。不改任何情节事实(注入时软约束于润色铁律下方)。
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class WritingCard(Base, TimestampMixin):
    __tablename__ = "writing_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), index=True
    )
    title: Mapped[str] = mapped_column(String(100))  # 卡名,如「冷峻硬汉对峙」
    body: Mapped[str] = mapped_column(Text)  # 手法描述,注入 prompt 的实际指令文本
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否注入当前书
    sort: Mapped[int] = mapped_column(Integer, default=0)  # 启用时的手动排序,越大越靠后