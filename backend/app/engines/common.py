# app/engines/common.py
# -*- coding: utf-8 -*-
"""引擎层公共小工具:大纲查询与架构简报。

architecture_brief 两个变体是按场景定制的提示词素材,字段取舍不同,
保留两份不合并(合并会改变生成行为)。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Outline, Project


def get_outline(db: Session, project_id: int, n: int) -> Outline | None:
    return (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )


def chapter_architecture_brief(project: Project) -> str:
    """逐章生成用:核心种子 + 世界观 + 角色动力学。"""
    arch = project.architecture
    if arch is None:
        return "(无)"
    return (
        f"核心种子:{arch.core_seed}\n\n"
        f"世界观(节选):{arch.world_building[:600]}\n\n"
        f"角色动力学(节选):{arch.character_dynamics[:900]}"
    )


def cascade_architecture_brief(project: Project) -> str:
    """级联重生成用:核心种子 + 情节架构。"""
    arch = project.architecture
    if arch is None:
        return "(无)"
    return f"核心种子:{arch.core_seed}\n情节架构(节选):{arch.plot_architecture[:800]}"
