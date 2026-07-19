# app/llm/router.py
# -*- coding: utf-8 -*-
"""
任务级模型路由（借鉴 AI_NovelGenerator 的 choose_configs 思路）。

不同生成任务用不同档位的模型，平衡成本与质量：
- 架构/蓝图/定稿/润色 → 强模型（quality）
- 草稿/摘要/事实抽取/一致性校验 → 快模型（fast）

路由表可被环境变量覆盖；阶段 0 先给一份合理默认值，
后续阶段（尤其阶段 7）再做成用户可配置。
"""
from __future__ import annotations

from enum import Enum

from app.config import get_settings

from .base import LLMAdapter
from .factory import create_llm_adapter


class Task(str, Enum):
    """生成任务类型，对应流水线里的各步骤。"""

    ARCHITECTURE = "architecture"       # 种子/角色/世界观/情节
    BLUEPRINT = "blueprint"             # 章节蓝图
    DRAFT = "draft"                     # 正文草稿
    SUMMARY = "summary"                 # 章节摘要
    FACT_EXTRACT = "fact_extract"       # 章后事实/状态抽取
    FINALIZE = "finalize"               # 定稿
    POLISH = "polish"                   # 润色
    CONSISTENCY = "consistency"         # 一致性校验
    IMPACT = "impact"                   # 大纲级联影响分析


class Tier(str, Enum):
    QUALITY = "quality"  # 强模型
    FAST = "fast"        # 快模型


# 任务 -> 档位。见 docs/01-architecture.md 第四节。
_TASK_TIER: dict[Task, Tier] = {
    Task.ARCHITECTURE: Tier.QUALITY,
    Task.BLUEPRINT: Tier.QUALITY,
    Task.DRAFT: Tier.FAST,
    Task.SUMMARY: Tier.FAST,
    Task.FACT_EXTRACT: Tier.FAST,
    Task.FINALIZE: Tier.QUALITY,
    Task.POLISH: Tier.QUALITY,
    Task.CONSISTENCY: Tier.FAST,
    Task.IMPACT: Tier.QUALITY,
}


def _tier_provider(tier: Tier) -> str:
    """档位 -> provider 名。

    阶段 0 简化策略：quality 与 fast 暂都走用户设置的默认 provider
    (设置页 > .env),后续接入多 provider 时按档位细化(留给阶段 7)。
    """
    from .factory import resolve_default_provider

    return resolve_default_provider()


def get_adapter_for(task: Task, **overrides) -> LLMAdapter:
    """按任务拿到合适的适配器。

    overrides 可覆盖 temperature / max_tokens 等（如摘要用低温度）。
    """
    tier = _TASK_TIER.get(task, Tier.FAST)
    provider = _tier_provider(tier)
    return create_llm_adapter(provider, **overrides)


def tier_of(task: Task) -> Tier:
    return _TASK_TIER.get(task, Tier.FAST)
