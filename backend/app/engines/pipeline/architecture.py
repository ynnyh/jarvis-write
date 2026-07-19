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
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.pipeline")


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
    tendency: Tendency | None = None,
    global_tendency: Tendency | None = None,
) -> ArchitectureResult:
    """执行雪花四步,返回完整架构。纯生成,不落库。"""
    assembled = assemble_tendency("outline", tendency, global_tendency)
    style_block = render_style_block(assembled)
    adapter = get_adapter_for(Task.ARCHITECTURE)

    # Step 1: 核心种子
    logger.info("架构生成 1/4:核心种子...")
    core_seed = (
        await adapter.ask(
            CORE_SEED_PROMPT.format(
                topic=topic,
                genre=genre,
                number_of_chapters=number_of_chapters,
                word_number=word_number,
                style_directives=style_block,
            )
        )
    ).strip()

    # Step 2: 角色动力学
    logger.info("架构生成 2/4:角色动力学...")
    character_dynamics = (
        await adapter.ask(
            CHARACTER_DYNAMICS_PROMPT.format(
                core_seed=core_seed, style_directives=style_block
            )
        )
    ).strip()

    # Step 3: 世界观
    logger.info("架构生成 3/4:世界观...")
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
