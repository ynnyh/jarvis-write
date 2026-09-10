# app/db/models/fact_usage.py
# -*- coding: utf-8 -*-
"""事实消费日志(§1.5 引用追踪与失效传播)。

为什么单独一张表而不是 JSON 列或现算:

- **现算不可行**:要判断「第 12 章引用了哪条事实」,得拿事实内容回检索每一章
  正文——那是 O(章数 × 事实数) 的全文扫描,改一条设定要等几十秒。
- **JSON 列不可行**:查询方向是「给定 fact_id,找出所有消费者」;JSON 列只能
  反着查(给定章,列出事实),没有索引。
- **本表的价值**:让「一条事实作废 → 哪些章要复核」从扫描变成一次索引查询。

一行 = 一个 (fact, 消费章) 对。同一章多次消费同一条事实只留一行(计数进
times),因为下游要的是「哪些章受影响」而不是「被提了几次」(后者噪声大,
且模型在一章里反复引用同一事实是常态,不说明更多问题)。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class FactUsage(Base, TimestampMixin):
    """事实消费日志:某条事实在第 N 章被用到了。

    source 区分日志来源(决定可信度与失效时的处置力度):
      - "retrieval":检索层把它喂进了某场的 prompt(强信号——模型看得见)
      - "extract":抽取时在新章正文里字面命中了它(弱信号——可能只是重述)
      - "manual":作者在设定面板手动标注的依赖关系(人工确认,最可信)
    """

    __tablename__ = "fact_usages"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    fact_id: Mapped[int] = mapped_column(
        ForeignKey("facts.id", ondelete="CASCADE"), index=True
    )
    # 消费章号(不是主键 id——章号是判定影响的域语言)
    chapter_number: Mapped[int] = mapped_column(Integer, index=True)
    # 细到场景(有则填):定点重写以场为单位,有它才能精准定位
    scene_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(12), default="retrieval")
    # 命中次数(同章同场重复命中只留一行,这里累计)
    times: Mapped[int] = mapped_column(Integer, default=1)
    # 命中处的原文片段(证据:让用户能判断是真引用还是巧合)
    evidence: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (
        # 主查询方向:给定事实,列出消费章(失效传播走它)
        Index("ix_fact_usages_fact_chapter", "fact_id", "chapter_number"),
        # 反方向:给定章,列出消费的事实(体检报告走它)
        Index("ix_fact_usages_project_chapter", "project_id", "chapter_number"),
    )
