# app/db/models/gen_input.py
# -*- coding: utf-8 -*-
"""生成输入快照(docs/29 留痕 / docs/32 §2.4)。

为什么单独一张表:复盘「这次为什么偏了」时,最关键的证据恰好是**用户输入**
(三问答案 / 引导语 / 带话重写的要求 / 理解卡草稿),而这些此前全在客户端内存里,
刷新即失——传国玉玺案例里,想查「他到底跟 AI 说了什么」时一句也找不到。
表只追加不改;写入失败静默(见 app.audit.record_gen_inputs),绝不拖垮生成。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base, TimestampMixin


class GenInput(Base, TimestampMixin):
    __tablename__ = "gen_inputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 归属用户(docs/37):灵感工坊等无项目上下文的输入也要能按人归档;
    # NULL = 历史记录或取不到上下文
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    # 归属项目;老入口(无项目上下文)可为 NULL
    project_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    # 归属 job(与 llm_usage.job_id 同口径,便于按任务把整条链串起来)
    job_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    # 环节标识:intent-readback / book-plans / revise-plan / intent-confirm /
    # arch-layer / skeleton / blueprint / chapter-revision …
    kind: Mapped[str] = mapped_column(String(40), index=True)
    # 原始输入(已裁剪到合理长度,只留复盘必需字段)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
