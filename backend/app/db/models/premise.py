# app/db/models/premise.py
"""核心梗卡(docs/19 规划):一本书的"纲"。

高概念负责把读者点进来,兑现机制负责把读者留下来,边界禁忌负责不把梗写崩。
它是一等对象而非概念里的一句话:蓝图(每章「梗兑现」拍)、正文注入、交稿对账、
体检「梗健康度」都以它为轴。一项目一条主梗(kind="main");结构上预留副梗
(kind="sub",情感副线等),UI 暂只消费主梗。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class Premise(Base, TimestampMixin):
    __tablename__ = "premises"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    # main = 主线梗;sub = 副线梗(结构预留,UI 暂不消费)
    kind: Mapped[str] = mapped_column(String(10), default="main", server_default="main")
    # 高概念一句话(≤50 字):"救人一次,寿命减一年"
    high_concept: Mapped[str] = mapped_column(Text, default="")
    # 兑现机制:这个梗为什么能反复产生冲突与满足(循环说明,一段话)
    payoff: Mapped[str] = mapped_column(Text, default="")
    # 兑现节拍表(3-6 拍,有序):["代价显形", "初次反转", "对手升级", ...]
    # 蓝图按拍标「梗兑现」,交稿对账按拍记账,体检按拍画健康度
    beats: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 边界禁忌(写什么会把梗写崩):["能力无代价", "寿命账不兑现", ...]
    # 生成时随本章节拍注入 prompt(与硬约束同路)
    boundaries: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # 钩子计划:{"opening": "开局钩", "mid": "中期反转", "climax": "大高潮"}
    hook_plan: Mapped[dict[Any, Any]] = mapped_column(JSON, default=dict)
    # ai = AI 提炼的草稿;human = 作者确认/手写。作者改过即 human,AI 不再覆盖
    source: Mapped[str] = mapped_column(String(10), default="ai", server_default="ai")
