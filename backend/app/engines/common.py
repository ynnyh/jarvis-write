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


def _concept_spark_block(project: Project) -> str:
    """把灵感工坊的原始火花(钩子/反转/主角/冲突/基调)回注逐章生成。

    信息链路是有损的:concept → 核心种子(≤100字)→ 蓝图简述(≤100字)→ 正文,
    最初的鲜活细节到章节层已被抽象成骨架。这里把 concept 的原始表述再带一份进来,
    让每章都能回看这本书"最打动人的那点东西",别只照着干巴巴的骨架填肉。
    """
    from app.schemas.concept import coerce_concept

    c = coerce_concept(project.concept)
    if c.is_empty():
        return ""
    # 只带最能定调的几项:钩子/反转/主角/冲突/基调(logline 已进核心种子,不重复)
    spark_fields = [
        ("hook", "核心钩子"),
        ("twist", "潜在反转"),
        ("protagonist", "主角"),
        ("conflict", "核心冲突"),
        ("setting", "世界/基调"),
    ]
    lines = [
        f"- {label}:{value.strip()}"
        for key, label in spark_fields
        if (value := getattr(c, key)).strip()
    ]
    if not lines:
        return ""
    return (
        "\n\n本书的创作初衷(始终记住这本书最打动人的地方,别写成套路化的骨架):\n"
        + "\n".join(lines)
    )


def chapter_architecture_brief(project: Project) -> str:
    """逐章生成用:创作初衷 + 核心种子 + 世界观 + 角色动力学。

    角色动力学给足篇幅并点明用途——不只是背景设定,更是揣摩各角色说话口吻、
    性格差异的依据(让笔下人物各有各的声音,是长篇质感的关键)。
    concept 的原始火花回注,缓解"灵感→种子→蓝图→正文"链路的信息减损。
    """
    arch = project.architecture
    if arch is None:
        return "(无)"
    return (
        f"核心种子:{arch.core_seed}\n\n"
        f"世界观(节选):{arch.world_building[:600]}\n\n"
        f"角色动力学(据此揣摩各角色的性格、处境与说话口吻,让他们的声音互不相同):\n"
        f"{arch.character_dynamics[:1800]}"
        f"{_concept_spark_block(project)}"
    )


def cascade_architecture_brief(project: Project) -> str:
    """级联重生成用:核心种子 + 情节架构。"""
    arch = project.architecture
    if arch is None:
        return "(无)"
    return f"核心种子:{arch.core_seed}\n情节架构(节选):{arch.plot_architecture[:800]}"
