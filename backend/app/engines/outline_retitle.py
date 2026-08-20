# app/engines/outline_retitle.py
# -*- coding: utf-8 -*-
"""章节标题润色:基于本章大纲生成若干候选标题,供作者挑选。

作者觉得生成的章节标题不合适(常见:太夸张 / 标题党)时用。
只产候选、不落库;前端选定后走 editOutline 只改 title —— 纯展示性改动,
不标正文失配、不触发级联(见 cascade.differ._COSMETIC_FIELDS)。
"""
from __future__ import annotations

import logging

from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for
from app.prompts.cascade import CHAPTER_RETITLE_PROMPT

logger = logging.getLogger("jarvis-write.outline-retitle")

# 作者没写具体要求时的默认导向:直接对应「让 AI 换个不夸张的」按钮
_DEFAULT_DIRECTIVE = "朴素、准确、不夸张,别用浮夸的大词和感叹句,像正经出版小说的目录"


async def suggest_chapter_titles(
    *,
    chapter_number: int,
    architecture_brief: str,
    outline_block: str,
    current_title: str,
    directive: str = "",
    count: int = 5,
) -> list[str]:
    """产出 count 个候选章节标题(已去重 / 去空 / 去掉与当前完全相同的 / 截断长度)。"""
    directive = (directive or "").strip() or _DEFAULT_DIRECTIVE
    raw = await get_adapter_for(Task.BLUEPRINT).ask(
        CHAPTER_RETITLE_PROMPT.format(
            chapter_number=chapter_number,
            architecture_brief=architecture_brief,
            outline_block=outline_block,
            current_title=(current_title or "").strip() or "(无)",
            directive=directive,
            count=count,
        )
    )
    try:
        data = parse_llm_json(raw)
    except Exception as exc:  # noqa: BLE001 — 解析失败给可读错误,前端提示重试
        logger.warning("章节标题候选解析失败: %s", exc)
        raise ValueError("AI 返回的标题候选无法解析,请重试") from exc
    if not isinstance(data, dict):
        raise ValueError("AI 返回的标题候选格式不对,请重试")

    cur = (current_title or "").strip()
    seen: set[str] = set()
    out: list[str] = []
    for t in data.get("titles") or []:
        t = str(t).strip().strip("《》\"'").strip()[:30]
        if not t or t == cur or t in seen:
            continue
        seen.add(t)
        out.append(t)
    if not out:
        # 垃圾输入时 parse_llm_json 返回 {},或候选全被过滤 → 别静默返回空列表
        # (前端点了按钮却毫无反应),抛错让路由转 400 提示重试。
        raise ValueError("AI 没给出可用的候选标题,请重试")
    return out[:count]
