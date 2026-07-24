# app/engines/pipeline/word_guard.py
# -*- coding: utf-8 -*-
"""字数守卫:finalize 后检查字数,超标则压缩重写或自动拆章。

判定逻辑(基于 project.word_guard_ratio,默认 1.5):
  ratio <= guard_ratio        → 不干预
  guard_ratio < ratio <= 2.0  → 压缩重写(一次 LLM 调用)
  ratio > 2.0 且 auto_split   → 拆成两章(断点选择 + 编号顺移 + 圣经重抽取)

拆章是最重操作:涉及 outline/chapter/summary/fact/foreshadowing/knowledge_state
六张表的编号顺移,以及原章圣经抽取的 purge + 两半重新抽取。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import (
    Chapter,
    ChapterSummary,
    Fact,
    Foreshadowing,
    KnowledgeState,
    Outline,
    Project,
    Relationship,
)
from app.engines.consistency import BibleService
from app.engines.consistency.extractor import extract_and_apply
from app.llm.router import Task, get_adapter_for
from app.prompts.chapter import COMPRESS_REWRITE_PROMPT, SPLIT_POINT_PROMPT

logger = logging.getLogger("jarvis-write.word-guard")

# 拆章时两半各自的最小字数比例(低于此值说明断点不合理,放弃拆章)
_MIN_SPLIT_HALF_RATIO = 0.4


@dataclass
class GuardResult:
    """字数守卫执行结果。"""

    action: str = "none"  # "none" | "compressed" | "split"
    final_text: str = ""  # 守卫后的正文(压缩后/拆章前半)
    split_info: dict = field(default_factory=dict)  # 拆章时的附加信息


async def word_count_guard(
    db: Session,
    project: Project,
    chapter_number: int,
    outline: Outline,
    final_text: str,
    style_block: str,
    report=None,
) -> GuardResult:
    """字数守卫入口。在 finalize 之后、落库之前调用。

    report: 可选进度回调 fn(str)。
    """
    if not project.word_guard_enabled:
        return GuardResult(action="none", final_text=final_text)

    target = project.target_words_per_chapter
    actual = len(final_text)
    ratio = actual / max(target, 1)
    guard_ratio = project.word_guard_ratio or 1.5

    if ratio <= guard_ratio:
        return GuardResult(action="none", final_text=final_text)

    logger.info(
        "第 %d 章字数守卫触发:目标 %d,实际 %d,比率 %.2f",
        chapter_number, target, actual, ratio,
    )

    # 中度超标:压缩重写
    if ratio <= 2.0:
        if report:
            report("字数超标,压缩重写中")
        compressed = await _compress_rewrite(final_text, target, outline.summary)
        if compressed and _compress_acceptable(compressed, target, guard_ratio):
            logger.info("第 %d 章压缩成功:%d → %d 字", chapter_number, actual, len(compressed))
            return GuardResult(action="compressed", final_text=compressed)
        # 压缩失败或不达标,回退原文
        logger.warning("第 %d 章压缩结果不达标,保留原文", chapter_number)
        return GuardResult(action="none", final_text=final_text)

    # 严重超标:拆章
    if not project.auto_split_enabled:
        logger.info("第 %d 章严重超标但未启用自动拆章,保留原文", chapter_number)
        return GuardResult(action="none", final_text=final_text)

    if report:
        report("字数严重超标,自动拆章中")
    result = await _split_chapter(db, project, chapter_number, outline, final_text, target)
    return result


# ---------------------------------------------------------------------------
# 压缩重写
# ---------------------------------------------------------------------------


async def _compress_rewrite(text: str, target: int, chapter_summary: str) -> str | None:
    """调用 LLM 压缩正文到目标字数。失败返回 None。"""
    try:
        prompt = COMPRESS_REWRITE_PROMPT.format(
            actual_words=len(text),
            target_words=target,
            chapter_summary=chapter_summary,
            draft_text=text,
        )
        result = await get_adapter_for(Task.FINALIZE).ask(prompt)
        return result.strip() if result else None
    except Exception:  # noqa: BLE001 — 压缩失败不阻塞生成
        logger.exception("压缩重写 LLM 调用失败")
        return None


def _compress_acceptable(compressed: str, target: int, guard_ratio: float) -> bool:
    """压缩结果是否可接受:不能仍超标,也不能砍太狠。"""
    length = len(compressed)
    if length > target * guard_ratio:
        return False  # 还是太长
    if length < target * 0.5:
        return False  # 砍太狠,内容可能丢失
    return True


# ---------------------------------------------------------------------------
# 拆章
# ---------------------------------------------------------------------------


async def _split_chapter(
    db: Session,
    project: Project,
    chapter_number: int,
    outline: Outline,
    full_text: str,
    target: int,
) -> GuardResult:
    """将超长章节拆为两章,同步所有关联表。"""

    # 1. 让 LLM 找断点
    split_info = await _find_split_point(full_text, target)
    if split_info is None:
        logger.warning("第 %d 章拆章断点解析失败,保留原文", chapter_number)
        return GuardResult(action="none", final_text=full_text)

    # 2. 按段落分割
    paragraphs = [p for p in full_text.split("\n") if p.strip()]
    idx = split_info["split_paragraph_index"]
    if idx < 1 or idx >= len(paragraphs) - 1:
        logger.warning("第 %d 章断点索引 %d 越界,保留原文", chapter_number, idx)
        return GuardResult(action="none", final_text=full_text)

    part_a = "\n".join(paragraphs[: idx + 1])
    part_b = "\n".join(paragraphs[idx + 1:])

    # 3. 校验两半长度
    if len(part_a) < target * _MIN_SPLIT_HALF_RATIO or len(part_b) < target * _MIN_SPLIT_HALF_RATIO:
        logger.warning(
            "第 %d 章拆章两半不均(a=%d, b=%d),保留原文",
            chapter_number, len(part_a), len(part_b),
        )
        return GuardResult(action="none", final_text=full_text)

    # 4. 执行数据库操作
    new_chapter_number = chapter_number + 1
    _shift_tables_after(db, project.id, chapter_number)

    # 5. 更新当前章 outline 为 part_a 的元信息
    outline.title = split_info.get("chapter_a_title", outline.title)
    outline.summary = split_info.get("chapter_a_summary", outline.summary)
    # 伏笔操作归属
    if split_info.get("foreshadowing_goes_to") == "b":
        outline.foreshadowing = "(伏笔操作已移至下一章)"

    # 6. 创建新 Outline(N+1)
    new_outline = Outline(
        project_id=project.id,
        chapter_number=new_chapter_number,
        title=split_info.get("chapter_b_title", f"第{new_chapter_number}章"),
        chapter_role="承接",
        chapter_purpose="承接上章,推进剧情",
        suspense_level=outline.suspense_level,
        foreshadowing=outline.foreshadowing if split_info.get("foreshadowing_goes_to") == "b" else "无",
        summary=split_info.get("chapter_b_summary", ""),
        characters_involved=outline.characters_involved,
        key_items=outline.key_items,
        scene_location=outline.scene_location,
        content_hash="",
        current_version=1,
    )
    db.add(new_outline)
    db.flush()

    # 7. 创建新 Chapter(N+1)
    new_chapter = Chapter(
        project_id=project.id,
        outline_id=new_outline.id,
        chapter_number=new_chapter_number,
        draft_content=part_b,
        final_content=part_b,
        word_count=len(part_b),
        outline_version_used=1,
        is_stale=False,
        status="finalized",
    )
    db.add(new_chapter)

    # 8. 把第 N 章正文原子性地改成 part_a —— 关键防损坏点。
    #    拆章跑在 generate_chapter 落库之前,第 N 章此刻可能还没建行(首次生成)或
    #    正文仍是拆前全文(重写)。若不在这里就把它落成 part_a,随后第 9 步
    #    _resync_bible 内的 extract_and_apply 入口即 commit(并发纪律硬约束),会把
    #    「第 N 章=全文 + 第 N+1 章=后半」的重复态刷上磁盘,并横跨随后数分钟的圣经/
    #    摘要 LLM 调用 —— 中途崩溃/被杀则正文重复、章号错乱。故必须让结构改动与
    #    「第 N 章=part_a」在同一事务先落地,提交点即自洽拆章态。
    cur_chapter = (
        db.query(Chapter)
        .filter(Chapter.project_id == project.id, Chapter.chapter_number == chapter_number)
        .first()
    )
    if cur_chapter is None:
        cur_chapter = Chapter(
            project_id=project.id,
            outline_id=outline.id,
            chapter_number=chapter_number,
        )
        db.add(cur_chapter)
    cur_chapter.outline_id = outline.id
    cur_chapter.draft_content = part_a
    cur_chapter.final_content = part_a
    cur_chapter.word_count = len(part_a)
    cur_chapter.outline_version_used = outline.current_version
    cur_chapter.is_stale = False
    cur_chapter.status = "finalized"

    # 9. 更新项目总章数
    project.target_chapters = (project.target_chapters or 0) + 1

    # 结构 + 两章正文原子提交:此刻磁盘已是完整可用的拆章结果,
    # 后续圣经/摘要即便崩在半途,留下的也只是缺一块可重建的辅助数据,而非损坏正文。
    db.commit()

    # 10. 圣经同步:purge 原章抽取 → 对两半分别重新抽取(self-commit)
    await _resync_bible_after_split(db, project, chapter_number, new_chapter_number, part_a, part_b)

    # 11. 为两章生成滚动摘要(self-commit)
    await _rebuild_split_summaries(db, project, chapter_number, new_chapter_number, part_a, part_b)

    logger.info(
        "第 %d 章拆章完成:a=%d字, b=%d字 → 新第 %d 章",
        chapter_number, len(part_a), len(part_b), new_chapter_number,
    )

    return GuardResult(
        action="split",
        final_text=part_a,
        split_info={
            "original_chapter": chapter_number,
            "new_chapter": new_chapter_number,
            "new_title": new_outline.title,
            "part_a_words": len(part_a),
            "part_b_words": len(part_b),
            "total_chapters_now": project.target_chapters,
            "reason": split_info.get("reason", ""),
        },
    )


async def _find_split_point(full_text: str, target: int) -> dict | None:
    """调用 LLM 找拆章断点,返回解析后的 dict 或 None。"""
    try:
        prompt = SPLIT_POINT_PROMPT.format(
            actual_words=len(full_text),
            target_words=target,
            full_text=full_text,
        )
        raw = await get_adapter_for(Task.FINALIZE).ask(prompt)
        # 提取 JSON(模型可能包裹在 ```json ... ``` 里)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        logger.exception("拆章断点 LLM 调用/解析失败")
        return None


# ---------------------------------------------------------------------------
# 编号顺移
# ---------------------------------------------------------------------------


def _shift_tables_after(db: Session, project_id: int, after_number: int) -> None:
    """将 chapter_number > after_number 的所有关联记录 +1。

    涉及: outlines, chapters, chapter_summaries, facts(valid_from/valid_until/source_chapter),
    foreshadowings(chapter_planted/expected_payoff_chapter/payoff_chapter/reinforcement_chapters),
    relationships(valid_from/valid_until), knowledge_states(known_from_chapter)。
    从大到小更新避免中间态冲突。
    """
    # --- outlines ---
    for row in (
        db.query(Outline)
        .filter(Outline.project_id == project_id, Outline.chapter_number > after_number)
        .order_by(Outline.chapter_number.desc())
        .all()
    ):
        row.chapter_number += 1

    # --- chapters ---
    for row in (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.chapter_number > after_number)
        .order_by(Chapter.chapter_number.desc())
        .all()
    ):
        row.chapter_number += 1

    # --- chapter_summaries ---
    for row in (
        db.query(ChapterSummary)
        .filter(ChapterSummary.project_id == project_id, ChapterSummary.chapter_number > after_number)
        .order_by(ChapterSummary.chapter_number.desc())
        .all()
    ):
        row.chapter_number += 1

    # --- facts: valid_from / valid_until / source_chapter ---
    facts = db.query(Fact).filter(Fact.project_id == project_id).all()
    for f in facts:
        if f.valid_from > after_number:
            f.valid_from += 1
        if f.valid_until is not None and f.valid_until > after_number:
            f.valid_until += 1
        if f.source_chapter > after_number:
            f.source_chapter += 1

    # --- relationships: valid_from / valid_until ---
    rels = db.query(Relationship).filter(Relationship.project_id == project_id).all()
    for r in rels:
        if r.valid_from > after_number:
            r.valid_from += 1
        if r.valid_until is not None and r.valid_until > after_number:
            r.valid_until += 1

    # --- foreshadowings ---
    foreshadows = db.query(Foreshadowing).filter(Foreshadowing.project_id == project_id).all()
    for fs in foreshadows:
        if fs.chapter_planted > after_number:
            fs.chapter_planted += 1
        if fs.expected_payoff_chapter is not None and fs.expected_payoff_chapter > after_number:
            fs.expected_payoff_chapter += 1
        if fs.earliest_payoff_chapter is not None and fs.earliest_payoff_chapter > after_number:
            fs.earliest_payoff_chapter += 1
        if fs.payoff_chapter is not None and fs.payoff_chapter > after_number:
            fs.payoff_chapter += 1
        # reinforcement_chapters 是 JSON 列表
        if fs.reinforcement_chapters:
            fs.reinforcement_chapters = [
                c + 1 if c > after_number else c for c in fs.reinforcement_chapters
            ]

    # --- knowledge_states ---
    for ks in (
        db.query(KnowledgeState)
        .filter(KnowledgeState.project_id == project_id, KnowledgeState.known_from_chapter > after_number)
        .all()
    ):
        ks.known_from_chapter += 1

    db.flush()
    logger.info("编号顺移完成:chapter_number > %d 全部 +1", after_number)


# ---------------------------------------------------------------------------
# 圣经重同步
# ---------------------------------------------------------------------------


async def _resync_bible_after_split(
    db: Session,
    project: Project,
    chapter_a: int,
    chapter_b: int,
    text_a: str,
    text_b: str,
) -> None:
    """拆章后重建故事圣经:purge 原章 → 对两半分别抽取。"""
    try:
        bible = BibleService(db, project.id)
        bible.purge_chapter_extraction(chapter_a)
        db.flush()

        await extract_and_apply(db, project.id, chapter_a, text_a)
        db.flush()
        await extract_and_apply(db, project.id, chapter_b, text_b)
        db.flush()
        logger.info("拆章圣经重同步完成:第 %d、%d 章", chapter_a, chapter_b)
    except Exception:  # noqa: BLE001 — 圣经同步失败不阻塞拆章
        logger.exception("拆章后圣经重同步失败(不影响正文)")


# ---------------------------------------------------------------------------
# 滚动摘要重建
# ---------------------------------------------------------------------------


async def _rebuild_split_summaries(
    db: Session,
    project: Project,
    chapter_a: int,
    chapter_b: int,
    text_a: str,
    text_b: str,
) -> None:
    """为拆出的两章生成滚动摘要。"""
    from app.prompts.chapter import ROLLING_SUMMARY_PROMPT

    try:
        # 取前一章的摘要作为基础
        prev_row = (
            db.query(ChapterSummary)
            .filter(
                ChapterSummary.project_id == project.id,
                ChapterSummary.chapter_number < chapter_a,
            )
            .order_by(ChapterSummary.chapter_number.desc())
            .first()
        )
        prev_summary = prev_row.rolling_summary if prev_row else "(无,本章为开篇)"

        outline_a = (
            db.query(Outline)
            .filter(Outline.project_id == project.id, Outline.chapter_number == chapter_a)
            .first()
        )
        outline_b = (
            db.query(Outline)
            .filter(Outline.project_id == project.id, Outline.chapter_number == chapter_b)
            .first()
        )

        # 第 A 章摘要
        summary_a = await get_adapter_for(Task.SUMMARY).ask(
            ROLLING_SUMMARY_PROMPT.format(
                previous_summary=prev_summary,
                chapter_number=chapter_a,
                chapter_title=outline_a.title if outline_a else "",
                chapter_text=text_a,
            )
        )
        row_a = (
            db.query(ChapterSummary)
            .filter(
                ChapterSummary.project_id == project.id,
                ChapterSummary.chapter_number == chapter_a,
            )
            .first()
        )
        if row_a is None:
            row_a = ChapterSummary(project_id=project.id, chapter_number=chapter_a)
            db.add(row_a)
        row_a.rolling_summary = summary_a.strip()
        db.flush()

        # 第 B 章摘要(基于 A 的摘要)
        summary_b = await get_adapter_for(Task.SUMMARY).ask(
            ROLLING_SUMMARY_PROMPT.format(
                previous_summary=summary_a.strip(),
                chapter_number=chapter_b,
                chapter_title=outline_b.title if outline_b else "",
                chapter_text=text_b,
            )
        )
        row_b = (
            db.query(ChapterSummary)
            .filter(
                ChapterSummary.project_id == project.id,
                ChapterSummary.chapter_number == chapter_b,
            )
            .first()
        )
        if row_b is None:
            row_b = ChapterSummary(project_id=project.id, chapter_number=chapter_b)
            db.add(row_b)
        row_b.rolling_summary = summary_b.strip()
        db.flush()
        # 自管事务:不依赖调用方提交(_split_chapter 末尾已无总 commit)。与
        # extract_and_apply 同一纪律——收尾函数自洽落地,崩在这之后也不丢已重建的摘要。
        db.commit()

        logger.info("拆章摘要重建完成:第 %d、%d 章", chapter_a, chapter_b)
    except Exception:  # noqa: BLE001 — 摘要失败不阻塞
        logger.exception("拆章后摘要重建失败(不影响正文)")
        db.rollback()  # 丢掉半截摘要,别把未提交的脏写带回调用方事务
