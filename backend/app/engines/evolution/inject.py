# app/engines/evolution/inject.py
# -*- coding: utf-8 -*-
"""记忆回注:把作者确认过的记忆渲染成回读 prompt 的一个注入块(docs/36 §5)。

只注这一处:开书回读是全链的入口,教训进了理解卡(flavor/must/avoid),确认后经
render_intent_block 传遍下游——下游模板一字不动,与理解卡的注入纪律一致。
空记忆返回空串,prompt 与现状完全一致(没开进化的用户零影响)。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import EvolutionMemory

_KIND_LABELS = {"style": "文风", "lesson": "教训", "preference": "偏好"}
# 注入上限:条数 × 单条 200 字,封顶约 1.7k 字,回读 prompt 承受得起
_INJECT_LIMIT = 8


def load_confirmed_memories(db: Session, user_id: int | None) -> list[EvolutionMemory]:
    """当前用户确认过的记忆,新→旧,截到注入上限。"""
    if user_id is None:
        return []
    return (
        db.query(EvolutionMemory)
        .filter(EvolutionMemory.user_id == user_id, EvolutionMemory.status == "confirmed")
        .order_by(EvolutionMemory.id.desc())
        .limit(_INJECT_LIMIT)
        .all()
    )


def render_memory_block(memories: list[Any]) -> str:
    """记忆 → 注入块;空列表返回空串(prompt 一字不变)。以换行开头,便于直接拼接。"""
    lines = [
        f"- ({_KIND_LABELS.get(m.kind, '偏好')}) {m.content}"
        for m in memories
        if str(getattr(m, "content", "") or "").strip()
    ]
    if not lines:
        return ""
    return (
        "\n【作者历史偏好与教训(来自其以往作品的复盘,作者已逐条确认)】\n"
        + "\n".join(lines)
        + "\n请把这些吸收进下面的复述:文风特征并入 flavor;偏好与教训里「要延续/"
        "要保留」的并入 must,「不要/避免」的并入 avoid;若与作者的点子冲突,以点子为准。\n"
    )
