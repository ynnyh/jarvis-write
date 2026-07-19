# app/engines/cascade/impact.py
# -*- coding: utf-8 -*-
"""下游影响分析:LLM 判断大纲改动波及哪些后续章节。

只分析不执行 —— 结果呈现给用户,由用户决定重生成哪些(docs/03-engines.md)。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Outline, OutlineVersion, Project
from app.engines.cascade.differ import outline_to_dict, _fmt
from app.engines.consistency import ForeshadowScheduler
from app.engines.consistency.extractor import parse_llm_json
from app.llm.router import Task, get_adapter_for
from app.prompts.cascade import IMPACT_ANALYSIS_PROMPT

logger = logging.getLogger("jarvis-write.cascade")


def _previous_snapshot(db: Session, outline: Outline) -> dict:
    """当前版本之前的快照(diff 的"旧"侧)。"""
    row = (
        db.query(OutlineVersion)
        .filter(
            OutlineVersion.outline_id == outline.id,
            OutlineVersion.version < outline.current_version,
        )
        .order_by(OutlineVersion.version.desc())
        .first()
    )
    return row.snapshot if row else {}


def _latest_change_summary(db: Session, outline: Outline) -> str:
    row = (
        db.query(OutlineVersion)
        .filter(OutlineVersion.outline_id == outline.id)
        .order_by(OutlineVersion.version.desc())
        .first()
    )
    return row.change_summary if row else "(无)"


async def analyze_impact(
    db: Session, project: Project, outline: Outline
) -> dict:
    """分析第 K 章大纲最新一次改动对下游的影响。

    返回 {affected: [{chapter_number, reason, action}], overall, source_chapter}。
    """
    k = outline.chapter_number
    downstream = (
        db.query(Outline)
        .filter(Outline.project_id == project.id, Outline.chapter_number > k)
        .order_by(Outline.chapter_number)
        .all()
    )
    if not downstream:
        return {"affected": [], "overall": "已是最后一章,无下游", "source_chapter": k}

    downstream_text = "\n".join(
        f"第{o.chapter_number}章《{o.title}》:{o.summary}(伏笔操作:{o.foreshadowing})"
        for o in downstream
    )
    open_fs = "\n".join(
        f"- {f.description}(埋于第{f.chapter_planted}章)"
        for f in ForeshadowScheduler(db, project.id).open_foreshadowings()
    ) or "(无)"

    old = _previous_snapshot(db, outline)
    prompt = IMPACT_ANALYSIS_PROMPT.format(
        chapter_number=k,
        change_summary=_latest_change_summary(db, outline),
        old_outline=_fmt(old) if old else "(无历史版本)",
        new_outline=_fmt(outline_to_dict(outline)),
        downstream_outlines=downstream_text,
        open_foreshadowings=open_fs,
    )
    raw = await get_adapter_for(Task.IMPACT).ask(prompt)
    data = parse_llm_json(raw)

    valid_numbers = {o.chapter_number for o in downstream}
    affected = [
        a
        for a in (data.get("affected") or [])
        if isinstance(a, dict) and a.get("chapter_number") in valid_numbers
    ]
    result = {
        "affected": affected,
        "overall": data.get("overall") or "",
        "source_chapter": k,
    }
    logger.info("影响分析: 第%d章改动波及 %d 章", k, len(affected))
    return result
