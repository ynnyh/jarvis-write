# app/engines/cascade/differ.py
# -*- coding: utf-8 -*-
"""大纲改动 diff 与分级。

流程(docs/03-engines.md):
1. 字段级 diff:内容没变(content_hash 相同)→ 直接返回 unchanged
2. 规则粗筛:动了情节性字段(summary/foreshadowing/plot_twist_level/
   characters_involved/title)→ 疑似 major;只动修饰性字段 → minor
3. 疑似 major → LLM 精判,产出 change_type + 改动概要
4. 存 OutlineVersion 快照;本章已有正文 → 标 is_stale
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Chapter, Outline, OutlineVersion
from app.engines.consistency.extractor import parse_llm_json
from app.engines.pipeline.blueprint import _outline_content_hash
from app.llm.router import Task, get_adapter_for
from app.prompts.cascade import CHANGE_CLASSIFY_PROMPT

logger = logging.getLogger("jarvis-write.cascade")

# 可编辑字段(与 outlines 表语义字段一致)
OUTLINE_EDITABLE_FIELDS = (
    "title",
    "chapter_role",
    "chapter_purpose",
    "suspense_level",
    "foreshadowing",
    "plot_twist_level",
    "summary",
    "characters_involved",
    "key_items",
    "scene_location",
)

# 动了这些字段 → 疑似 major,交 LLM 精判
_PLOT_FIELDS = {
    "title",
    "summary",
    "foreshadowing",
    "plot_twist_level",
    "characters_involved",
    "chapter_purpose",
}


def outline_to_dict(o: Outline) -> dict[str, Any]:
    d = {f: getattr(o, f) for f in OUTLINE_EDITABLE_FIELDS}
    d["chapter_number"] = o.chapter_number
    return d


def _fmt(d: dict[str, Any]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in d.items() if k != "chapter_number")


async def apply_outline_edit(
    db: Session, outline: Outline, updates: dict[str, Any]
) -> dict[str, Any]:
    """应用编辑并分级。返回:
    {status: unchanged|saved, change_type, change_summary, changed_fields,
     own_chapter_stale, needs_impact_analysis}
    """
    old = outline_to_dict(outline)

    # 应用编辑
    for field, value in updates.items():
        if field in OUTLINE_EDITABLE_FIELDS and value is not None:
            setattr(outline, field, value)
    new = outline_to_dict(outline)

    new_hash = _outline_content_hash(new)
    if new_hash == outline.content_hash:
        return {
            "status": "unchanged",
            "change_type": None,
            "change_summary": "内容无实质变化",
            "changed_fields": [],
            "own_chapter_stale": False,
            "needs_impact_analysis": False,
        }

    changed = [f for f in OUTLINE_EDITABLE_FIELDS if old.get(f) != new.get(f)]

    # ---- 分级:规则粗筛 ----
    suspect_major = bool(set(changed) & _PLOT_FIELDS)
    change_type, change_summary = "minor", f"修改了 {'、'.join(changed)}"

    # ---- LLM 精判(仅疑似 major 时) ----
    if suspect_major:
        prompt = CHANGE_CLASSIFY_PROMPT.format(
            chapter_number=outline.chapter_number,
            old_outline=_fmt(old),
            new_outline=_fmt(new),
            changed_fields="、".join(changed),
        )
        try:
            raw = await get_adapter_for(Task.IMPACT).ask(prompt)
            data = parse_llm_json(raw)
            if data.get("change_type") in ("major", "minor"):
                change_type = data["change_type"]
                change_summary = data.get("summary") or change_summary
            else:
                change_type = "major"  # 精判失败,保守当 major
        except Exception as exc:  # noqa: BLE001 — LLM 失败保守当 major
            logger.warning("改动精判失败,保守按 major 处理: %s", exc)
            change_type = "major"

    # ---- 落库:升版本 + 快照 ----
    outline.content_hash = new_hash
    outline.current_version += 1
    db.add(
        OutlineVersion(
            outline_id=outline.id,
            version=outline.current_version,
            snapshot=new,
            change_type=change_type,
            change_summary=change_summary,
        )
    )

    # ---- 本章已有正文 → 失配标记 ----
    own_stale = False
    ch = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == outline.project_id,
            Chapter.chapter_number == outline.chapter_number,
            Chapter.final_content != "",
        )
        .first()
    )
    if ch:
        ch.is_stale = True
        ch.status = "stale"
        own_stale = True

    db.flush()
    logger.info(
        "大纲编辑: 第%d章 v%d [%s] %s",
        outline.chapter_number, outline.current_version, change_type, change_summary,
    )
    return {
        "status": "saved",
        "change_type": change_type,
        "change_summary": change_summary,
        "changed_fields": changed,
        "own_chapter_stale": own_stale,
        "needs_impact_analysis": change_type == "major",
    }
