# app/engines/cascade/__init__.py
"""C. 大纲级联更新引擎:改一处 → 影响分析 → 用户拍板 → 下游对齐。"""
from .differ import apply_outline_edit, apply_outline_revision, OUTLINE_EDITABLE_FIELDS
from .impact import analyze_impact
from .regenerate import cascade_regenerate

__all__ = [
    "apply_outline_edit",
    "apply_outline_revision",
    "analyze_impact",
    "cascade_regenerate",
    "OUTLINE_EDITABLE_FIELDS",
]
