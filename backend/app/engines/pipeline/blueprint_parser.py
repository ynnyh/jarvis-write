# app/engines/pipeline/blueprint_parser.py
# -*- coding: utf-8 -*-
"""章节蓝图解析器:把 LLM 按固定格式输出的蓝图文本解析成结构化 dict。

格式约定见 prompts/snowflake.py 的 _BLUEPRINT_FORMAT,字段与 outlines 表对应。
解析要宽容:LLM 可能用中英文冒号、多余空行、markdown 加粗等,都要兼容。
"""
from __future__ import annotations

import re
from typing import Any

# "第12章 - 标题" / "第 12 章 - 标题" / "**第12章 - 标题**"
_CHAPTER_HEAD = re.compile(
    r"^\s*\**\s*第\s*(\d+)\s*章\s*[-—–\s]*\s*(.*?)\s*\**\s*$"
)

# 字段名 -> outlines 表字段
_FIELD_MAP = {
    "本章定位": "chapter_role",
    "核心作用": "chapter_purpose",
    "悬念密度": "suspense_level",
    "伏笔操作": "foreshadowing",
    "认知颠覆": "plot_twist_level",
    "涉及人物": "characters_involved",
    "关键道具": "key_items",
    "场景地点": "scene_location",
    "本章简述": "summary",
}

# 需要拆成列表的字段
_LIST_FIELDS = {"characters_involved", "key_items"}

_NONE_VALUES = {"无", "暂无", "none", "None", "N/A", "n/a", ""}


def _clean(text: str) -> str:
    """去掉 markdown 粗体、首尾空白与包裹的方括号。"""
    text = text.strip().strip("*").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text


def _split_list(value: str) -> list[str]:
    if _clean(value) in _NONE_VALUES:
        return []
    parts = re.split(r"[,，、;；/]", value)
    return [p for p in (_clean(x) for x in parts) if p and p not in _NONE_VALUES]


def parse_blueprint(text: str) -> list[dict[str, Any]]:
    """解析蓝图文本 → [{chapter_number, title, chapter_role, ...}, ...]。

    容错策略:未识别的行忽略;缺失字段留空;同章字段重复以后者为准。
    """
    chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        head = _CHAPTER_HEAD.match(line)
        if head:
            if current is not None:
                chapters.append(current)
            current = {
                "chapter_number": int(head.group(1)),
                "title": _clean(head.group(2)),
            }
            continue

        if current is None:
            continue  # 章节头之前的说明文字,忽略

        # "字段名:值"(中英文冒号均可)
        m = re.match(r"^\**([^:：*]+)\**\s*[:：]\s*(.*)$", line)
        if not m:
            continue
        field_cn = m.group(1).strip()
        value = m.group(2).strip()
        field = _FIELD_MAP.get(field_cn)
        if field is None:
            continue

        if field in _LIST_FIELDS:
            current[field] = _split_list(value)
        else:
            current[field] = _clean(value)

    if current is not None:
        chapters.append(current)

    return chapters


def validate_blueprint(
    chapters: list[dict[str, Any]], expected_start: int, expected_end: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """校验解析结果:章号范围、缺章、重复章。

    返回 (过滤后的章节列表, 警告列表)。超范围的章丢弃,缺章只警告。
    """
    warnings: list[str] = []
    seen: dict[int, dict[str, Any]] = {}

    for ch in chapters:
        num = ch.get("chapter_number")
        if num is None or not (expected_start <= num <= expected_end):
            warnings.append(f"章号 {num} 超出预期范围 [{expected_start}, {expected_end}],已丢弃")
            continue
        if num in seen:
            warnings.append(f"第 {num} 章重复出现,以后者为准")
        seen[num] = ch

    missing = [
        n for n in range(expected_start, expected_end + 1) if n not in seen
    ]
    if missing:
        warnings.append(f"缺少章节: {missing}")

    ordered = [seen[n] for n in sorted(seen)]
    return ordered, warnings
