# app/engines/drift/__init__.py
# -*- coding: utf-8 -*-
"""题材/口味漂移治理(drift):双向治漂门。

痛点:选了「青春校园」却生成「学生觉醒异能」——题材标签只说了分类,漂移无人拦。
本包提供「双向门」:
- 负向硬门(forbidden):现实向禁 超能力/系统/穿越/觉醒 等非现实入侵,regex 高召回
  预筛 + LLM-judge 精筛去误报,确认越界即毙+重生(用户从不看到跑偏版本)。
- 正向味道(genre_fit / taste):必须有的看点是否落地、味道贴不贴,给分 + 定向重写。

三个模块:
- contracts   题材契约单一真相源:category→mode/must/taste + 禁忌元素正则注册表
- genre_fit   纯正则/统计检测器(仿 polish/ai_flavor):禁忌命中 + 味道贴合报告
- self_heal   硬门重生闭环 + 正向味道自愈(仿 polish/polisher.deai_self_heal)
"""
from __future__ import annotations

from app.engines.drift.contracts import (
    FORBIDDEN_ELEMENTS,
    GENRE_CONTRACTS,
    ForbiddenElement,
    GenreContract,
    contract_for,
    effective_patterns,
    forbidden_for_mode,
    forbidden_labels_for_mode,
    mode_for_category,
)
from app.engines.drift.genre_fit import GenreFitReport, GenreHit, genre_fit_report
from app.engines.drift.self_heal import (
    confirm_forbidden,
    enforce_genre_gate,
    taste_self_heal,
)

__all__ = [
    "FORBIDDEN_ELEMENTS",
    "GENRE_CONTRACTS",
    "ForbiddenElement",
    "GenreContract",
    "contract_for",
    "effective_patterns",
    "forbidden_for_mode",
    "forbidden_labels_for_mode",
    "mode_for_category",
    "GenreFitReport",
    "GenreHit",
    "genre_fit_report",
    "confirm_forbidden",
    "enforce_genre_gate",
    "taste_self_heal",
]
