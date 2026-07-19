# app/engines/tendency/catalog.py
# -*- coding: utf-8 -*-
"""读取倾向预设配置(config/tag_presets.json)。

预设 chips 存在配置文件而非硬编码,新增一个 chip = 加一条记录,
前后端都不用改代码(见 docs/04-tag-system.md §7)。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterator

# backend/config/tag_presets.json
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "tag_presets.json"


@lru_cache
def get_catalog() -> dict:
    """加载并缓存整个倾向目录。"""
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("nodes", {})


def get_node_catalog(node: str) -> dict:
    """取某个生成节点(outline/chapter/polish)的可选倾向。"""
    catalog = get_catalog()
    if node not in catalog:
        raise ValueError(
            f"未知生成节点: {node},可选: {list(catalog)}"
        )
    return catalog[node]


def _directive_index(node: str) -> dict[str, dict[str, str]]:
    """构建 {维度key: {chip文案: 指令片段}} 的查找索引。"""
    index: dict[str, dict[str, str]] = {}
    for dim in get_node_catalog(node).get("dimensions", []):
        key = dim["key"]
        index[key] = {c["label"]: c["directive"] for c in dim.get("chips", [])}
    return index


def _dimension_select(node: str) -> dict[str, str]:
    """{维度key: 'single'|'multi'}。"""
    return {
        dim["key"]: dim.get("select", "single")
        for dim in get_node_catalog(node).get("dimensions", [])
    }


def iter_chip_directives(node: str) -> Iterator[tuple[str, str, str]]:
    """遍历某节点所有 (维度key, chip文案, 指令) 三元组。"""
    for dim in get_node_catalog(node).get("dimensions", []):
        for chip in dim.get("chips", []):
            yield dim["key"], chip["label"], chip["directive"]
