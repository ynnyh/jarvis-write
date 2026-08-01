# app/engines/timeline.py
# -*- coding: utf-8 -*-
"""全书剧情时间线(docs/08 §7 P2-⑨ 的轻量落地:不独立建表,从契约聚合)。

设计取舍:契约(chapter_states)里已有各章章末的剧情时间/地点/时间跳跃提示,
再建一张 LLM 时间线表是重复真相源——这里零 LLM 直接从有效契约(提取成功
且指纹对应当前正文)聚合,两个用途:
1. prompt 注入(timeline_block):写前预审与写后门禁在比对"相邻两章"之外,
   再看到全书时间走向,抓跨章时间倒流/跳跃不合理(相邻对照盖不住的盲区);
2. 看板展示(book_timeline → GET /api/projects/{id}/timeline)。

契约缺失的老章节自然跳过(时间线断档),批量补提契约后自动补齐。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterState
from app.engines.pipeline.handoff import _fresh_contract

# prompt 注入只带最近若干条,防长书 token 膨胀(相邻对照已有上章契约兜底)
_PROMPT_MAX_ENTRIES = 15


def book_timeline(db: Session, project_id: int) -> list[dict]:
    """全书剧情时间线:各章章末的剧情时间/地点/跳跃提示,按章号升序。

    只收有效契约(提取成功 + 指纹对应当前正文);无契约/失效的章跳过。
    """
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )
    items: list[dict] = []
    for ch in chapters:
        row = db.query(ChapterState).filter(ChapterState.chapter_id == ch.id).first()
        contract = _fresh_contract(row, ch)
        if contract is None:
            continue
        items.append({
            "chapter": ch.chapter_number,
            "in_story_time": contract.get("in_story_time"),
            "location": contract.get("location"),
            "scene_continues": bool(contract.get("scene_continues")),
            "time_jump_hint": contract.get("time_jump_hint") or "none",
        })
    return items


def timeline_block(db: Session, project_id: int, upto: int) -> str:
    """prompt 注入文本块:第 upto 章之前的全书时间线(只带最近 N 条)。

    无有效契约 → "(无)"占位(老书时间线断档,由调用方提示先补契约)。
    """
    items = [i for i in book_timeline(db, project_id) if i["chapter"] < upto]
    if not items:
        return "(无全书时间线——老书缺章末契约,可先在编辑部批量补提)"
    items = items[-_PROMPT_MAX_ENTRIES:]
    lines = ["(各章章末的剧情时间/地点,供判断时间是否倒流、位置迁移是否合理)"]
    for i in items:
        seg = f"第{i['chapter']}章末:{i['in_story_time'] or '时间未知'}"
        if i.get("location"):
            seg += f" @ {i['location']}"
        hint = i.get("time_jump_hint")
        if hint and hint != "none":
            seg += f"(下章跳跃:{hint})"
        lines.append(seg)
    return "\n".join(lines)
