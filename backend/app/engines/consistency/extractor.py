# app/engines/consistency/extractor.py
# -*- coding: utf-8 -*-
"""章后状态抽取器:LLM 从正文抽取持久状态变化 → 写回圣经与伏笔表。

这是让圣经"活起来"的闭环:不抽取,圣经就是死数据。
"""
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from app.db.models import Entity
from app.engines.consistency.bible import BibleService
from app.engines.consistency.foreshadow import ForeshadowScheduler
from app.llm.router import Task, get_adapter_for
from app.prompts.consistency import EXTRACTION_PROMPT

logger = logging.getLogger("jarvis-write.extractor")


def parse_llm_json(text: str) -> dict:
    """宽容解析 LLM 输出的 JSON:剥 markdown 围栏、截取首尾大括号。"""
    text = text.strip()
    # 剥 ```json ... ```
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # 截取最外层大括号
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("LLM JSON 解析失败: %s;原文前200字: %s", exc, text[:200])
        return {}


async def extract_and_apply(
    db: Session, project_id: int, chapter_number: int, chapter_text: str
) -> dict:
    """跑一次抽取并写库。返回统计;抽取失败返回空统计,不阻塞主流程。"""
    bible = BibleService(db, project_id)
    scheduler = ForeshadowScheduler(db, project_id)

    known_entities = "\n".join(
        f"- {e.name}({e.entity_type})"
        for e in db.query(Entity).filter(Entity.project_id == project_id)
    ) or "(暂无)"

    active_facts = bible.hard_constraints_block(chapter_number)
    open_fs = "\n".join(
        f"- {f.description}(第{f.chapter_planted}章埋设)"
        for f in scheduler.open_foreshadowings()
    ) or "(暂无)"

    prompt = EXTRACTION_PROMPT.format(
        known_entities=known_entities,
        active_facts=active_facts,
        open_foreshadowings=open_fs,
        chapter_number=chapter_number,
        chapter_text=chapter_text[:12000],  # 防超长
    )

    try:
        raw = await get_adapter_for(Task.FACT_EXTRACT).ask(prompt)
    except Exception as exc:  # noqa: BLE001 — 抽取失败不阻塞章节生成
        logger.error("抽取调用失败: %s", exc)
        return {}

    extraction = parse_llm_json(raw)
    if not extraction:
        return {}

    bible_stats = bible.apply_extraction(chapter_number, extraction)
    fs_stats = scheduler.apply_ops(
        chapter_number, extraction.get("foreshadow_ops") or []
    )
    return {"bible": bible_stats, "foreshadow": fs_stats}
