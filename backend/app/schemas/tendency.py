# app/schemas/tendency.py
# -*- coding: utf-8 -*-
"""标签化倾向系统的请求/响应模型。

倾向以「维度 key -> 选中的 chip 文案」的字典形式传递。
- 单选维度:值为 str
- 多选维度:值为 list[str]
- 自定义值:放在 `_custom` 里,原样保留(见 docs/04-tag-system.md §3)
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChipOut(BaseModel):
    """一个预设 chip。"""

    label: str
    directive: str
    # 两级题材库扩展字段(仅 genre 维度有):所属大类 key / 用户向一句话卖点
    category: str | None = None
    desc: str | None = None


class DimensionOut(BaseModel):
    """一个倾向维度(如"节奏""基调")。"""

    key: str
    label: str
    select: str = Field(description="single 单选 / multi 多选")
    chips: list[ChipOut]
    # 两级题材库:大类清单 [{key, label}](仅 genre 维度有)
    categories: list[dict[str, str]] | None = None


class NodeCatalogOut(BaseModel):
    """一个生成节点(outline / chapter / polish)的完整可选倾向。"""

    node: str
    label: str
    dimensions: list[DimensionOut]


# 一次生成携带的倾向对象:{"genre": "赛博朋克", "tone": ["悬疑","暗黑"], "_custom": {...}}
Tendency = dict[str, Any]


class AssembledTendency(BaseModel):
    """拼装结果:把选中的倾向转成一段可注入 Prompt 的指令文本。"""

    directives_text: str = Field(description="拼好的『本次写作倾向』文本块")
    applied: dict[str, Any] = Field(
        default_factory=dict, description="最终生效的倾向(全局+临时合并后)"
    )
