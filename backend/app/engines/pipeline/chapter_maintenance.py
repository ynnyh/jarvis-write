# app/engines/pipeline/chapter_maintenance.py
# -*- coding: utf-8 -*-
"""章后维护链路:文风备忘增量更新 / 滚动摘要链重建 / 章后状态落库。

从 chapter.py 拆出:这三件事都是「章定稿之后」的旁路维护,与生成主流程
(generate_chapter)共享 _rolling_summary 读路径(见 chapter_context),但
触发方不同——生成收尾、圣经抽取后的手工重建、发布/抽取后的补偿。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterSummary, Project
from app.engines.common import get_outline
from app.engines.consistency.extractor import extract_and_apply
from app.engines.consistency.motifs import persist_motif_issues
from app.engines.devices import persist_device_issues
from app.engines.pipeline.chapter_context import _rolling_summary
from app.engines.pipeline.handoff import extract_handoff_contract
from app.engines.timeline import persist_clock_issues
from app.llm.router import Task, get_adapter_for
from app.prompts.chapter import ROLLING_SUMMARY_PROMPT, STYLE_MEMO_UPDATE_PROMPT

logger = logging.getLogger("jarvis-write.chapter")


async def update_style_memo(
    db: Session,
    project: Project,
    chapter_number: int,
    chapter_text: str,
    flavor_notes: str = "",
) -> str | None:
    """写完一章后增量更新文风备忘(快模型档,失败不阻塞主流程)。

    与滚动摘要互补:摘要记"发生了什么",备忘记"这本书怎么写"(调性/人物声音/意象)。
    随书累积,注入后续章草稿,防长篇后段人物声音漂移、调性变淡。返回更新后的备忘。
    flavor_notes:本章 AI 味体检的高频类别(memo_notes_block 渲染,病灶回流 P5),
    沉淀进备忘「要避开的」小节;干净章节传空串,整块省略。
    """
    prev = (project.style_memo or "").strip() or "(尚无,这是第一次积累)"
    try:
        memo = await get_adapter_for(Task.SUMMARY).ask(
            STYLE_MEMO_UPDATE_PROMPT.format(
                previous_memo=prev,
                chapter_number=chapter_number,
                chapter_text=chapter_text[:8000],
                flavor_notes=flavor_notes,
            )
        )
    except Exception:  # noqa: BLE001 — 备忘更新失败不影响正文与主流程
        logger.warning("文风备忘更新失败(第 %d 章),跳过", chapter_number, exc_info=True)
        return None
    memo = (memo or "").strip()
    if not memo:
        return None
    project.style_memo = memo
    db.flush()
    db.commit()
    return memo


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
        outline = get_outline(db, project.id, ch.chapter_number)
        title = outline.title if outline else ""
        text = ch.final_content
        # 读完即提交,释放读快照:LLM 调用期间用量记账会在别的连接提交,让旧快照过期,
        # 之后 UPDATE 升级写锁会撞 SQLITE_BUSY(WAL 下该错误不走 busy_timeout,直接失败)。
        db.commit()
        new_summary = await get_adapter_for(Task.SUMMARY).ask(
            ROLLING_SUMMARY_PROMPT.format(
                previous_summary=prev,
                chapter_number=ch.chapter_number,
                chapter_title=title,
                chapter_text=text,
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
        # 每章提交一次:别拿着写事务跨下一轮 LLM 调用(阻塞并发写,快照也会过期)
        db.commit()
        rebuilt.append(ch.chapter_number)

    logger.info("摘要链重建完成: %s", rebuilt)
    return rebuilt


async def apply_chapter_tail(
    db: Session,
    project: Project,
    chapter: Chapter,
    chapter_number: int,
    final: str,
    outline_title: str,
    report=None,
) -> dict:
    """门禁通过后的章后链路:抽取写圣经 → 滚动摘要 → 章末交接契约。

    generate_chapter 干净路径与 quarantined 放行端点(gate-release)共用。
    滚动摘要按「本章之前」的链尾现算(与生成时口径一致);契约提取放在摘要
    之后:摘要链是下游章生成的硬依赖,契约失败(只落 failed 行)绝不能拖累它。
    返回抽取统计。
    """

    def _report(stage: str) -> None:
        if report:
            try:
                report(stage)
            except Exception:  # noqa: BLE001 — 进度上报绝不影响主流程
                pass

    # ---- 章后抽取:状态变化写回圣经/伏笔表(闭环) ----
    # extract_and_apply 自管事务纪律(入口丢掉遗留读快照、LLM 前后各提交),
    # 故这里无需再手工 commit。
    _report("5/6 抽取状态写入故事圣经")
    logger.info("第 %d 章:抽取状态变化...", chapter_number)
    extraction_stats = await extract_and_apply(db, project.id, chapter_number, final)

    # ---- 滚动摘要更新 ----
    _report("6/6 更新前情摘要")
    logger.info("第 %d 章:更新前情摘要...", chapter_number)
    rolling = _rolling_summary(db, project.id, chapter_number)
    new_summary = await get_adapter_for(Task.SUMMARY).ask(
        ROLLING_SUMMARY_PROMPT.format(
            previous_summary=rolling,
            chapter_number=chapter_number,
            chapter_title=outline_title,
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
    db.commit()

    # ---- 章末交接契约提取:章末瞬态(时间/地点/人物即时状态)落 chapter_states ----
    # 供下一章草稿注入(见 load_handoff_block)与下章门禁对照(见 checker)。
    # 失败只落 failed 行留痕,不阻塞主流程(docs/08 §4 可降级)。
    _report("提取章末交接契约")
    await extract_handoff_contract(
        db, chapter, chapter_number, final, get_adapter_for(Task.HANDOFF_EXTRACT)
    )

    # ---- 确定性故事时钟校验(advisory,不阻断):落 source=clock 建议 ----
    # 放在契约抽取【之后】——此刻本章 story_day 已入库,book_timeline 能看到本章这条,
    # 才能算牵涉本章的倒计时口径/天数倒流。纯算术、无 LLM;自吞异常 + rollback,
    # 绝不拖垮章后主链路(对齐 canon 建议的隔离范式)。门禁阻断路径另由 timeline_block
    # 把权威天数轴喂给 LLM 完成,这里是事后精确补网。
    try:
        persist_clock_issues(db, project.id, chapter, final)
    except Exception as exc:  # noqa: BLE001 — 时钟校验绝不阻塞主流程
        db.rollback()
        logger.warning("第 %d 章故事时钟校验失败(已跳过): %s", chapter_number, exc)

    # ---- 常驻装置断档校验(advisory,不阻断):落 source=devices 建议 ----
    # 同样放在契约抽取之后(此刻本章 devices_present 已入库)。与生成端催场块
    # (devices_reminder_block)配对成闭环:催过了本章仍没让装置出场,才在这里软报。
    # 与时钟校验各自 try/except,一边挂了不影响另一边。
    try:
        persist_device_issues(db, project.id, chapter, final)
    except Exception as exc:  # noqa: BLE001 — 装置校验绝不阻塞主流程
        db.rollback()
        logger.warning("第 %d 章常驻装置校验失败(已跳过): %s", chapter_number, exc)

    # ---- 桥段台账软报(advisory,不阻断):落 source=repeat 建议 ----
    # 纯 contains 零 LLM:终稿里又出现雷区/已写滥母题时亮出来(雷区 major、
    # 台账 minor),放在抽取之后——此刻本章母题已入库,台账聚合口径才完整。
    # 与时钟/装置各自 try/except,一边挂了不影响另一边。
    try:
        persist_motif_issues(db, project.id, chapter, final)
    except Exception as exc:  # noqa: BLE001 — 桥段软报绝不阻塞主流程
        db.rollback()
        logger.warning("第 %d 章桥段台账校验失败(已跳过): %s", chapter_number, exc)
    return extraction_stats
