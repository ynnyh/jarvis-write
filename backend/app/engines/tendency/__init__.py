# app/engines/tendency/__init__.py
"""E. 倾向拼装器:标签 chips -> 写作指令片段 -> 注入 Prompt。"""
from .catalog import get_catalog, get_node_catalog, iter_chip_directives
from .assembler import assemble_tendency, merge_tendency

__all__ = [
    "get_catalog",
    "get_node_catalog",
    "iter_chip_directives",
    "assemble_tendency",
    "merge_tendency",
]
