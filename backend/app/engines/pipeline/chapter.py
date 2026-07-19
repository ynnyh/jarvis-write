# app/engines/pipeline/chapter.py
# -*- coding: utf-8 -*-
"""逐章生成:上下文组装 → 草稿 → 定稿 → 滚动摘要 → 入向量库。

上下文来源(见 docs/02-data-model.md 数据流):
  本章蓝图 + 下章蓝图 + 最近 2 章正文尾部(直接衔接)
  + 滚动前情摘要 + 语义检索历史片段(排除最近 2 章)+ 倾向块
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterSummary, Outline, Project
from app.engines.consistency import BibleService, ForeshadowScheduler
from app.engines.consistency.checker import check_chapter
from app.engines.consistency.extractor import extract_and_apply
from app.engines.consistency.repetition import avoid_block
from app.engines.memory import ChapterMemory
from app.engines.tendency import assemble_tendency
from app.engines.tendency.assembler import render_style_block
from app.llm.router import Task, get_adapter_for
from app.prompts.chapter import (
    CHAPTER_DRAFT_PROMPT,
    CHAPTER_FINALIZE_PROMPT,
    ROLLING_SUMMARY_PROMPT,
)
from app.schemas.tendency import Tendency

logger = logging.getLogger("jarvis-write.chapter")

_RECENT_TAIL_CHARS = 900   # 每章取结尾多少字作直接上文
_RECENT_WINDOW = 2         # 直接注入最近几章的结尾


def _strip_meta(text: str) -> str:
    """清理模型输出的元信息:开头的 markdown 标题/章节名行。"""
    lines = text.strip().splitlines()
    while lines and (
        lines[0].strip().startswith("#")
        or lines[0].strip().startswith("第")
        and "章" in lines[0][:12]
        and len(lines[0].strip()) < 30
    ):
        lines.pop(0)
    return "\n".join(lines).strip()


def _get_outline(db: Session, project_id: int, n: int) -> Outline | None:
    return (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number == n)
        .first()
    )


def _architecture_brief(project: Project) -> str:
    arch = project.architecture
    if arch is None:
        return "(无)"
    return (
        f"核心种子:{arch.core_seed}\n\n"
        f"世界观(节选):{arch.world_building[:600]}\n\n"
        f"角色动力学(节选):{arch.character_dynamics[:900]}"
    )


def _next_chapter_brief(nxt: Outline | None) -> str:
    if nxt is None:
        return "(本章为最后一章,收束全书)"
    return (
        f"第{nxt.chapter_number}章《{nxt.title}》:{nxt.summary}"
        f"(伏笔操作:{nxt.foreshadowing})"
    )


def _recent_tail(db: Session, project_id: int, current: int) -> str:
    """取最近 _RECENT_WINDOW 章定稿的结尾拼接。"""
    parts: list[str] = []
    for n in range(max(1, current - _RECENT_WINDOW), current):
        ch = (
            db.query(Chapter)
            .filter(Chapter.project_id == project_id, Chapter.chapter_number == n)
            .first()
        )
        if ch and ch.final_content:
            parts.append(f"(第{n}章结尾)…{ch.final_content[-_RECENT_TAIL_CHARS:]}")
    return "\n\n".join(parts) or "(本章是第一章,无上文)"


def _rolling_summary(db: Session, project_id: int, current: int) -> str:
    row = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.project_id == project_id,
            ChapterSummary.chapter_number < current,
        )
        .order_by(ChapterSummary.chapter_number.desc())
        .first()
    )
    return row.rolling_summary if row else "(无,本章为开篇)"


async def rebuild_summaries_after(
    db: Session, project: Project, changed_chapter: int, progress=None
) -> list[int]:
    """重建第 changed_chapter 章之后的滚动摘要链。

    重写/手改某章正文后,后续章的滚动摘要都基于旧文,必须顺序重算
    (快模型档,每章一次调用)。返回重建的章号列表。
    """
    laters = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.chapter_number > changed_chapter,
            Chapter.final_content != "",
        )
        .order_by(Chapter.chapter_number)
        .all()
    )
    # 只有当后续章已存在摘要时才需要重建
    later_nums = [c.chapter_number for c in laters]
    if not later_nums:
        return []

    rebuilt: list[int] = []
    for ch in laters:
        if progress:
            try:
                progress(f"重建第 {ch.chapter_number} 章前情摘要")
            except Exception:  # noqa: BLE001
                pass
        prev = _rolling_summary(db, project.id, ch.chapter_number)
        outline = _get_outline(db, project.id, ch.chapter_number)
        new_summary = await get_adapter_for(Task.SUMMARY).ask(
            ROLLING_SUMMARY_PROMPT.format(
                previous_summary=prev,
                chapter_number=ch.chapter_number,
                chapter_title=outline.title if outline else "",
                chapter_text=ch.final_content,
            )
        )
        row = (
            db.query(ChapterSummary)
            .filter(
                ChapterSummary.project_id == project.id,
                ChapterSummary.chapter_number == ch.chapter_number,
            )
            .first()
        )
        if row is None:
            row = ChapterSummary(
                project_id=project.id, chapter_number=ch.chapter_number
            )
            db.add(row)
        row.rolling_summary = new_summary.strip()
        db.flush()
        rebuilt.append(ch.chapter_number)

    logger.info("摘要链重建完成: %s", rebuilt)
    return rebuilt


async def generate_chapter(
    db: Session,
    project: Project,
    chapter_number: int,
    tendency: Tendency | None = None,
    progress=None,
) -> tuple[Chapter, list[dict], dict]:
    """生成一章:草稿 → 定稿 → 一致性检查 → 抽取写圣经 → 摘要 → 入库。

    progress: 可选回调 fn(stage_text),五段各报一次(异步任务进度用)。
    返回 (Chapter, 一致性问题列表, 抽取统计)。
    """

    def _report(stage: str) -> None:
        if progress:
            try:
                progress(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响生成
                pass

    outline = _get_outline(db, project.id, chapter_number)
    if outline is None:
        raise ValueError(f"第 {chapter_number} 章没有大纲,请先生成蓝图")
    next_outline = _get_outline(db, project.id, chapter_number + 1)

    assembled = assemble_tendency("chapter", tendency, project.global_tendency)
    style_block = render_style_block(assembled)

    rolling = _rolling_summary(db, project.id, chapter_number)
    recent = _recent_tail(db, project.id, chapter_number)

    # 语义检索:排除直接上文窗口内的章
    memory = ChapterMemory(project.id)
    query = f"{outline.title} {outline.summary} {' '.join(map(str, outline.characters_involved))}"
    retrieved = await memory.retrieve(
        query, exclude_after=chapter_number - _RECENT_WINDOW
    )
    retrieved_text = "\n---\n".join(retrieved) or "(暂无)"

    # ---- 一致性引擎:硬约束 + 伏笔提醒 + 重复检测 ----
    bible = BibleService(db, project.id)
    hard_constraints = bible.hard_constraints_block(
        chapter_number, [str(c) for c in outline.characters_involved]
    )
    scheduler = ForeshadowScheduler(db, project.id)
    foreshadow_reminders = scheduler.reminder_block(chapter_number)

    recent_full = [
        c.final_content
        for c in db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.chapter_number < chapter_number,
            Chapter.final_content != "",
        )
        .order_by(Chapter.chapter_number.desc())
        .limit(3)
    ]
    avoid_repetition = avoid_block(recent_full)

    # ---- 草稿 ----
    _report("1/5 生成草稿")
    logger.info("第 %d 章:生成草稿...", chapter_number)
    draft_prompt = CHAPTER_DRAFT_PROMPT.format(
        chapter_number=chapter_number,
        chapter_title=outline.title,
        architecture_brief=_architecture_brief(project),
        rolling_summary=rolling,
        recent_tail=recent,
        retrieved_context=retrieved_text,
        hard_constraints=hard_constraints,
        foreshadow_reminders=foreshadow_reminders,
        avoid_repetition=avoid_repetition,
        chapter_role=outline.chapter_role,
        chapter_purpose=outline.chapter_purpose,
        suspense_level=outline.suspense_level,
        foreshadowing=outline.foreshadowing,
        characters_involved="、".join(map(str, outline.characters_involved)) or "(未指定)",
        key_items="、".join(map(str, outline.key_items)) or "无",
        scene_location=outline.scene_location,
        chapter_summary=outline.summary,
        next_chapter_brief=_next_chapter_brief(next_outline),
        word_number=project.target_words_per_chapter,
        style_directives=style_block,
    )
    draft = _strip_meta(await get_adapter_for(Task.DRAFT).ask(draft_prompt))

    # ---- 定稿 ----
    _report("2/5 定稿修订")
    logger.info("第 %d 章:定稿修订...", chapter_number)
    finalize_prompt = CHAPTER_FINALIZE_PROMPT.format(
        chapter_number=chapter_number,
        chapter_title=outline.title,
        chapter_purpose=outline.chapter_purpose,
        foreshadowing=outline.foreshadowing,
        chapter_summary=outline.summary,
        rolling_summary=rolling,
        draft_text=draft,
        style_directives=style_block,
    )
    final = _strip_meta(await get_adapter_for(Task.FINALIZE).ask(finalize_prompt))

    # ---- 落库 ----
    chapter = (
        db.query(Chapter)
        .filter(
            Chapter.project_id == project.id,
            Chapter.chapter_number == chapter_number,
        )
        .first()
    )
    if chapter is None:
        chapter = Chapter(
            project_id=project.id,
            outline_id=outline.id,
            chapter_number=chapter_number,
        )
        db.add(chapter)
    chapter.outline_id = outline.id
    chapter.draft_content = draft
    chapter.final_content = final
    chapter.word_count = len(final)
    chapter.outline_version_used = outline.current_version
    chapter.is_stale = False
    chapter.status = "finalized"
    db.flush()

    # ---- 一致性检查(vs 本章之前的圣经状态) ----
    _report("3/5 一致性检查")
    logger.info("第 %d 章:一致性检查...", chapter_number)
    issues = await check_chapter(
        db, project.id, chapter_number, final, rolling_summary=rolling
    )

    # ---- 章后抽取:状态变化写回圣经/伏笔表(闭环) ----
    _report("4/5 抽取状态写入故事圣经")
    logger.info("第 %d 章:抽取状态变化...", chapter_number)
    extraction_stats = await extract_and_apply(
        db, project.id, chapter_number, final
    )

    # ---- 滚动摘要更新 ----
    _report("5/5 更新前情摘要")
    logger.info("第 %d 章:更新前情摘要...", chapter_number)
    new_summary = await get_adapter_for(Task.SUMMARY).ask(
        ROLLING_SUMMARY_PROMPT.format(
            previous_summary=rolling,
            chapter_number=chapter_number,
            chapter_title=outline.title,
            chapter_text=final,
        )
    )
    srow = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.project_id == project.id,
            ChapterSummary.chapter_number == chapter_number,
        )
        .first()
    )
    if srow is None:
        srow = ChapterSummary(project_id=project.id, chapter_number=chapter_number)
        db.add(srow)
    srow.rolling_summary = new_summary.strip()
    db.flush()

    # ---- 入向量库(失败自动降级,不阻塞) ----
    await memory.add_chapter(chapter_number, final)

    # ---- 重写场景:下游章节的滚动摘要基于旧文,重建 ----
    rebuilt = await rebuild_summaries_after(db, project, chapter_number, progress)
    if rebuilt:
        logger.info("第 %d 章重写,已重建下游摘要: %s", chapter_number, rebuilt)

    logger.info("第 %d 章完成,共 %d 字。", chapter_number, chapter.word_count)
    return chapter, issues, extraction_stats
