# app/engines/pipeline/chapter_context.py
# -*- coding: utf-8 -*-
"""逐章生成的共享上下文查询:直接上文尾部 + 滚动前情摘要。

从 chapter.py 拆出:generate_chapter(草稿上下文组装)与章后链路
(摘要重建/章后链路)两边的公共读路径,避免循环导入,也让
chapter.py 回归「生成主流程」单一职责。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterSummary

_RECENT_TAIL_CHARS = 900   # 每章取结尾多少字作直接上文
_RECENT_WINDOW = 2         # 直接注入最近几章的结尾


def _recent_tail(db: Session, project_id: int, current: int) -> str:
    """取最近 _RECENT_WINDOW 章定稿的结尾拼接。"""
    parts: list[str] = []
    for n in range(max(1, current - _RECENT_WINDOW), current):
        ch = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
            .first()
        )
        if ch and ch.final_content:
            parts.append(f"(第{n}章结尾)…{ch.final_content[-_RECENT_TAIL_CHARS:]}")
    return "\n\n".join(parts) or "(本章是第一章,无上文)"


def _rolling_summary(db: Session, project_id: int, current: int) -> str:
    row = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.project_id == project_id,
            ChapterSummary.chapter_number < current,
        )
        .order_by(ChapterSummary.chapter_number.desc())
        .first()
    )
    return row.rolling_summary if row else "(无,本章为开篇)"
