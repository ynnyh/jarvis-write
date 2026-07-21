# app/schemas/concept.py
# -*- coding: utf-8 -*-
"""结构化故事概念(灵感工坊产出,喂养架构生成的核心种子)。

topic 一句话时代的升级:把"想法"从有损的一句话,变成六字段结构化对象,
信息无损地往架构层传递。落库在 projects.concept(JSON,可空)。

字段设计原则:全部可空/可空串——灵感是渐进成形的,任何一步都可能只填了一部分。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# concept 的字段顺序(渲染/遍历时保持稳定),中文标签供 prompt 与前端复用
CONCEPT_FIELDS: tuple[tuple[str, str], ...] = (
    ("logline", "一句话故事"),
    ("hook", "核心钩子"),
    ("twist", "潜在反转"),
    ("protagonist", "主角"),
    ("conflict", "核心冲突"),
    ("setting", "世界/背景"),
)


class Concept(BaseModel):
    """一部小说的结构化概念。所有字段可空:灵感是逐步捏出来的。"""

    logline: str = Field(default="", description="一句话故事:主角+冲突+赌注")
    hook: str = Field(default="", description="核心钩子:读者为什么想读")
    twist: str = Field(default="", description="潜在的大反转方向")
    protagonist: str = Field(default="", description="主角:身份/目标/困境")
    conflict: str = Field(default="", description="核心冲突/对立面")
    setting: str = Field(default="", description="世界/背景/基调")

    def is_empty(self) -> bool:
        """六字段全空视为无概念(等价于没填)。"""
        return not any(getattr(self, k).strip() for k, _ in CONCEPT_FIELDS)

    def render(self) -> str:
        """渲染成富文本块,供架构 prompt 注入。只输出非空字段。"""
        lines = [
            f"【{label}】{value.strip()}"
            for key, label in CONCEPT_FIELDS
            if (value := getattr(self, key)).strip()
        ]
        return "\n".join(lines)


def coerce_concept(raw: object) -> Concept:
    """把任意来源(LLM dict / 存量 None / 脏数据)收敛成合法 Concept。

    - None / 非 dict → 空概念
    - dict → 只取已知字段,值统一转 str 并 strip;未知键丢弃(防 LLM 幻觉字段)
    """
    if not isinstance(raw, dict):
        return Concept()
    clean = {
        key: str(raw.get(key) or "").strip()
        for key, _ in CONCEPT_FIELDS
    }
    return Concept(**clean)
