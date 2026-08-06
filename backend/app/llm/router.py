# app/llm/router.py
# -*- coding: utf-8 -*-
"""
任务级模型路由（借鉴 AI_NovelGenerator 的 choose_configs 思路）。

不同生成任务用不同档位的配置，平衡成本与质量：
- 架构/蓝图/定稿/润色 → 强模型（quality 档 = 设置页标「默认」的配置）
- 草稿/摘要/事实抽取/一致性校验 → 快模型（fast 档 = 设置页标「快档」的配置,
  未单独指定时跟随 quality 档）
"""
from __future__ import annotations

from enum import Enum

from .base import LLMAdapter
from .factory import create_llm_adapter


class Task(str, Enum):
    """生成任务类型，对应流水线里的各步骤。"""

    ARCHITECTURE = "architecture"       # 种子/角色/世界观/情节
    BLUEPRINT = "blueprint"             # 章节蓝图
    DRAFT = "draft"                     # 正文草稿
    SUMMARY = "summary"                 # 章节摘要
    FACT_EXTRACT = "fact_extract"       # 章后事实/状态抽取
    HANDOFF_EXTRACT = "handoff_extract" # 章末交接契约提取
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
    Task.FACT_EXTRACT: Tier.QUALITY,  # 抽取写圣经是长程一致性的数据源头,抽错污染全书,上强档
    Task.HANDOFF_EXTRACT: Tier.QUALITY,  # 章末契约是下章衔接与门禁比对的事实源,理由同上
    Task.FINALIZE: Tier.QUALITY,
    Task.POLISH: Tier.QUALITY,
    Task.CONSISTENCY: Tier.FAST,
    Task.IMPACT: Tier.QUALITY,
}

# 任务 -> 采样温度。创作类任务要发散(高温),判断/抽取/压缩类要稳准(低温)。
# 之前所有任务都用 default_temperature=0.7,文风千章一面、抽取偶发跳字都与此有关。
# 注:推理类模型可能忽略 temperature,传了无害;非推理模型立竿见影。
_TASK_TEMPERATURE: dict[Task, float] = {
    Task.ARCHITECTURE: 0.85,   # 顶层设定要有想象力
    Task.BLUEPRINT: 0.75,      # 章节施工图,创意与结构兼顾
    Task.DRAFT: 0.95,          # 草稿最发散,先把灵气写出来,后面定稿收
    Task.FINALIZE: 0.7,        # 定稿拔高但克制,不放飞
    Task.POLISH: 0.7,          # 润色同上
    Task.SUMMARY: 0.3,         # 摘要忠实压缩,不要二次创作
    Task.FACT_EXTRACT: 0.2,    # 抽取求准,低温最稳
    Task.HANDOFF_EXTRACT: 0.2, # 契约提取同为抽取类,低温最稳
    Task.CONSISTENCY: 0.3,     # 一致性/审校判断要稳定可复现
    Task.IMPACT: 0.4,          # 影响分析偏判断
}


# 任务 -> 输出预算。长文本任务(草稿/定稿/润色)给足 max_tokens:
# 全局默认 8192 对长章节偏紧,推理模型思考还会再吃掉一截,容易截断/空正文。
# 短任务不在表里,用配置/全局默认即可。
_TASK_MAX_TOKENS: dict[Task, int] = {
    Task.DRAFT: 16384,
    Task.FINALIZE: 16384,
    Task.POLISH: 12288,
    Task.ARCHITECTURE: 8192,
    Task.BLUEPRINT: 8192,
}


def _tier_config(tier: Tier) -> dict:
    """档位 -> 当前生效的命名配置(cc-switch 风格)。

    quality 档用标了「默认」的配置;fast 档用标了「快档」的配置,
    未单独指定快档时跟随 quality 档(见 factory.resolve_tier_config)。
    """
    from .factory import resolve_tier_config

    return resolve_tier_config(tier.value)


def get_adapter_for(task: Task, **overrides) -> LLMAdapter:
    """按任务拿到合适的适配器。

    按任务档位选配置,按任务默认温度/输出预算补默认值(调用方显式传入的
    temperature / max_tokens 优先,其次任务默认,最后才是配置/全局默认)。
    overrides 可覆盖 temperature / max_tokens / timeout 等。
    """
    tier = _TASK_TIER.get(task, Tier.FAST)
    cfg = _tier_config(tier)
    if "temperature" not in overrides and task in _TASK_TEMPERATURE:
        overrides["temperature"] = _TASK_TEMPERATURE[task]
    if "max_tokens" not in overrides and task in _TASK_MAX_TOKENS:
        overrides["max_tokens"] = _TASK_MAX_TOKENS[task]
    if cfg.get("id") is not None:
        return create_llm_adapter(config_id=cfg["id"], **overrides)
    # .env 兜底的配置没有 id,按协议名走旧路径
    return create_llm_adapter(cfg["interface_format"], **overrides)


def tier_of(task: Task) -> Tier:
    return _TASK_TIER.get(task, Tier.FAST)
