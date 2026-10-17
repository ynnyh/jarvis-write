# app/db/models/evolution.py
# -*- coding: utf-8 -*-
"""进化系统资产(docs/36):用户级自治旋钮 + 复盘提炼出的记忆条目。

两张表都是新表,走 Base.metadata.create_all 自动建,老库启动零迁移。

设计要点:
- 记忆是**用户资产**,严格按 user_id 隔离;project_id 故意不加 FK(项目删除后
  教训还在——「这本书教了作者什么」不随书走);kind 只有 style/lesson/preference,
  status 只有 pending/confirmed/dismissed,白名单在引擎层收敛,库里不设枚举约束
  (与 Chapter.status 同一做法)。
- off 档不产生任何行;删除记忆立即影响下一次注入。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class EvolutionPref(Base, TimestampMixin):
    """用户级自治旋钮(docs/36 §2)。缺行 = off,与默认档一致,不写兜底行。"""

    __tablename__ = "evolution_prefs"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # off:不采集不注入 / confirm:候选待作者逐条拍板 / auto:直接入库仍可见可删
    level: Mapped[str] = mapped_column(String(10), default="off")


class EvolutionMemory(Base, TimestampMixin):
    """一条复盘记忆:给下一次开书看的、带出处的规则。"""

    __tablename__ = "evolution_memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    # 来源项目;仅用于展示出处,不参与隔离(隔离只看 user_id),故不加 FK
    project_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    # style=文风特征 / lesson=教训(下次避免) / preference=偏好(下次延续)
    kind: Mapped[str] = mapped_column(String(20), default="preference")
    # 具体可执行的规则(写给下一次开书时的 AI 看),引擎层截到 200 字
    content: Mapped[str] = mapped_column(Text, default="")
    # 出处摘要(支撑这条的证据),引擎层截到 120 字
    evidence: Mapped[str] = mapped_column(Text, default="")
    # high / medium / low,模型自评,仅作展示排序参考
    confidence: Mapped[str] = mapped_column(String(10), default="medium")
    # pending(等作者拍板) / confirmed(会注入) / dismissed(忽略,不再注入)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    source: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
