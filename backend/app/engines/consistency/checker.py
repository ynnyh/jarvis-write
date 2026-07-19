# app/engines/consistency/checker.py
# -*- coding: utf-8 -*-
"""一致性检查:新章正文 vs 故事圣经,LLM 找矛盾。

在定稿后、抽取前运行:发现的问题随 API 返回给用户,由用户决定改不改
(见 docs/03-engines.md:级联与修改永远不自动执行)。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.engines.consistency.bible import BibleService
from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for
from app.prompts.consistency import CONSISTENCY_CHECK_PROMPT

logger = logging.getLogger("jarvis-write.checker")


async def check_chapter(
    db: Session,
    project_id: int,
    chapter_number: int,
    chapter_text: str,
    rolling_summary: str = "",
) -> list[dict]:
    """返回问题列表 [{severity,type,description,conflicting_fact,suggestion}]。

    检查失败(LLM 异常/解析失败)返回空列表并告警,不阻塞流程。
    """
    bible = BibleService(db, project_id)
    active_facts = bible.hard_constraints_block(chapter_number)
    if active_facts.startswith("(暂无"):
        return []  # 圣经还是空的,没有可对照的约束

    prompt = CONSISTENCY_CHECK_PROMPT.format(
        active_facts=active_facts,
        rolling_summary=rolling_summary or "(无)",
        chapter_number=chapter_number,
        chapter_text=chapter_text[:12000],
    )
    try:
        raw = await get_adapter_for(Task.CONSISTENCY).ask(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.error("一致性检查调用失败: %s", exc)
        return []

    data = parse_llm_json(raw)
    issues = data.get("issues") or []
    if issues:
        logger.warning("第 %d 章发现 %d 个一致性问题", chapter_number, len(issues))
    return [i for i in issues if isinstance(i, dict)]
