# app/engines/refresh.py
# -*- coding: utf-8 -*-
"""重构翻新引擎:把已有书按新逻辑(beats/文风备忘/去AI味)翻新。

四个能力,都能作用于单章或选定章节批量(见 api/refresh.py 的 job 封装):
1. backfill_beats     —— 为已有 outline 回填场景节拍(重度翻新的前置)
2. seed_style_memo    —— 扫已有正文生成初始文风备忘(重度翻新前先跑,保证声音统一)
3. light_refresh_chapter —— 轻度:锁情节重润(去AI味+新文风),不动情节,复用润色引擎
4. 重度翻新 = 直接重跑 generate_chapter(它自带 beats/concept/备忘注入 + 重抽圣经 +
   重建下游摘要),故不在本模块另写,由 api 层调用 pipeline.generate_chapter。

设计取舍:轻度走 polish_chapter(安全、快、锁事实),重度走整章重写(彻底但要重抽圣经)。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.chapter_versions import snapshot_chapter
from app.db.models import Chapter, Outline, Project
from app.engines.common import get_outline
from app.engines.pipeline.chapter import update_style_memo
from app.engines.polish.polisher import polish_text
from app.llm.router import Task, get_adapter_for
from app.prompts.refresh import BEATS_BACKFILL_PROMPT

logger = logging.getLogger("jarvis-write.refresh")

_MAX_BEATS = 6
_BEAT_MAX_CHARS = 60


def _parse_beats(raw: str) -> list[str]:
    """把 LLM 逐行输出解析成节拍 list:去编号/符号、去空行、限长限量。"""
    beats: list[str] = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        # 去掉行首可能残留的编号/符号:"1. " "1、" "- " "* " "• "
        s = s.lstrip("0123456789.、)）·-*• 　").strip()
        if not s:
            continue
        beats.append(s[:_BEAT_MAX_CHARS])
        if len(beats) >= _MAX_BEATS:
            break
    return beats


async def backfill_beats_one(
    db: Session, project: Project, chapter_number: int
) -> list[str]:
    """为一章回填节拍:有正文优先依正文反推,否则依蓝图简述。写回 outline.beats。

    返回回填的节拍;outline 不存在或没内容可依据时返回空 list(不写)。
    """
    outline = get_outline(db, project.id, chapter_number)
    if outline is None:
        return []
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project.id, Chapter.chapter_number == chapter_number)
        .first()
    )
    chapter_text = (ch.final_content if ch else "") or ""
    # 有正文取正文(反推真实场景),无正文回落简述;两者皆空则跳过
    basis = chapter_text[:6000] if chapter_text.strip() else ""
    if not basis and not (outline.summary or "").strip():
        return []
    prompt = BEATS_BACKFILL_PROMPT.format(
        title=outline.title or f"第{chapter_number}章",
        summary=outline.summary or "(无简述)",
        chapter_role=outline.chapter_role or "(未标注)",
        characters="、".join(map(str, outline.characters_involved)) or "(未指定)",
        chapter_text=basis or "(本章尚无正文,请依据简述合理设计)",
    )
    db.commit()  # 释放读快照,别拿着跨 LLM 调用(WAL 写锁纪律)
    raw = await get_adapter_for(Task.BLUEPRINT).ask(prompt)
    beats = _parse_beats(raw)
    if not beats:
        return []
    outline.beats = beats
    db.flush()
    db.commit()
    logger.info("回填节拍(第%d章): %d 个", chapter_number, len(beats))
    return beats


async def backfill_beats(
    db: Session, project: Project, chapter_numbers: list[int], progress=None
) -> dict:
    """批量回填节拍:逐章调用 backfill_beats_one,单章失败不中断整批。

    返回 {filled, skipped, failed}:无大纲/无内容可依据的章进 skipped,
    LLM 异常等失败的章进 failed(已完成的不受影响,可单独重试)。
    """
    filled: list[int] = []
    skipped: list[int] = []
    failed: list[dict] = []
    total = len(chapter_numbers)
    for i, n in enumerate(chapter_numbers, 1):
        if progress:
            try:
                progress(f"[{i}/{total}] 第 {n} 章:回填节拍")
            except Exception:  # noqa: BLE001
                pass
        try:
            beats = await backfill_beats_one(db, project, n)
        except Exception as exc:  # noqa: BLE001 — 单章失败不中断整批
            db.rollback()
            logger.warning("回填节拍失败(第%d章): %s", n, exc)
            failed.append({"chapter": n, "error": str(exc)[:200]})
            continue
        (filled if beats else skipped).append(n)
    return {"filled": filled, "skipped": skipped, "failed": failed}


async def seed_style_memo(
    db: Session, project: Project, sample_chapters: int = 6, progress=None
) -> str | None:
    """扫已有正文,累积出一份初始文风备忘。

    从前 sample_chapters 章按序喂给 update_style_memo(增量累积),让重度翻新一开始
    就有"这本书怎么写"的基线,避免翻新后声音漂移。已有 style_memo 时不覆盖(返回它)。
    """
    if (project.style_memo or "").strip():
        return project.style_memo
    chapters = (
        db.query(Chapter)
        .filter(Chapter.project_id == project.id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .limit(sample_chapters)
        .all()
    )
    if not chapters:
        return None
    memo: str | None = None
    for ch in chapters:
        if progress:
            try:
                progress(f"扫描第 {ch.chapter_number} 章积累文风备忘")
            except Exception:  # noqa: BLE001
                pass
        memo = await update_style_memo(db, project, ch.chapter_number, ch.final_content)
    logger.info("初始文风备忘生成完成(扫 %d 章)", len(chapters))
    return memo


async def light_refresh_chapter(
    db: Session, project: Project, chapter_number: int
) -> dict:
    """轻度翻新:锁情节重润(去AI味+当前文风约束),留快照后覆盖正文。

    复用 polish_text(抽事实→检测AI腔→润色→校验事实),不改情节,故不必重抽圣经。
    返回 {chapter_number, applied, violations, flavor_before, flavor_after, word_count}。
    """
    ch = (
        db.query(Chapter)
        .filter(Chapter.project_id == project.id, Chapter.chapter_number == chapter_number)
        .first()
    )
    if ch is None or not (ch.final_content or "").strip():
        raise ValueError(f"第 {chapter_number} 章还没有正文,无法重润")
    result = await polish_text(ch.final_content, None, project.global_tendency)
    after = (result.get("polished") or "").strip()
    applied = False
    if after and after != ch.final_content.strip():
        snapshot_chapter(db, ch, source="polished")
        ch.final_content = after
        ch.word_count = len(after)
        db.commit()
        applied = True
    return {
        "chapter_number": chapter_number,
        "applied": applied,
        "violations": result.get("violations") or [],
        "flavor_before": result.get("flavor_before"),
        "flavor_after": result.get("flavor_after"),
        "word_count": ch.word_count,
    }
