# app/db/models/chapter_order.py
"""章节订单(docs/20 订单制):写前确认单——六单结构,生成前人拍板。

订单是「意图」,圣经是「事实」(docs/20 铁律 4):确认的订单注入生成提示词
(替换蓝图行的简述/节拍/人物槽位),成品由交稿对账对着订单验收。

一章一份当前订单(唯一约束),历史版本收在 history JSON 里——沿
ChapterState.contract「一章一份当前契约」的轻量哲学,不另建版本表。
无订单的章走蓝图行生成,行为与历史版本字节级一致(存量书零变化)。
"""
from __future__ import annotations

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ChapterOrder(Base, TimestampMixin):
    __tablename__ = "chapter_orders"
    __table_args__ = (UniqueConstraint("project_id", "chapter_number", name="uq_order_chapter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    # 六单(docs/20 §5.1):cast(人物进出)/relations(关系变动)/beats(节拍)/
    # hooks(钩子)/foreshadow(伏笔)/scenes(场景序)+ free_directive(自由指令)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    # draft:编辑中,不参与生成;confirmed:按单生成
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # 历史版本 [{version, payload, confirmed_at}],只留最近 10 版防膨胀
    history: Mapped[list] = mapped_column(JSON, default=list)
    # 章后节拍判定结果(apply_chapter_tail 写入):[{beat, hit, note}];
    # None = 未判定(无订单/判定失败降级)
    beat_check: Mapped[list | None] = mapped_column(JSON, nullable=True)
