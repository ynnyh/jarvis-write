# app/engines/diagnosis.py
# -*- coding: utf-8 -*-
"""全书体检与老书补契约(docs/08 §7 P2-⑧ 与「历史数据兼容」)。

- diagnose_book:逐章跑一致性检查(圣经 + 上章契约 + 上章结尾三路对照),
  问题以 source="diag" 幂等落库(清旧账重建,与门禁同规约),在各章
  「审核报告」面板按来源「诊断」展示与流转(open → resolved/ignored)。
- backfill_contracts:为没有有效契约(从未提取 / 提取失败 / 正文已改指纹失效)
  的已成文章节批量补提章末交接契约,让门禁与写前预审对老书也能三路对照。
- rule_scan_book:规则扫描——逐章对照项目里的「世界观硬规则」(world_rules
  钉板)体检正文,违反项以 source="rules" 幂等落库。与全书体检互补:
  体检查前后矛盾,规则扫描查"违反作者钉死的设定/常识"(错得前后一致也能抓到)。

两函数都按章推进、逐章提交,不拿写锁跨 LLM 调用(对齐 extractor 事务纪律);
单章失败不中断整批,留痕后继续。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.db.models import Chapter, ChapterState, Outline, Project
from app.engines.consistency.checker import (
    _normalize_issue,
    blockers_of,
    check_chapter,
    persist_issues,
)
from app.engines.consistency.extractor import parse_llm_json
from app.engines.pipeline.chapter import _rolling_summary
from app.engines.pipeline.handoff import _fresh_contract, extract_handoff_contract
from app.llm.router import Task, get_adapter_for

logger = logging.getLogger("jarvis-write.diagnosis")


def _written_chapters(db: Session, project_id: int) -> list[Chapter]:
    return (
        db.query(Chapter)
        .filter(Chapter.project_id == project_id, Chapter.final_content != "")
        .order_by(Chapter.chapter_number)
        .all()
    )


def chapters_missing_contract(db: Session, project_id: int) -> list[int]:
    """缺有效契约的已成文章号(从未提取/提取失败/指纹失效)。零 LLM,审核报告聚合用。"""
    missing = []
    for ch in _written_chapters(db, project_id):
        row = db.query(ChapterState).filter(ChapterState.chapter_id == ch.id).first()
        if _fresh_contract(row, ch) is None:
            missing.append(ch.chapter_number)
    return missing


async def diagnose_book(db: Session, project_id: int, progress=None) -> dict:
    """全书体检:逐章一致性检查,问题落库(source="diag")。

    返回 {scanned, with_issues, total_issues, total_blockers}。
    单章检查异常不中断整批(check_chapter 自身已降级返空,这里再兜一层)。
    """
    chapters = _written_chapters(db, project_id)
    total = len(chapters)
    with_issues: list[int] = []
    total_issues = 0
    total_blockers = 0
    for i, ch in enumerate(chapters, 1):
        if progress:
            progress(f"[{i}/{total}] 第 {ch.chapter_number} 章:一致性扫描")
        try:
            rolling = _rolling_summary(db, project_id, ch.chapter_number)
            db.commit()  # 结束读事务,不拿快照跨 LLM 调用
            issues = await check_chapter(
                db, project_id, ch.chapter_number, ch.final_content,
                rolling_summary=rolling,
            )
            persist_issues(db, ch, issues, source="diag", text=ch.final_content)
            db.commit()
        except Exception as exc:  # noqa: BLE001 — 单章失败不中断整批
            db.rollback()
            logger.warning("体检第 %d 章失败,跳过: %s", ch.chapter_number, exc)
            continue
        if issues:
            with_issues.append(ch.chapter_number)
            total_issues += len(issues)
            total_blockers += len(blockers_of(issues))
    logger.info(
        "全书体检完成:扫 %d 章,%d 章有问题,共 %d 个(blocker %d)",
        total, len(with_issues), total_issues, total_blockers,
    )
    return {
        "scanned": total,
        "with_issues": with_issues,
        "total_issues": total_issues,
        "total_blockers": total_blockers,
    }


async def backfill_contracts(db: Session, project_id: int, progress=None) -> dict:
    """批量补提取契约:已有有效契约的章跳过,其余逐章重提(失败留痕不中断)。

    返回 {extracted, skipped, failed}(章号列表)。
    """
    chapters = _written_chapters(db, project_id)
    total = len(chapters)
    extracted: list[int] = []
    skipped: list[int] = []
    failed: list[int] = []
    adapter = get_adapter_for(Task.HANDOFF_EXTRACT)
    for i, ch in enumerate(chapters, 1):
        row = db.query(ChapterState).filter(ChapterState.chapter_id == ch.id).first()
        if _fresh_contract(row, ch) is not None:
            skipped.append(ch.chapter_number)
            continue
        if progress:
            progress(f"[{i}/{total}] 第 {ch.chapter_number} 章:提取章末契约")
        try:
            # 内部自管事务(入口 commit / purge 后 commit / 落库 commit),且
            # LLM 或解析失败只落 failed 行不抛异常
            await extract_handoff_contract(
                db, ch, ch.chapter_number, ch.final_content, adapter
            )
        except Exception as exc:  # noqa: BLE001 — 兜底:单章失败不中断整批
            db.rollback()
            logger.warning("补提第 %d 章契约异常,跳过: %s", ch.chapter_number, exc)
            failed.append(ch.chapter_number)
            continue
        row = db.query(ChapterState).filter(ChapterState.chapter_id == ch.id).first()
        if row is not None and row.extract_status == "ok":
            extracted.append(ch.chapter_number)
        else:
            failed.append(ch.chapter_number)
    logger.info(
        "批量补契约完成:成功 %d,跳过 %d,失败 %d",
        len(extracted), len(skipped), len(failed),
    )
    return {"extracted": extracted, "skipped": skipped, "failed": failed}


async def rule_scan_book(db: Session, project_id: int, progress=None) -> dict:
    """规则扫描:逐章对照「世界观硬规则」体检正文,问题落库(source="rules")。

    与全书体检(跨章矛盾)互补:体检查的是"与圣经/契约/上文矛不矛盾",
    错得前后一致就抓不到;规则扫描查的是"是否违反作者钉死的设定/常识",
    理科生背政治、高考天数写错这类全文性事实错误即属此类。

    复用 checker._normalize_issue 做防幻觉举证(evidence 必须是正文逐字引用)。
    返回 {scanned, with_issues, total_issues, total_blockers}。
    """
    from app.prompts.consistency import RULE_SCAN_PROMPT  # noqa: PLC0415 — 避免环

    project = db.get(Project, project_id)
    rules = (project.world_rules or "").strip() if project else ""
    if not rules:
        raise RuntimeError("尚未设置世界观硬规则,请先在「审核报告」页填写")

    chapters = _written_chapters(db, project_id)
    titles = {
        o.chapter_number: o.title
        for o in db.query(Outline).filter(Outline.project_id == project_id).all()
    }
    total = len(chapters)
    with_issues: list[int] = []
    total_issues = 0
    total_blockers = 0
    adapter = get_adapter_for(Task.CONSISTENCY)
    for i, ch in enumerate(chapters, 1):
        if progress:
            progress(f"[{i}/{total}] 第 {ch.chapter_number} 章:规则扫描")
        try:
            db.commit()  # 结束读事务,不拿快照跨 LLM 调用
            prompt = RULE_SCAN_PROMPT.format(
                world_rules=rules,
                chapter_number=ch.chapter_number,
                title=titles.get(ch.chapter_number) or f"第{ch.chapter_number}章",
                chapter_text=ch.final_content[:12000],
            )
            raw = await adapter.ask(prompt)
            data = parse_llm_json(raw) or {}
            issues = [
                _normalize_issue(it, ch.final_content)
                for it in (data.get("issues") or [])
                if isinstance(it, dict)
            ]
            issues = [it for it in issues if it["description"]]
            persist_issues(db, ch, issues, source="rules", text=ch.final_content)
            db.commit()
        except Exception as exc:  # noqa: BLE001 — 单章失败不中断整批
            db.rollback()
            logger.warning("规则扫描第 %d 章失败,跳过: %s", ch.chapter_number, exc)
            continue
        if issues:
            with_issues.append(ch.chapter_number)
            total_issues += len(issues)
            total_blockers += len(blockers_of(issues))
    logger.info(
        "规则扫描完成:扫 %d 章,%d 章违反,共 %d 个(blocker %d)",
        total, len(with_issues), total_issues, total_blockers,
    )
    return {
        "scanned": total,
        "with_issues": with_issues,
        "total_issues": total_issues,
        "total_blockers": total_blockers,
    }
