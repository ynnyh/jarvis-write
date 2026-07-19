# app/prompts/__init__.py
"""Prompt 模板包。

模板借鉴 AI_NovelGenerator 的雪花写作法体系(种子→角色→世界观→情节→蓝图),
关键差异:每个模板带 {style_directives} 占位符,由倾向拼装器注入用户选择的
写作倾向,而非写死(见 docs/04-tag-system.md)。
"""
from .snowflake import (
    CORE_SEED_PROMPT,
    CHARACTER_DYNAMICS_PROMPT,
    WORLD_BUILDING_PROMPT,
    PLOT_ARCHITECTURE_PROMPT,
    CHAPTER_BLUEPRINT_PROMPT,
    CHUNKED_BLUEPRINT_PROMPT,
)

__all__ = [
    "CORE_SEED_PROMPT",
    "CHARACTER_DYNAMICS_PROMPT",
    "WORLD_BUILDING_PROMPT",
    "PLOT_ARCHITECTURE_PROMPT",
    "CHAPTER_BLUEPRINT_PROMPT",
    "CHUNKED_BLUEPRINT_PROMPT",
]
