# app/engines/pipeline/architecture.py
# -*- coding: utf-8 -*-
"""小说顶层架构生成:雪花写作法四步串行。

种子 → 角色动力学 → 世界观 → 情节架构,每步产出作为下一步输入。
结果落库到 architecture 表(见 docs/02-data-model.md)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Architecture, Project
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.router import Task, get_adapter_for
from app.prompts import (
    CHARACTER_DYNAMICS_PROMPT,
    CORE_SEED_PROMPT,
    PLOT_ARCHITECTURE_PROMPT,
    WORLD_BUILDING_PROMPT,
)
from app.schemas.concept import Concept, coerce_concept
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.pipeline")


def _render_topic_block(topic: str, concept: Concept | dict | None) -> str:
    """把结构化概念渲染成喂给核心种子的富文本;无概念时回落 topic 一句话。

    向后兼容:存量项目 concept 为 None,只有 topic。有概念但字段全空时也回落。
    """
    c = concept if isinstance(concept, Concept) else coerce_concept(concept)
    if not c.is_empty():
        return c.render()
    return (topic or "").strip() or "(自由发挥)"


@dataclass
class ArchitectureResult:
    core_seed: str
    character_dynamics: str
    world_building: str
    plot_architecture: str

    @property
    def full_text(self) -> str:
        """四步拼成完整架构文本,供蓝图生成使用。"""
        return (
            f"【核心种子】\n{self.core_seed}\n\n"
            f"【角色动力学】\n{self.character_dynamics}\n\n"
            f"【世界观】\n{self.world_building}\n\n"
            f"【情节架构】\n{self.plot_architecture}"
        )


async def generate_architecture(
    *,
    topic: str,
    genre: str,
    number_of_chapters: int,
    word_number: int,
    concept: Concept | dict | None = None,
    tendency: Tendency | None = None,
    global_tendency: Tendency | None = None,
    progress=None,
) -> ArchitectureResult:
    """执行雪花四步,返回完整架构。纯生成,不落库。

    concept: 结构化故事概念(灵感工坊产出),优先于 topic 喂给核心种子;
             为空/None 时回落到 topic 一句话(向后兼容存量项目)。
    progress: 可选回调 fn(stage_text),四步各报一次(异步任务进度用)。
    """

    def _report(stage: str) -> None:
        if progress:
            try:
                progress(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

    assembled = assemble_tendency("outline", tendency, global_tendency)
    style_block = render_style_block(assembled)
    adapter = get_adapter_for(Task.ARCHITECTURE)
    topic_block = _render_topic_block(topic, concept)

    # Step 1: 核心种子
    logger.info("架构生成 1/4:核心种子...")
    _report("1/4 核心种子")
    core_seed = (
        await adapter.ask(
            CORE_SEED_PROMPT.format(
                topic=topic_block,
                genre=genre,
                number_of_chapters=number_of_chapters,
                word_number=word_number,
                style_directives=style_block,
            )
        )
    ).strip()

    # Step 2: 角色动力学
    logger.info("架构生成 2/4:角色动力学...")
    _report("2/4 角色动力学")
    character_dynamics = (
        await adapter.ask(
            CHARACTER_DYNAMICS_PROMPT.format(
                core_seed=core_seed, style_directives=style_block
            )
        )
    ).strip()

    # Step 3: 世界观
    logger.info("架构生成 3/4:世界观...")
    _report("3/4 世界观")
    world_building = (
        await adapter.ask(
            WORLD_BUILDING_PROMPT.format(
                core_seed=core_seed,
                character_dynamics=character_dynamics,
                style_directives=style_block,
            )
        )
    ).strip()

    # Step 4: 情节架构
    logger.info("架构生成 4/4:情节架构...")
    _report("4/4 情节架构")
    plot_architecture = (
        await adapter.ask(
            PLOT_ARCHITECTURE_PROMPT.format(
                core_seed=core_seed,
                character_dynamics=character_dynamics,
                world_building=world_building,
                number_of_chapters=number_of_chapters,
                style_directives=style_block,
            )
        )
    ).strip()

    logger.info("架构生成完成。")
    return ArchitectureResult(
        core_seed=core_seed,
        character_dynamics=character_dynamics,
        world_building=world_building,
        plot_architecture=plot_architecture,
    )


def save_architecture(
    db: Session, project: Project, result: ArchitectureResult
) -> Architecture:
    """架构落库:已有则覆盖并 version+1,否则新建。"""
    arch = project.architecture
    if arch is None:
        arch = Architecture(project_id=project.id, version=1)
        # 通过关系赋值,同时更新 session 里已缓存的 project.architecture
        project.architecture = arch
        db.add(arch)
    else:
        arch.version += 1

    arch.core_seed = result.core_seed
    arch.character_dynamics = result.character_dynamics
    arch.world_building = result.world_building
    arch.plot_architecture = result.plot_architecture

    project.status = "outlining"
    db.flush()
    return arch
